"""GooglePlaces: real hotels near the venue, with photos, from the Google Places API (New).

Set GOOGLE_MAPS_API_KEY (server key, Places API (New) enabled) in .env / Render. Without it the
planner uses the hotels in the catalog, as before.

- hotels_near(): Text Search for lodging around the venue. Google doesn't sell rooms or publish
  room rates, so price_per_night is an ESTIMATE from Google's price level (marked estimated=True).
- photo_url(): a signed /api/places/photo/<token> link. The server swaps the token for a fresh
  photo URL from Google, so the API key never reaches the browser and only photos this app
  handed out can be fetched.
Results are kept in memory for a short time to save quota (Google's free monthly allowance).
"""
import base64
import hashlib
import hmac
import os
import re
import time

import requests

from geo import drive_minutes, haversine_km

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PHOTO_URL = "https://places.googleapis.com/v1/{name}/media"
FIELDS = ",".join("places." + f for f in (
    "id", "displayName", "formattedAddress", "location", "rating", "userRatingCount",
    "priceLevel", "photos", "googleMapsUri", "types"))
# Rough nightly rate (USD, one room) by Google price level; used only when Google has a price level.
PRICE_BY_LEVEL = {"PRICE_LEVEL_INEXPENSIVE": 95.0, "PRICE_LEVEL_MODERATE": 140.0,
                  "PRICE_LEVEL_EXPENSIVE": 210.0, "PRICE_LEVEL_VERY_EXPENSIVE": 320.0}
DEFAULT_PRICE = 135.0
PHOTO_NAME = re.compile(r"^places/[A-Za-z0-9_-]+/photos/[A-Za-z0-9_-]+$")


class PlacesError(RuntimeError):
    pass


class GooglePlaces:
    def __init__(self, api_key=None, fetch=None, ttl_s=1800):
        self.api_key = (api_key if api_key is not None else os.environ.get("GOOGLE_MAPS_API_KEY", "")).strip()
        self._fetch = fetch or self._http
        self._cache = {}
        self.ttl_s = ttl_s
        self._secret = hashlib.sha256(b"photo-link:" + self.api_key.encode()).digest()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    # -- HTTP --------------------------------------------------------------
    def _http(self, method, url, headers=None, json_body=None, params=None):
        resp = requests.request(method, url, headers=headers, json=json_body, params=params, timeout=15)
        if resp.status_code >= 400:
            try:
                msg = resp.json().get("error", {}).get("message", "")
            except ValueError:
                msg = resp.text[:200]
            raise PlacesError(f"Google Places returned {resp.status_code}: {msg}")
        return resp.json()

    def _cached(self, key, fn):
        hit = self._cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        value = fn()
        self._cache[key] = (time.time() + self.ttl_s, value)
        return value

    # -- hotels ------------------------------------------------------------
    def hotels_near(self, lat, lng, radius_km=15, limit=10):
        if not self.configured:
            return []
        key = ("hotels", round(lat, 4), round(lng, 4), radius_km, limit)
        return self._cached(key, lambda: self._search_hotels(lat, lng, radius_km, limit))

    def _search_hotels(self, lat, lng, radius_km, limit):
        data = self._fetch("POST", SEARCH_URL,
                           headers={"X-Goog-Api-Key": self.api_key, "X-Goog-FieldMask": FIELDS},
                           json_body={"textQuery": "hotel", "includedType": "lodging", "pageSize": min(limit * 2, 20),
                                      "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lng},
                                                                  "radius": radius_km * 1000.0}}})
        out = []
        for p in data.get("places") or []:
            loc = p.get("location") or {}
            if "latitude" not in loc:
                continue
            km = haversine_km(lat, lng, loc["latitude"], loc["longitude"])
            if km > radius_km:
                continue
            photo = (p.get("photos") or [None])[0]
            author = ((photo or {}).get("authorAttributions") or [{}])[0]
            level = p.get("priceLevel")
            out.append({
                "id": "gp:" + p["id"], "place_id": p["id"],
                "name": (p.get("displayName") or {}).get("text") or "Hotel",
                "address": p.get("formattedAddress") or "",
                "lat": loc["latitude"], "lng": loc["longitude"],
                "rating": p.get("rating"), "user_rating_count": p.get("userRatingCount"),
                "price_level": level, "price_per_night": PRICE_BY_LEVEL.get(level, DEFAULT_PRICE), "price_estimated": True,
                "stars": None, "room_capacity": 2, "rooms_available": None, "amenities": [],
                "cancellation_policy": "Confirm the rate and cancellation policy with the hotel",
                "google_maps_uri": p.get("googleMapsUri"),
                "photo": {"name": photo["name"], "author": author.get("displayName"), "author_uri": author.get("uri")} if photo else None,
                "km_to_venue": round(km, 2), "drive_min_to_venue": drive_minutes(km * 1.3, city=True),
                "source": "google",
            })
        out.sort(key=lambda h: h["km_to_venue"])
        return out[:limit]

    # -- photos ------------------------------------------------------------
    def photo_url(self, photo):
        """Signed same-origin link for a hotel's Google photo (None if no photo / not configured)."""
        if not (self.configured and photo and photo.get("name")):
            return None
        raw = photo["name"].encode()
        sig = hmac.new(self._secret, raw, hashlib.sha256).hexdigest()[:24]
        return f"/api/places/photo/{base64.urlsafe_b64encode(raw).decode().rstrip('=')}.{sig}"

    def resolve_photo(self, token, max_px=640):
        """Token from photo_url() -> a short-lived https photo URL from Google."""
        try:
            b64, sig = token.rsplit(".", 1)
            name = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)).decode()
        except (ValueError, UnicodeDecodeError):
            raise PlacesError("Bad photo link.")
        good = hmac.new(self._secret, name.encode(), hashlib.sha256).hexdigest()[:24]
        if not (self.configured and hmac.compare_digest(sig, good) and PHOTO_NAME.match(name)):
            raise PlacesError("Bad photo link.")

        def fetch():
            data = self._fetch("GET", PHOTO_URL.format(name=name),
                               params={"maxWidthPx": max_px, "skipHttpRedirect": "true", "key": self.api_key})
            uri = data.get("photoUri")
            if not uri:
                raise PlacesError("Google returned no photo.")
            return uri
        return self._cached(("photo", name, max_px), fetch)


def maps_link(place):
    """Google Maps link for a hotel/restaurant/venue dict (no API key needed)."""
    if place.get("google_maps_uri"):
        return place["google_maps_uri"]
    q = f"{place.get('lat')},{place.get('lng')}"
    link = f"https://www.google.com/maps/search/?api=1&query={q}"
    if place.get("place_id"):
        link += f"&query_place_id={place['place_id']}"
    return link
