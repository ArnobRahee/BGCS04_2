"""TravelPlanner: the coordinator. No math or AI itself; it calls the right class in order.

Trip lifecycle (trips.status):
  PLANNING --select_package--> AWAITING_APPROVAL --approve--> BOOKED
      ^                              |   (provider fails / coach cancels)
      +------------------------------+
  Players see hotel, restaurant and times only once the coach publishes a BOOKED trip.
"""
import copy
import math
from datetime import datetime, timedelta

from aivana_client import AivanaClient
from booking_service import BookingService, MockBookingProvider
from cost_calc import CostCalc
from models import TravelRequest, parse_dt
from google_places import maps_link
from travel_data import TravelData


class PlanError(ValueError):
    pass


class NotFound(PlanError):
    """A trip that doesn't exist or belongs to another team (both look the same: 404)."""


# Sensible limits for one away trip. They stop typos (100000 players, 365 nights)
# from producing absurd packages; the form shows the same limits.
LIMITS = {
    "num_players": (0, 150, "players"),
    "num_coaches": (0, 30, "coaches"),
    "num_staff": (0, 50, "staff"),
    "nights": (0, 14, "hotel nights"),
    "meals": (0, 30, "team meals"),
}
MAX_BUDGET = 1_000_000
# Match times are local wall-clock times; the server may run in UTC, so allow
# half a day of slack before calling a date "in the past".
PAST_GRACE = timedelta(hours=12)


def _count(form, key, default):
    value = form.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        raise ValueError(key)
    number = float(value)
    if not number.is_integer():
        raise PlanError(f"Use a whole number for {LIMITS[key][2]}.")
    return int(number)


def validate_trip(row: dict, now: datetime | None = None, check_past: bool = True) -> None:
    """Raise PlanError with a plain message when a trip's details don't make sense."""
    now = now or datetime.now()
    if not row["opponent"]:
        raise PlanError("Enter the opponent.")
    if not row.get("venue_name"):
        raise PlanError("Enter the venue name.")
    if not (-90 <= row["venue_lat"] <= 90) or not (-180 <= row["venue_lng"] <= 180):
        raise PlanError("The venue location is invalid (latitude -90 to 90, longitude -180 to 180).")
    for key, (low, high, name) in LIMITS.items():
        if row[key] < 0:
            raise PlanError("Numbers can't be negative.")
        if not low <= row[key] <= high:
            raise PlanError(f"Enter between {low} and {high} {name}.")
    if row["num_players"] + row["num_coaches"] + row["num_staff"] == 0:
        raise PlanError("Add at least one traveler.")
    if not math.isfinite(row["budget"]) or row["budget"] < 0:
        raise PlanError("The budget can't be negative.")
    if row["budget"] > MAX_BUDGET:
        raise PlanError(f"Enter a budget up to ${MAX_BUDGET:,}.")
    match_at = datetime.fromisoformat(row["match_at"])
    if check_past and match_at < now - PAST_GRACE:
        raise PlanError("The match date is in the past. Pick an upcoming game.")
    if row.get("depart_after"):
        depart = datetime.fromisoformat(row["depart_after"])
        if depart >= match_at:
            raise PlanError("The bus must be able to leave before the match starts.")
        if check_past and depart < now - PAST_GRACE:
            raise PlanError("The earliest departure time is in the past.")


