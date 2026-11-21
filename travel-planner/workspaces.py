"""Team workspaces: one per NCAA program (school + sport + division), each with its own trips.

The selected team is the user's `profiles.team_id`; every trip, roster and announcement query is
scoped by that id on the server. A user can belong to several workspaces (`team_members`) and
switch between them. Choosing a different NCAA program switches to (or creates) that program's
workspace; it never relabels the current one, so two universities' data can't mix.

`identity(team)` is the single source for the team's name, sport, logo and season everywhere in the
app (sidebar, top bar, season block, Team Pulse).
"""
from datetime import date

from ncaa_client import BASE_URL as NCAA_BASE, DIVISION_NAMES, SPORTS

# Fall sports are labelled with one calendar year; the rest span two (e.g. 2026–27).
FALL_SPORTS = {"football", "soccer-men", "soccer-women", "volleyball-women", "fieldhockey",
               "waterpolo-men", "waterpolo-women"}


class WorkspaceError(ValueError):
    pass


def season_label(sport: str | None, today: date | None = None) -> str:
    today = today or date.today()
    if sport in FALL_SPORTS or not sport:
        return str(today.year)
    start = today.year if today.month >= 7 else today.year - 1
    return f"{start}–{str(start + 1)[2:]}"


def initials(name: str) -> str:
    words = [w for w in "".join(c if c.isalnum() else " " for c in (name or "")).split() if w]
    return ("".join(w[0] for w in words[:2]) or "T").upper()


def identity(team: dict | None) -> dict | None:
    """Everything the UI shows about the selected team, from the team's own row only."""
    if not team:
        return None
    sport, division, seo = team.get("sport"), team.get("division"), team.get("ncaa_seo")
    linked = bool(seo and sport and division)
    name = (team.get("name") or team.get("ncaa_name") or "").strip()
    return {
        "team_id": team["id"],
        "display_name": name,
        "university": team.get("ncaa_name") if linked else None,
        "sport": sport if linked else None,
        "sport_name": SPORTS.get(sport, {}).get("name") if linked else None,
        "division": division if linked else None,
        "division_name": DIVISION_NAMES.get(division, division) if linked else None,
        "ncaa_seo": seo if linked else None,
        "logo": f"{NCAA_BASE}/logo/{seo}.svg" if linked else None,   # same source as Team Pulse
        "initials": initials(name),
        "season": season_label(sport if linked else None),
        "linked": linked,
    }


class Workspaces:
    def __init__(self, db, ncaa=None, geocoder=None, join_code=None):
        self.db, self.ncaa, self.geocoder = db, ncaa, geocoder
        self.join_code = join_code

    # -- membership --------------------------------------------------------
    def team_ids(self, profile: dict) -> list:
        ids = [m["team_id"] for m in self.db.select("team_members", {"profile_id": profile["id"]})]
        if profile.get("team_id") and profile["team_id"] not in ids:      # rows from before team_members
            ids.append(profile["team_id"])
        return ids

    def add_member(self, team_id, profile_id, role):
        if not self.db.select("team_members", {"team_id": team_id, "profile_id": profile_id}):
            self.db.insert("team_members", {"team_id": team_id, "profile_id": profile_id, "role": role})

    def is_member(self, profile: dict, team_id) -> bool:
        return team_id in self.team_ids(profile)

    def list_for(self, profile: dict) -> list:
        ids = self.team_ids(profile)
        teams = self.db.select("teams", {"id": ids}) if ids else []
        out = [{**identity(t), "selected": t["id"] == profile.get("team_id")} for t in teams]
        return sorted(out, key=lambda t: (not t["selected"], t["display_name"].lower()))

    def select(self, profile: dict, team_id) -> dict:
        team_id = str(team_id or "")
        if not team_id or not self.is_member(profile, team_id):
            raise PermissionError("You're not a member of that team.")
        return self.db.update("profiles", {"team_id": team_id}, {"id": profile["id"]})[0]

    # -- choosing an NCAA program ----------------------------------------------
    def choose_program(self, profile: dict, current: dict, sport, division, seo, name, long_name="") -> dict:
        """Returns {"team": row, "action": "linked" | "switched" | "created"}.
        - current workspace not linked yet: link it (first-time setup)
        - same program: nothing to change
        - one of the user's workspaces is that program: switch to it
        - otherwise: create a new, empty workspace for that program and switch to it"""
        same = lambda t: (t.get("ncaa_seo"), t.get("sport"), t.get("division")) == (seo, sport, division)
        if same(current):
            return {"team": current, "action": "unchanged"}
        if not current.get("ncaa_seo"):
            team = self.db.update("teams", {"ncaa_seo": seo, "ncaa_name": name, "sport": sport, "division": division},
                                  {"id": current["id"]})[0]
            return {"team": team, "action": "linked"}
        ids = self.team_ids(profile)
        mine = [t for t in (self.db.select("teams", {"id": ids}) if ids else []) if same(t)]
        if mine:
            self.select(profile, mine[0]["id"])
            return {"team": mine[0], "action": "switched"}
        home = self._home_for(long_name or name)
        row = {"name": name, "join_code": self.join_code(name), **home, "owner_id": profile["id"],
               "ncaa_seo": seo, "ncaa_name": name, "sport": sport, "division": division}
        team = self.db.insert("teams", row)[0]
        self.add_member(team["id"], profile["id"], profile["role"])
        self.select(profile, team["id"])
        return {"team": team, "action": "created"}

    def _home_for(self, school: str) -> dict:
        """The bus leaves from the school's campus: find it by name (OpenStreetMap). Never borrow
        another team's home location."""
        try:
            found = self.geocoder.search(school) if self.geocoder else []
        except Exception:
            found = []
        if not found:
            raise WorkspaceError(f"Couldn't find {school}'s campus on the map, so the new team's home location "
                                 "is unknown. Try again in a moment.")
        return {"home_name": school, "home_lat": found[0]["lat"], "home_lng": found[0]["lng"]}
