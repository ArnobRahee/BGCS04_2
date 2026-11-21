"""CostCalc: all the arithmetic. The AI never does math.

Rules (change them here, in one place):
- Rooms: 2 people per room (hotel.room_capacity).
- Bus: ceil(travelers / seats) buses; each costs max(min_charge, rate_per_km x round-trip road km)
  plus the driver's hotel room (driver_overnight_fee) for every night away.
- Hotel: rooms x price_per_night x nights. Day trips (0 nights) have no hotel.
- Meals: travelers x restaurant price_per_person x team meals.
- Timing: overnight trips arrive by 6 PM the day before the match; day trips arrive 3 hours
  before kickoff. The bus never leaves before the trip's depart_after time.
- Three highlighted options, always different packages (and different hotels when possible):
  lowest_cost (smallest total), closest (hotel nearest the venue; the team-meal restaurant on
  day trips) and best_value (lowest value_score, see value_scores()).
"""
import math
from datetime import datetime, timedelta
from itertools import product

from geo import drive_minutes, road_km
from models import TravelPackage, TravelRequest
from travel_data import TravelData

HOTEL_RADIUS_KM = 25
RESTAURANT_RADIUS_KM = 15
MAX_HOTELS = 6
MAX_RESTAURANTS = 4
MAX_PACKAGES = 12

# Best-value weights (lower score is better). Each part is scaled 0..1 across the packages.
VALUE_WEIGHTS = {"cost": 0.45, "distance": 0.25, "diet": 0.15, "quality": 0.10, "late": 0.05}
OVER_BUDGET_PENALTY = 0.5
HIGHLIGHTS = ("lowest_cost", "closest", "best_value")


