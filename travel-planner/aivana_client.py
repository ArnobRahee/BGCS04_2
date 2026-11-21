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
import os
from datetime import datetime

SYSTEM_LIMIT = 8000          # Aivana rejects longer system prompts


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

    def _post(self, prompt: str) -> str:
        """One-shot prompt that must return JSON; strips ```json fences models like to add."""
        raw = self.complete([{"role": "user", "content": prompt}]).strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0]
        return raw.strip()

    # -- recommendation ----------------------------------------------------
    def recommend(self, request: dict, packages: list[dict]) -> dict:
        """packages: [{"package_no", "total_cost", "detail"}]. Returns {package_no, reason, source, note?}."""
        if not packages:
            return None
        if self.configured:
            try:
                return self._recommend_ai(request, packages)
            except Exception as exc:                     # network error, bad JSON, unknown package...
                return {**self._recommend_local(request, packages), "note": f"Aivana call failed ({exc}), so this is the best-value ranking."}
        return {**self._recommend_local(request, packages), "note": "Aivana isn't connected, so this is the best-value ranking (cost, budget, diets, distance)."}

    def _recommend_ai(self, request, packages):
        brief = [{"package_no": p["package_no"], "total_cost": p["total_cost"],
                  "within_budget": p["detail"]["within_budget"],
                  "hotel": _pick(p["detail"]["hotel"], "name", "stars", "km_to_venue", "amenities"),
                  "restaurant": _pick(p["detail"]["restaurant"], "name", "cuisine", "missing_diets"),
                  "late_arrival": p["detail"]["late_arrival"]} for p in packages]
        prompt = (
            "You are helping a college sports coach choose an away-game travel package. "
            "All costs are already calculated; do not recompute them. Prefer packages that are within "
            "budget, cover every dietary need, keep the hotel close to the stadium and arrive on time. "
            'Reply only with JSON: {"package_no": <int>, "reason": "<one or two sentences for the coach>"}.\n\n'
            f"Trip: {json.dumps(request, default=str)}\nPackages: {json.dumps(brief)}"
        )
        parsed = json.loads(self._post(prompt))
        valid = {p["package_no"] for p in packages}
        if parsed.get("package_no") not in valid:
            raise ValueError("Aivana returned an unknown package_no")
        return {"package_no": parsed["package_no"], "reason": parsed.get("reason", ""), "source": "aivana"}

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
        best = min(packages, key=self.score)
        d = best["detail"]
        bits = []
        bits.append("within budget" if d["within_budget"] else "the closest option to the budget")
        if d.get("hotel"):
            bits.append(f"{d['hotel']['name']} is {d['hotel']['km_to_venue']} km from the stadium")
        rest = d.get("restaurant")
        if rest:
            if request.get("diet_tags") and not rest.get("missing_diets"):
                bits.append(f"{rest['name']} covers every dietary need ({', '.join(request['diet_tags'])})")
            elif rest.get("missing_diets"):
                bits.append(f"{rest['name']} has no main for: {', '.join(rest['missing_diets'])}")
        reason = f"Package {best['package_no']} at ${best['total_cost']:,.2f}: " + "; ".join(bits) + "."
        return {"package_no": best["package_no"], "reason": reason, "source": "ranking"}

    # -- announcement ------------------------------------------------------
    def draft_announcement(self, trip: dict, package: dict) -> dict:
        template = self._draft_local(trip, package)
        if not self.configured:
            return template
        try:
            prompt = ("Write a short, friendly announcement from a coach to their players about an away trip. "
                      "Use only these facts; do not mention money. Reply only with JSON "
                      '{"title": "...", "body": "..."}.\n' + json.dumps({"facts": template["body"]}))
            parsed = json.loads(self._post(prompt))
            return {"title": parsed["title"], "body": parsed["body"], "source": "aivana"}
        except Exception:
            return template

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
