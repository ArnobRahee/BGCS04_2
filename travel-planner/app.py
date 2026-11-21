"""Flask entry point. Run locally: python app.py   |   Render: gunicorn app:app

With SUPABASE_URL + SUPABASE_SERVICE_KEY set, data and logins go through Supabase.
Without them the app runs in demo mode: an in-memory database, the bundled catalog and
two one-click demo accounts (coach and player), so anyone can try it with no setup.
"""
import os
import secrets
from datetime import date

from dotenv import load_dotenv

load_dotenv()  # must run before the modules below read environment variables

from flask import Flask, g, jsonify, redirect, render_template, request  # noqa: E402

import db as dbmod  # noqa: E402
from auth import login_required  # noqa: E402
from chat_assistant import ChatAssistant  # noqa: E402
from demo import seed_demo  # noqa: E402
from google_places import PlacesError  # noqa: E402
from ncaa_client import BASE_URL as NCAA_BASE, DIVISION_NAMES, SPORTS, NCAAClient, NCAAError, game_start_local  # noqa: E402
from travel_planner import PlanError, TravelPlanner  # noqa: E402

DIET_TAGS = ["vegetarian", "vegan", "halal", "gluten_free", "nut_free"]

app = Flask(__name__)
app.json.sort_keys = False          # keep NCAA table columns in the order ncaa.com shows them
DB = dbmod.from_env()
DEMO = DB is None
if DEMO:
    DB = dbmod.MemoryDB()
planner = TravelPlanner(DB)
ncaa = NCAAClient(None if DEMO else DB)          # Supabase also stores the NCAA cache
if DEMO:
    seed_demo(DB, planner)


def get_db():
    return DB


coach_only = login_required(get_db, role="coach")
player_only = login_required(get_db, role="player")
member = login_required(get_db)


@app.errorhandler(PlanError)
def plan_error(exc):
    return jsonify({"error": str(exc)}), 400


@app.errorhandler(NCAAError)
def ncaa_error(exc):
    return jsonify({"error": str(exc)}), 502


@app.errorhandler(dbmod.DBError)
def db_error(exc):
    return jsonify({"error": f"Database problem: {exc}"}), 502


def require_main_planner(team=None):
    """Hotel, bus and restaurant bookings are limited to the team's main planner (teams.owner_id)."""
    team = team or planner.team(g.profile["team_id"])
    if team.get("owner_id") != g.profile["id"]:
        raise PermissionError("Only the team's main planner can book hotels, buses and restaurants.")
    return team


@app.errorhandler(PermissionError)
def forbidden(exc):
    return jsonify({"error": str(exc)}), 403


def body():
    return request.get_json(silent=True) or {}


def my_trip(trip_id):
    return planner.trip(trip_id, g.profile["team_id"])


# -- pages ----------------------------------------------------------------
@app.get("/")
def index():
    config = {"supabaseUrl": os.environ.get("SUPABASE_URL", ""),
              "supabaseKey": os.environ.get("SUPABASE_PUBLISHABLE_KEY", ""),
              "demo": DEMO, "dietTags": DIET_TAGS, "ncaaBase": NCAA_BASE,
              # Browser key for the Maps JavaScript API. Restrict it to your site's address in Google Cloud.
              "mapsKey": os.environ.get("GOOGLE_MAPS_BROWSER_KEY", "")}
    return render_template("index.html", config=config)


@app.get("/api/health")
def health():
    return jsonify({"database": "memory (demo)" if DEMO else "supabase", "catalog": planner.data.source,
                    "aivana": "connected" if planner.aivana.configured else "not connected",
                    "google_hotels": "on" if planner.data.places.configured else "off (catalog hotels)",
                    "google_map": "on" if os.environ.get("GOOGLE_MAPS_BROWSER_KEY") else "off"})


