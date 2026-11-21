# Away Game Travel Planner

Enter an away game's details. The app calculates bus, hotel and meal costs, builds every
hotel + restaurant + bus package, and asks Aivana to recommend the best one.

## Run locally

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env    # then fill in keys
python app.py           # http://localhost:5000
```

Without `AIVANA_API_KEY` / `AIVANA_API_URL` the app still works and falls back to the cheapest package.

## Structure

| File | Role (from the UML) |
|---|---|
| `models.py` | `TravelRequest`, `TravelPackage` |
| `travel_data.py` | `TravelData` (hotels, restaurants, bus options) |
| `cost_calc.py` | `CostCalc` (`calc_buses`, `calc_rooms`, `generate_packages`, `calc_total`) |
| `aivana_client.py` | `AivanaClient` (fill in the TODOs from Aivana's docs) |
| `travel_planner.py` | `TravelPlanner` (coordinator) |
| `app.py` | Flask routes: `GET /`, `POST /api/plan` |

## Deploy on Render

Build command: `pip install -r requirements.txt`  
Start command: `gunicorn app:app`  
Add the env vars from `.env.example` in Render's dashboard.

## Assumptions to confirm

- 2 people per room, 2 meals per day, bus cost = buses x (base fee + per-mile x round-trip miles).
- Departure constraint filters out bus options whose departure time is before the requested time.
- Sample hotels/restaurants/buses are hardcoded in `travel_data.py`.
