"""AivanaClient: wrapper around the Aivana API.

IMPORTANT: I don't know Aivana's real endpoint or payload format. Fill in
the two TODO spots below from their docs. Until then (or if the call fails),
a simple local fallback recommends the cheapest package so the demo still works.
"""
import json
import os


class AivanaClient:
    def __init__(self):
        self.api_key = os.environ.get("AIVANA_API_KEY")
        self.api_url = os.environ.get("AIVANA_API_URL")  # TODO: set from Aivana docs

    def recommend(self, request: dict, packages: list[dict]) -> dict:
        if self.api_key and self.api_url:
            try:
                return self._call_aivana(request, packages)
            except Exception as exc:  # network error, bad response, etc.
                result = self._fallback(packages)
                result["note"] = f"Aivana call failed ({exc}); showing local fallback."
                return result
        result = self._fallback(packages)
        result["note"] = "No Aivana key configured; showing cheapest-package fallback."
        return result

    def _call_aivana(self, request: dict, packages: list[dict]) -> dict:
        import requests

        prompt = (
            "You are helping a sports team pick an away-game travel package. "
            "Costs are already calculated; do not recompute them. "
            "Pick the best package considering cost, hotel quality and the "
            "departure constraint. Reply as JSON: "
            '{"package_id": <int>, "reason": "<one or two sentences>"}.\n\n'
            f"Trip: {json.dumps(request)}\nPackages: {json.dumps(packages)}"
        )
        # TODO: adjust headers / body / response parsing to Aivana's actual API.
        resp = requests.post(
            self.api_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"prompt": prompt},
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "")  # TODO: real response field
        parsed = json.loads(text)
        valid_ids = {p["package_id"] for p in packages}
        if parsed.get("package_id") not in valid_ids:
            raise ValueError("Aivana returned an unknown package_id")
        return {"package_id": parsed["package_id"], "reason": parsed.get("reason", ""), "source": "aivana"}

    def _fallback(self, packages: list[dict]) -> dict:
        best = min(packages, key=lambda p: p["total_cost"])
        return {"package_id": best["package_id"],
                "reason": "Lowest total cost that meets the departure constraint.",
                "source": "fallback"}
