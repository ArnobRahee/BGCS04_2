# Rally · Away Game Travel Planner

A team planner enters an away game. The app prices every hotel + team-meal restaurant + bus
package near the venue and highlights **three different options: lowest cost, closest and best
value**. Aivana (the team's AI, through its official SDK) recommends one of the priced packages
and answers questions; the **main planner approves** before anything is booked (bookings are
**simulated**). Players then see where they're staying, when the bus leaves, their room, and the
dishes that fit their diet, never the prices.

**Live demo:** https://bgcs04-team-travel.onrender.com (free instance: give it a minute to wake up).

**Who does what**

| Code (deterministic) | Aivana (AI) | People |
|---|---|---|
| Every number: rooms, buses, hotel/meal/bus costs, totals, budget checks, distances, departure times | Picks one package **by number** from the priced list (schema-limited to real numbers) and explains why | The main planner approves; the AI can only prepare bookings |
| Chooses the three highlighted options and the best-value score | Drafts the players' announcement from the booked facts | Players RSVP; their counts re-price the trip |
| Re-prices "what if" changes Aivana proposes in chat | Turns "what if we stay 2 nights?" into a proposed change | The planner clicks **Apply** to keep it |

## Run it in 1 minute (demo mode, no keys)

```bash
cd travel-planner
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py                                     # http://localhost:5000
```

With no `SUPABASE_SERVICE_KEY`, the app runs in **demo mode**: in-memory data, the bundled
catalog, and **Demo mode: Team Planner / Player** buttons on the sign-in screen. Data resets when
the server restarts (on Render's free plan that's after 15 idle minutes).

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

## Aivana (your AI)

Aivana is the AI API you were given; this app doesn't contain its own AI. Everything "smart" goes to
your Aivana endpoint:
- **Ask Aivana panel**: questions about trips, the team's record and games, bus time, hotel, room,
  diet-friendly dishes. Aivana may also ask the app to **prepare a booking** (main planner only;
  bookings stay *pending* until a person clicks **Approve**) or **record an RSVP** for a player.
- **Package recommendation** and **announcement drafts**.

When Aivana isn't connected, the panel says so and answers nothing. The package list then shows a
plain **best-value ranking** (budget, diet coverage, distance, cost) and drafts use a fill-in template;
both are labelled as not coming from Aivana. Players' context never includes prices or budget.

**Connecting Aivana:** the app uses Aivana's official Python SDK (`aivana` in `requirements.txt`),
which already knows Aivana's address and request format. Put only your key in `travel-planner/.env`:

```
AIVANA_API_KEY=ai_live_...
```

That file is in `.gitignore`: never commit it. Restart the app; `/api/health` then shows
`"aivana": "connected"`. Add the key to Render only if you want the live site to use Aivana.
Get keys and test prompts at https://developers.aivana.ai (Playground).

## Main planner

The coach who registers the team is its **main planner** (`teams.owner_id`). Only the main planner can
request, approve or cancel hotel, bus and restaurant bookings, in the app and through Aivana. Other
coaches see trips and packages read-only. The main planner can hand the role to another coach on the
Team page (`POST /api/team/planner`).

## Hotels from Google Maps

With `GOOGLE_MAPS_API_KEY` set, hotels come from **Google Places**: real hotels within 20 km of the
venue, with their Google photo (and the photographer credit Google requires), rating and a Google
Maps link. With `GOOGLE_MAPS_BROWSER_KEY` set, the trip page shows a real **Google map** with the
stadium, the hotels and the team-meal restaurant. Without the keys the app uses the catalog hotels
and a drawn map.

Google doesn't publish room rates, so a Google hotel's nightly price is an **estimate** from its
price level and is labelled that way. Bookings are still simulated (MockBookingProvider): real
reservations need a booking partner API.

Setup (Google Cloud Console, one project, billing enabled; the free monthly usage covers a hackathon):
1. APIs & Services → Library: enable **Places API (New)** and **Maps JavaScript API**.
2. Credentials → Create API key → name it "server". Restrict it to *Places API (New)*. Put it in
   `.env` / Render as `GOOGLE_MAPS_API_KEY`. It never reaches the browser: photos go through
   `/api/places/photo/<signed token>`.
3. Create a second key, "browser". Restrict it to *Maps JavaScript API* and, under *Website
   restrictions*, add `http://localhost:5000/*` and `https://<your-render-app>.onrender.com/*`.
   Put it in `.env` / Render as `GOOGLE_MAPS_BROWSER_KEY`.
4. Billing → Budgets & alerts: add a small budget alert so a demo can't run up a bill.

## Confirmation email at sign-up

New email/password accounts get a **confirm your email** link before they can sign in. The app shows
a "Check your inbox" screen with a **Resend** button and signs the user in when they come back from the
link. Google sign-ins are already verified by Google.

Supabase setup (dashboard):
1. Authentication → Sign In / Providers → Email: **Confirm email** on.
2. Authentication → URL Configuration: Site URL = your Render URL; Redirect URLs: add
   `http://localhost:5000` and the Render URL.
3. Authentication → Emails → **SMTP Settings**: turn on custom SMTP with a free sender such as Resend
   or Brevo. Supabase's built-in sender is for testing only: it only emails your Supabase team
   members and allows a few emails per hour.
4. Optional: edit the "Confirm signup" template under Authentication → Emails.

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
| `cost_calc.py` | `CostCalc` | Rooms, buses, costs, departure time, diet coverage, the best-value score and the three highlighted options; all the math |
| `aivana_client.py` | `AivanaClient` | Picks a package and drafts the announcement (local fallback without a key) |
| `booking_service.py` | `BookingService`, `MockBookingProvider` | Pending bookings → coach approves → confirmation codes |
| `travel_planner.py` | `TravelPlanner` | Coordinates everything; player vs coach views |
| `ncaa_client.py` | | NCAA schools, rankings, standings, scores, brackets; throttled + cached |
| `chat_assistant.py` | | "Ask Aivana" chatbot: answers from team data (+ web search), prepares bookings, proposes what-if changes, RSVPs |
| `geocode.py` | | Venue name → coordinates via OpenStreetMap Nominatim (free, cached, 1 request/s) |
| `app.py`, `auth.py` | | Flask API; every route checks the user's role and team |
| `templates/`, `static/` | | HTML/CSS/JS front end; sign-in with Supabase Auth |

**Trip flow:** Planning (packages + Aivana pick) → coach chooses → **Awaiting approval** (nothing
reserved yet) → coach approves → **Booked** → assign rooms/buses → publish → players see the plan.
If a hotel sells out, approval fails, nothing stays half-booked, and the trip returns to Planning.

**Team workspaces:** every NCAA program (school + sport + division) is its own team with its own
trips, roster and announcements, keyed by the team's id. A user can belong to several and switch
from the top bar or the Team page; picking a new program creates an empty workspace (home campus
found on the map). The selected team's identity (name, sport, division, NCAA logo, season) comes
from one place (`workspaces.identity`) and drives the sidebar, top bar, season block and Team Pulse.

**Upcoming games are not trips.** The dashboard lists upcoming away games as suggestions: code keeps
only future away games with no trip yet and a known opponent and date, and Aivana picks which to plan
next (schema-limited to those game ids). "Plan with Aivana" opens the wizard prefilled; a trip is saved
only when the planner clicks Generate packages. One trip per scheduled game. New teams (and the demo)
start with zero trips.

**Describe the trip:** the wizard can turn a sentence ("At Toledo Dec 5, 22 players…") into form
fields with Aivana's structured output; the planner checks every field before saving.

**Input checks (server and form):** no past match dates; the earliest departure must be before
the match; 0–150 players, 0–30 coaches, 0–50 staff, at least one traveler; 0–14 nights; 0–30 team
meals; budget $0–$1,000,000; valid coordinates; no duplicate trip for the same opponent and time.

**What players never see:** budget, prices or booking amounts. Hotel and restaurant details
appear only after the coach publishes a booked trip.

## Database (Supabase)

- Accounts, teams, trips, packages, bookings, announcements:
  `supabase/migrations/20261121021933_accounts_teams_trips_bookings_announcements.sql`
  (exported from the live project).
- `supabase/migrations/20261121180800_revoke_public_execute_rls_auto_enable.sql`: API roles can't
  call the `rls_auto_enable()` event-trigger function.
- Row level security is on for every table. Private tables have no policies on purpose (deny-all
  to the browser); only the Flask server, with the secret key, reads and writes them.
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
`SUPABASE_SERVICE_KEY`, `GOOGLE_MAPS_API_KEY` and `GOOGLE_MAPS_BROWSER_KEY` (and the Aivana keys only
if you want the live site to use Aivana) in the dashboard. Free instances sleep
after 15 minutes idle; open the site a minute before you demo.

## Demo script (3–5 minutes)

1. **Sign-in screen (20 s).** "Sign in as a Team Planner / Player" shows the sign-in pop-up (real
   Supabase Auth when keys are set). Click **Demo mode → Team Planner**. The demo team starts with no
   trips; the dashboard suggests its next away game. (Team → *Switch or add a team* shows another
   program's identity and schedule in a separate, empty workspace.)
2. **Plan a trip (60 s).** Plan a Trip → opponent, venue (pick from the list, or *Other venue* →
   **Find on map**), date. Try a past date or a departure after kick-off to show the checks. Type
   the traveler counts, set nights and budget → **Generate packages**.
3. **Three options + Aivana (60 s).** Point at *Lowest cost / Closest / Best value* and Aivana's
   pick with its reason. Open a package: every line of the cost breakdown adds up to the total.
   Filter by amenity; compare two packages.
4. **What-if (40 s).** Ask Aivana: "What if we stay 2 nights?" The app prices it; nothing changes
   until you click **Apply and re-price**.
5. **Approve (40 s).** Request the package → *Awaiting approval* (the SIMULATED banner) → Approve →
   simulated confirmation codes → Assign rooms → Aivana drafts the announcement → Publish.
6. **Player view (40 s).** Switch to *Player*: next trip, bus time, room, the menu filtered to
   their diet, RSVP. No prices anywhere.

## Limitations (known and deliberate)

- **Bookings are simulated.** No hotel, bus or restaurant is contacted and nothing is paid; the
  confirmation codes are generated by the app and labelled as such.
- **Sample places.** Without a Google Places key, hotels, restaurants, menus, room rates and bus
  quotes come from a mock catalog that covers the four venues in the Plan a Trip menu (labelled in
  the app). With Google Places on, hotels and photos are real but room prices are estimates.
  Hotel photos need a Google Maps key with billing enabled.
- **Other venues** are found by name with OpenStreetMap; without Google Places they have no
  catalog hotels, so plan them as day trips or switch Google Places on.
- **Demo mode** keeps data in memory, so the live demo resets when Render's free instance sleeps.
  Adding `SUPABASE_SERVICE_KEY` on Render makes data persist.
- **Aivana** needs `AIVANA_API_KEY`. When it's missing or a call fails, the app says so in plain
  words and shows its own best-value ranking / a fill-in template, both labelled. Aivana never
  computes a price and can't approve or pay.
- **One main planner per team** (transferable). University/administrator accounts and invitations
  from the original spec are not built.
- **NCAA data** comes from the unofficial ncaa-api.henrygd.me (rate-limited, cached). Linking an
  NCAA program changes the record, ranking and schedule shown, not the team's name or its trips.
