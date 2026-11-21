"""AivanaClient: talks to Aivana (https://developers.aivana.ai) through its official Python SDK
(`pip install aivana`, Python 3.10+). The SDK knows Aivana's address and request format.

Configure with environment variables (never in code):
  AIVANA_API_KEY    your key, "ai_live_..." (required)
  AIVANA_API_BASE   optional: only to point the SDK at a different Aivana deployment
  AIVANA_EFFORT     optional: auto (default) | low | medium | high
  AIVANA_WEB_SEARCH optional: on (default) lets the chat panel search the web for general facts
Aivana is the only AI in the app. When it isn't connected (or a call fails) nothing pretends to be AI:
  - recommend(): a plain best-value ranking (budget, diet coverage, distance, cost), labelled as such
  - draft_announcement(): a fill-in-the-blanks template from the booked package
Aivana only ranks and writes text. Every number comes from CostCalc.
"""
import json
import logging
import os
import re
from datetime import datetime

log = logging.getLogger("aivana")

SYSTEM_LIMIT = 8000          # Aivana rejects longer system prompts
DIET_LABELS = {"gluten_free": "gluten-free", "nut_free": "nut-free", "dairy_free": "dairy-free"}


def diet_label(tag: str) -> str:
    return DIET_LABELS.get(tag, str(tag).replace("_", " "))


def friendly_error(exc: Exception) -> str:
    """Turn an Aivana/SDK failure into a sentence a coach can act on. Never includes the key."""
    text = f"{type(exc).__name__}: {exc}"
    low = text.lower()
    if "isn't installed" in low or "no module named" in low:
        return "Aivana isn't set up on this server: its Python package is missing"
    if "autherror" in low or "401" in low or "invalid api key" in low or "unauthorized" in low:
        return "Aivana rejected the API key; check AIVANA_API_KEY"
    if "402" in low or "insufficient" in low or "balance" in low or "quota" in low or "spend limit" in low:
        return "the Aivana account is out of credit or over its spend limit"
    if "ratelimit" in low or "429" in low or "rate limit" in low:
        return "Aivana hit its rate limit; try again in a minute"
    if "timeout" in low or "timed out" in low:
        return "Aivana took too long to answer"
    if ("connection" in low or "network" in low or "resolve" in low or "unreachable" in low
            or "proxy" in low):
        return "the server couldn't reach Aivana"
    if ("structured_output_failed" in low or "json" in low or "package_no" in low or "package " in low
            or "structured" in low or "expecting value" in low):
        return "Aivana's answer didn't match the priced packages"
    if "upstream" in low or "500" in low or "502" in low or "503" in low:
        return "Aivana had a temporary problem"
    return "Aivana couldn't answer"


