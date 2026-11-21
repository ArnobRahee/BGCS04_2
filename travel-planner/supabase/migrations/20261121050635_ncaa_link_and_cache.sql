-- NCAA integration. Additive only: new nullable columns and one new table.

-- Which NCAA school / sport / division a team is (for record, rankings, schedule).
alter table public.teams add column if not exists ncaa_seo text;
alter table public.teams add column if not exists ncaa_name text;
alter table public.teams add column if not exists sport text;
alter table public.teams add column if not exists division text;

-- Lets an away game against a school find that school's stadium in the catalog.
alter table public.venues add column if not exists school_seo text;
update public.venues set school_seo = v.seo
from (values ('V001', 'ohio-st'), ('V002', 'toledo'), ('V003', 'kent-st'), ('V004', 'michigan')) as v(id, seo)
where venues.id = v.id and venues.school_seo is null;
create index if not exists venues_school_seo_idx on public.venues(school_seo);

-- Optional link from a trip to the NCAA game it's for.
alter table public.trips add column if not exists ncaa_game_id text;

-- Server-side cache of NCAA API responses (the public API allows 3 requests/second).
create table if not exists public.ncaa_cache (
  key         text primary key,
  payload     jsonb not null,
  fetched_at  timestamptz not null default now()
);
alter table public.ncaa_cache enable row level security;
-- No policies on purpose: only the Flask server (secret key) reads and writes the cache.
