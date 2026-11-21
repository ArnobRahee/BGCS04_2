"""Chat panel backend. Every answer comes from your Aivana API: this module only builds the
context (the team's trips and NCAA info) and carries out two safe actions Aivana may ask for:
  - main planner: request_booking -> PENDING_APPROVAL bookings (a person still clicks Approve)
  - player:       rsvp            -> going / maybe / not_going
It never approves or pays for anything. If Aivana isn't connected (no AIVANA_API_URL/KEY) or a
call fails, the reply says so; there are no made-up answers. Players' context never has prices.
"""
import json
import re

from ncaa_client import NCAAError, SPORTS
from travel_planner import PlanError

ACTIONS_HELP = {
    "coach": 'To prepare a booking reply with {"type": "request_booking", "trip_id": "<id>", "package_no": <n>}.',
    "player": 'To RSVP reply with {"type": "rsvp", "trip_id": "<id>", "rsvp": "going|maybe|not_going"}.',
}


class ChatAssistant:
    def __init__(self, db, planner, ncaa, aivana):
        self.db, self.planner, self.ncaa, self.aivana = db, planner, ncaa, aivana

    # -- context -------------------------------------------------------------
    def context(self, profile, team, focus_trip_id=None):
        trips = self.db.select("trips", {"team_id": team["id"]}, order="match_at.asc")
        ctx = {"you": {"name": profile["full_name"], "role": profile["role"], "diet": profile.get("diet_tags") or []},
               "team": {"name": team["name"], "sport": SPORTS.get(team.get("sport") or "", {}).get("name"),
                        "division": team.get("division"), "ncaa_school": team.get("ncaa_name")},
               "focus_trip_id": focus_trip_id, "trips": []}
        if profile["role"] == "coach":
            ctx["you"]["you_are_main_planner"] = team.get("owner_id") == profile["id"]
        if team.get("ncaa_seo") and self.ncaa is not None:
            try:
                ctx["ncaa"] = {"summary": self.ncaa.team_summary(team["sport"], team["division"], team.get("ncaa_name") or ""),
                               "games": [{k: g.get(k) for k in ("start_date", "start_time", "is_away", "state", "network")}
                                         | {"opponent": (g.get("opponent") or {}).get("short")}
                                         for g in self.ncaa.team_games(team["sport"], team["division"], team["ncaa_seo"])[:5]]}
            except NCAAError as exc:
                ctx["ncaa"] = {"error": str(exc)}
        for t in trips:
            if profile["role"] == "coach":
                item = {"trip_id": t["id"], "opponent": t["opponent"], "venue": t["venue_name"], "match_at": t["match_at"],
                        "status": t["status"], "budget": t["budget"], "travelers": t["num_players"] + t["num_coaches"] + t["num_staff"],
                        "ai_pick": t.get("ai_recommendation"), "selected_package": t.get("selected_package_no"),
                        "packages": [_pkg_brief(p) for p in self.planner.packages(t["id"])[:5]]}
            else:
                item = self.planner.player_view(t, profile)
                item = {k: v for k, v in item.items() if k not in ("venue_lat", "venue_lng")}
                item["trip_id"] = item.pop("id")
            ctx["trips"].append(item)
        return ctx

    # -- entry point -----------------------------------------------------------
    def handle(self, profile, team, messages, focus_trip_id=None):
        messages = [m for m in messages if m.get("role") in ("user", "assistant") and str(m.get("content") or "").strip()][-12:]
        if not messages or messages[-1]["role"] != "user":
            return {"reply": "Ask something about your trips or your team.", "action": None, "source": "none"}
        if not self.aivana.configured:
            return {"reply": "Aivana isn't connected yet, so the assistant can't answer. "
                             + ("Add AIVANA_API_URL and AIVANA_API_KEY to the server's .env file." if profile["role"] == "coach"
                                else "Ask your coach."), "action": None, "source": "not_connected"}
        ctx = self.context(profile, team, focus_trip_id)
        try:
            reply, action = self._ask_ai(profile, ctx, messages)
        except Exception as exc:
            return {"reply": f"Couldn't reach Aivana right now ({exc}). Try again in a moment.", "action": None, "source": "error"}
        source = "aivana"
        result = self._run_action(profile, team, action) if action else None
        if result:
            reply = (reply + "\n\n" if reply else "") + result["message"]
        return {"reply": reply, "action": result, "source": source}

    # -- model -------------------------------------------------------------------
    def _ask_ai(self, profile, ctx, messages):
        role = profile["role"]
        system = (
            "You are answering questions inside a college sports team's away-game travel planner. Be brief and friendly. "
            "Answer only from the JSON context; if something isn't there, say so. Never invent prices, times or hotels. "
            + ("Never mention money, prices or budget. " if role == "player" else "")
            + "You cannot approve or pay for bookings: the team's main planner approves on the trip page. "
            + ("Only the main planner may prepare bookings; you_are_main_planner tells you if this user is. " if role == "coach" else "")
            + ACTIONS_HELP[role]
            + ' Reply ONLY with JSON: {"reply": "<text for the user>", "action": null or the action object}.'
            + "\nContext: " + json.dumps(ctx, default=str)
        )
        raw = self.aivana.complete([{"role": "system", "content": system}] + messages)
        try:
            parsed = json.loads(_strip_fences(raw))
            return str(parsed.get("reply") or "").strip(), parsed.get("action")
        except (ValueError, AttributeError):
            return raw.strip(), None                                    # plain text answer, no action

    # -- actions -----------------------------------------------------------------
    def _run_action(self, profile, team, action):
        if not isinstance(action, dict):
            return None
        kind = action.get("type")
        try:
            trip = self.planner.trip(str(action.get("trip_id")), team["id"])
        except PlanError:
            return {"type": kind, "ok": False, "message": "I couldn't find that trip, so I didn't change anything."}
        if kind == "request_booking" and profile["role"] == "coach":
            if team.get("owner_id") != profile["id"]:
                return {"type": kind, "ok": False, "message": "Only the team's main planner can prepare bookings."}
            try:
                no = int(action.get("package_no"))
                if trip["status"] == "AWAITING_APPROVAL":
                    self.planner.cancel_bookings(trip)
                    trip = self.planner.trip(trip["id"], team["id"])
                self.planner.select_package(trip, no, profile["id"])
            except (PlanError, TypeError, ValueError) as exc:
                return {"type": kind, "ok": False, "message": f"I couldn't prepare that booking: {exc}"}
            return {"type": kind, "ok": True, "trip_id": trip["id"],
                    "message": f"Done: package {no} for the {trip['opponent']} trip is waiting for your approval. "
                               "Nothing is reserved until you click Approve on the trip page."}
        if kind == "rsvp" and profile["role"] == "player":
            choice = action.get("rsvp")
            if choice not in ("going", "maybe", "not_going"):
                return None
            key = {"trip_id": trip["id"], "player_id": profile["id"]}
            if self.db.select("trip_participants", key):
                self.db.update("trip_participants", {"rsvp": choice}, key)
            else:
                self.db.insert("trip_participants", {**key, "rsvp": choice, "room_no": None, "bus_no": None})
            return {"type": kind, "ok": True, "trip_id": trip["id"],
                    "message": f"RSVP saved for the {trip['opponent']} trip: {choice.replace('_', ' ')}."}
        return None


def _pkg_brief(p):
    d = p["detail"]
    return {"package_no": p["package_no"], "total_cost": float(p["total_cost"]), "within_budget": d["within_budget"],
            "hotel": (d.get("hotel") or {}).get("name"), "hotel_km": (d.get("hotel") or {}).get("km_to_venue"),
            "restaurant": (d.get("restaurant") or {}).get("name"),
            "missing_diets": (d.get("restaurant") or {}).get("missing_diets"), "departure": d.get("suggested_departure")}


def _strip_fences(raw):
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw).rsplit("```", 1)[0]
    return raw.strip()
