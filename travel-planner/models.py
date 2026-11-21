"""Plain data containers: TravelRequest and TravelPackage."""
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class TravelRequest:
    opponent: str
    date: str                       # "YYYY-MM-DD"
    num_players: int
    num_coaches: int
    num_staff: int
    destination: str = ""
    distance_miles: float = 100.0   # one-way; used for bus cost
    nights: int = 1                 # hotel nights
    earliest_departure: Optional[str] = None  # "HH:MM", e.g. "14:00"

    def total_travelers(self) -> int:
        return self.num_players + self.num_coaches + self.num_staff

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total_travelers"] = self.total_travelers()
        return d


@dataclass
class TravelPackage:
    package_id: int
    hotel_id: str
    restaurant_id: str
    bus_option_id: str
    bus_cost: float = 0.0
    hotel_cost: float = 0.0
    meal_cost: float = 0.0
    total_cost: float = 0.0
    num_buses: int = 0
    num_rooms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)
