"""NCAA data: schools, rankings, standings, scores and brackets for every sport and division.

Source: the free ncaa-api (https://github.com/henrygd/ncaa-api), which mirrors ncaa.com.
There is no official NCAA public API. Things to know:
  - The public server allows 3 requests/second per IP. We throttle to 2.5/s.
  - No team rosters or per-team schedules exist. A team's games are built from the daily
    scoreboards (one request per date returns every game in that sport/division).
  - Results are cached in memory and, when Supabase is configured, in the `ncaa_cache` table,
    so a Render restart or many users don't trigger new requests.
Set NCAA_API_URL to your own instance (docker run henrygd/ncaa-api) for heavy use.
"""
import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone

import requests

BASE_URL = os.environ.get("NCAA_API_URL", "https://ncaa-api.henrygd.me").rstrip("/")
TIMEOUT = 20
MIN_INTERVAL = 0.4                    # seconds between requests (2.5/s, under the 3/s limit)

# Sports and divisions the API supports (from the API's own codes.ts).
# scoreboard=False: ncaa.com publishes rankings/standings only, no game scoreboard.
SPORTS = {
    "football": {"name": "Football", "divisions": ["fbs", "fcs", "d2", "d3"], "weekly": True},
    "basketball-men": {"name": "Men's Basketball", "divisions": ["d1", "d2", "d3"]},
    "basketball-women": {"name": "Women's Basketball", "divisions": ["d1", "d2", "d3"]},
    "baseball": {"name": "Baseball", "divisions": ["d1", "d2", "d3"]},
    "softball": {"name": "Softball", "divisions": ["d1", "d2", "d3"]},
    "soccer-men": {"name": "Men's Soccer", "divisions": ["d1", "d2", "d3"]},
    "soccer-women": {"name": "Women's Soccer", "divisions": ["d1", "d2", "d3"]},
    "volleyball-women": {"name": "Women's Volleyball", "divisions": ["d1", "d2", "d3"]},
    "volleyball-men": {"name": "Men's Volleyball", "divisions": ["d1", "d3"]},
    "beach-volleyball": {"name": "Beach Volleyball", "divisions": ["d1"]},
    "icehockey-men": {"name": "Men's Ice Hockey", "divisions": ["d1", "d3"]},
    "icehockey-women": {"name": "Women's Ice Hockey", "divisions": ["d1", "d3"]},
    "lacrosse-men": {"name": "Men's Lacrosse", "divisions": ["d1", "d2", "d3"]},
    "lacrosse-women": {"name": "Women's Lacrosse", "divisions": ["d1", "d2", "d3"]},
    "fieldhockey": {"name": "Field Hockey", "divisions": ["d1", "d2", "d3"]},
    "waterpolo-men": {"name": "Men's Water Polo", "divisions": ["d1"]},
    "waterpolo-women": {"name": "Women's Water Polo", "divisions": ["d1"]},
    "tennis-men": {"name": "Men's Tennis", "divisions": ["d1", "d2", "d3"], "scoreboard": False},
    "tennis-women": {"name": "Women's Tennis", "divisions": ["d1", "d2", "d3"], "scoreboard": False},
    "wrestling-men": {"name": "Wrestling", "divisions": ["d1", "d2", "d3"], "scoreboard": False},
    "gymnastics-women": {"name": "Women's Gymnastics", "divisions": ["d1"], "scoreboard": False},
    "gymnastics-men": {"name": "Men's Gymnastics", "divisions": ["d1"], "scoreboard": False},
    "swimming-men": {"name": "Men's Swimming", "divisions": ["d1", "d2", "d3"], "scoreboard": False},
    "swimming-women": {"name": "Women's Swimming", "divisions": ["d1", "d2", "d3"], "scoreboard": False},
    "golf-men": {"name": "Men's Golf", "divisions": ["d1", "d2", "d3"], "scoreboard": False},
    "golf-women": {"name": "Women's Golf", "divisions": ["d1", "d2", "d3"], "scoreboard": False},
    "bowling": {"name": "Bowling", "divisions": ["nc"], "scoreboard": False},
    "rifle": {"name": "Rifle", "divisions": ["nc"], "scoreboard": False},
}
DIVISION_NAMES = {"fbs": "FBS", "fcs": "FCS", "d1": "Division I", "d2": "Division II", "d3": "Division III",
                  "nc": "National Collegiate"}

HOUR = 3600
TTL = {"schools-index": 7 * 24 * HOUR, "rankings": 6 * HOUR, "standings": 6 * HOUR, "brackets": 15 * 60,
       "schedule": 12 * HOUR, "scoreboard-past": 7 * 24 * HOUR, "scoreboard-live": 10 * 60}


class NCAAError(RuntimeError):
    pass


