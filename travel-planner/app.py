"""Flask entry point. Run locally: python app.py   |   Render: gunicorn app:app"""
import os

from dotenv import load_dotenv

load_dotenv()  # must run before the modules below read environment variables

from flask import Flask, jsonify, render_template, request  # noqa: E402

import db  # noqa: E402
from models import TravelRequest  # noqa: E402
from travel_planner import TravelPlanner

app = Flask(__name__)
planner = TravelPlanner()


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/plan")
def plan():
    body = request.get_json(silent=True) or {}
    try:
        req = TravelRequest(
            opponent=str(body["opponent"]).strip(),
            date=str(body["date"]),
            num_players=int(body["num_players"]),
            num_coaches=int(body["num_coaches"]),
            num_staff=int(body["num_staff"]),
            destination=str(body.get("destination", "")),
            distance_miles=float(body.get("distance_miles") or 100),
            nights=int(body.get("nights") or 1),
            earliest_departure=body.get("earliest_departure") or None,
        )
    except (KeyError, ValueError, TypeError) as exc:
        return jsonify({"error": f"Invalid trip details: {exc}"}), 400
    if req.total_travelers() <= 0:
        return jsonify({"error": "Add at least one traveler."}), 400
    result = planner.plan(req)
    result["trip_id"] = db.save_plan(result)  # None if Supabase isn't configured
    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
