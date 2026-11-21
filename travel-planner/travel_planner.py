"""TravelPlanner: the coordinator. No math or AI itself; it calls the right class."""
from aivana_client import AivanaClient
from cost_calc import CostCalc
from models import TravelRequest
from travel_data import TravelData


class TravelPlanner:
    def __init__(self):
        self.data = TravelData()
        self.calc = CostCalc(self.data)
        self.aivana = AivanaClient()
        self.request: TravelRequest | None = None
        self.packages = []

    def plan(self, request: TravelRequest) -> dict:
        self.request = request
        self.packages = self.calc.generate_packages(request)
        if not self.packages:
            return {"request": request.to_dict(), "packages": [], "recommendation": None,
                    "error": "No bus option meets the departure constraint."}

        pkg_dicts = [self._enrich(p.to_dict()) for p in self.packages]
        rec = self.aivana.recommend(request.to_dict(), pkg_dicts)
        return {"request": request.to_dict(), "packages": pkg_dicts, "recommendation": rec}

    def _enrich(self, pkg: dict) -> dict:
        """Add human-readable names next to the stored IDs."""
        pkg["hotel_name"] = self.data.hotels[pkg["hotel_id"]]["name"]
        pkg["restaurant_name"] = self.data.restaurants[pkg["restaurant_id"]]["name"]
        pkg["bus_company"] = self.data.bus_options[pkg["bus_option_id"]]["company"]
        return pkg