class NCAAClient:
    def __init__(self, db=None, fetch=None, base_url=BASE_URL):
        self.db = db                              # SupabaseDB/MemoryDB for the persistent cache (optional)
        self.base_url = base_url
        self._fetch = fetch or self._http_get     # injectable for tests
        self._mem = {}                            # path -> (expires_at, payload)
        self._lock = threading.Lock()
        self._last_call = 0.0

    # -- plumbing ----------------------------------------------------------
    def _http_get(self, path):
        with self._lock:                          # global throttle shared by all users of this server
            wait = self._last_call + MIN_INTERVAL - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
        try:
            resp = requests.get(self.base_url + path, timeout=TIMEOUT,
                                headers={"User-Agent": "AwayGameTravelPlanner/1.0 (hackathon)"})
        except requests.RequestException as exc:
            raise NCAAError(f"NCAA data service unreachable: {exc}") from exc
        if resp.status_code == 404:
            raise NCAAError("NCAA has no data for that request.")
        if resp.status_code == 429:
            raise NCAAError("NCAA data service is busy; try again in a few seconds.")
        if resp.status_code >= 400:
            raise NCAAError(f"NCAA data service error ({resp.status_code}).")
        try:
            return resp.json()
        except ValueError as exc:
            raise NCAAError("NCAA data service returned something unreadable.") from exc

    def get(self, path, ttl):
        now = time.time()
        hit = self._mem.get(path)
        if hit and hit[0] > now:
            return hit[1]
        row = self._db_get(path)
        cached = _unwrap(row["payload"]) if row is not None else None
        if row is not None:
            fetched = _parse_ts(row["fetched_at"])
            if fetched and (datetime.now(timezone.utc) - fetched).total_seconds() < ttl:
                self._mem[path] = (now + ttl, cached)
                return cached
        try:
            payload = self._fetch(path)
        except NCAAError:
            if row is not None:                   # serve stale data rather than nothing
                return cached
            raise
        self._mem[path] = (now + ttl, payload)
        self._db_put(path, payload)
        return payload

    def _db_get(self, key):
        if self.db is None:
            return None
        try:
            rows = self.db.select("ncaa_cache", {"key": key})
            return rows[0] if rows else None
        except Exception:
            return None

    def _db_put(self, key, payload):
        if self.db is None:
            return
        # stored as JSON text inside jsonb: Postgres jsonb reorders object keys, which would
        # scramble the column order of rankings/standings tables
        wrapped = {"json": json.dumps(payload)}
        row = {"key": key, "payload": wrapped, "fetched_at": datetime.now(timezone.utc).isoformat()}
        try:
            if self.db.select("ncaa_cache", {"key": key}):
                self.db.update("ncaa_cache", {"payload": wrapped, "fetched_at": row["fetched_at"]}, {"key": key})
            else:
                self.db.insert("ncaa_cache", row)
        except Exception:
            pass                                   # cache is best effort

    # -- validation --------------------------------------------------------
    @staticmethod
    def check(sport, division):
        if sport not in SPORTS:
            raise NCAAError(f"Unknown sport: {sport}")
        if division not in SPORTS[sport]["divisions"]:
            raise NCAAError(f"{SPORTS[sport]['name']} has no {division.upper()} data. "
                            f"Choose one of: {', '.join(SPORTS[sport]['divisions'])}.")

    # -- public data -------------------------------------------------------
    def schools(self):
        return self.get("/schools-index", TTL["schools-index"])

    def search_schools(self, query, limit=20):
        q = _norm(query)
        if not q:
            return []
        out = [s for s in self.schools() if q in _norm(s.get("name")) or q in _norm(s.get("long")) or q in _norm(s.get("slug"))]
        out.sort(key=lambda s: (not _norm(s.get("name")).startswith(q), len(s.get("name") or "")))
        return out[:limit]

    def rankings(self, sport, division, poll=None):
        self.check(sport, division)
        return self.get(f"/rankings/{sport}/{division}" + (f"/{poll}" if poll else ""), TTL["rankings"])

    def standings(self, sport, division):
        self.check(sport, division)
        return self.get(f"/standings/{sport}/{division}", TTL["standings"])

    def brackets(self, sport, division, year):
        self.check(sport, division)
        return self.get(f"/brackets/{sport}/{division}/{int(year)}", TTL["brackets"])

    def scoreboard(self, sport, division, day: date | None = None, week=None, season=None):
        """Games for one date (or one football week). No date = the current day/week."""
        self.check(sport, division)
        if SPORTS[sport].get("scoreboard") is False:
            raise NCAAError(f"NCAA doesn't publish scoreboards for {SPORTS[sport]['name']}.")
        if SPORTS[sport].get("weekly"):
            path = f"/scoreboard/{sport}/{division}" + (f"/{int(season)}/{str(week).zfill(2)}" if week else "")
            ttl = TTL["scoreboard-live"]
        else:
            path = f"/scoreboard/{sport}/{division}" + (f"/{day:%Y/%m/%d}" if day else "")
            ttl = TTL["scoreboard-past"] if day and day < date.today() - timedelta(days=1) else TTL["scoreboard-live"]
        data = self.get(path, ttl)
        return [_game(g.get("game") or {}) for g in (data.get("games") or [])]

    # -- team helpers --------------------------------------------------------
    def team_games(self, sport, division, seo, start: date | None = None, days=14):
        """A team's games over the next `days` days (football: the current week)."""
        seo = (seo or "").lower()
        start = start or date.today()
        if SPORTS.get(sport, {}).get("weekly"):
            games = self.scoreboard(sport, division)
        else:
            games = []
            for i in range(days):
                try:
                    games += self.scoreboard(sport, division, start + timedelta(days=i))
                except NCAAError:
                    continue                       # no games / no data that day
        mine = [g for g in games if seo in (g["home"]["seo"], g["away"]["seo"])]
        for g in mine:
            g["is_away"] = g["away"]["seo"] == seo
            g["opponent"] = g["home"] if g["is_away"] else g["away"]
        return sorted(mine, key=lambda g: g.get("start_epoch") or 0)

    def team_summary(self, sport, division, school_name):
        """Record (from standings) and poll rank (from rankings) for one school, when listed."""
        out = {"record": None, "conference": None, "rank": None, "poll": None, "errors": []}
        target = _norm(school_name)
        try:
            st = self.standings(sport, division)
            for conf in st.get("data") or []:
                for row in conf.get("standings") or []:
                    name = row.get("School") or row.get("SCHOOL") or row.get("Team") or ""
                    if _norm(name) == target:
                        out["conference"] = conf.get("conference")
                        out["record"] = _record(row)
                        out["standings_row"] = row
        except NCAAError as exc:
            out["errors"].append(f"standings: {exc}")
        try:
            rk = self.rankings(sport, division)
            out["poll"] = rk.get("title")
            for row in rk.get("data") or []:
                if _norm(_strip_votes(row.get("SCHOOL") or row.get("School") or "")) == target:
                    out["rank"] = row.get("RANK") or row.get("Rank")
                    out["record"] = out["record"] or row.get("RECORD") or row.get("Record")
        except NCAAError as exc:
            out["errors"].append(f"rankings: {exc}")
        return out