class AivanaClient:
    def __init__(self, sdk=None):
        self.api_key = os.environ.get("AIVANA_API_KEY", "").strip()
        self.api_base = (os.environ.get("AIVANA_API_BASE") or os.environ.get("AIVANA_API_URL") or "").strip()
        self.effort = os.environ.get("AIVANA_EFFORT", "").strip().lower()
        # Aivana's own web search, used by the chat panel for facts outside the team's data.
        self.web_search = os.environ.get("AIVANA_WEB_SEARCH", "on").strip().lower() not in ("0", "off", "false", "no")
        self._sdk = sdk

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _client(self):
        sdk = self._sdk
        if sdk is None:
            try:
                import aivana as sdk                      # official SDK
            except ImportError as exc:
                raise RuntimeError("the 'aivana' package isn't installed (pip install -r requirements.txt)") from exc
        sdk.api_key = self.api_key
        if self.api_base:
            sdk.set_api_base(self.api_base)
        return sdk

    def complete(self, messages: list[dict], web_search: bool = False) -> str:
        """Send a conversation ([{role, content}], roles system/user/assistant) and return Aivana's answer."""
        sdk = self._client()
        system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system").strip()
        turns = [{"role": m["role"], "content": m["content"]} for m in messages if m.get("role") in ("user", "assistant")][-40:]
        options = {"messages": turns}
        if system:
            options["system"] = system[:SYSTEM_LIMIT]
        if web_search:
            options["web_search"] = True
        if self.effort in ("low", "medium", "high"):
            options["effort"] = self.effort
        try:
            res = sdk.generate(**options)
        except Exception as exc:                          # AuthError, RateLimitError, UpstreamError, network...
            raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc
        answer = getattr(res, "answer", None)
        if answer is None and isinstance(res, dict):
            answer = res.get("answer")
        if not answer:
            raise RuntimeError("Aivana returned an empty answer")
        return str(answer)

    # -- recommendation ----------------------------------------------------
    def recommend(self, request: dict, packages: list[dict]) -> dict:
        """packages: [{"package_no", "total_cost", "detail"}]. Returns {package_no, reason, source, note?}."""
        if not packages:
            return None
        if self.configured:
            try:
                return self._recommend_ai(request, packages)
            except Exception as exc:                     # network error, bad JSON, unknown package...
                log.warning("Aivana recommendation failed: %s: %s", type(exc).__name__, exc)
                return {**self._recommend_local(request, packages),
                        "note": f"Aivana's pick isn't available: {friendly_error(exc)}. This is the app's own best-value ranking.",
                        "aivana_error": friendly_error(exc)}
        return {**self._recommend_local(request, packages), "note": "Aivana isn't connected, so this is the app's own best-value ranking (cost, budget, diets, distance)."}

    def structured(self, prompt: str, schema: dict, system: str = "") -> dict:
        """One-shot call that must return JSON matching `schema` (Aivana's response_format).
        Returns the parsed object; raises RuntimeError/ValueError when Aivana can't comply."""
        sdk = self._client()
        options = {"messages": [{"role": "user", "content": prompt}],
                   "response_format": {"type": "json_schema", "schema": schema}}
        if system:
            options["system"] = system[:SYSTEM_LIMIT]
        if self.effort in ("low", "medium", "high"):
            options["effort"] = self.effort
        try:
            res = sdk.generate(**options)
        except Exception as exc:
            raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc
        out = getattr(res, "structured", None)
        if out is None and isinstance(res, dict):
            out = res.get("structured")
        if out is None:                                   # older SDKs: JSON in the answer text
            answer = getattr(res, "answer", None) or (res.get("answer") if isinstance(res, dict) else None)
            if not answer:
                raise ValueError("Aivana returned no structured output")
            out = json.loads(_strip_fences(str(answer)))
        if not isinstance(out, dict):
            raise ValueError("Aivana's structured output wasn't an object")
        return out

    def _recommend_ai(self, request, packages):
        labels = {"lowest_cost": "lowest cost", "closest": "closest to the venue", "best_value": "best value"}
        brief = [{"package_no": p["package_no"], "total_cost": p["total_cost"],
                  "option": labels.get(p["detail"].get("highlight")),
                  "within_budget": p["detail"]["within_budget"],
                  "hotel": _pick(p["detail"]["hotel"], "name", "stars", "rating", "km_to_venue", "amenities"),
                  "restaurant": _pick(p["detail"]["restaurant"], "name", "cuisine", "km_to_venue", "missing_diets", "fits_group"),
                  "late_arrival": p["detail"]["late_arrival"]} for p in packages]
        valid = sorted({int(p["package_no"]) for p in packages})
        schema = {
            "type": "object",
            "properties": {
                "package_no": {"type": "integer", "enum": valid,
                               "description": "One package_no from the list. Never invent one."},
                "reason": {"type": "string", "description": "One or two sentences for the coach."},
            },
            "required": ["package_no", "reason"],
            "additionalProperties": False,
        }
        system = ("You help a college sports team planner choose an away-game travel package. "
                  "Choose only from the packages given, by package_no. Every cost is already calculated by "
                  "the app: never recompute, add up or change any price, and don't state a number that isn't in the data. "
                  "Prefer packages within budget that cover every dietary need, keep the hotel close and arrive on time. "
                  "Three packages are marked as options (lowest cost, closest, best value); you may pick any package.")
        prompt = f"Trip: {json.dumps(request, default=str)}\nPackages: {json.dumps(brief)}"
        try:
            parsed = self.structured(prompt, schema, system)
        except RuntimeError as exc:
            if "InvalidRequest" not in str(exc):
                raise
            # A deployment that rejects `enum` in schemas: retry with a plain integer field.
            plain = {**schema, "properties": {**schema["properties"], "package_no": {"type": "integer"}}}
            parsed = self.structured(prompt, plain, system)
        try:
            package_no = int(str(parsed.get("package_no")).strip())
        except (TypeError, ValueError):
            raise ValueError(f"Aivana returned an invalid package_no ({parsed.get('package_no')!r})")
        if package_no not in valid:
            raise ValueError(f"Aivana returned package {package_no}, which isn't one of the {len(valid)} priced packages")
        reason = str(parsed.get("reason") or "").strip()[:600]
        return {"package_no": package_no, "reason": reason, "source": "aivana"}

    @staticmethod
    def score(p: dict) -> float:
        d = p["detail"]
        s = float(p["total_cost"])
        s += 0 if d["within_budget"] else 5000
        s += 1500 * len((d.get("restaurant") or {}).get("missing_diets") or [])
        s += 1000 if d.get("late_arrival") else 0
        if d.get("hotel"):
            s += 40 * float(d["hotel"]["km_to_venue"]) - 60 * int(d["hotel"].get("stars") or 0)
        return s

    def _recommend_local(self, request, packages):
        # CostCalc marks the best-value package; older rows without the mark use score().
        marked = [p for p in packages if p["detail"].get("highlight") == "best_value"]
        best = marked[0] if marked else min(packages, key=self.score)
        d = best["detail"]
        bits = []
        bits.append("within budget" if d["within_budget"] else "the closest option to the budget")
        if d.get("hotel"):
            bits.append(f"{d['hotel']['name']} is {d['hotel']['km_to_venue']} km from the stadium")
        rest = d.get("restaurant")
        if rest:
            if request.get("diet_tags") and not rest.get("missing_diets"):
                bits.append(f"{rest['name']} covers every dietary need ({', '.join(map(diet_label, request['diet_tags']))})")
            elif rest.get("missing_diets"):
                bits.append(f"{rest['name']} has no main for: {', '.join(map(diet_label, rest['missing_diets']))}")
            if rest.get("fits_group") is False:
                bits.append(f"{rest['name']} can't seat the whole group")
        reason = f"Package {best['package_no']} at ${best['total_cost']:,.2f}: " + "; ".join(bits) + "."
        return {"package_no": best["package_no"], "reason": reason, "source": "ranking"}

    # -- which game to plan next -------------------------------------------
    def suggest_game(self, team: dict, games: list) -> dict:
        """games: eligible upcoming away games (already filtered by code). Aivana picks one id and
        explains; it may not invent dates, venues or costs. Cached per team + game list."""
        soonest = games[0]
        fallback = {"game_id": soonest["game_id"], "source": "schedule",
                    "reason": f"Your next away game is at {soonest['opponent']}"
                              + (f" on {soonest['start_date']}" if soonest.get("start_date") else "") + "."}
        if not self.configured:
            return {**fallback, "note": "Aivana isn't connected, so this is simply the soonest away game."}
        key = (team.get("team_id"), tuple(g["game_id"] for g in games))
        cache = self.__dict__.setdefault("_suggest_cache", {})
        if key in cache:
            return cache[key]
        brief = [{"game_id": g["game_id"], "opponent": g["opponent_full"] or g["opponent"], "date": g["start_date"],
                  "time": g["start_time"], "venue_known": "venue" not in g["missing"], "missing": g["missing"]}
                 for g in games[:10]]
        valid = [g["game_id"] for g in games[:10]]
        schema = {"type": "object", "additionalProperties": False, "required": ["game_id", "reason"],
                  "properties": {"game_id": {"type": "string", "enum": valid},
                                 "reason": {"type": "string", "description": "One or two sentences for the team planner."}}}
        system = ("You help a college team's travel planner decide which upcoming away game to plan next. "
                  "Choose one game_id from the list. Use only the facts given: never invent dates, times, venues, "
                  "distances or costs. Usually the soonest game matters most; mention missing details the planner must fill in. "
                  "End by asking if they'd like to start planning transportation, lodging and meals.")
        prompt = (f"Team: {team.get('display_name')} ({team.get('sport_name')}). Today: {datetime.now():%Y-%m-%d}.\n"
                  f"Eligible away games: {json.dumps(brief)}")
        try:
            out = self.structured(prompt, schema, system)
            gid = str(out.get("game_id") or "").strip()
            if gid not in valid:
                raise ValueError(f"Aivana returned game {gid!r}, which isn't one of the listed games")
            result = {"game_id": gid, "reason": _plain(out.get("reason"))[:500], "source": "aivana"}
        except Exception as exc:
            log.warning("Aivana game suggestion failed: %s: %s", type(exc).__name__, exc)
            return {**fallback, "note": f"Aivana's suggestion isn't available: {friendly_error(exc)}. Showing the soonest away game."}
        cache[key] = result
        return result

    # -- trip request parsing ---------------------------------------------
    def parse_trip(self, text: str, today) -> dict:
        """Free text -> trip form fields with Aivana's structured output. Only what's stated; the
        form and the server still validate everything before a trip is saved."""
        if not self.configured:
            raise RuntimeError("Aivana isn't connected, so describe-the-trip is off. Fill in the form instead.")
        num = {"type": ["integer", "null"]}
        txt = {"type": ["string", "null"]}
        schema = {"type": "object", "additionalProperties": False,
                  "required": ["opponent", "match_date", "match_time", "venue_name", "num_players", "num_coaches",
                               "num_staff", "nights", "meals", "budget", "depart_after_date", "depart_after_time"],
                  "properties": {"opponent": txt, "match_date": {**txt, "description": "YYYY-MM-DD"},
                                 "match_time": {**txt, "description": "HH:MM, 24-hour"}, "venue_name": txt,
                                 "num_players": num, "num_coaches": num, "num_staff": num, "nights": num, "meals": num,
                                 "budget": {"type": ["number", "null"]},
                                 "depart_after_date": {**txt, "description": "YYYY-MM-DD"},
                                 "depart_after_time": {**txt, "description": "HH:MM, 24-hour"}}}
        system = ("Extract an away-game trip request into the given fields. Use null for anything the text doesn't "
                  f"state; never guess. Today is {today:%A %Y-%m-%d}; resolve relative dates like 'next Friday' from it. "
                  "Do not calculate costs.")
        try:
            out = self.structured(text, schema, system)
        except Exception as exc:
            log.warning("Aivana trip parsing failed: %s: %s", type(exc).__name__, exc)
            raise RuntimeError(f"Aivana couldn't read that: {friendly_error(exc)}. Fill in the form instead.") from exc
        return _clean_trip_fields(out)

    # -- announcement ------------------------------------------------------
    def draft_announcement(self, trip: dict, package: dict) -> dict:
        template = self._draft_local(trip, package)
        if not self.configured:
            return {**template, "note": "Aivana isn't connected, so this is a fill-in template. Edit it before sending."}
        schema = {"type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
                  "required": ["title", "body"], "additionalProperties": False}
        system = ("Write a short, friendly announcement from a coach to their players about an away trip. "
                  "Use only the facts given, keep every date and time exactly as written, and don't mention money. "
                  "Plain text only: no markdown, no asterisks, no headings.")
        try:
            parsed = self.structured(json.dumps({"facts": template["body"]}), schema, system)
            title, body = _plain(parsed["title"]), _plain(parsed["body"])
            if not title or not body:
                raise ValueError("empty draft")
            return {"title": title[:150], "body": body[:2000], "source": "aivana"}
        except Exception as exc:
            log.warning("Aivana draft failed: %s: %s", type(exc).__name__, exc)
            return {**template, "note": f"Aivana couldn't write the draft: {friendly_error(exc)}. This is a fill-in template; edit it before sending."}

    @staticmethod
    def _draft_local(trip, package):
        d = package["detail"]
        depart = _nice_time(d["suggested_departure"])
        lines = [f"We're heading to {trip['venue_name']} to play {trip['opponent']} ({_nice_time(trip['match_at'])})."]
        lines.append(f"Bus: {d['bus']['company']} leaves {depart}. Be there 15 minutes early.")
        if d.get("hotel"):
            h = d["hotel"]
            lines.append(f"Hotel: {h['name']}{', ' + h['address'] if h.get('address') else ''} "
                         f"({h['km_to_venue']} km from the stadium), {trip['nights']} night(s). Room assignments are in the app.")
        if d.get("restaurant"):
            r = d["restaurant"]
            lines.append(f"Team meal: {r['name']} ({r['cuisine']}). Check the menu in the app and pre-pick a dish that fits your diet.")
        lines.append("Questions? Reply to your coach.")
        return {"title": f"Away trip: {trip['opponent']}", "body": "\n".join(lines), "source": "template"}


