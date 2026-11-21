"""Supabase access through its REST API (PostgREST). Server-side only.

Uses SUPABASE_URL and SUPABASE_SERVICE_KEY. The service key bypasses row level
security, so it must live only in .env / Render environment variables and
never in frontend code or GitHub.

If Supabase isn't configured or can't be reached, callers fall back gracefully
(hardcoded catalog, plans simply aren't saved).
"""
import os

import requests

TIMEOUT = 10


def _config():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    return (url, key) if url and key else (None, None)


def enabled() -> bool:
    return _config()[0] is not None


def _request(method: str, table: str, params=None, json=None):
    url, key = _config()
    resp = requests.request(
        method,
        f"{url}/rest/v1/{table}",
        params=params,
        json=json,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def load_catalog():
    """Return (hotels, restaurants, bus_options) lists of dicts, or None on any problem."""
    if not enabled():
        return None
    try:
        hotels = _request("GET", "hotels", {"select": "*", "order": "id"})
        restaurants = _request("GET", "restaurants", {"select": "*", "order": "id"})
        buses = _request("GET", "bus_options", {"select": "*", "order": "id"})
    except requests.RequestException:
        return None
    if not (hotels and restaurants and buses):
        return None
    return hotels, restaurants, buses


def save_plan(result: dict):
    """Save a trip, its packages and the recommendation. Returns the trip id, or None."""
    if not enabled() or not result.get("packages"):
        return None
    req = result["request"]
    try:
        trip = _request("POST", "trips", json={
            "opponent": req["opponent"],
            "game_date": req["date"],
            "destination": req["destination"],
            "distance_miles": req["distance_miles"],
            "num_players": req["num_players"],
            "num_coaches": req["num_coaches"],
            "num_staff": req["num_staff"],
            "nights": req["nights"],
            "earliest_departure": req["earliest_departure"],
        })[0]
        trip_id = trip["id"]
        _request("POST", "packages", json=[{
            "trip_id": trip_id,
            "package_number": p["package_id"],
            "hotel_id": p["hotel_id"],
            "restaurant_id": p["restaurant_id"],
            "bus_option_id": p["bus_option_id"],
            "bus_cost": p["bus_cost"],
            "hotel_cost": p["hotel_cost"],
            "meal_cost": p["meal_cost"],
            "total_cost": p["total_cost"],
            "num_buses": p["num_buses"],
            "num_rooms": p["num_rooms"],
        } for p in result["packages"]])
        rec = result.get("recommendation")
        if rec:
            _request("POST", "recommendations", json={
                "trip_id": trip_id,
                "package_number": rec["package_id"],
                "reason": rec.get("reason", ""),
                "source": rec.get("source", "fallback"),
                "note": rec.get("note"),
            })
        return trip_id
    except requests.RequestException:
        return None