# -- shaping helpers ------------------------------------------------------
def _norm(s):
    s = (s or "").lower().replace("&", "and").replace("saint ", "st ").replace("st.", "st")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _strip_votes(name):
    return re.sub(r"\s*\(\d+\)\s*$", "", name or "").strip()


def _record(row):
    """Build 'W-L' (or 'W-L-T') from standings columns, whatever the sport calls them."""
    def pick(name):
        for key in row:
            if " ".join(key.upper().split()) == name:     # exact column, so "OVERALL W%" isn't "OVERALL W"
                return row[key]
        return None
    for scope in ("OVERALL", "CONFERENCE", ""):
        w = pick(f"{scope} W".strip()) if scope else row.get("W")
        l = pick(f"{scope} L".strip()) if scope else row.get("L")
        if w not in (None, "") and l not in (None, ""):
            t = pick(f"{scope} T".strip()) if scope else row.get("T")
            return f"{w}-{l}" + (f"-{t}" if t not in (None, "", "0") else "")
    return None


def _team(t):
    names = t.get("names") or {}
    return {"short": names.get("short") or "", "full": names.get("full") or "", "seo": (names.get("seo") or "").lower(),
            "char6": names.get("char6") or "", "score": t.get("score") or "", "rank": t.get("rank") or "",
            "seed": t.get("seed") or "", "winner": bool(t.get("winner")),
            "conference": ((t.get("conferences") or [{}])[0] or {}).get("conferenceSeo", "")}


def _game(g):
    epoch = g.get("startTimeEpoch")
    try:
        epoch = int(epoch) if epoch not in (None, "") else None
    except (TypeError, ValueError):
        epoch = None
    return {"id": str(g.get("gameID") or ""), "home": _team(g.get("home") or {}), "away": _team(g.get("away") or {}),
            "start_date": g.get("startDate") or "", "start_time": g.get("startTime") or "", "start_epoch": epoch,
            "state": g.get("gameState") or "", "final_message": g.get("finalMessage") or "",
            "network": g.get("network") or "", "url": ("https://www.ncaa.com" + g["url"]) if g.get("url") else ""}


def _unwrap(payload):
    if isinstance(payload, dict) and set(payload) == {"json"} and isinstance(payload["json"], str):
        return json.loads(payload["json"])
    return payload


def _parse_ts(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def game_start_local(game) -> str | None:
    """'YYYY-MM-DDTHH:MM' for a datetime-local input, from the game's epoch (shown in Eastern time)."""
    if not game.get("start_epoch"):
        return None
    eastern = timezone(timedelta(hours=-5))         # NCAA lists times in ET; DST ignored for the prefill
    return datetime.fromtimestamp(game["start_epoch"], eastern).strftime("%Y-%m-%dT%H:%M")


def dumps(obj):
    return json.dumps(obj, default=str)
