"""TravelData: the hotels, restaurants and bus options the planner can choose from.

Hardcoded sample data for the hackathon. Later you can load these from a
Supabase table instead (see README).
"""

HOTELS = [
    {"id": "hotel_a", "name": "Budget Inn", "price_per_night": 89.0, "rating": 3.5},
    {"id": "hotel_b", "name": "Comfort Suites", "price_per_night": 119.0, "rating": 4.0},
    {"id": "hotel_c", "name": "Grand Plaza", "price_per_night": 169.0, "rating": 4.6},
]

RESTAURANTS = [
    {"id": "rest_a", "name": "Pasta House", "price_per_person": 14.0},
    {"id": "rest_b", "name": "Grill & Co.", "price_per_person": 22.0},
]

BUS_OPTIONS = [
    {"id": "bus_1", "company": "Falcon Coaches", "seats": 56,
     "base_fee": 400.0, "per_mile": 3.2, "departure_time": "15:00"},
    {"id": "bus_2", "company": "Metro Charter", "seats": 48,
     "base_fee": 300.0, "per_mile": 3.6, "departure_time": "12:00"},
]


class TravelData:
    def __init__(self):
        import db

        # Prefer the Supabase catalog; fall back to the hardcoded sample data.
        catalog = db.load_catalog()
        hotels, restaurants, buses = catalog if catalog else (HOTELS, RESTAURANTS, BUS_OPTIONS)
        self.source = "supabase" if catalog else "sample"
        self.hotels = {h["id"]: h for h in hotels}
        self.restaurants = {r["id"]: r for r in restaurants}
        self.bus_options = {b["id"]: b for b in buses}
