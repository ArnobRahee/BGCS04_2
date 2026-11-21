import pytest

import app as appmod
from geocode import Geocoder
from ncaa_client import NCAAClient
from tests.ncaa_fixtures import FakeNCAA

C, P = {"Authorization": "Bearer demo-coach"}, {"Authorization": "Bearer demo-player"}


@pytest.fixture()
def client(monkeypatch):
    db = appmod.dbmod.MemoryDB()
    planner = appmod.TravelPlanner(db)
    appmod.seed_demo(db, planner, with_trip=True)
    monkeypatch.setattr(appmod, "DB", db)
    monkeypatch.setattr(appmod, "planner", planner)
    monkeypatch.setattr(appmod, "ncaa", NCAAClient(db=db, fetch=FakeNCAA()))
    monkeypatch.setattr(appmod, "geocoder", Geocoder(fetch=lambda q: [{"name": q, "display_name": q, "lat": "41.65", "lon": "-83.61"}]))
    return appmod.app.test_client(), db


def test_my_team_shows_record_rank_and_plannable_away_games(client):
    c, _ = client
    t = c.get("/api/ncaa/my-team", headers=C).json
    assert t["linked"] and t["summary"]["record"] == "9-2" and t["summary"]["rank"] == "24"
    game = t["games"][0]
    assert game["is_away"] and game["plan"]["venue_id"] == "V001" and game["plan"]["match_at"] == "2026-11-28T12:00"
    pt = c.get("/api/ncaa/my-team", headers=P).json
    assert "plan" not in pt["games"][0]                       # players can't plan trips


def test_trip_from_ncaa_game_keeps_the_game_id(client):
    c, db = client
    plan = c.get("/api/ncaa/my-team", headers=C).json["games"][0]["plan"]
    r = c.post("/api/trips", headers=C, json={**plan, "nights": 1, "meals": 1, "budget": 8000})
    assert r.status_code == 201 and r.json["ncaa_game_id"] == "6400001" and r.json["venue_name"] == "Ohio Stadium"


def test_link_team_validates_sport_division_and_school(client):
    c, db = client
    assert c.patch("/api/team", headers=C, json={"sport": "football", "division": "d1", "ncaa_seo": "toledo"}).status_code == 400
    assert c.patch("/api/team", headers=C, json={"sport": "football", "division": "fbs", "ncaa_seo": "hogwarts"}).status_code == 400
    r = c.patch("/api/team", headers=C, json={"sport": "basketball-men", "division": "d1", "ncaa_seo": "toledo"})
    assert r.status_code == 200 and r.json["team"]["ncaa_name"] == "Toledo" and r.json["action"] == "created"
    assert c.patch("/api/team", headers=P, json={"sport": "football", "division": "fbs", "ncaa_seo": "toledo"}).status_code == 403


def test_explorer_routes(client):
    c, _ = client
    assert len(c.get("/api/ncaa/sports", headers=P).json) >= 20
    assert c.get("/api/ncaa/schools?q=kent", headers=P).json[0]["slug"] == "kent-st"
    assert c.get("/api/ncaa/rankings/football/fbs", headers=P).json["data"][0]["RANK"] == "1"
    assert c.get("/api/ncaa/standings/football/fbs", headers=P).json["data"][0]["conference"] == "Mid-American"
    assert c.get("/api/ncaa/scoreboard/football/fbs", headers=P).json[0]["home"]["seo"] == "ohio-st"
    assert c.get("/api/ncaa/rankings/football/fbs?poll=../x", headers=P).status_code == 400
    assert c.get("/api/ncaa/standings/football/zz", headers=P).status_code == 502
    assert c.get("/api/ncaa/standings/football/fbs").status_code == 401
