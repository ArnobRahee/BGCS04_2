-- Exported read-only from the live Supabase project (supabase_migrations.schema_migrations) so the
-- core schema is reproducible from the repo. It replaced the first prototype schema
-- (20261121010100_initial_travel_planner_schema), which it drops.
-- Private tables have RLS on and no policies: only the Flask server (service key) can read or write them.

drop table if exists public.saved_items, public.announcement_reads, public.announcements,
  public.trip_participants, public.bookings, public.trip_packages, public.recommendations,
  public.packages, public.trips, public.hotels, public.restaurants, public.bus_options,
  public.profiles, public.teams cascade;

create table public.teams (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  join_code text not null unique,
  home_name text not null,
  home_lat double precision not null,
  home_lng double precision not null,
  owner_id uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now()
);

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  full_name text not null,
  role text not null check (role in ('coach', 'player')),
  team_id uuid references public.teams(id) on delete set null,
  diet_tags text[] not null default '{}',
  created_at timestamptz not null default now()
);

create table public.trips (
  id uuid primary key default gen_random_uuid(),
  team_id uuid not null references public.teams(id) on delete cascade,
  created_by uuid references public.profiles(id) on delete set null,
  opponent text not null,
  venue_name text not null,
  venue_address text not null default '',
  venue_lat double precision not null,
  venue_lng double precision not null,
  match_at timestamp not null,
  depart_after timestamp,
  nights int not null default 1 check (nights >= 0),
  budget numeric(12,2) not null check (budget >= 0),
  num_players int not null check (num_players >= 0),
  num_coaches int not null check (num_coaches >= 0),
  num_staff int not null check (num_staff >= 0),
  diet_tags text[] not null default '{}',
  meals int not null default 1 check (meals >= 0),
  status text not null default 'PLANNING' check (status in
    ('DRAFT','PLANNING','AWAITING_APPROVAL','BOOKED','IN_PROGRESS','COMPLETED','CANCELLED')),
  selected_package_no int,
  published boolean not null default false,
  ai_recommendation jsonb,
  created_at timestamptz not null default now()
);

create table public.trip_packages (
  id uuid primary key default gen_random_uuid(),
  trip_id uuid not null references public.trips(id) on delete cascade,
  package_no int not null,
  total_cost numeric(12,2) not null,
  route_km numeric(10,1) not null,
  detail jsonb not null,
  unique (trip_id, package_no)
);

create table public.bookings (
  id uuid primary key default gen_random_uuid(),
  trip_id uuid not null references public.trips(id) on delete cascade,
  type text not null check (type in ('hotel','bus','meal')),
  quantity int not null default 1,
  check_in date,
  check_out date,
  amount numeric(12,2) not null default 0,
  status text not null default 'PENDING_APPROVAL' check (status in
    ('PENDING_APPROVAL','CONFIRMED','FAILED','CANCELLED')),
  confirmation_code text,
  summary text not null default '',
  requested_by uuid references public.profiles(id) on delete set null,
  approved_by uuid references public.profiles(id) on delete set null,
  created_at timestamptz not null default now()
);

create table public.trip_participants (
  trip_id uuid not null references public.trips(id) on delete cascade,
  player_id uuid not null references public.profiles(id) on delete cascade,
  rsvp text not null default 'maybe' check (rsvp in ('going','maybe','not_going')),
  room_no int,
  bus_no int,
  primary key (trip_id, player_id)
);

create table public.announcements (
  id uuid primary key default gen_random_uuid(),
  trip_id uuid not null references public.trips(id) on delete cascade,
  team_id uuid not null references public.teams(id) on delete cascade,
  author_id uuid references public.profiles(id) on delete set null,
  title text not null,
  body text not null default '',
  created_at timestamptz not null default now()
);

create table public.announcement_reads (
  announcement_id uuid not null references public.announcements(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  primary key (announcement_id, user_id)
);

create table public.saved_items (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  announcement_id uuid references public.announcements(id) on delete cascade,
  trip_id uuid references public.trips(id) on delete cascade,
  created_at timestamptz not null default now(),
  check (num_nonnulls(announcement_id, trip_id) = 1)
);

create unique index saved_items_user_announcement_idx on public.saved_items(user_id, announcement_id) where announcement_id is not null;
create unique index saved_items_user_trip_idx on public.saved_items(user_id, trip_id) where trip_id is not null;
create index saved_items_announcement_idx on public.saved_items(announcement_id);
create index saved_items_trip_idx on public.saved_items(trip_id);
create index teams_owner_idx on public.teams(owner_id);
create index profiles_team_idx on public.profiles(team_id);
create index trips_team_idx on public.trips(team_id);
create index trips_created_by_idx on public.trips(created_by);
create index bookings_trip_idx on public.bookings(trip_id);
create index bookings_requested_by_idx on public.bookings(requested_by);
create index bookings_approved_by_idx on public.bookings(approved_by);
create index trip_participants_player_idx on public.trip_participants(player_id);
create index announcements_trip_idx on public.announcements(trip_id);
create index announcements_team_idx on public.announcements(team_id);
create index announcements_author_idx on public.announcements(author_id);
create index announcement_reads_user_idx on public.announcement_reads(user_id);

alter table public.teams enable row level security;
alter table public.profiles enable row level security;
alter table public.trips enable row level security;
alter table public.trip_packages enable row level security;
alter table public.bookings enable row level security;
alter table public.trip_participants enable row level security;
alter table public.announcements enable row level security;
alter table public.announcement_reads enable row level security;
alter table public.saved_items enable row level security;