@app.get("/api/places/photo/<token>")
def place_photo(token):
    """Hotel photo from Google. Public (img tags can't send tokens) but only signed links work."""
    try:
        uri = planner.data.places.resolve_photo(token)
    except PlacesError:
        return jsonify({"error": "Photo not available."}), 404
    resp = redirect(uri, code=302)
    resp.headers["Cache-Control"] = "private, max-age=1800"
    return resp


# -- account --------------------------------------------------------------
@app.get("/api/me")
@login_required(get_db, need_profile=False)
def me():
    team = planner.team(g.profile["team_id"]) if g.profile and g.profile.get("team_id") else None
    if team and g.profile["role"] != "coach":
        team = {k: team.get(k) for k in ("id", "name", "home_name", "ncaa_seo", "ncaa_name", "sport", "division")}
    meta = g.user.get("user_metadata") or {}
    user = {"id": g.user["id"], "email": g.user.get("email"), "name": meta.get("full_name") or meta.get("name") or "",
            "provider": (g.user.get("app_metadata") or {}).get("provider", "email")}
    planner_row = DB.select("profiles", {"id": team["owner_id"]}) if team and team.get("owner_id") else []
    return jsonify({"user": user, "profile": g.profile, "team": team,
                    "main_planner": {"id": planner_row[0]["id"], "name": planner_row[0]["full_name"]} if planner_row else None,
                    "is_main_planner": bool(g.profile and planner_row and planner_row[0]["id"] == g.profile["id"]),
                    "aivana": planner.aivana.configured})


@app.post("/api/onboard")
@login_required(get_db, need_profile=False)
def onboard():
    b = body()
    if g.profile and g.profile.get("team_id"):
        return jsonify({"error": "Your account is already set up."}), 400
    name = str(b.get("full_name") or "").strip()
    role = b.get("role")
    diet = [t for t in (b.get("diet_tags") or []) if t in DIET_TAGS]
    if not name or role not in ("coach", "player"):
        return jsonify({"error": "Enter your name and choose Team Planner or Player."}), 400
    if role == "coach":
        try:
            team_row = {"name": str(b["team_name"]).strip(), "home_name": str(b.get("home_name") or "Home campus"),
                        "home_lat": float(b["home_lat"]), "home_lng": float(b["home_lng"]),
                        "owner_id": g.user["id"], "join_code": new_join_code(str(b["team_name"]))}
        except (KeyError, ValueError, TypeError):
            return jsonify({"error": "Enter a team name and the home campus latitude/longitude."}), 400
        if not team_row["name"]:
            return jsonify({"error": "Enter a team name."}), 400
        team = DB.insert("teams", team_row)[0]
    else:
        code = str(b.get("join_code") or "").strip().upper()
        found = DB.select("teams", {"join_code": code})
        if not found:
            return jsonify({"error": "That join code doesn't match any team. Ask your team planner for it."}), 400
        team = found[0]
    profile = {"id": g.user["id"], "full_name": name, "role": role, "team_id": team["id"], "diet_tags": diet}
    if g.profile:
        DB.update("profiles", profile, {"id": g.user["id"]})
    else:
        DB.insert("profiles", profile)
    return jsonify({"ok": True})


@app.patch("/api/me")
@member
def update_me():
    b, values = body(), {}
    if "diet_tags" in b:
        values["diet_tags"] = [t for t in b["diet_tags"] if t in DIET_TAGS]
    if str(b.get("full_name") or "").strip():
        values["full_name"] = b["full_name"].strip()
    if values:
        DB.update("profiles", values, {"id": g.profile["id"]})
    return jsonify({"ok": True})


def new_join_code(team_name: str) -> str:
    prefix = "".join(c for c in team_name.upper() if c.isalpha())[:6] or "TEAM"
    for _ in range(20):
        code = f"{prefix}-{secrets.randbelow(9000) + 1000}"
        if not DB.select("teams", {"join_code": code}):
            return code
    return f"{prefix}-{secrets.token_hex(3).upper()}"


