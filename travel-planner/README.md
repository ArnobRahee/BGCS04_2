# Away Game Travel Planner

A coach enters an away game. The app prices every hotel + restaurant + bus package near the
venue, Aivana recommends one, the coach approves the booking, and players see where they're
staying, when the bus leaves, their room, and the dishes on the menu that fit their diet.

## Run it in 1 minute (demo mode, no keys)

```bash
cd travel-planner
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py                                     # http://localhost:5000
```

With no `SUPABASE_SERVICE_KEY`, the app runs in **demo mode**: in-memory data, the bundled
catalog, and **Try as coach / Try as player** buttons. Data resets when the server restarts.

## Run it with Supabase (real accounts)

1. `cp .env.example .env` and paste the **secret** key from Supabase → Project Settings → API Keys.
2. Supabase → Authentication → Sign In / Providers → Email: for the hackathon demo, turn off
   **Confirm email** so new accounts can sign in right away.
3. `python app.py`. Coaches create an account, name the team and get a join code; players sign up
   with that code.

## NCAA data

Coaches link the team to its NCAA school, sport and division (team card → **Link your NCAA team**).
The app then shows the team's record, conference, poll rank and upcoming games; away games have a
**Plan trip** button that fills in the opponent, kickoff time and (for catalog stadiums) the venue.
The **NCAA** tab explores rankings, standings, scores and schools for every sport in D1, D2 and D3
(FBS/FCS for football).

- Source: the free [ncaa-api](https://github.com/henrygd/ncaa-api), which mirrors ncaa.com. There is no
  official public NCAA API. It has no team rosters, so the app's players are the people who join your team.
- The public server allows 3 requests/second; `ncaa_client.py` throttles to 2.5/s and caches every
  response in memory and in the Supabase `ncaa_cache` table. For heavy use run your own copy
  (`docker run --rm -p 3000:3000 henrygd/ncaa-api`) and set `NCAA_API_URL`.
- A team's schedule is built from daily scoreboards (next 14 days; football: this week), so the first
  load for a new sport/division takes a few seconds; after that it's cached.

## Ask Aivana (chatbot)

The **Ask Aivana** button opens a help chat for coaches and players. It answers from the team's own data
(packages, the AI pick, record, next games, bus time, hotel, room, diet-friendly dishes) and can:
- **prepare a booking** for a coach ("book package 2"): bookings are created as *pending*; nothing is
  reserved until the coach clicks **Approve**. The bot can never approve or pay.
- **record an RSVP** for a player ("I'm going").

Players' chat context never includes prices or budget. Chat history is kept in the browser.
Without Aivana keys a rule-based fallback answers the common questions.

**Connecting Aivana:** set `AIVANA_API_URL`, `AIVANA_API_KEY` (and `AIVANA_MODEL` if needed) in `.env` /
Render. If Aivana's API isn't OpenAI-style, set `AIVANA_API_STYLE=prompt` or adjust
`AivanaClient.complete()` (the only place that knows the request format). Never commit the key.

## Google sign-in (one-time setup)

1. Google Cloud Console → APIs & Services → Credentials → **Create OAuth client ID** (Web application).
   Authorized redirect URI: `https://mucqewiinntpqhdhektw.supabase.co/auth/v1/callback`
2. Supabase → Authentication → Sign In / Providers → **Google**: enable, paste the client ID and secret.
3. Supabase → Authentication → URL Configuration: Site URL = your Render URL; add
   `http://localhost:5000` and the Render URL to **Redirect URLs**.

Users stay signed in on their device (Supabase keeps the session in the browser) until they sign out.

## How it works

| File | UML class | What it does |
|---|---|---|
| `models.py` | `TravelRequest`, `TravelPackage` | Trip details in; priced package out |
| `travel_data.py` | `TravelData` | Venues, hotels, restaurants, menus, buses (Supabase, or `data/catalog.json`) |
| `geo.py` | `DistanceService` | Haversine distance, road km, drive time |
| `cost_calc.py` | `CostCalc` | Rooms, buses, costs, departure time, diet coverage; all the math |
| `aivana_client.py` | `AivanaClient` | Picks a package and drafts the announcement (local fallback without a key) |
| `booking_service.py` | `BookingService`, `MockBookingProvider` | Pending bookings → coach approves → confirmation codes |
| `travel_planner.py` | `TravelPlanner` | Coordinates everything; player vs coach views |
| `ncaa_client.py` | | NCAA schools, rankings, standings, scores, brackets; throttled + cached |
| `chat_assistant.py` | | "Ask Aivana" chatbot: answers from team data, prepares bookings, RSVPs |
| `app.py`, `auth.py` | | Flask API; every route checks the user's role and team |
| `templates/`, `static/` | | HTML/CSS/JS front end; sign-in with Supabase Auth |

**Trip flow:** Planning (packages + Aivana pick) → coach chooses → **Awaiting approval** (nothing
reserved yet) → coach approves → **Booked** → assign rooms/buses → publish → players see the plan.
If a hotel sells out, approval fails, nothing stays half-booked, and the trip returns to Planning.

**What players never see:** budget, prices or booking amounts. Hotel and restaurant details
appear only after the coach publishes a booked trip.

## Database (Supabase)

- Accounts, teams, trips, packages, bookings, announcements: migration `accounts_teams_trips_bookings_announcements`.
- NCAA link columns on teams/trips/venues and the `ncaa_cache` table: `supabase/migrations/20261121050635_ncaa_link_and_cache.sql`.
- Catalog (venues, hotels, restaurants, menu_items, bus_options):
  `supabase/migrations/20261121025814_catalog_venues_hotels_restaurants_menus_buses.sql`, loaded by
  `supabase/seed/catalog_seed.sql` (4 away venues, 48 hotels, 60 restaurants, 416 menu items, 4 bus companies).
- The Flask server uses the secret key and does all reads/writes; the browser only signs in.

**Catalog data:** places in the seed are mock. To use real hotels and restaurants from
OpenStreetMap, open `data/build_dataset.ipynb` from the `data/` folder, put your email in
`USER_AGENT`, run all cells, then:

```bash
python scripts/csv_to_seed_sql.py data/catalog_csv > supabase/seed/catalog_seed_osm.sql
```

and paste that file into the Supabase SQL editor. Prices, room counts, menus and bus companies
stay mock (no free source has them). Show "© OpenStreetMap contributors" if you use real places.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q tests
```

The tests run the whole coach/player flow on the in-memory database and check that every row the
app writes matches the Supabase schema.

## Deploy on Render

`render.yaml` in the repo root is a Blueprint: Render → New → Blueprint → this repo, then paste
`SUPABASE_SERVICE_KEY` (and Aivana keys if you have them) in the dashboard. Free instances sleep
after 15 minutes idle; open the site a minute before you demo.

## Still to do

- **Aivana:** confirm the request format (see "Connecting Aivana" above).
- **Row level security policies** for direct browser access (today only the server touches data).
