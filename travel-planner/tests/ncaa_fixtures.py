"""Fake NCAA API responses, shaped like henrygd/ncaa-api's output (see its src/index.ts and scoreboard.ts)."""
from datetime import date, timedelta

SCHOOLS = [
    {"slug": "bowling-green", "name": "Bowling Green", "long": "Bowling Green State University"},
    {"slug": "ohio-st", "name": "Ohio St.", "long": "The Ohio State University"},
    {"slug": "toledo", "name": "Toledo", "long": "University of Toledo"},
    {"slug": "kent-st", "name": "Kent St.", "long": "Kent State University"},
    {"slug": "ohio", "name": "Ohio", "long": "Ohio University"},
]
RANKINGS = {"sport": "Football", "title": "Associated Press", "updated": "Through Games NOV. 16, 2026", "page": 1, "pages": 1,
            "data": [{"RANK": "1", "SCHOOL": "Ohio St. (52)", "POINTS": "1,625", "PREVIOUS": "1", "RECORD": "11-0"},
                     {"RANK": "24", "SCHOOL": "Bowling Green", "POINTS": "88", "PREVIOUS": "NR", "RECORD": "9-2"}]}
STANDINGS = {"sport": "Football", "title": "ALL CONFERENCES", "updated": "Nov 17", "page": 1, "pages": 1,
             "data": [{"conference": "Mid-American", "standings": [
                 {"School": "Bowling Green", "CONFERENCE W": "6", "CONFERENCE L": "1", "CONFERENCE PCT": "0.857",
                  "OVERALL W": "9", "OVERALL L": "2", "OVERALL PCT": "0.818", "STREAK": "W4"},
                 {"School": "Toledo", "CONFERENCE W": "5", "CONFERENCE L": "2", "CONFERENCE PCT": "0.714",
                  "OVERALL W": "7", "OVERALL L": "4", "OVERALL PCT": "0.636", "STREAK": "L1"}]}]}


def team(seo, short, full, rank=""):
    return {"score": "", "names": {"char6": short[:6].upper(), "short": short, "seo": seo, "full": full},
            "winner": False, "seed": "", "description": "", "rank": rank,
            "conferences": [{"conferenceName": "", "conferenceSeo": "mac"}]}


def scoreboard(home, away, epoch, game_id="6400001"):
    return {"inputMD5Sum": "x", "instanceId": "y", "updated_at": "2026-11-21 05:00:00",
            "games": [{"game": {"gameID": game_id, "away": away, "home": home, "finalMessage": "", "title": "",
                                "url": f"/game/{game_id}", "network": "ESPN2", "liveVideoEnabled": False,
                                "startTime": "12:00PM ET", "startTimeEpoch": str(epoch), "gameState": "pre",
                                "startDate": "11-28-2026", "currentPeriod": "", "contestClock": "0:00",
                                "bracketId": "", "bracketRound": ""}}]}


AWAY_AT_OHIO_ST = scoreboard(team("ohio-st", "Ohio St.", "The Ohio State University", "1"),
                             team("bowling-green", "Bowling Green", "Bowling Green State University", "24"),
                             epoch=1795885200)
HOME_VS_OHIO = scoreboard(team("bowling-green", "Bowling Green", "Bowling Green State University"),
                          team("ohio", "Ohio", "Ohio University"), epoch=1796490000, game_id="6400002")


class FakeNCAA:
    """Callable used as NCAAClient(fetch=...). Records every path requested."""
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, path):
        from ncaa_client import NCAAError, NCAANoData
        self.calls.append(path)
        if self.fail:
            raise NCAAError("down")
        if path == "/schools-index":
            return SCHOOLS
        if path.startswith("/rankings/"):
            return RANKINGS
        if path.startswith("/standings/"):
            return STANDINGS
        if path == "/scoreboard/football/fbs":
            return AWAY_AT_OHIO_ST
        if path.startswith("/scoreboard/basketball-men/d1/"):
            d = (date.today() + timedelta(days=3)).strftime("%Y/%m/%d")
            return HOME_VS_OHIO if path.endswith(d) else {"games": []}
        raise NCAANoData("NCAA has no data for that request.")
