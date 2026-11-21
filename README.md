# NCAAway · Away-Game Travel Planner (BGCS04)

**2026 BGSU Hackathon (ACM & ACM-W), Project B: Away-Game Travel Planner.** Team BGCS04.

NCAAway helps the staff who plan away-game travel. A team planner enters a trip (as a form or as
one sentence), and the app prices every hotel + team-meal restaurant + bus combination near the
venue from its own data. It then shows three options: **lowest cost, closest and best value**.
Aivana recommends one package by its ID and explains the tradeoffs. A person approves before
anything is booked. Players get a price-free itinerary with their bus time, room and the menu items
that fit their diet.

- **Live demo:** https://bgcs04-team-travel.onrender.com (free Render instance: the first load can take about a minute)
- **Code:** everything is in [`travel-planner/`](travel-planner/). That folder's README has the architecture, data model and a demo script.

---

## How to run it

Needs Python 3.11+.

```bash
cd travel-planner
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # then put your Aivana key in AIVANA_API_KEY
python app.py                                         # open http://localhost:5000
```

On the sign-in screen, click **Demo mode → Team Planner** (or **Player**). Demo mode needs no
database: the data lives in memory and resets when the server restarts.

| Environment variable | Needed? | What it does |
|---|---|---|
| `AIVANA_API_KEY` | For AI features | Server-side only. Without it, the app still works with its own labelled ranking and templates |
| `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SERVICE_KEY` | Optional | Real accounts (email confirmation) and saved data. Without the service key the app runs in demo mode |
| `NCAA_API_URL` | Optional | NCAA schedule, record and ranking (defaults to the free ncaa-api) |
| `GOOGLE_MAPS_API_KEY`, `GOOGLE_MAPS_BROWSER_KEY` | Optional | Real hotels and photos from Google Places (needs Google billing) |

**Tests:** `cd travel-planner && pip install -r requirements-dev.txt && python -m pytest -q tests`

**Keys:** API keys are read from environment variables on the server. They are never in this
repository or in browser code; `.env` is git-ignored.

---

## Aivana features used

Every Aivana call goes through the backend (`travel-planner/aivana_client.py`, official `aivana` Python SDK).

| Feature | Where | Details |
|---|---|---|
| **Structured output (JSON Schema)** | Trip request parsing | "At Toledo Dec 5 at 7pm, 22 players, 3 coaches, leave after 2pm, $8,000" → opponent, date, counts, departure limit, nights, budget. Fields not stated come back `null`; the planner checks the form before saving |
| **Structured output (JSON Schema)** | Package recommendation | `package_no` is an `enum` of the package IDs the app actually priced, plus a `reason`. An invented or invalid ID is rejected and the app falls back to its own best-value ranking, clearly labelled |
| **Structured output (JSON Schema)** | Which game to plan next | `game_id` is an `enum` of upcoming away games that the code has already filtered (future, away, not yet planned) |
| **Structured output (JSON Schema)** | Players' announcement | Title and body written only from the booked facts; no money mentioned |
| **Multi-turn chat** | "Ask Aivana" panel | Answers from the team's own data, which is sent as context. It can prepare a booking request (main planner only) or propose a what-if change |
| **Web search** | "Ask Aivana" panel | For outside questions; those answers are labelled "From the web:" |

**The model never does the math.** Rooms, buses, meals, totals, budget checks, distances, departure
times, sorting and the three highlighted options are all computed in code (`cost_calc.py`). Aivana
only chooses among IDs the app gives it, and the app displays costs from its own data.

**Tokens and errors:**
- Prompts are kept short; only the data needed for the question is sent, and no PDFs are used.
- Rate limits, an empty balance or spend limit, a bad key, timeouts and malformed output each get a
  plain-language message.
- When Aivana is unavailable, the app keeps working with labelled fallbacks.

---

## What we built, by level

