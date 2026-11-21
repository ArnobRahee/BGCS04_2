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


def connect(monkeypatch, planner, reply):
    """Pretend Aivana is connected; `reply` is a function of the messages sent to it."""
    monkeypatch.setattr(planner.aivana, "api_key", "k")
    sent, sent_kw = [], []
    connect.kw = sent_kw
    monkeypatch.setattr(planner.aivana, "complete", lambda msgs, **kw: (sent.append(msgs), sent_kw.append(kw), reply(msgs))[2])
    return sent


def say(c, headers, text, trip_id=None):
    return c.post("/api/chat", headers=headers, json={"messages": [{"role": "user", "content": text}], "trip_id": trip_id}).json


def test_without_aivana_the_assistant_says_so_and_does_nothing(env):
    c, db, _ = env
    r = say(c, C, "book package 2")
    assert r["source"] == "not_connected" and r["action"] is None and "isn't connected" in r["reply"]
    assert db.select("trips")[0]["status"] == "PLANNING"


def test_main_planner_can_prepare_but_never_approve_a_booking(env, monkeypatch):
    c, db, planner = env
    trip_id = db.select("trips")[0]["id"]
    connect(monkeypatch, planner, lambda msgs: "```json\n" + json.dumps(
        {"reply": "Preparing package 3.", "action": {"type": "request_booking", "trip_id": trip_id, "package_no": 3}}) + "\n```")
    r = say(c, C, "go with the third one")
    assert r["source"] == "aivana" and r["action"]["ok"] and "waiting for your approval" in r["reply"]
    trip = db.select("trips")[0]
    assert trip["status"] == "AWAITING_APPROVAL" and trip["selected_package_no"] == 3
    assert {b["status"] for b in db.select("bookings")} == {"PENDING_APPROVAL"}


def test_coach_who_is_not_main_planner_cannot_book_through_chat(env, monkeypatch):
    c, db, planner = env
    trip_id = db.select("trips")[0]["id"]
    db.update("teams", {"owner_id": "someone-else"}, {"id": db.select("teams")[0]["id"]})
    connect(monkeypatch, planner, lambda msgs: json.dumps(
        {"reply": "Ok", "action": {"type": "request_booking", "trip_id": trip_id, "package_no": 1}}))
    r = say(c, C, "book package 1")
    assert r["action"]["ok"] is False and "main planner" in r["action"]["message"]
    assert db.select("trips")[0]["status"] == "PLANNING"


def test_context_sent_to_aivana_has_ncaa_and_player_context_has_no_money(env, monkeypatch):
    c, db, planner = env
    sent = connect(monkeypatch, planner, lambda msgs: json.dumps({"reply": "You're 9-2.", "action": None}))
    assert say(c, C, "what's our record?")["reply"] == "You're 9-2."
    system, question = sent[-1][0]["content"], sent[-1][-1]["content"]
    assert '"record": "9-2"' in question and "Aivana" not in system
    player = db.select("profiles", {"id": "demo-player-2"})[0]
    team = db.select("teams")[0]
    ctx = json.dumps(ChatAssistant(db, planner, None, planner.aivana).context(player, team), default=str)
    for word in ("total_cost", "budget", "price", "amount", "costs"):
        assert word not in ctx


def test_player_rsvp_and_cannot_trigger_coach_actions(env, monkeypatch):
    c, db, planner = env
    trip_id = db.select("trips")[0]["id"]
    connect(monkeypatch, planner, lambda msgs: json.dumps(
        {"reply": "Sure!", "action": {"type": "rsvp", "trip_id": trip_id, "rsvp": "going"}}))
    assert say(c, P, "I'm going")["action"]["ok"]
    assert db.select("trip_participants", {"player_id": "demo-player-2"})[0]["rsvp"] == "going"
    connect(monkeypatch, planner, lambda msgs: json.dumps(
        {"reply": "Sure!", "action": {"type": "request_booking", "trip_id": trip_id, "package_no": 1}}))
    r = say(c, P, "book package 1")
    assert r["source"] == "aivana" and r["action"] is None
    assert db.select("trips")[0]["status"] == "PLANNING"