@app.get("/api/team")
@coach_only
def team():
    tid = g.profile["team_id"]
    staff = DB.select("profiles", {"team_id": tid, "role": "coach"}, order="full_name.asc")
    return jsonify({"team": planner.team(tid), "players": planner.team_players(tid),
                    "staff": [{k: c.get(k) for k in ("id", "full_name", "created_at")} for c in staff]})


@app.post("/api/team/planner")
@coach_only
def transfer_planner():
    """Hand the main planner role to another coach on the same team (only the current planner can)."""
    team = require_main_planner()
    new_id = str(body().get("profile_id") or "")
    if not DB.select("profiles", {"id": new_id, "team_id": team["id"], "role": "coach"}):
        raise PlanError("Pick a team planner on your team.")
    return jsonify(DB.update("teams", {"owner_id": new_id}, {"id": team["id"]})[0])


# -- catalog --------------------------------------------------------------
@app.get("/api/venues")
@member
def venues():
    return jsonify(sorted(planner.data.venues.values(), key=lambda v: v["name"]))


@app.get("/api/restaurants/<rid>/menu")
@member
def menu(rid):
    tags = [t for t in request.args.get("diet", "").split(",") if t]
    return jsonify(planner.data.menu(rid, tags))


# -- trips ----------------------------------------------------------------
@app.get("/api/trips")
@member
def trips():
    rows = DB.select("trips", {"team_id": g.profile["team_id"]}, order="match_at.asc")
    if g.profile["role"] == "coach":
        return jsonify(rows)
    return jsonify([planner.player_view(t, g.profile) for t in rows])


@app.post("/api/trips")
@coach_only
def create_trip():
    team = planner.team(g.profile["team_id"])
    return jsonify(planner.coach_view(planner.create_trip(team, g.profile, body()))), 201


@app.get("/api/trips/<trip_id>")
@member
def get_trip(trip_id):
    trip = my_trip(trip_id)
    if g.profile["role"] == "coach":
        return jsonify(planner.coach_view(trip))
    return jsonify(planner.player_view(trip, g.profile))


@app.post("/api/trips/<trip_id>/replan")
@coach_only
def replan(trip_id):
    trip = my_trip(trip_id)
    if trip["status"] in ("AWAITING_APPROVAL", "BOOKED"):
        raise PlanError("Cancel the current bookings before re-planning.")
    return jsonify(planner.coach_view(planner.replan_with_rsvps(trip, planner.team(trip["team_id"]))))


@app.post("/api/trips/<trip_id>/select")
@coach_only
def select(trip_id):
    require_main_planner()
    try:
        no = int(body()["package_no"])
    except (KeyError, ValueError, TypeError):
        raise PlanError("Choose a package.")
    return jsonify(planner.coach_view(planner.select_package(my_trip(trip_id), no, g.profile["id"])))


@app.post("/api/trips/<trip_id>/approve")
@coach_only
def approve(trip_id):
    require_main_planner()
    result = planner.approve(my_trip(trip_id), g.profile["id"])
    return jsonify({"ok": result["ok"], "error": result.get("error"), "trip": planner.coach_view(result["trip"])})


@app.post("/api/trips/<trip_id>/cancel")
@coach_only
def cancel(trip_id):
    require_main_planner()
    return jsonify(planner.coach_view(planner.cancel_bookings(my_trip(trip_id))))


@app.post("/api/trips/<trip_id>/rooms")
@coach_only
def rooms(trip_id):
    trip = my_trip(trip_id)
    planner.assign_rooms(trip)
    return jsonify(planner.coach_view(trip))


@app.get("/api/trips/<trip_id>/draft")
@coach_only
def draft(trip_id):
    return jsonify(planner.draft_announcement(my_trip(trip_id)))


@app.post("/api/trips/<trip_id>/publish")
@coach_only
def publish(trip_id):
    b = body()
    title, text = str(b.get("title") or "").strip(), str(b.get("body") or "").strip()
    if not title:
        raise PlanError("Add a title for the announcement.")
    planner.publish(my_trip(trip_id), g.profile["id"], title, text)
    return jsonify(planner.coach_view(my_trip(trip_id)))