class CostCalc:
    def __init__(self, data: TravelData):
        self.data = data

    # -- the pieces --------------------------------------------------------
    @staticmethod
    def calc_rooms(request: TravelRequest, room_capacity: int = 2) -> int:
        return math.ceil(request.total_travelers() / max(1, room_capacity)) if request.nights > 0 else 0

    @staticmethod
    def calc_buses(request: TravelRequest, bus: dict) -> int:
        return math.ceil(request.total_travelers() / int(bus["seats"]))

    def bus_cost(self, request: TravelRequest, bus: dict, route_km: float) -> float:
        per_bus = max(float(bus["min_charge"]), float(bus["rate_per_km"]) * route_km * 2)
        per_bus += float(bus["driver_overnight_fee"]) * request.nights
        return round(self.calc_buses(request, bus) * per_bus, 2)

    @staticmethod
    def hotel_cost(request: TravelRequest, hotel: dict, rooms: int) -> float:
        return round(rooms * float(hotel["price_per_night"]) * request.nights, 2)

    @staticmethod
    def meal_cost(request: TravelRequest, restaurant: dict) -> float:
        return round(request.total_travelers() * float(restaurant["price_per_person"]) * request.meals, 2)

    @staticmethod
    def timing(request: TravelRequest, drive_min: int) -> dict:
        if request.nights > 0:
            arrive_by = datetime.combine(request.match_at.date() - timedelta(days=1), datetime.min.time()) + timedelta(hours=18)
        else:
            arrive_by = request.match_at - timedelta(hours=3)
        depart = arrive_by - timedelta(minutes=drive_min + 30)          # 30 min buffer for loading/traffic
        late = False
        if request.depart_after and depart < request.depart_after:
            depart = request.depart_after
            late = depart + timedelta(minutes=drive_min) > arrive_by
        return {"suggested_departure": depart.isoformat(timespec="minutes"),
                "arrive_by": arrive_by.isoformat(timespec="minutes"), "late_arrival": late}

    def calc_total(self, bus_cost: float, hotel_cost: float, meal_cost: float) -> float:
        return round(bus_cost + hotel_cost + meal_cost, 2)

    # -- candidates --------------------------------------------------------
    def candidate_hotels(self, request: TravelRequest) -> list:
        if request.nights == 0:
            return [None]
        hotels = [h for h in self.data.hotels_near(request.venue_lat, request.venue_lng, HOTEL_RADIUS_KM)
                  if h.get("rooms_available") is None            # Google hotels: availability unknown
                  or int(h["rooms_available"]) >= self.calc_rooms(request, int(h["room_capacity"]))]
        # best value first: cheap, close, decent stars
        hotels.sort(key=lambda h: float(h["price_per_night"]) + 8 * h["km_to_venue"] - 10 * int(h.get("stars") or 0))
        return hotels[:MAX_HOTELS]

    def candidate_restaurants(self, request: TravelRequest) -> list:
        if request.meals == 0:
            return [None]
        near = self.data.restaurants_near(request.venue_lat, request.venue_lng, RESTAURANT_RADIUS_KM)
        def seats_group(r):
            return r.get("takes_group_reservations", True) and int(r.get("group_capacity") or 0) >= request.total_travelers()
        # If nothing nearby seats the whole group, still show the options but flag them.
        fits = [r for r in near if seats_group(r)] or near
        for r in fits:
            r["missing_diets"] = self.data.missing_diets(r["id"], request.diet_tags)
            r["fits_group"] = bool(seats_group(r))
        fits.sort(key=lambda r: (len(r["missing_diets"]), float(r["price_per_person"]), r["km_to_venue"]))
        return fits[:MAX_RESTAURANTS]

    # -- packages ----------------------------------------------------------
    def generate_packages(self, request: TravelRequest) -> list[TravelPackage]:
        route = road_km(request.home_lat, request.home_lng, request.venue_lat, request.venue_lng)
        drive = drive_minutes(route)
        timing = self.timing(request, drive)

        buses = sorted(((b, self.bus_cost(request, b, route)) for b in self.data.bus_options.values()),
                       key=lambda pair: pair[1])
        if not buses:
            return []
        bus, b_cost = buses[0]                                         # cheapest way to move everyone
        bus_alternatives = [{"id": b["id"], "company": b["company"], "count": self.calc_buses(request, b),
                             "cost": c} for b, c in buses]

        packages = []
        for hotel, rest in product(self.candidate_hotels(request), self.candidate_restaurants(request)):
            rooms = self.calc_rooms(request, int(hotel["room_capacity"])) if hotel else 0
            h_cost = self.hotel_cost(request, hotel, rooms) if hotel else 0.0
            m_cost = self.meal_cost(request, rest) if rest else 0.0
            total = self.calc_total(b_cost, h_cost, m_cost)
            packages.append({
                "total": total,
                "detail": {
                    "travelers": request.total_travelers(),
                    "rooms": rooms,
                    "hotel": _hotel_view(hotel),
                    "restaurant": _restaurant_view(rest),
                    "bus": {"id": bus["id"], "company": bus["company"], "vehicle": bus.get("vehicle"),
                            "seats": int(bus["seats"]), "count": self.calc_buses(request, bus),
                            "wifi": bus.get("wifi")},
                    "bus_alternatives": bus_alternatives,
                    "costs": {"bus": b_cost, "hotel": h_cost, "meal": m_cost},
                    "within_budget": request.budget <= 0 or total <= request.budget,
                    "route_km": route, "drive_min": drive, **timing,
                },
            })
        if not packages:
            return []
        scores = self.value_scores(packages, request)
        for p, score in zip(packages, scores):
            p["detail"]["value_score"] = score
            p["detail"]["highlight"] = None
        picks = self.highlights(packages)
        for key, p in picks.items():
            p["detail"]["highlight"] = key
        if picks.get("closest") is not None and picks.get("lowest_cost") is not None:
            closest_km = min(_distance(p) for p in packages)
            # Honest labels: if the cheapest package is also the nearest, say so, and call
            # the second option "next closest".
            picks["lowest_cost"]["detail"]["also_closest"] = _distance(picks["lowest_cost"]) == closest_km
            picks["closest"]["detail"]["next_closest"] = _distance(picks["closest"]) > closest_km
        # Keep the three highlights, then fill up with the cheapest of the rest.
        chosen = list(picks.values())
        rest = sorted((p for p in packages if p not in chosen), key=lambda p: p["total"])
        kept = sorted(chosen + rest[:max(0, MAX_PACKAGES - len(chosen))], key=lambda p: p["total"])
        return [TravelPackage(package_no=i, total_cost=p["total"], route_km=route, detail=p["detail"])
                for i, p in enumerate(kept, start=1)]

    # -- comparing packages ------------------------------------------------
    @staticmethod
    def value_scores(packages: list[dict], request: TravelRequest) -> list[float]:
        """Best-value score per package (lower is better): cost, distance, diet coverage,
        hotel quality and on-time arrival, each scaled 0..1, plus a penalty when over budget."""
        def scaled(values):
            lo, hi = min(values), max(values)
            return [0.0 if hi == lo else (v - lo) / (hi - lo) for v in values]

        cost = scaled([p["total"] for p in packages])
        dist = scaled([_distance(p) for p in packages])
        n_diets = max(1, len(request.diet_tags))
        out = []
        for p, c, km in zip(packages, cost, dist):
            d = p["detail"]
            diet = len((d.get("restaurant") or {}).get("missing_diets") or []) / n_diets
            if d.get("restaurant") and d["restaurant"].get("fits_group") is False:
                diet = 1.0                                # can't seat everyone: as bad as missing every diet
            hotel = d.get("hotel") or {}
            if hotel.get("rating"):
                quality = 1 - float(hotel["rating"]) / 5
            elif hotel.get("stars"):
                quality = 1 - int(hotel["stars"]) / 5
            else:
                quality = 0.5 if hotel else 0.0
            w = VALUE_WEIGHTS
            score = (w["cost"] * c + w["distance"] * km + w["diet"] * min(1.0, diet)
                     + w["quality"] * quality + w["late"] * (1 if d.get("late_arrival") else 0))
            if not d.get("within_budget", True):
                score += OVER_BUDGET_PENALTY
            out.append(round(score, 4))
        return out

    @staticmethod
    def highlights(packages: list[dict]) -> dict:
        """Pick three different packages: lowest cost, closest, best value.
        Prefers a different hotel for each so the coach sees real alternatives."""
        hotel_of = lambda p: (p["detail"].get("hotel") or {}).get("id")
        picks, used, used_hotels = {}, [], set()

        def take(key, candidates, order):
            fresh = [p for p in candidates if p not in used]
            if not fresh:
                return
            other_hotel = [p for p in fresh if hotel_of(p) is None or hotel_of(p) not in used_hotels]
            best = min(other_hotel or fresh, key=order)
            picks[key] = best
            used.append(best)
            if hotel_of(best):
                used_hotels.add(hotel_of(best))

        take("lowest_cost", packages, lambda p: (p["total"], p["detail"]["value_score"]))
        take("closest", packages, lambda p: (_distance(p), p["total"]))
        take("best_value", packages, lambda p: (p["detail"]["value_score"], p["total"]))
        return picks


def _distance(p: dict) -> float:
    """Hotel-to-venue km; for day trips, the team-meal restaurant's km to the venue."""
    d = p["detail"]
    place = d.get("hotel") or d.get("restaurant") or {}
    return float(place.get("km_to_venue") or 0)


def _hotel_view(h):
    if not h:
        return None
    keep = ("id", "name", "address", "lat", "lng", "stars", "price_per_night", "amenities",
            "cancellation_policy", "km_to_venue", "drive_min_to_venue", "source",
            "place_id", "rating", "user_rating_count", "price_level", "price_estimated", "google_maps_uri", "photo")
    return {k: h.get(k) for k in keep}


def _restaurant_view(r):
    if not r:
        return None
    keep = ("id", "name", "address", "lat", "lng", "cuisine", "price_per_person", "halal_certified",
            "km_to_venue", "missing_diets", "source", "group_capacity", "fits_group")
    return {k: r.get(k) for k in keep}