def _pick(d, *keys):
    return {k: d.get(k) for k in keys} if d else None


def _nice_time(value) -> str:
    try:
        dt = datetime.fromisoformat(str(value)[:16])
        hour = dt.hour % 12 or 12                       # no %-I: it breaks on Windows
        return f"{dt.strftime('%a %b')} {dt.day}, {hour}:{dt.minute:02d} {'PM' if dt.hour >= 12 else 'AM'}"
    except (ValueError, TypeError):
        return str(value)


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        raw = raw.rsplit("```", 1)[0]
    return raw.strip()


def _plain(text) -> str:
    """Drop markdown that models add (**bold**, # headings) so announcements read as plain text."""
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), str(text or ""))
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    return text.replace("**", "").strip()


def _clean_trip_fields(out: dict) -> dict:
    """Keep only well-formed values; the wizard shows what was filled for the planner to check."""
    fields = {}
    if isinstance(out.get("opponent"), str) and out["opponent"].strip():
        fields["opponent"] = out["opponent"].strip()[:80]
    if isinstance(out.get("venue_name"), str) and out["venue_name"].strip():
        fields["venue_name"] = out["venue_name"].strip()[:120]

    def when(d, t, default_time):
        try:
            day = datetime.strptime(str(d), "%Y-%m-%d")
        except (TypeError, ValueError):
            return None
        try:
            hm = datetime.strptime(str(t), "%H:%M") if t else datetime.strptime(default_time, "%H:%M")
        except ValueError:
            hm = datetime.strptime(default_time, "%H:%M")
        return day.replace(hour=hm.hour, minute=hm.minute).strftime("%Y-%m-%dT%H:%M")

    if out.get("match_date"):
        fields["match_at"] = when(out["match_date"], out.get("match_time"), "12:00")
        if not out.get("match_time"):
            fields["match_time_missing"] = True
    if out.get("depart_after_date"):
        fields["depart_after"] = when(out["depart_after_date"], out.get("depart_after_time"), "08:00")
    for k in ("num_players", "num_coaches", "num_staff", "nights", "meals"):
        v = out.get(k)
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            fields[k] = v
    if isinstance(out.get("budget"), (int, float)) and not isinstance(out.get("budget"), bool) and out["budget"] >= 0:
        fields["budget"] = float(out["budget"])
    return {"fields": {k: v for k, v in fields.items() if v is not None}, "source": "aivana"}
