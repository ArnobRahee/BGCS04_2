"""Help chatbot ("Ask Aivana"): answers questions about trips, teams and NCAA data, and can
take two safe actions:
  - coach:  request_booking -> creates PENDING_APPROVAL bookings (the coach still clicks Approve)
  - player: rsvp            -> going / maybe / not_going
It never approves or pays for anything. With an Aivana key the model writes the reply and picks
the action (as JSON); without one, a small rule-based fallback handles common questions.
Players' context never includes prices or budget.
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
            return {"reply": "Ask me something about your trips or your team.", "source": "local"}
        ctx = self.context(profile, team, focus_trip_id)
        if self.aivana.configured:
            try:
                reply, action = self._ask_ai(profile, ctx, messages)
                source = "aivana"
            except Exception as exc:                                   # fall back rather than fail
                reply, action = self._local(profile, team, ctx, messages[-1]["content"])
                reply += f"\n\n(Aivana was unavailable: {exc}. Answered locally.)"
                source = "local"
        else:
            reply, action = self._local(profile, team, ctx, messages[-1]["content"])
            source = "local"
        result = self._run_action(profile, team, action) if action else None
        if result:
            reply = (reply + "\n\n" if reply else "") + result["message"]
        return {"reply": reply, "action": result, "source": source}

    # -- model -------------------------------------------------------------------
    def _ask_ai(self, profile, ctx, messages):
        role = profile["role"]
        system = (
            "You are Aivana, the travel assistant for a college sports team's away games. Be brief and friendly. "
            "Answer only from the JSON context; if something isn't there, say so. Never invent prices, times or hotels. "
            + ("Never mention money, prices or budget. " if role == "player" else "")
            + "You cannot approve or pay for bookings: the coach approves on the trip page. "
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

    # -- rule-based fallback -----------------------------------------------------
    def _local(self, profile, team, ctx, text):
        t = text.lower()
        trips = ctx["trips"]
        trip = _pick_trip(trips, t, ctx.get("focus_trip_id"))
        coach = profile["role"] == "coach"

        if re.search(r"\b(help|what can you do|hi|hello)\b", t):
            return (("I can compare packages, explain Aivana's pick, prepare a booking (\"book package 2\"), "
                     "and tell you your team's record, ranking and next games." if coach else
                     "I can tell you when the bus leaves, your hotel and room, dishes that fit your diet, "
                     "and record your RSVP (\"I'm going\")."), None)

        if re.search(r"\b(record|rank|ranking|standing|standings|schedule|next game|games?)\b", t) and team.get("ncaa_seo"):
            return self._ncaa_answer(team, t), None

        if not trip:
            return ("There are no trips yet." if not trips else
                    "Which trip? Mention the opponent, e.g. \"bus time for Ohio State\"."), None

        if coach:
            m = re.search(r"\b(book|choose|pick|select|go with)\b.*?\bpackage\s*(\d+)", t) or re.search(r"\bpackage\s*(\d+)\b.*\b(book|choose|pick)", t)
            if m:
                no = int(next(g for g in m.groups() if g and g.isdigit()))
                return "", {"type": "request_booking", "trip_id": trip["trip_id"], "package_no": no}
            if re.search(r"\b(book|go with)\b.*\b(pick|recommend|aivana|best)\b", t) and trip.get("ai_pick"):
                return "", {"type": "request_booking", "trip_id": trip["trip_id"], "package_no": trip["ai_pick"]["package_no"]}
            if re.search(r"\b(why|recommend|best|pick|suggest)\b", t) and trip.get("ai_pick"):
                return trip["ai_pick"].get("reason") or "Aivana picked the best-value package.", None
            if re.search(r"\b(packages?|options?|cheapest|compare|hotels?)\b", t):
                lines = [f"Package {p['package_no']}: ${p['total_cost']:,.2f} · {p['hotel'] or 'no hotel'}"
                         f"{' (' + str(p['hotel_km']) + ' km)' if p['hotel_km'] is not None else ''} · {p['restaurant'] or 'no meal'}"
                         f"{'' if p['within_budget'] else ' · over budget'}" for p in trip["packages"][:3]]
                return (f"Top packages for {trip['opponent']}:\n" + "\n".join(lines)) if lines else "No packages yet.", None
            if re.search(r"\b(status|approved|booked)\b", t):
                return f"{trip['opponent']}: {trip['status'].replace('_', ' ').lower()}.", None
            return (f"For {trip['opponent']} ({trip['status'].replace('_', ' ').lower()}): ask me to compare packages, "
                    "explain the pick, or \"book package N\"."), None

        # player
        m = re.search(r"\b(not going|can't go|cannot go|i'm going|im going|i am going|count me in|maybe)\b", t)
        if m:
            word = m.group(1)
            choice = "maybe" if word == "maybe" else ("not_going" if word in ("not going", "can't go", "cannot go") else "going")
            return "", {"type": "rsvp", "trip_id": trip["trip_id"], "rsvp": choice}
        if not trip.get("bus"):
            return f"The {trip['opponent']} trip is still being planned. You'll see the details here once your coach publishes them.", None
        if re.search(r"\b(bus|leave|leaves|depart|when)\b", t):
            return f"The bus ({trip['bus']['company']}) leaves {trip['suggested_departure'].replace('T', ' at ')}.", None
        if re.search(r"\b(hotel|room|stay|sleep)\b", t) and trip.get("hotel"):
            room = (trip.get("my") or {}).get("room_no")
            return (f"You're staying at {trip['hotel']['name']}, {trip['hotel']['km_to_venue']} km from the stadium"
                    + (f", room {room}." if room else ". Rooms haven't been assigned yet.")), None
        if re.search(r"\b(menu|eat|food|dinner|lunch|meal|dish)\b", t) and trip.get("restaurant"):
            dishes = [m["name"] for m in trip.get("my_menu") or [] if m.get("course") == "main"][:5]
            return (f"Team meal at {trip['restaurant']['name']}. "
                    + (f"Mains that fit your diet: {', '.join(dishes)}." if dishes else "No main dish fits all your needs; tell your coach.")), None
        return "Ask me when the bus leaves, where you're staying, or what you can eat.", None

    def _ncaa_answer(self, team, t):
        sport, div, seo = team.get("sport"), team.get("division"), team.get("ncaa_seo")
        try:
            if re.search(r"\b(schedule|next game|games?)\b", t):
                games = self.ncaa.team_games(sport, div, seo)
                upcoming = [g for g in games if g["state"] in ("pre", "")][:3]
                if not upcoming:
                    return "No games found for the next two weeks."
                return "Next games: " + "; ".join(
                    f"{'at' if g['is_away'] else 'vs'} {g['opponent']['short']} {g['start_date']} {g['start_time']}" for g in upcoming)
            s = self.ncaa.team_summary(sport, div, team.get("ncaa_name") or "")
            bits = []
            if s.get("record"):
                bits.append(f"record {s['record']}" + (f" ({s['conference']})" if s.get("conference") else ""))
            if s.get("rank"):
                bits.append(f"ranked #{s['rank']} in {s.get('poll') or 'the poll'}")
            return f"{team['ncaa_name']}: " + (", ".join(bits) if bits else "not listed in the current standings or rankings") + "."
        except NCAAError as exc:
            return f"I couldn't reach NCAA data right now ({exc})."


def _pkg_brief(p):
    d = p["detail"]
    return {"package_no": p["package_no"], "total_cost": float(p["total_cost"]), "within_budget": d["within_budget"],
            "hotel": (d.get("hotel") or {}).get("name"), "hotel_km": (d.get("hotel") or {}).get("km_to_venue"),
            "restaurant": (d.get("restaurant") or {}).get("name"),
            "missing_diets": (d.get("restaurant") or {}).get("missing_diets"), "departure": d.get("suggested_departure")}


def _pick_trip(trips, text, focus_id):
    for t in trips:
        if t["opponent"].lower() in text:
            return t
    for t in trips:
        if t["trip_id"] == focus_id:
            return t
    active = [t for t in trips if t.get("status") not in ("COMPLETED", "CANCELLED")]
    return (active or trips or [None])[0]


def _strip_fences(raw):
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw).rsplit("```", 1)[0]
    return raw.strip()
