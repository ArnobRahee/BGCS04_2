"""Which upcoming games can be planned. Pure code: Aivana never decides eligibility."""
import time


def eligible_games(games: list, planned: dict, prefill, now: float | None = None) -> list:
    """Future away games with no saved trip and a known opponent and date, soonest first.
    `missing` lists what the planner still has to fill in (never guessed)."""
    now = now or time.time()
    out = []
    for gm in games:
        if not gm.get("is_away") or gm.get("id") in planned:
            continue
        if not gm.get("start_epoch") or gm["start_epoch"] <= now or gm.get("state") in ("live", "final"):
            continue
        plan = prefill(gm)
        if not plan.get("opponent") or not plan.get("match_at"):
            continue
        missing = []
        if not plan.get("venue_confirmed"):
            missing.append("venue")
        if str(gm.get("start_time") or "").upper() in ("", "TBA", "TBD"):
            missing.append("game time")
        out.append({"game_id": gm["id"], "opponent": plan["opponent"], "opponent_full": gm["home"].get("full"),
                    "start_epoch": gm["start_epoch"], "match_at": plan["match_at"],
                    "start_date": gm.get("start_date"), "start_time": gm.get("start_time"),
                    "network": gm.get("network"), "venue_name": plan.get("venue_label"), "venue_id": plan.get("venue_id"),
                    "missing": missing, "plan": plan})
    return sorted(out, key=lambda g: g["start_epoch"])
