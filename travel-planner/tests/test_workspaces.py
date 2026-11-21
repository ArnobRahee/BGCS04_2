"""Scenarios A-F (API level): team identity, separate workspaces, empty start, suggestions, isolation."""
import json
from datetime import datetime, timedelta

import pytest

import app as appmod
from geocode import Geocoder
from ncaa_client import NCAAClient
from tests.ncaa_fixtures import FakeNCAA

SOON = (datetime.now() + timedelta(days=20)).strftime("%Y-%m-%dT12:00")
C, P = {"Authorization": "Bearer demo-coach"}, {"Authorization": "Bearer demo-player"}
CAMPUS = {"harvard university": ("42.3770", "-71.1167"), "the ohio state university": ("40.0067", "-83.0305")}


def fake_geo(q):
    lat, lng = CAMPUS.get(q.lower(), ("41.0", "-83.0"))
    return [{"name": q, "display_name": q, "lat": lat, "lon": lng}]


@pytest.fixture()
def env(monkeypatch):
    db = appmod.dbmod.MemoryDB()
    planner = appmod.TravelPlanner(db)
    appmod.seed_demo(db, planner)                       # the app's real demo seed: no trips
    monkeypatch.setattr(appmod, "DB", db)
    monkeypatch.setattr(appmod, "planner", planner)
    monkeypatch.setattr(appmod, "ncaa", NCAAClient(db=db, fetch=FakeNCAA()))
    monkeypatch.setattr(appmod, "geocoder", Geocoder(fetch=fake_geo))
    return appmod.app.test_client(), db, planner


def pick(c, sport, division, seo):
    r = c.patch("/api/team", headers=C, json={"sport": sport, "division": division, "ncaa_seo": seo})
    assert r.status_code == 200, r.json
    return r.json


def me(c, h=C):
    return c.get("/api/me", headers=h).json


def test_demo_workspace_starts_with_zero_trips(env):
    c, db, _ = env
    assert c.get("/api/trips", headers=C).json == [] and db.select("trips") == []
    assert me(c)["team"]["identity"]["display_name"] == "BGSU Falcons"


def test_scenario_a_harvard_identity_everywhere(env):
    c, db, _ = env
    r = pick(c, "volleyball-women", "d1", "harvard")
    assert r["action"] == "created"
    ident = me(c)["team"]["identity"]
    assert ident["display_name"] == "Harvard" and ident["university"] == "Harvard"
    assert ident["sport_name"] == "Women's Volleyball" and ident["division_name"] == "Division I"
    assert ident["logo"].endswith("/logo/harvard.svg") and ident["season"] == "2026"
    pulse = c.get("/api/ncaa/my-team", headers=C).json
    assert pulse["team_id"] == ident["team_id"] and pulse["name"] == "Harvard"
    assert "BGSU" not in json.dumps(me(c)["team"]) and c.get("/api/trips", headers=C).json == []
    team = db.select("teams", {"id": ident["team_id"]})[0]
    assert (team["home_lat"], team["home_lng"]) == (42.377, -71.1167)          # Harvard's campus, not BGSU's
    s = c.get("/api/suggestions", headers=C).json
    assert [g["opponent"] for g in s["games"]] == ["Yale"] and s["pick"]["game_id"] == "7700001"
    assert s["games"][0]["missing"] == ["venue"] and s["games"][0]["venue_name"] == "Yale University"
    assert c.get("/api/trips", headers=C).json == []                           # suggesting creates nothing


def test_scenario_b_ohio_state_football(env):
    c, _, _ = env
    pick(c, "volleyball-women", "d1", "harvard")
    pick(c, "football", "fbs", "ohio-st")
    ident = me(c)["team"]["identity"]
    assert ident["display_name"] == "Ohio St." and ident["sport_name"] == "Football"
    s = c.get("/api/suggestions", headers=C).json
    assert s["games"] == [] and s["upcoming_away"] == 0                        # its only game is at home
    sched = c.get("/api/ncaa/my-team", headers=C).json["games"]
    assert all("ohio-st" in (g["home"]["seo"], g["away"]["seo"]) for g in sched)
    assert {t["display_name"] for t in me(c)["teams"]} == {"BGSU Falcons", "Harvard", "Ohio St."}


def test_scenario_d_plan_from_suggestion_and_no_duplicates(env):
    c, db, _ = env
    pick(c, "volleyball-women", "d1", "harvard")
    g = c.get("/api/suggestions", headers=C).json["games"][0]
    plan = g["plan"]
    assert plan["opponent"] == "Yale" and plan["ncaa_game_id"] == "7700001" and plan["venue_confirmed"] is False
    body = {"team_id": plan["team_id"], "ncaa_game_id": plan["ncaa_game_id"], "opponent": plan["opponent"],
            "match_at": plan["match_at"], "venue_name": "Yale Volleyball Arena", "venue_lat": 41.31, "venue_lng": -72.93,
            "num_players": 14, "num_coaches": 2, "num_staff": 1, "nights": 0, "meals": 1, "budget": 3000}
    r = c.post("/api/trips", headers=C, json=body)
    assert r.status_code == 201 and r.json["team_id"] == plan["team_id"]
    assert [t["id"] for t in c.get("/api/trips", headers=C).json] == [r.json["id"]]
    again = c.post("/api/trips", headers=C, json={**body, "opponent": "Yale Bulldogs"})
    assert again.status_code == 400 and "already has a trip" in again.json["error"]
    assert c.get("/api/suggestions", headers=C).json["games"] == []           # planned games drop out
    games = c.get("/api/ncaa/my-team", headers=C).json["games"]
    assert games[0]["planned_trip_id"] == r.json["id"]