### Level 1: Core ✅
- Trip request as a **form or free text** (opponent, date, players / coaches / staff, departure constraint), parsed with structured output.
- Input checks:
  - no past dates;
  - departure must be before kickoff;
  - sensible limits on counts, nights, meals and budget;
  - no duplicate trips for the same game.
- **Code calculates:**
  - buses: `ceil(travelers / seats)`, each priced at `max(minimum charge, rate × round-trip km)` plus a driver overnight fee;
  - hotel rooms: `ceil(travelers / room occupancy) × nights`;
  - meals: `travelers × price per person × meals`;
  - the total of all three.
- Every hotel × restaurant × bus combination is priced, and **three distinct packages are highlighted** (lowest cost, closest, best value).
- **Aivana recommends one package by ID with its tradeoffs** (structured), and the app shows its own cost breakdown next to it.
- **Human review:** the main planner requests and approves the package. The AI cannot approve or pay. Bookings are mock records labelled "Simulated".

### Level 2: Standard ✅ (partly)
- ✅ **What-if chat:** "What if we stay 2 nights?" or "leave three players home". Aivana proposes the change, the code re-prices it (before → after for each option), and nothing changes until the planner clicks **Apply and re-price**.
- ✅ Multi-turn chat that uses the team data plus web search.
- ✅ Aivana-written announcement for players.
- ❌ **Streamed trip memo:** not built. The itinerary page and the announcement are not streamed.

### Level 3: Advanced ✅ (several)
- ✅ **Rooming list:** "Assign rooms & buses" gives each player a room and a bus; players see their own.
- ✅ **Risk flags:**
  - late arrival;
  - over budget;
  - restaurant can't seat the whole group;
  - no main dish for a player's dietary need.
- ✅ **Exported itinerary:** Print / save as PDF.
- ✅ **Extra rigor:** 110+ automated tests, covering:
  - cost math;
  - input validation;
  - the recommendation is always a real package ID;
  - Aivana failures;
  - team data isolation;
  - deletion.
- ➕ **Beyond the brief:**
  - NCAA schedule integration, with a separate workspace per program;
  - Aivana suggests which away game to plan next;
  - players RSVP and their count re-prices the trip;
  - Supabase sign-in with email confirmation;
  - one transferable main planner per team.
- ❌ Not built: real driving distances from a map service, and a budget-versus-actual view.

---

## Data

- **Catalog:** `travel-planner/data/catalog.json`, built by `data/build_dataset.ipynb`, with 4 away venues, 48 hotels, 60 restaurants (416 menu items) and 4 bus companies.
  - Hotel and restaurant names and locations come from OpenStreetMap.
  - Prices, room counts, amenities, menus and bus rates are **synthetic**, generated with a fixed seed.
- **People:** the demo roster (one coach, eight players) is fictional. No real personal data is used.
- Seats per bus, room occupancy and meal prices are values in the catalog, so they can be edited in one place. **All costs are estimates**, and the app labels sample data as simulated.

---

## Known limits

- **Bookings are simulated.** Nothing is reserved or paid; confirmation codes are generated by the app.
- **Hotels only near the 4 venues.** The hotel list covers 4 venues. For other venues, the app finds the stadium by name (OpenStreetMap), but hotels need Google Places with billing, or the trip can be planned as a day trip.
- **Straight-line distances.** Distances are straight-line × a road factor, not real driving routes.
- **Settings in the data, not the app.** Seats per bus, room occupancy and meal allowance are edited in the catalog data (or Supabase tables), not in a settings screen.
- **No streaming.** The trip memo is not streamed (Level 2 item not built).
- **Demo data resets.** The live demo runs in demo mode, so its data resets when the free Render instance sleeps (about 15 minutes idle).
- **NCAA data.** It comes from the unofficial ncaa-api. It is cached, and the first schedule load for a team can take around 15 seconds.
- **Aivana usage.** Aivana calls cost credits. If the key is missing or the balance runs out, the app shows its own ranking and templates, labelled.

---

## Team

Team BGCS04, 2026 BGSU Hackathon.
