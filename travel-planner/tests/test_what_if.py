"""What-if changes: Aivana proposes, the app prices, the coach confirms; nothing changes before that."""
import json

from tests.test_chat import C, P, connect, env, say  # noqa: F401  (fixture)


def propose(monkeypatch, planner, trip_id, changes, text="What if we stay 2 nights?"):
    connect(monkeypatch, planner, lambda msgs: json.dumps(
        {"reply": "Let me price that.", "action": {"type": "propose_change", "trip_id": trip_id, "changes": changes}}))


def test_proposal_is_priced_by_the_app_and_changes_nothing(env, monkeypatch):
    c, db, planner = env
    trip = db.select("trips")[0]
    before = [p["total_cost"] for p in planner.packages(trip["id"])]
    propose(monkeypatch, planner, trip["id"], {"nights": 2})
    r = say(c, C, "What if we stay 2 nights?")
    a = r["action"]
    assert a["ok"] and a["type"] == "propose_change" and a["changes"] == {"nights": 2}
    assert "hotel nights 1 → 2" in r["reply"] and "Nothing has changed yet" in r["reply"]
    # every $ amount in the reply comes from CostCalc's preview
    prev = a["preview"]
    for value in list(prev["options_after"].values()) + list(prev["options_before"].values()):
        assert f"${value:,.2f}" in r["reply"]
    assert prev["options_after"]["lowest_cost"] > prev["options_before"]["lowest_cost"]
    assert db.select("trips")[0]["nights"] == 1
    assert [p["total_cost"] for p in planner.packages(trip["id"])] == before


def test_confirmed_change_is_saved_and_repriced(env):
    c, db, planner = env
    tid = db.select("trips")[0]["id"]
    r = c.post(f"/api/trips/{tid}/update", headers=C, json={"changes": {"nights": 2, "budget": "12000"}})
    assert r.status_code == 200
    assert r.json["nights"] == 2 and r.json["budget"] == 12000 and r.json["status"] == "PLANNING"
    assert r.json["selected_package_no"] is None and db.select("bookings") == []
    assert all(p["detail"]["rooms"] * 2 >= p["detail"]["travelers"] for p in r.json["packages"])


def test_bad_or_unsafe_changes_are_refused(env, monkeypatch):
    c, db, planner = env
    tid = db.select("trips")[0]["id"]
    for changes, words in [({"nights": 99}, "between"), ({"budget": -1}, "negative"), ({"status": "BOOKED"}, "can't be changed"),
                           ({"venue_lat": 1}, "can't be changed"), ({}, "no changes"), ({"nights": 1}, "already"),
                           ({"depart_after": "2099-01-01T10:00"}, "before the match")]:
        r = c.post(f"/api/trips/{tid}/update", headers=C, json={"changes": changes})
        assert r.status_code == 400 and words in r.json["error"].lower(), (changes, r.json)
    propose(monkeypatch, planner, tid, {"nights": 400})
    r = say(c, C, "what if 400 nights")
    assert r["action"]["ok"] is False and "can't price" in r["action"]["message"]
    assert db.select("trips")[0]["nights"] == 1


def test_players_cannot_change_trips(env, monkeypatch):
    c, db, planner = env
    tid = db.select("trips")[0]["id"]
    assert c.post(f"/api/trips/{tid}/update", headers=P, json={"changes": {"nights": 2}}).status_code == 403
    propose(monkeypatch, planner, tid, {"nights": 2})
    assert say(c, P, "what if 2 nights")["action"] is None


def test_booked_trips_must_be_cancelled_first(env):
    c, db, planner = env
    tid = db.select("trips")[0]["id"]
    assert c.post(f"/api/trips/{tid}/select", headers=C, json={"package_no": 1}).status_code == 200
    r = c.post(f"/api/trips/{tid}/update", headers=C, json={"changes": {"nights": 2}})
    assert r.status_code == 400 and "cancel" in r.json["error"].lower()
