import app as appmod
from google_places import GooglePlaces, PlacesError
from travel_data import TravelData
from travel_planner import TravelPlanner

VENUE = (40.00167, -83.01972)  # Ohio Stadium


class FakeGoogle:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers=None, json_body=None, params=None):
        self.calls.append((method, url, headers, json_body, params))
        if url.endswith(":searchText"):
            assert headers["X-Goog-Api-Key"] == "test-key" and "places.photos" in headers["X-Goog-FieldMask"]
            return {"places": [
                {"id": "ChIJnear", "displayName": {"text": "Blackwell Inn"}, "formattedAddress": "2110 Tuttle Park Pl, Columbus, OH",
                 "location": {"latitude": 40.0035, "longitude": -83.0167}, "rating": 4.5, "userRatingCount": 1200,
                 "priceLevel": "PRICE_LEVEL_EXPENSIVE", "googleMapsUri": "https://maps.google.com/?cid=1",
                 "photos": [{"name": "places/ChIJnear/photos/AbC_1", "authorAttributions": [{"displayName": "Jo", "uri": "https://maps.google.com/u/1"}]}]},
                {"id": "ChIJmid", "displayName": {"text": "Budget Stay"}, "formattedAddress": "1 Road, Columbus, OH",
                 "location": {"latitude": 39.97, "longitude": -83.0}},
                {"id": "ChIJfar", "displayName": {"text": "Far Away Lodge"}, "location": {"latitude": 41.5, "longitude": -81.7}},
            ]}
        if url.endswith("/media"):
            assert params["skipHttpRedirect"] == "true" and params["key"] == "test-key"
            return {"photoUri": "https://lh3.googleusercontent.com/p/photo123"}
        raise AssertionError(url)


def test_hotels_near_venue_with_photo_rating_and_estimated_price():
    fake = FakeGoogle()
    gp = GooglePlaces(api_key="test-key", fetch=fake)
    hotels = gp.hotels_near(*VENUE)
    assert [h["name"] for h in hotels] == ["Blackwell Inn", "Budget Stay"]          # far one dropped, nearest first
    inn = hotels[0]
    assert inn["id"] == "gp:ChIJnear" and inn["rating"] == 4.5 and inn["price_estimated"]
    assert inn["price_per_night"] == 210.0 and hotels[1]["price_per_night"] == 135.0
    assert inn["photo"]["author"] == "Jo"
    gp.hotels_near(*VENUE)
    assert len(fake.calls) == 1                                                   # cached


def test_photo_links_are_signed_and_never_expose_the_key():
    fake = FakeGoogle()
    gp = GooglePlaces(api_key="test-key", fetch=fake)
    link = gp.photo_url({"name": "places/ChIJnear/photos/AbC_1"})
    assert link.startswith("/api/places/photo/") and "test-key" not in link
    assert gp.resolve_photo(link.rsplit("/", 1)[1]) == "https://lh3.googleusercontent.com/p/photo123"
    forged = GooglePlaces(api_key="other", fetch=fake).photo_url({"name": "places/X/photos/Y"}).rsplit("/", 1)[1]
    for bad in (forged, "garbage", link.rsplit("/", 1)[1][:-2] + "00"):
        try:
            gp.resolve_photo(bad)
            raise AssertionError("accepted a bad token")
        except PlacesError:
            pass


def test_planner_uses_google_hotels_and_books_them(monkeypatch):
    db = appmod.dbmod.MemoryDB()
    gp = GooglePlaces(api_key="test-key", fetch=FakeGoogle())
    planner = TravelPlanner(db, data=TravelData(None, places=gp))
    appmod.seed_demo(db, planner)
    trip = db.select("trips")[0]
    view = planner.coach_view(trip)
    hotels = {p["detail"]["hotel"]["name"] for p in view["packages"]}
    assert hotels <= {"Blackwell Inn", "Budget Stay"} and hotels
    assert view["ai_recommendation"]["hotel_source"] == "google"
    h = next(p["detail"]["hotel"] for p in view["packages"] if p["detail"]["hotel"]["name"] == "Blackwell Inn")
    assert h["photo_url"].startswith("/api/places/photo/") and h["maps_url"] == "https://maps.google.com/?cid=1"
    assert "photo_url" not in db.select("trip_packages")[0]["detail"].get("hotel", {})   # links aren't stored
    planner.select_package(trip, 1, "demo-coach-id")
    result = planner.approve(planner.trip(trip["id"], trip["team_id"]), "demo-coach-id")
    assert result["ok"], result.get("error")


def test_without_a_key_catalog_hotels_are_used():
    data = TravelData(None, places=GooglePlaces(api_key=""))
    assert all(h["source"] != "google" for h in data.hotels_near(*VENUE))


def test_photo_route(monkeypatch):
    gp = GooglePlaces(api_key="test-key", fetch=FakeGoogle())
    monkeypatch.setattr(appmod.planner.data, "places", gp)
    c = appmod.app.test_client()
    link = gp.photo_url({"name": "places/ChIJnear/photos/AbC_1"})
    r = c.get(link)
    assert r.status_code == 302 and r.headers["Location"] == "https://lh3.googleusercontent.com/p/photo123"
    assert c.get("/api/places/photo/nope.123").status_code == 404