def test_aivana_failure_is_reported_not_faked(env, monkeypatch):
    c, _, planner = env

    def boom(msgs):
        raise RuntimeError("timeout")
    connect(monkeypatch, planner, boom)
    r = say(c, C, "compare packages")
    assert r["source"] == "error" and "Couldn't reach Aivana" in r["reply"]


def test_only_main_planner_books_and_can_hand_over_the_role(env):
    c, db, _ = env
    team = db.select("teams")[0]
    trip_id = db.select("trips")[0]["id"]
    me = c.get("/api/me", headers=C).json
    assert me["is_main_planner"] and me["main_planner"]["name"] == "Coach Morgan"
    # a second coach on the same team (invitations come with the administrator accounts)
    db.insert("profiles", {"id": "coach-2", "full_name": "Assistant Lee", "role": "coach", "team_id": team["id"], "diet_tags": []})
    assert c.post("/api/team/planner", headers=C, json={"profile_id": "demo-player-2"}).status_code == 400
    assert c.post("/api/team/planner", headers=C, json={"profile_id": "coach-2"}).status_code == 200
    r = c.post(f"/api/trips/{trip_id}/select", headers=C, json={"package_no": 1})
    assert r.status_code == 403 and "main planner" in r.json["error"]
    assert c.post("/api/team/planner", headers=C, json={"profile_id": "demo-coach-id"}).status_code == 403
    assert c.get("/api/me", headers=C).json["is_main_planner"] is False


def test_client_uses_the_official_sdk_shape():
    """AivanaClient.complete() -> aivana.generate(messages=..., system=...) -> res.answer."""
    from aivana_client import AivanaClient

    class FakeSDK:
        api_key = None
        calls = []

        @classmethod
        def generate(cls, **kw):
            cls.calls.append(kw)
            return type("Res", (), {"answer": "Hello from Aivana"})()

    client = AivanaClient(sdk=FakeSDK)
    client.api_key = "ai_live_test"
    out = client.complete([{"role": "system", "content": "S" * 9000}, {"role": "user", "content": "hi"},
                           {"role": "assistant", "content": "hey"}, {"role": "user", "content": "bus time?"}])
    assert out == "Hello from Aivana" and FakeSDK.api_key == "ai_live_test"
    sent = FakeSDK.calls[-1]
    assert len(sent["system"]) == 8000 and [m["role"] for m in sent["messages"]] == ["user", "assistant", "user"]
    assert "web_search" not in sent
    client.complete([{"role": "user", "content": "weather?"}], web_search=True)
    assert FakeSDK.calls[-1]["web_search"] is True


def test_chat_context_goes_with_the_question_not_the_system_prompt(env, monkeypatch):
    c, db, planner = env
    sent = connect(monkeypatch, planner, lambda msgs: json.dumps({"reply": "ok", "action": None}))
    say(c, C, "compare packages")
    system, last = sent[-1][0]["content"], sent[-1][-1]["content"]
    assert len(system) < 8000 and "Context (JSON" in last and last.endswith("Question: compare packages")


def test_chat_sends_the_whole_area_and_turns_on_web_search(env, monkeypatch):
    c, db, planner = env
    sent = connect(monkeypatch, planner, lambda msgs: json.dumps({"reply": "ok", "action": None}))
    say(c, C, "which hotel is closest to the stadium?")
    ctx = json.loads(sent[-1][-1]["content"].split("\n", 1)[1].rsplit("\n\nQuestion:", 1)[0])
    trip = ctx["trips"][0]
    hotels = trip["area"]["hotels_near_venue_by_distance"]
    assert len(trip["priced_packages"]) == len(planner.packages(trip["trip_id"]))      # all packages, not a top 5
    assert hotels[0]["km_to_venue"] <= min(p["hotel_km"] for p in trip["priced_packages"] if p["hotel_km"] is not None)
    assert connect.kw[-1] == {"web_search": True}
    # players get the same area, without prices
    sent = connect(monkeypatch, planner, lambda msgs: json.dumps({"reply": "ok", "action": None}))
    say(c, P, "closest hotel?")
    assert "price" not in sent[-1][-1]["content"] and "hotels_near_venue_by_distance" in sent[-1][-1]["content"]
