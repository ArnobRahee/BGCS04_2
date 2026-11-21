"""Find a venue's coordinates from its name, so coaches never have to type latitude/longitude.

Uses OpenStreetMap's free Nominatim search (no key, no billing). Its usage policy asks for a real
User-Agent, at most one request per second and caching; all three are done here.
https://operations.osmfoundation.org/policies/nominatim/
"""
import threading
import time

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "RallyTeamTravelPlanner/1.0 (BGSU hackathon demo)"
TIMEOUT = 8


class GeocodeError(RuntimeError):
    pass


class Geocoder:
    def __init__(self, fetch=None):
        self._fetch = fetch or self._http_get       # injectable for tests
        self._cache = {}
        self._lock = threading.Lock()
        self._last = 0.0

    def _http_get(self, query):
        with self._lock:                            # one request per second, server-wide
            wait = self._last + 1.0 - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        try:
            resp = requests.get(NOMINATIM_URL, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT},
                                params={"q": query, "format": "jsonv2", "limit": 5, "countrycodes": "us",
                                        "addressdetails": 0})
        except requests.RequestException as exc:
            raise GeocodeError("The map search couldn't be reached. Try again, or enter the coordinates yourself.") from exc
        if resp.status_code != 200:
            raise GeocodeError(f"The map search is unavailable right now ({resp.status_code}).")
        try:
            return resp.json()
        except ValueError as exc:
            raise GeocodeError("The map search returned something unreadable.") from exc

    def search(self, query: str) -> list[dict]:
        query = " ".join(str(query or "").split())[:200]
        if len(query) < 3:
            raise GeocodeError("Type at least 3 characters of the venue name.")
        key = query.lower()
        if key not in self._cache:
            rows = self._fetch(query) or []
            out = []
            for r in rows:
                try:
                    lat, lng = float(r["lat"]), float(r["lon"])
                except (KeyError, TypeError, ValueError):
                    continue
                out.append({"name": (r.get("name") or r.get("display_name") or query).strip(),
                            "address": r.get("display_name") or "", "lat": round(lat, 6), "lng": round(lng, 6)})
            self._cache[key] = out
        return self._cache[key]
