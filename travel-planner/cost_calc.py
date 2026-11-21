"""CostCalc: all the arithmetic. The AI never does math."""
import math
from itertools import product

from models import TravelPackage, TravelRequest
from travel_data import TravelData

PEOPLE_PER_ROOM = 2
MEALS_PER_DAY = 2


class CostCalc:
    def __init__(self, data: TravelData):
        self.data = data

    def calc_buses(self, request: TravelRequest, bus: dict) -> int:
        return math.ceil(request.total_travelers() / bus["seats"])

    def calc_rooms(self, request: TravelRequest) -> int:
        return math.ceil(request.total_travelers() / PEOPLE_PER_ROOM)

    def _bus_ok(self, request: TravelRequest, bus: dict) -> bool:
        """Departure constraint: bus must leave at or after the earliest time (HH:MM)."""
        if not request.earliest_departure:
            return True
        return bus["departure_time"] >= request.earliest_departure

    def calc_total(self, request: TravelRequest, hotel: dict, restaurant: dict, bus: dict):
        buses = self.calc_buses(request, bus)
        rooms = self.calc_rooms(request)
        bus_cost = buses * (bus["base_fee"] + bus["per_mile"] * request.distance_miles * 2)
        hotel_cost = rooms * hotel["price_per_night"] * request.nights
        days = request.nights + 1
        meal_cost = request.total_travelers() * restaurant["price_per_person"] * MEALS_PER_DAY * days
        total = bus_cost + hotel_cost + meal_cost
        return buses, rooms, round(bus_cost, 2), round(hotel_cost, 2), round(meal_cost, 2), round(total, 2)

    def generate_packages(self, request: TravelRequest) -> list[TravelPackage]:
        packages = []
        buses = [b for b in self.data.bus_options.values() if self._bus_ok(request, b)]
        combos = product(self.data.hotels.values(), self.data.restaurants.values(), buses)
        for i, (hotel, rest, bus) in enumerate(combos, start=1):
            nb, nr, bc, hc, mc, total = self.calc_total(request, hotel, rest, bus)
            packages.append(TravelPackage(
                package_id=i, hotel_id=hotel["id"], restaurant_id=rest["id"],
                bus_option_id=bus["id"], bus_cost=bc, hotel_cost=hc, meal_cost=mc,
                total_cost=total, num_buses=nb, num_rooms=nr,
            ))
        return packages