@app.post("/api/trips/<trip_id>/rsvp")
@player_only
def rsvp(trip_id):
    choice = body().get("rsvp")
    if choice not in ("going", "maybe", "not_going"):
        raise PlanError("RSVP must be going, maybe or not_going.")
    trip = my_trip(trip_id)
    key = {"trip_id": trip["id"], "player_id": g.profile["id"]}
    if DB.select("trip_participants", key):
        DB.update("trip_participants", {"rsvp": choice}, key)
    else:
        DB.insert("trip_participants", {**key, "rsvp": choice, "room_no": None, "bus_no": None})
    return jsonify(planner.player_view(trip, g.profile))


# -- announcements --------------------------------------------------------
@app.get("/api/announcements")
@member
def announcements():
    rows = DB.select("announcements", {"team_id": g.profile["team_id"]}, order="created_at.desc", limit=100)
    ids = [a["id"] for a in rows]
    read = {r["announcement_id"] for r in DB.select("announcement_reads", {"user_id": g.profile["id"], "announcement_id": ids})}
    saved = {s["announcement_id"] for s in DB.select("saved_items", {"user_id": g.profile["id"], "announcement_id": ids})}
    authors = {p["id"]: p["full_name"] for p in DB.select("profiles", {"team_id": g.profile["team_id"], "role": "coach"})}
    return jsonify([{**a, "read": a["id"] in read, "saved": a["id"] in saved,
                     "author": authors.get(a.get("author_id"), "Team planner")} for a in rows])


@app.post("/api/trips/<trip_id>/announcements")
@coach_only
def post_announcement(trip_id):
    trip = my_trip(trip_id)
    b = body()
    title, text = str(b.get("title") or "").strip(), str(b.get("body") or "").strip()
    if not title:
        raise PlanError("Add a title.")
    return jsonify(DB.insert("announcements", {"trip_id": trip["id"], "team_id": trip["team_id"],
                                                "author_id": g.profile["id"], "title": title, "body": text})[0]), 201


def my_announcement(aid):
    rows = DB.select("announcements", {"id": aid, "team_id": g.profile["team_id"]})
    if not rows:
        raise PlanError("Announcement not found.")
    return rows[0]


@app.post("/api/announcements/<aid>/read")
@member
def mark_read(aid):
    my_announcement(aid)
    key = {"announcement_id": aid, "user_id": g.profile["id"]}
    if not DB.select("announcement_reads", key):
        DB.insert("announcement_reads", key)
    return jsonify({"ok": True})


@app.post("/api/announcements/<aid>/save")
@member
def toggle_save(aid):
    my_announcement(aid)
    key = {"announcement_id": aid, "user_id": g.profile["id"]}
    if DB.select("saved_items", key):
        DB.delete("saved_items", key)
        return jsonify({"saved": False})
    DB.insert("saved_items", key)
    return jsonify({"saved": True})


# -- NCAA ------------------------------------------------------------------
@app.get("/api/ncaa/sports")
@member
def ncaa_sports():
    return jsonify([{"slug": k, "name": v["name"], "weekly": bool(v.get("weekly")), "scoreboard": v.get("scoreboard", True),
                     "divisions": [{"slug": d, "name": DIVISION_NAMES.get(d, d.upper())} for d in v["divisions"]]}
                    for k, v in SPORTS.items()])


@app.get("/api/ncaa/schools")
@member
def ncaa_schools():
    return jsonify(ncaa.search_schools(request.args.get("q", ""), limit=25))


@app.get("/api/ncaa/rankings/<sport>/<division>")
@member
def ncaa_rankings(sport, division):
    poll = request.args.get("poll") or None
    if poll and not all(c.isalnum() or c == "-" for c in poll):
        raise PlanError("Invalid poll name.")
    return jsonify(ncaa.rankings(sport, division, poll))


@app.get("/api/ncaa/standings/<sport>/<division>")
@member
def ncaa_standings(sport, division):
    return jsonify(ncaa.standings(sport, division))


