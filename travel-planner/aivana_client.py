"""AivanaClient: wrapper around the Aivana API.

Configure with environment variables (never in code):
  AIVANA_API_URL    full endpoint URL
  AIVANA_API_KEY    sent as "Authorization: Bearer <key>"
  AIVANA_API_STYLE  "openai" (default): body {"model", "messages"} -> choices[0].message.content
                    "prompt": body {"prompt"} -> "response" (or "text"/"output") field
  AIVANA_MODEL      model name for the "openai" style (optional)
If Aivana's API looks different, change only complete(). Aivana is the only AI in the app.
When it isn't connected (or a call fails) nothing pretends to be AI:
  - recommend(): a plain best-value ranking (budget, diet coverage, distance, cost), labelled as such
  - draft_announcement(): a fill-in-the-blanks template from the booked package
Aivana only ranks and writes text. Every number comes from CostCalc.
"""
import json
import os
from datetime import datetime

import requests


class AivanaClient:
    def __init__(self):
        self.api_key = os.environ.get("AIVANA_API_KEY", "").strip()
        self.api_url = os.environ.get("AIVANA_API_URL", "").strip()
        self.style = os.environ.get("AIVANA_API_STYLE", "openai").strip().lower()
        self.model = os.environ.get("AIVANA_MODEL", "").strip()

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_url)

    def complete(self, messages: list[dict]) -> str:
        """Send a conversation ([{role, content}]) and return the model's text."""
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self.style == "prompt":
            text = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages) + "\n\nASSISTANT:"
            body = {"prompt": text}
        else:
            body = {"messages": messages, "temperature": 0.3}
            if self.model:
                body["model"] = self.model
        resp = requests.post(self.api_url, headers=headers, json=body, timeout=45)
        if resp.status_code >= 400:
            raise RuntimeError(f"Aivana returned {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if self.style == "prompt":
            return str(data.get("response") or data.get("text") or data.get("output") or "")
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Unexpected Aivana response shape; set AIVANA_API_STYLE or adjust complete()") from exc

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
