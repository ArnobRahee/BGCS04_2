"""Venue search by name (OpenStreetMap) so coaches don't type coordinates."""
import pytest

import app as appmod
from geocode import Geocoder, GeocodeError

C, P = {"Authorization": "Bearer demo-coach"}, {"Authorization": "Bearer demo-player"}
ROWS = [{"name": "Kelly/Shorts Stadium", "display_name": "Kelly/Shorts Stadium, Mount Pleasant, Michigan, USA",
         "lat": "43.5778", "lon": "-84.7706"}, {"lat": "bad", "lon": "x"}]


def test_search_parses_and_caches():
    calls = []
    geo = Geocoder(fetch=lambda q: (calls.append(q), ROWS)[1])
    first = geo.search("Kelly Shorts Stadium")
    assert first == [{"name": "Kelly/Shorts Stadium", "address": "Kelly/Shorts Stadium, Mount Pleasant, Michigan, USA",
                      "lat": 43.5778, "lng": -84.7706}]
    geo.search("kelly  shorts stadium")
    assert len(calls) == 1
    with pytest.raises(GeocodeError):
        geo.search("ab")


@pytest.fixture()
def client(monkeypatch):
    db = appmod.dbmod.MemoryDB()
    planner = appmod.TravelPlanner(db)
    appmod.seed_demo(db, planner, with_trip=True)
    monkeypatch.setattr(appmod, "DB", db)
    monkeypatch.setattr(appmod, "planner", planner)
    monkeypatch.setattr(appmod, "geocoder", Geocoder(fetch=lambda q: ROWS))
    return appmod.app.test_client()


def test_geocode_route(client):
    r = client.get("/api/geocode?q=Kelly Shorts", headers=C)
    assert r.status_code == 200 and r.json[0]["lat"] == 43.5778
    assert client.get("/api/geocode?q=ab", headers=C).status_code == 400
    assert client.get("/api/geocode?q=Kelly", headers=P).status_code == 200      # signed-in users (onboarding too)
    assert client.get("/api/geocode?q=Kelly").status_code == 401