@app.get("/api/ncaa/scoreboard/<sport>/<division>")
@member
def ncaa_scoreboard(sport, division):
    day = request.args.get("date")
    try:
        day = date.fromisoformat(day) if day else None
        week = int(request.args["week"]) if request.args.get("week") else None
        season = int(request.args["season"]) if request.args.get("season") else None
    except ValueError:
        raise PlanError("Use date=YYYY-MM-DD, or week and season numbers.")
    if week and not season:
        season = date.today().year if date.today().month >= 8 else date.today().year - 1
    return jsonify(ncaa.scoreboard(sport, division, day, week, season))


@app.get("/api/ncaa/brackets/<sport>/<division>/<int:year>")
@member
def ncaa_brackets(sport, division, year):
    return jsonify(ncaa.brackets(sport, division, year))


@app.get("/api/ncaa/my-team")
@member
def ncaa_my_team():
    team = planner.team(g.profile["team_id"])
    if not (team.get("ncaa_seo") and team.get("sport") and team.get("division")):
        return jsonify({"linked": False})
    out = {"linked": True, "seo": team["ncaa_seo"], "name": team.get("ncaa_name"), "sport": team["sport"],
           "sport_name": SPORTS.get(team["sport"], {}).get("name"), "division": team["division"],
           "division_name": DIVISION_NAMES.get(team["division"], team["division"]),
           "logo": f"{NCAA_BASE}/logo/{team['ncaa_seo']}.svg"}
    out["summary"] = ncaa.team_summary(team["sport"], team["division"], team.get("ncaa_name") or "")
    games, warning = [], None
    if SPORTS.get(team["sport"], {}).get("scoreboard", True):
        try:
            games = ncaa.team_games(team["sport"], team["division"], team["ncaa_seo"])
        except NCAAError as exc:
            warning = str(exc)
    venues_by_school = {v.get("school_seo"): v for v in planner.data.venues.values() if v.get("school_seo")}
    for gm in games:
        if gm["is_away"] and g.profile["role"] == "coach":
            v = venues_by_school.get(gm["home"]["seo"])
            gm["plan"] = {"opponent": gm["home"]["short"], "match_at": game_start_local(gm), "ncaa_game_id": gm["id"],
                          "venue_id": v["id"] if v else None,
                          "venue_name": None if v else f"{gm['home']['full'] or gm['home']['short']} home venue"}
    out["games"], out["games_warning"] = games, warning
    return jsonify(out)


@app.patch("/api/team")
@coach_only
def link_team():
    b = body()
    sport, division = b.get("sport"), b.get("division")
    seo = str(b.get("ncaa_seo") or "").strip().lower()
    try:
        NCAAClient.check(sport or "", division or "")
    except NCAAError as exc:
        raise PlanError(str(exc))
    if not seo:
        raise PlanError("Pick your school from the list.")
    name = str(b.get("ncaa_name") or "").strip()
    try:                                    # confirm the school exists when NCAA data is reachable
        match = next((s for s in ncaa.schools() if s.get("slug") == seo), None)
        if match is None:
            raise PlanError("That school isn't in the NCAA list.")
        name = match.get("name") or name
    except NCAAError:
        pass
    team = DB.update("teams", {"ncaa_seo": seo, "ncaa_name": name or seo, "sport": sport, "division": division},
                     {"id": g.profile["team_id"]})[0]
    return jsonify(team)


# -- chatbot ----------------------------------------------------------------
@app.post("/api/chat")
@member
def chat():
    b = body()
    messages = b.get("messages") if isinstance(b.get("messages"), list) else []
    messages = [{"role": m.get("role"), "content": str(m.get("content") or "")[:2000]} for m in messages if isinstance(m, dict)]
    team = planner.team(g.profile["team_id"])
    assistant = ChatAssistant(DB, planner, ncaa, planner.aivana)
    return jsonify(assistant.handle(g.profile, team, messages, focus_trip_id=b.get("trip_id")))


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
