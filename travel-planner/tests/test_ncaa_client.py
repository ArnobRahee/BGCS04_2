import pytest

from db import MemoryDB
from ncaa_client import NCAAClient, NCAAError, _record, game_start_local
from tests.ncaa_fixtures import FakeNCAA


def test_school_search_ranks_prefix_matches_first():
    c = NCAAClient(fetch=FakeNCAA())
    names = [s["slug"] for s in c.search_schools("ohio")]
    assert names[0] == "ohio" and "ohio-st" in names
    assert c.search_schools("bowling green state")[0]["slug"] == "bowling-green"
    assert c.search_schools("") == []


def test_team_summary_record_conference_and_rank():
    s = NCAAClient(fetch=FakeNCAA()).team_summary("football", "fbs", "Bowling Green")
    assert s["record"] == "9-2" and s["conference"] == "Mid-American"
    assert s["rank"] == "24" and s["poll"] == "Associated Press"
    s = NCAAClient(fetch=FakeNCAA()).team_summary("football", "fbs", "Ohio St.")    # "(52)" first-place votes stripped
    assert s["rank"] == "1" and s["record"] == "11-0"


def test_record_handles_ties_and_missing_columns():
    assert _record({"OVERALL W": "10", "OVERALL L": "3", "OVERALL T": "2"}) == "10-3-2"
    assert _record({"W": "4", "L": "1"}) == "4-1"
    assert _record({"Overall  W%": ".800"}) is None


def test_team_games_marks_away_and_opponent():
    c = NCAAClient(fetch=FakeNCAA())
    games = c.team_games("football", "fbs", "bowling-green")
    assert len(games) == 1 and games[0]["is_away"] and games[0]["opponent"]["seo"] == "ohio-st"
    assert games[0]["url"].startswith("https://www.ncaa.com/game/")
    assert game_start_local(games[0]) == "2026-11-28T12:00"
    hoops = c.team_games("basketball-men", "d1", "bowling-green", days=7)
    assert len(hoops) == 1 and not hoops[0]["is_away"]


def test_memory_and_database_cache_avoid_repeat_requests():
    db, fake = MemoryDB(), FakeNCAA()
    c = NCAAClient(db=db, fetch=fake)
    c.standings("football", "fbs"); c.standings("football", "fbs")
    assert fake.calls.count("/standings/football/fbs") == 1
    fake2 = FakeNCAA()
    NCAAClient(db=db, fetch=fake2).standings("football", "fbs")        # new server process, same Supabase
    assert fake2.calls == []
    stored = db.select("ncaa_cache", {"key": "/standings/football/fbs"})[0]["payload"]
    row = NCAAClient(db=db, fetch=FakeNCAA()).standings("football", "fbs")["data"][0]["standings"][0]
    assert list(row)[0] == "School" and isinstance(stored["json"], str)   # column order survives the cache


def test_stale_cache_served_when_ncaa_is_down():
    db = MemoryDB()
    NCAAClient(db=db, fetch=FakeNCAA()).schools()
    db.tables["ncaa_cache"][0]["fetched_at"] = "2000-01-01T00:00:00+00:00"
    assert NCAAClient(db=db, fetch=FakeNCAA(fail=True)).schools()[0]["slug"] == "bowling-green"
    with pytest.raises(NCAAError):
        NCAAClient(fetch=FakeNCAA(fail=True)).schools()


def test_invalid_sport_or_division_never_hits_the_api():
    fake = FakeNCAA()
    c = NCAAClient(fetch=fake)
    for sport, div in (("football", "d1"), ("quidditch", "d1"), ("icehockey-men", "d2")):
        with pytest.raises(NCAAError):
            c.standings(sport, div)
    with pytest.raises(NCAAError):
        c.scoreboard("tennis-men", "d1")
    assert fake.calls == []