class TravelPlanner:
    def __init__(self, db, data: TravelData | None = None, aivana: AivanaClient | None = None):
        self.db = db
        self.data = data or TravelData(db)
        self.calc = CostCalc(self.data)
        self.aivana = aivana or AivanaClient()
        self.bookings = BookingService(db, MockBookingProvider(self.data))

    # -- reads -------------------------------------------------------------
    def team(self, team_id):
        rows = self.db.select("teams", {"id": team_id})
        if not rows:
            raise PlanError("Team not found.")
        return rows[0]

    def trip(self, trip_id, team_id):
        rows = self.db.select("trips", {"id": trip_id, "team_id": team_id})
        if not rows:
            raise NotFound("Trip not found.")
        return rows[0]

    def packages(self, trip_id):
        return self.db.select("trip_packages", {"trip_id": trip_id}, order="package_no.asc")

    def package(self, trip, package_no=None):
        no = package_no if package_no is not None else trip.get("selected_package_no")
        rows = self.db.select("trip_packages", {"trip_id": trip["id"], "package_no": no})
        if not rows:
            raise PlanError("Package not found. Re-plan the trip and pick again.")
        return rows[0]

    def team_players(self, team_id):
        return self.db.select("profiles", {"team_id": team_id, "role": "player"}, order="full_name.asc")

    # -- planning ----------------------------------------------------------
    def create_trip(self, team: dict, coach: dict, form: dict) -> dict:
        venue = self._venue_from_form(form)
        players = self.team_players(team["id"])
        diet = sorted({t for p in players for t in (p.get("diet_tags") or [])} | set(form.get("diet_tags") or []))
        try:
            row = {
                "team_id": team["id"], "created_by": coach["id"],
                "opponent": str(form["opponent"]).strip(),
                **venue,
                "match_at": parse_dt(form["match_at"]).isoformat(timespec="minutes"),
                "depart_after": (parse_dt(form.get("depart_after")).isoformat(timespec="minutes")
                                 if form.get("depart_after") else None),
                "nights": _count(form, "nights", 1),
                "budget": float(form.get("budget") or 0),
                "num_players": _count(form, "num_players", len(players)),
                "num_coaches": _count(form, "num_coaches", 1),
                "num_staff": _count(form, "num_staff", 0),
                "meals": _count(form, "meals", 1),
                "diet_tags": diet,
                "status": "PLANNING", "published": False, "selected_package_no": None,
                "ncaa_game_id": str(form["ncaa_game_id"]) if form.get("ncaa_game_id") else None,
            }
        except KeyError as exc:
            raise PlanError(f"Missing trip detail: {exc.args[0]}.") from exc
        except PlanError:
            raise
        except (ValueError, TypeError, AttributeError, OverflowError) as exc:
            raise PlanError("Check the match date/time and the numbers in the form.") from exc
        validate_trip(row)
        for other in self.db.select("trips", {"team_id": team["id"]}):
            if (other.get("status") != "CANCELLED" and other["opponent"].strip().lower() == row["opponent"].lower()
                    and str(other["match_at"])[:16].replace(" ", "T") == row["match_at"][:16]):
                raise PlanError(f"You already have a trip for {other['opponent']} at that date and time. "
                                "Open it from My Trips instead of creating a copy.")
        trip = self.db.insert("trips", row)[0]
        if players:
            self.db.insert("trip_participants", [{"trip_id": trip["id"], "player_id": p["id"], "rsvp": "maybe",
                                                  "room_no": None, "bus_no": None}
                                                 for p in players])
        return self.plan(trip, team)

    def _venue_from_form(self, form):
        if form.get("venue_id"):
            v = self.data.venues.get(form["venue_id"])
            if not v:
                raise PlanError("Unknown venue.")
            return {"venue_name": v["name"], "venue_address": v.get("address") or "",
                    "venue_lat": float(v["lat"]), "venue_lng": float(v["lng"])}
        try:
            return {"venue_name": str(form["venue_name"]).strip(), "venue_address": str(form.get("venue_address") or ""),
                    "venue_lat": float(form["venue_lat"]), "venue_lng": float(form["venue_lng"])}
        except (KeyError, ValueError, TypeError) as exc:
            raise PlanError("Pick a venue, or enter a venue name with latitude and longitude.") from exc

    def plan(self, trip: dict, team: dict) -> dict:
        """(Re)build packages and the AI pick. Clears any earlier selection."""
        request = TravelRequest.from_trip(trip, team)
        packages = self.calc.generate_packages(request)
        self.db.delete("trip_packages", {"trip_id": trip["id"]})
        if packages:
            self.db.insert("trip_packages", [p.to_row(trip["id"]) for p in packages])
        pkg_dicts = [{"package_no": p.package_no, "total_cost": p.total_cost, "detail": p.detail} for p in packages]
        brief = {"opponent": request.opponent, "venue": request.venue_name, "travelers": request.total_travelers(),
                 "nights": request.nights, "budget": request.budget, "diet_tags": request.diet_tags}
        rec = self.aivana.recommend(brief, pkg_dicts) if packages else None
        hotel_source = "google" if any((p.detail.get("hotel") or {}).get("source") == "google" for p in packages) else "catalog"
        if rec is not None:
            rec = {**rec, "hotel_source": hotel_source, "hotel_note": self.data.hotel_warning}
        return self.db.update("trips", {"ai_recommendation": rec, "status": "PLANNING", "selected_package_no": None},
                              {"id": trip["id"]})[0]

    # -- what-if changes (proposed by Aivana in chat, applied only when the coach confirms) ----
    EDITABLE = ("nights", "meals", "budget", "num_players", "num_coaches", "num_staff", "depart_after")

    def clean_changes(self, trip: dict, changes) -> dict:
        """Keep only editable trip fields, convert them, and drop ones that don't change anything."""
        if not isinstance(changes, dict) or not changes:
            raise PlanError("No changes to apply.")
        unknown = set(changes) - set(self.EDITABLE)
        if unknown:
            raise PlanError(f"These details can't be changed here: {', '.join(sorted(unknown))}.")
        out = {}
        try:
            for key, value in changes.items():
                if key == "budget":
                    out[key] = float(value if value not in (None, "") else 0)
                elif key == "depart_after":
                    out[key] = parse_dt(value).isoformat(timespec="minutes") if value not in (None, "") else None
                else:
                    out[key] = _count(changes, key, trip[key])
        except PlanError:
            raise
        except (ValueError, TypeError, AttributeError, OverflowError) as exc:
            raise PlanError("Check the numbers in the change.") from exc
        out = {k: v for k, v in out.items() if v != trip.get(k) and not (k == "budget" and float(trip.get(k) or 0) == v)}
        if not out:
            raise PlanError("That's already how the trip is set up.")
        merged = {**trip, **out, "match_at": parse_dt(trip["match_at"]).isoformat(timespec="minutes"),
                  "budget": float(out.get("budget", trip.get("budget") or 0)),
                  "venue_lat": float(trip["venue_lat"]), "venue_lng": float(trip["venue_lng"])}
        validate_trip(merged, check_past="depart_after" in out)
        return out

    def preview_changes(self, trip: dict, team: dict, changes) -> dict:
        """Price the trip with the changes applied, without saving anything."""
        if trip["status"] != "PLANNING":
            raise PlanError("Cancel the current bookings before changing the trip.")
        clean = self.clean_changes(trip, changes)
        after = self.calc.generate_packages(TravelRequest.from_trip({**trip, **clean}, team))
        before = self.packages(trip["id"])
        summary = lambda pkgs: {(p["detail"] if isinstance(p, dict) else p.detail).get("highlight"):
                                round(float(p["total_cost"] if isinstance(p, dict) else p.total_cost), 2)
                                for p in pkgs if (p["detail"] if isinstance(p, dict) else p.detail).get("highlight")}
        return {"changes": clean, "before": {k: trip.get(k) for k in clean},
                "options_before": summary(before), "options_after": summary(after), "packages_after": len(after),
                "within_budget_after": sum(1 for p in after if p.detail["within_budget"])}

    def apply_changes(self, trip: dict, team: dict, changes) -> dict:
        """Save the confirmed changes and re-price every package (clears any earlier pick)."""
        if trip["status"] != "PLANNING":
            raise PlanError("Cancel the current bookings before changing the trip.")
        clean = self.clean_changes(trip, changes)
        trip = self.db.update("trips", clean, {"id": trip["id"]})[0]
        return self.plan(trip, team)

    def replan_with_rsvps(self, trip: dict, team: dict) -> dict:
        going = self.db.select("trip_participants", {"trip_id": trip["id"], "rsvp": "going"})
        if going:
            trip = self.db.update("trips", {"num_players": len(going)}, {"id": trip["id"]})[0]
        return self.plan(trip, team)

    # -- booking -----------------------------------------------------------
    def select_package(self, trip: dict, package_no: int, coach_id: str) -> dict:
        if trip["status"] == "BOOKED":
            raise PlanError("This trip is already booked. Cancel the bookings first.")
        pkg = self.package(trip, package_no)
        rec = trip.get("ai_recommendation") or {}
        ai = rec.get("source") == "aivana" and rec.get("package_no") == package_no
        self.bookings.request(trip, pkg, coach_id, ai_pick=ai)
        return self.db.update("trips", {"selected_package_no": package_no, "status": "AWAITING_APPROVAL"},
                              {"id": trip["id"]})[0]

    def approve(self, trip: dict, coach_id: str) -> dict:
        if trip["status"] != "AWAITING_APPROVAL":
            raise PlanError("Pick a package first.")
        result = self.bookings.approve(trip, self.package(trip), coach_id)
        status = "BOOKED" if result["ok"] else "PLANNING"
        result["trip"] = self.db.update("trips", {"status": status}, {"id": trip["id"]})[0]
        if not result["ok"]:
            failed = [b["summary"] for b in result["bookings"] if b["status"] == "FAILED"]
            result["error"] = "Booking failed, nothing was charged: " + "; ".join(failed) + ". Pick another package."
        return result

    def cancel_bookings(self, trip: dict) -> dict:
        self.bookings.cancel(trip)
        return self.db.update("trips", {"status": "PLANNING", "published": False}, {"id": trip["id"]})[0]

    def assign_rooms(self, trip: dict) -> list:
        """Two players per room in name order; buses filled in the same order."""
        if not trip.get("selected_package_no"):
            raise PlanError("Pick a package first.")
        detail = self.package(trip)["detail"]
        parts = self.db.select("trip_participants", {"trip_id": trip["id"]})
        going = [p for p in parts if p["rsvp"] == "going"] or [p for p in parts if p["rsvp"] != "not_going"]
        names = {p["id"]: p["full_name"] for p in self.team_players(trip["team_id"])}
        going.sort(key=lambda p: names.get(p["player_id"], ""))
        seats = int(detail["bus"]["seats"])
        self.db.update("trip_participants", {"room_no": None, "bus_no": None}, {"trip_id": trip["id"]})
        for i, p in enumerate(going):
            values = {"room_no": (i // 2 + 1) if detail.get("hotel") else None, "bus_no": i // seats + 1}
            self.db.update("trip_participants", values, {"trip_id": trip["id"], "player_id": p["player_id"]})
        return self.db.select("trip_participants", {"trip_id": trip["id"]})

    # -- players -----------------------------------------------------------
    def publish(self, trip: dict, author_id: str, title: str, body: str) -> dict:
        if trip["status"] != "BOOKED":
            raise PlanError("Approve the bookings before publishing the trip to players.")
        self.db.update("trips", {"published": True}, {"id": trip["id"]})
        return self.db.insert("announcements", {"trip_id": trip["id"], "team_id": trip["team_id"],
                                                 "author_id": author_id, "title": title, "body": body})[0]

    def draft_announcement(self, trip: dict) -> dict:
        if not trip.get("selected_package_no"):
            raise PlanError("Pick a package first.")
        return self.aivana.draft_announcement(trip, self.package(trip))

    def player_view(self, trip: dict, player: dict) -> dict:
        """What a player may see: never costs or budget; hotel/restaurant only once published."""
        mine = self.db.select("trip_participants", {"trip_id": trip["id"], "player_id": player["id"]})
        view = {k: trip[k] for k in ("id", "opponent", "venue_name", "venue_address", "venue_lat", "venue_lng",
                                     "match_at", "nights", "status", "published")}
        view["my"] = mine[0] if mine else None
        if not (trip["published"] and trip["status"] in ("BOOKED", "IN_PROGRESS", "COMPLETED")):
            return view
        d = self.package(trip)["detail"]
        view.update({"suggested_departure": d["suggested_departure"], "drive_min": d["drive_min"],
                     "bus": {k: d["bus"].get(k) for k in ("company", "vehicle", "wifi")}})
        if d.get("hotel"):
            view["hotel"] = self._hotel_public(d["hotel"])
        if d.get("restaurant"):
            r = d["restaurant"]
            view["restaurant"] = {k: r.get(k) for k in ("id", "name", "address", "lat", "lng", "cuisine", "km_to_venue")}
            view["restaurant"]["maps_url"] = maps_link(r)
            view["my_menu"] = self.data.menu(r["id"], player.get("diet_tags") or [])
        return view

    def _hotel_public(self, h):
        out = {k: h.get(k) for k in ("name", "address", "lat", "lng", "stars", "amenities", "km_to_venue",
                                     "rating", "user_rating_count", "source")}
        out["photo_url"] = self.data.places.photo_url(h.get("photo"))
        out["photo_author"] = (h.get("photo") or {}).get("author")
        out["photo_author_uri"] = (h.get("photo") or {}).get("author_uri")
        out["maps_url"] = maps_link(h)
        return out

    def _with_links(self, pkg):
        pkg = copy.deepcopy(pkg)
        h = (pkg.get("detail") or {}).get("hotel")
        if h:
            h["photo_url"] = self.data.places.photo_url(h.get("photo"))
            h["maps_url"] = maps_link(h)
        r = (pkg.get("detail") or {}).get("restaurant")
        if r:
            r["maps_url"] = maps_link(r)
        return pkg

    def coach_view(self, trip: dict) -> dict:
        parts = self.db.select("trip_participants", {"trip_id": trip["id"]})
        names = {p["id"]: p["full_name"] for p in self.team_players(trip["team_id"])}
        return {**trip,
                "packages": [self._with_links(p) for p in self.packages(trip["id"])],
                "bookings": self.db.select("bookings", {"trip_id": trip["id"]}, order="created_at.asc"),
                "participants": [{**p, "full_name": names.get(p["player_id"], "?")} for p in parts],
                "rsvp_counts": {s: sum(p["rsvp"] == s for p in parts) for s in ("going", "maybe", "not_going")},
                "rooms_needed": math.ceil((trip["num_players"] + trip["num_coaches"] + trip["num_staff"]) / 2)}
