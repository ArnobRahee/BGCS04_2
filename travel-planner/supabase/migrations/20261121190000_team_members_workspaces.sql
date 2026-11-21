-- Team workspaces: a user can belong to several teams (one per NCAA program) and switch between
-- them; profiles.team_id stays the currently selected team. Additive only: nothing is dropped.
create table if not exists public.team_members (
  id uuid not null default gen_random_uuid() unique,
  team_id uuid not null references public.teams(id) on delete cascade,
  profile_id uuid not null references public.profiles(id) on delete cascade,
  role text not null check (role in ('coach', 'player')),
  created_at timestamptz not null default now(),
  primary key (team_id, profile_id)
);
create index if not exists team_members_profile_idx on public.team_members(profile_id);

-- Existing memberships (from profiles.team_id)
insert into public.team_members (team_id, profile_id, role)
select team_id, id, role from public.profiles where team_id is not null
on conflict do nothing;

-- One planned trip per scheduled NCAA game per team (cancelled trips don't count)
create unique index if not exists trips_team_game_idx on public.trips(team_id, ncaa_game_id)
  where ncaa_game_id is not null and status <> 'CANCELLED';

-- Private like the other team tables: RLS on, no policies, so only the server (secret key) reads/writes.
alter table public.team_members enable row level security;