def test_scenario_e_switching_teams_isolates_trips(env):
    c, db, planner = env
    bgsu = me(c)["team"]["id"]
    pick(c, "volleyball-women", "d1", "harvard")
    harvard = me(c)["team"]["id"]
    body = {"opponent": "Yale", "venue_id": "V002", "match_at": SOON, "num_players": 12}
    tid = c.post("/api/trips", headers=C, json={**body, "match_at": SOON}).json["id"]
    assert c.post("/api/teams/select", headers=C, json={"team_id": bgsu}).status_code == 200
    assert c.get("/api/trips", headers=C).json == []
    assert c.get(f"/api/trips/{tid}", headers=C).status_code == 404            # not visible from BGSU
    assert c.post(f"/api/trips/{tid}/select", headers=C, json={"package_no": 1}).status_code == 404
    stale = c.post("/api/trips", headers=C, json={**body, "team_id": harvard, "opponent": "Brown"})
    assert stale.status_code == 400 and "switched teams" in stale.json["error"]
    assert c.post("/api/teams/select", headers=C, json={"team_id": harvard}).status_code == 200
    assert [t["id"] for t in c.get("/api/trips", headers=C).json] == [tid]


def test_cannot_select_a_team_you_dont_belong_to(env):
    c, db, _ = env
    other = db.insert("teams", {"name": "Other U", "join_code": "OTHER-1", "home_name": "x", "home_lat": 1, "home_lng": 1,
                                "owner_id": "someone"})[0]
    assert c.post("/api/teams/select", headers=C, json={"team_id": other["id"]}).status_code == 403
    assert c.post("/api/teams/select", headers=P, json={"team_id": other["id"]}).status_code == 403
    assert c.patch("/api/team", headers=P, json={"sport": "football", "division": "fbs", "ncaa_seo": "toledo"}).status_code == 403


def test_scenario_f_aivana_down_dashboard_and_manual_trip_still_work(env, monkeypatch):
    c, _, planner = env
    pick(c, "volleyball-women", "d1", "harvard")
    monkeypatch.setattr(planner.aivana, "api_key", "ai_live_x")

    class Down:
        api_key = None

        @staticmethod
        def generate(**kw):
            raise RuntimeError("RateLimitError: 429 too many requests")
    monkeypatch.setattr(planner.aivana, "_sdk", Down)
    s = c.get("/api/suggestions", headers=C).json
    assert s["pick"]["source"] == "schedule" and "rate limit" in s["pick"]["note"]
    r = c.post("/api/trips", headers=C, json={"opponent": "Yale", "venue_id": "V002", "match_at": SOON})
    assert r.status_code == 201 and r.json["ai_recommendation"]["source"] == "ranking"
    p = c.post("/api/trips/parse", headers=C, json={"text": "At Yale next Friday, 14 players"})
    assert p.status_code == 503 and "rate limit" in p.json["error"]


def test_aivana_pick_must_be_a_listed_game(env, monkeypatch):
    c, _, planner = env
    pick(c, "volleyball-women", "d1", "harvard")
    monkeypatch.setattr(planner.aivana, "api_key", "ai_live_x")
    calls = []

    class SDK:
        api_key = None

        @staticmethod
        def generate(**kw):
            calls.append(kw)
            return type("R", (), {"structured": {"game_id": "9999999", "reason": "made up"}})()
    monkeypatch.setattr(planner.aivana, "_sdk", SDK)
    s = c.get("/api/suggestions", headers=C).json
    assert s["pick"]["game_id"] == "7700001" and s["pick"]["source"] == "schedule"
    assert calls[0]["response_format"]["schema"]["properties"]["game_id"]["enum"] == ["7700001"]


def test_trip_text_is_parsed_with_structured_output(env, monkeypatch):
    c, _, planner = env
    monkeypatch.setattr(planner.aivana, "api_key", "ai_live_x")

    class SDK:
        api_key = None

        @staticmethod
        def generate(**kw):
            assert kw["response_format"]["type"] == "json_schema"
            return type("R", (), {"structured": {
                "opponent": "Toledo", "match_date": "2026-12-05", "match_time": None, "venue_name": None,
                "num_players": 22, "num_coaches": 3, "num_staff": None, "nights": 1, "meals": 2, "budget": 8000,
                "depart_after_date": "2026-12-04", "depart_after_time": "14:00"}})()
    monkeypatch.setattr(planner.aivana, "_sdk", SDK)
    r = c.post("/api/trips/parse", headers=C, json={"text": "At Toledo Dec 5, 22 players 3 coaches, leave after 2pm the day before, $8k"})
    f = r.json["fields"]
    assert f == {"opponent": "Toledo", "match_at": "2026-12-05T12:00", "match_time_missing": True,
                 "depart_after": "2026-12-04T14:00", "num_players": 22, "num_coaches": 3, "nights": 1,
                 "meals": 2, "budget": 8000.0}
    assert c.get("/api/trips", headers=C).json == []                           # parsing saves nothing
