"""Deleting trips at every stage, and the demo Player preview following the planner's team."""
import pytest

from tests.test_workspaces import C, P, SOON, env, me, pick  # noqa: F401  (fixture)


def new_trip(c, **over):
    body = {"opponent": "Toledo", "venue_id": "V002", "match_at": SOON, "num_players": 12, "nights": 1, "meals": 1}
    body.update(over)
    r = c.post("/api/trips", headers=C, json=body)
    assert r.status_code == 201, r.json
    return r.json["id"]


def to_stage(c, tid, stage):
    if stage in ("AWAITING_APPROVAL", "BOOKED", "PUBLISHED"):
        assert c.post(f"/api/trips/{tid}/select", headers=C, json={"package_no": 1}).status_code == 200
    if stage in ("BOOKED", "PUBLISHED"):
        assert c.post(f"/api/trips/{tid}/approve", headers=C).json["ok"]
        c.post(f"/api/trips/{tid}/rooms", headers=C)
    if stage == "PUBLISHED":
        assert c.post(f"/api/trips/{tid}/publish", headers=C, json={"title": "Plan", "body": "x"}).json["published"]
        c.post(f"/api/trips/{tid}/rsvp", headers=P, json={"rsvp": "going"})
        ann = c.get("/api/announcements", headers=P).json[0]
        c.post(f"/api/announcements/{ann['id']}/save", headers=P)
        c.post(f"/api/announcements/{ann['id']}/read", headers=P)


@pytest.mark.parametrize("stage", ["PLANNING", "AWAITING_APPROVAL", "BOOKED", "PUBLISHED"])
def test_delete_trip_at_every_stage_leaves_nothing_behind(env, stage):
    c, db, _ = env
    keep = new_trip(c, opponent="Kent State", match_at=SOON.replace("T12", "T18"))
    tid = new_trip(c)
    to_stage(c, tid, stage)
    r = c.delete(f"/api/trips/{tid}", headers=C)
    assert r.status_code == 200 and r.json["deleted"] == tid
    assert [t["id"] for t in c.get("/api/trips", headers=C).json] == [keep]
    assert c.get(f"/api/trips/{tid}", headers=C).status_code == 404
    for table in ("trip_packages", "bookings", "trip_participants", "announcements", "saved_items"):
        assert not [row for row in db.select(table) if row.get("trip_id") == tid], table
    ann_ids = {a["id"] for a in db.select("announcements")}
    assert all(r["announcement_id"] in ann_ids for r in db.select("announcement_reads"))
    assert all(s.get("announcement_id") in ann_ids or s.get("trip_id") for s in db.select("saved_items"))
    assert c.delete(f"/api/trips/{tid}", headers=C).status_code == 404          # second request: already gone
    assert len(c.get(f"/api/trips/{keep}", headers=C).json["packages"]) > 0      # the other trip is untouched


def test_only_the_main_planner_of_that_team_can_delete(env):
    c, db, _ = env
    tid = new_trip(c)
    assert c.delete(f"/api/trips/{tid}", headers=P).status_code == 403
    team = db.select("teams")[0]
    db.update("teams", {"owner_id": "someone-else"}, {"id": team["id"]})
    r = c.delete(f"/api/trips/{tid}", headers=C)
    assert r.status_code == 403 and "main planner" in r.json["error"]
    db.update("teams", {"owner_id": "demo-coach-id"}, {"id": team["id"]})
    pick(c, "volleyball-women", "d1", "harvard")                                # switch to another team
    assert c.delete(f"/api/trips/{tid}", headers=C).status_code == 404          # not visible from Harvard
    assert db.select("trips", {"id": tid})


def test_player_preview_follows_the_planners_team(env):
    c, db, _ = env
    bgsu = me(c)["team"]["id"]
    pick(c, "volleyball-women", "d1", "harvard")
    harvard = me(c)["team"]["id"]
    pl = me(c, P)
    assert pl["team"]["id"] == harvard and pl["team"]["identity"]["display_name"] == "Harvard"
    assert pl["team"]["identity"]["sport_name"] == "Women's Volleyball" and pl["profile"]["role"] == "player"
    # the player still only sees published information
    tid = new_trip(c)
    view = c.get(f"/api/trips/{tid}", headers=P).json
    assert "packages" not in view and "budget" not in str(view) and "hotel" not in view
    assert c.delete(f"/api/trips/{tid}", headers=P).status_code == 403
    assert c.post(f"/api/trips/{tid}/select", headers=P, json={"package_no": 1}).status_code == 403
    # switching back moves the preview back too
    c.post("/api/teams/select", headers=C, json={"team_id": bgsu})
    assert me(c, P)["team"]["id"] == bgsu
    assert me(c)["team"]["id"] == bgsu                                          # planner selection unchanged
