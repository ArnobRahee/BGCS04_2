"""Trip input validation (create_trip): impossible or out-of-range details are rejected with a clear message."""
from datetime import datetime, timedelta

import pytest

import app as appmod

C = {"Authorization": "Bearer demo-coach"}
MATCH = (datetime.now() + timedelta(days=10)).replace(hour=15, minute=0, second=0, microsecond=0)


def iso(d):
    return d.isoformat(timespec="minutes")


@pytest.fixture()
def client(monkeypatch):
    db = appmod.dbmod.MemoryDB()
    planner = appmod.TravelPlanner(db)
    appmod.seed_demo(db, planner)
    monkeypatch.setattr(appmod, "DB", db)
    monkeypatch.setattr(appmod, "planner", planner)
    return appmod.app.test_client()


def post(c, **over):
    body = {"opponent": "Toledo", "venue_id": "V002", "match_at": iso(MATCH), "num_players": 20,
            "num_coaches": 2, "num_staff": 1, "nights": 1, "meals": 2, "budget": 9000}
    body.update(over)
    return c.post("/api/trips", headers=C, json=body)


def test_zero_coaches_is_kept(client):
    r = post(client, num_coaches=0)
    assert r.status_code == 201
    assert r.json["num_coaches"] == 0
    assert r.json["num_players"] + r.json["num_coaches"] + r.json["num_staff"] == 21


def test_counts_typed_as_text_are_accepted(client):
    r = post(client, num_players="18", num_coaches="0", num_staff="0")
    assert r.status_code == 201 and (r.json["num_players"], r.json["num_coaches"]) == (18, 0)


@pytest.mark.parametrize("over, words", [
    ({"match_at": "2020-01-01T12:00"}, "past"),
    ({"depart_after": iso(MATCH + timedelta(hours=1))}, "before the match"),
    ({"depart_after": iso(MATCH)}, "before the match"),
    ({"depart_after": "2020-01-01T12:00"}, "past"),
    ({"num_players": 100000}, "between"),
    ({"nights": 365}, "between"),
    ({"meals": 500}, "between"),
    ({"num_players": 2.5}, "whole number"),
    ({"num_players": -1}, "negative"),
    ({"budget": -5}, "negative"),
    ({"budget": 5_000_000}, "budget"),
    ({"num_players": 0, "num_coaches": 0, "num_staff": 0}, "at least one"),
    ({"venue_id": "", "venue_name": "Somewhere", "venue_lat": 999, "venue_lng": 10}, "location"),
    ({"venue_id": "", "venue_name": "", "venue_lat": 40, "venue_lng": -83}, "venue name"),
    ({"match_at": "not a date"}, "date"),
])
def test_bad_trip_details_are_rejected(client, over, words):
    r = post(client, **over)
    assert r.status_code == 400, r.json
    assert words in r.json["error"].lower()


def test_rejected_trip_is_not_saved(client):
    before = len(client.get("/api/trips", headers=C).json)
    post(client, nights=365)
    assert len(client.get("/api/trips", headers=C).json) == before


def test_departure_before_match_is_fine(client):
    r = post(client, depart_after=iso(MATCH - timedelta(days=1)))
    assert r.status_code == 201


def test_duplicate_trip_is_refused(client):
    assert post(client).status_code == 201
    r = post(client, opponent=" toledo ")
    assert r.status_code == 400 and "already have a trip" in r.json["error"]
    assert post(client, match_at=iso(MATCH + timedelta(days=1))).status_code == 201
