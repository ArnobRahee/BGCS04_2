import json

import pytest

import app as appmod
from chat_assistant import ChatAssistant
from ncaa_client import NCAAClient
from tests.ncaa_fixtures import FakeNCAA

C, P = {"Authorization": "Bearer demo-coach"}, {"Authorization": "Bearer demo-player"}


@pytest.fixture()
def env(monkeypatch):
    db = appmod.dbmod.MemoryDB()
    planner = appmod.TravelPlanner(db)
    appmod.seed_demo(db, planner)
    monkeypatch.setattr(appmod, "DB", db)
    monkeypatch.setattr(appmod, "planner", planner)
    monkeypatch.setattr(appmod, "ncaa", NCAAClient(db=db, fetch=FakeNCAA()))
    return appmod.app.test_client(), db, planner


def say(c, headers, text, trip_id=None):
    return c.post("/api/chat", headers=headers, json={"messages": [{"role": "user", "content": text}], "trip_id": trip_id}).json


def test_coach_can_prepare_but_never_approve_a_booking(env):
    c, db, _ = env
    r = say(c, C, "book package 2 for Ohio State")
    assert r["action"]["ok"] and "waiting for your approval" in r["reply"]
    trip = db.select("trips")[0]
    assert trip["status"] == "AWAITING_APPROVAL" and trip["selected_package_no"] == 2
    assert {b["status"] for b in db.select("bookings")} == {"PENDING_APPROVAL"}
    r = say(c, C, "actually book package 1")               # switching packages replaces the pending request
    assert r["action"]["ok"] and db.select("trips")[0]["selected_package_no"] == 1
    assert all(b["status"] in ("PENDING_APPROVAL", "CANCELLED") for b in db.select("bookings"))


def test_coach_questions_and_ncaa_answers(env):
    c, _, _ = env
    assert "Package 1" in say(c, C, "compare the packages")["reply"]
    assert "covers every dietary need" in say(c, C, "why did you recommend that?")["reply"]
    assert "9-2" in say(c, C, "what's our record?")["reply"]
    assert "at Ohio St." in say(c, C, "when is our next game?")["reply"]


def test_player_rsvp_and_no_money_in_context(env):
    c, db, planner = env
    r = say(c, P, "I'm going to Ohio State")
    assert r["action"]["ok"]
    assert db.select("trip_participants", {"player_id": "demo-player-2"})[0]["rsvp"] == "going"
    assert "still being planned" in say(c, P, "when does the bus leave?")["reply"]
    player = db.select("profiles", {"id": "demo-player-2"})[0]
    team = db.select("teams")[0]
    ctx = json.dumps(ChatAssistant(db, planner, None, planner.aivana).context(player, team), default=str)
    for word in ("total_cost", "budget", "price", "amount", "costs"):
        assert word not in ctx


def test_player_cannot_trigger_coach_actions_even_through_the_model(env, monkeypatch):
    c, db, planner = env
    monkeypatch.setattr(planner.aivana, "api_key", "k")
    monkeypatch.setattr(planner.aivana, "api_url", "https://example.invalid")
    trip_id = db.select("trips")[0]["id"]
    monkeypatch.setattr(planner.aivana, "complete", lambda msgs: json.dumps(
        {"reply": "Sure!", "action": {"type": "request_booking", "trip_id": trip_id, "package_no": 1}}))
    r = say(c, P, "book package 1")
    assert r["source"] == "aivana" and r["action"] is None
    assert db.select("trips")[0]["status"] == "PLANNING"


def test_model_reply_with_action_and_fallback_when_model_fails(env, monkeypatch):
    c, db, planner = env
    monkeypatch.setattr(planner.aivana, "api_key", "k")
    monkeypatch.setattr(planner.aivana, "api_url", "https://example.invalid")
    trip_id = db.select("trips")[0]["id"]
    monkeypatch.setattr(planner.aivana, "complete", lambda msgs: "```json\n" + json.dumps(
        {"reply": "Preparing package 3.", "action": {"type": "request_booking", "trip_id": trip_id, "package_no": 3}}) + "\n```")
    r = say(c, C, "go with the third one")
    assert r["source"] == "aivana" and r["action"]["ok"] and db.select("trips")[0]["selected_package_no"] == 3

    def boom(msgs):
        raise RuntimeError("timeout")
    monkeypatch.setattr(planner.aivana, "complete", boom)
    r = say(c, C, "compare packages")
    assert r["source"] == "local" and "Aivana was unavailable" in r["reply"]
