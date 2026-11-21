"""Chat panel backend. Every answer comes from your Aivana API: this module only builds the
context (the team's trips and NCAA info) and carries out two safe actions Aivana may ask for:
  - main planner: request_booking -> PENDING_APPROVAL bookings (a person still clicks Approve)
  - player:       rsvp            -> going / maybe / not_going
It never approves or pays for anything. If Aivana isn't connected (no AIVANA_API_KEY) or a
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
        coach = profile["role"] == "coach"
        for t in trips:
            if coach:
                item = {"trip_id": t["id"], "opponent": t["opponent"], "venue": t["venue_name"], "venue_address": t.get("venue_address"),
                        "match_at": t["match_at"], "status": t["status"], "published_to_players": t.get("published"),
                        "budget": t["budget"], "nights": t["nights"], "team_meals": t["meals"],
                        "travelers": {"players": t["num_players"], "coaches": t["num_coaches"], "staff": t["num_staff"]},
                        "diet_needs": t.get("diet_tags") or [],
                        "recommendation": t.get("ai_recommendation"), "selected_package": t.get("selected_package_no"),
                        "priced_packages": [_pkg_brief(p) for p in self.planner.packages(t["id"])]}
            else:
                item = self.planner.player_view(t, profile)
                item = {k: v for k, v in item.items() if k not in ("venue_lat", "venue_lng")}
                item["trip_id"] = item.pop("id")
            item["area"] = self._area(t, coach)
            ctx["trips"].append(item)
        ctx["announcements"] = [{"title": a["title"], "body": (a.get("body") or "")[:600], "posted": a.get("created_at")}
                                for a in self.db.select("announcements", {"team_id": team["id"]}, order="created_at.desc", limit=5)]
        return ctx

    def _area(self, trip, coach):
        """Every hotel and restaurant the planner knows near the venue (not just the priced packages),
        so "closest hotel" or "is there a halal place" questions are answered from real data."""
        data, lat, lng = self.planner.data, float(trip["venue_lat"]), float(trip["venue_lng"])
        diets = trip.get("diet_tags") or []
        hotels = []
        for h in data.hotels_near(lat, lng)[:12]:
            row = {k: h.get(k) for k in ("name", "address", "km_to_venue", "drive_min_to_venue", "stars", "rating", "amenities")}
            if coach:
                row.update({"price_per_night": h.get("price_per_night"), "price_is_estimate": bool(h.get("price_estimated")),
                            "rooms_available": h.get("rooms_available")})
            hotels.append({k: v for k, v in row.items() if v not in (None, "", [])})
        rests = []
        for r in data.restaurants_near(lat, lng)[:12]:
            row = {"name": r["name"], "cuisine": r.get("cuisine"), "km_to_venue": r.get("km_to_venue"),
                   "halal_certified": r.get("halal_certified"), "no_main_dish_for": data.missing_diets(r["id"], diets)}
            if coach:
                row.update({"price_per_person": r.get("price_per_person"), "group_capacity": r.get("group_capacity")})
            rests.append(row)
        return {"hotels_near_venue_by_distance": hotels, "restaurants_near_venue_by_distance": rests}

    # -- entry point -----------------------------------------------------------
    def handle(self, profile, team, messages, focus_trip_id=None):
        messages = [m for m in messages if m.get("role") in ("user", "assistant") and str(m.get("content") or "").strip()][-12:]
        if not messages or messages[-1]["role"] != "user":
            return {"reply": "Ask something about your trips or your team.", "action": None, "source": "none"}
        if not self.aivana.configured:
            return {"reply": "Aivana isn't connected yet, so the assistant can't answer. "
                             + ("Add AIVANA_API_KEY to the server's .env file and restart it." if profile["role"] == "coach"
                                else "Ask your team planner."), "action": None, "source": "not_connected"}
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
            "You answer questions inside a college sports team's away-game travel planner. Be brief and friendly. "
            "You have two sources. (1) TEAM DATA: the JSON context sent with the question. It is the only source for "
            "this team's trips, packages, prices, bookings, rooms, bus times, roster and announcements; never contradict "
            "it or add details to it. priced_packages are the hotel+restaurant combinations the planner priced; "
            "area lists every hotel and restaurant the planner knows near the venue, sorted by distance. "
            "(2) WEB SEARCH: use it for general facts that are not in the team data (weather, other teams and coaches, "
            "rankings news, local places, travel rules). Start that part with 'From the web:'. "
            "If neither source has the answer, say you don't know. Never guess numbers, times, names or prices. "
            + ("Never mention money, prices or budget. " if role == "player" else "")
            + "You cannot approve or pay for bookings: the team's main planner approves on the trip page. "
            + ("Only the main planner may prepare bookings; you_are_main_planner tells you if this user is. " if role == "coach" else "")
            + ACTIONS_HELP[role]
            + ' Reply ONLY with JSON: {"reply": "<text for the user>", "action": null or the action object}.'
        )
        # Aivana caps the system prompt at 8,000 characters, so the team's data travels with the
        # latest question instead (messages may be much longer).
        convo = [dict(m) for m in messages]
        convo[-1]["content"] = ("Context (JSON, the app's current data):\n" + json.dumps(ctx, default=str)
                                + "\n\nQuestion: " + convo[-1]["content"])
        raw = self.aivana.complete([{"role": "system", "content": system}] + convo, web_search=self.aivana.web_search)
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
    h, r, b = d.get("hotel") or {}, d.get("restaurant") or {}, d.get("bus") or {}
    return {"package_no": p["package_no"], "total_cost": float(p["total_cost"]), "within_budget": d["within_budget"],
            "costs": d.get("costs"), "rooms": d.get("rooms"),
            "hotel": h.get("name"), "hotel_km": h.get("km_to_venue"), "hotel_stars": h.get("stars"), "hotel_rating": h.get("rating"),
            "hotel_price_is_estimate": bool(h.get("price_estimated")),
            "restaurant": r.get("name"), "cuisine": r.get("cuisine"), "missing_diets": r.get("missing_diets"),
            "bus": f"{b.get('count')} x {b.get('company')} {b.get('vehicle')}", "departure": d.get("suggested_departure"),
            "arrive_by": d.get("arrive_by"), "late_arrival": d.get("late_arrival")}


def _strip_fences(raw):
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw).rsplit("```", 1)[0]
    return raw.strip()
