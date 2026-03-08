-- Algobets AI core schema (PostgreSQL)

create extension if not exists "pgcrypto";

create table if not exists picks (
  id uuid primary key default gen_random_uuid(),
  sport text not null,
  game_id text not null,
  market_type text not null check (market_type in ('spread','ml','total')),
  selection text not null,
  line numeric not null,
  odds integer not null,
  edge_percent numeric not null,
  confidence_score numeric not null,
  agents jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  is_active boolean not null default true
);

create table if not exists line_movements (
  id bigserial primary key,
  pick_id uuid not null references picks(id) on delete cascade,
  bookmaker text not null,
  line numeric not null,
  odds integer not null,
  timestamp timestamptz not null default now(),
  is_closing_line boolean not null default false
);

create table if not exists user_picks (
  id bigserial primary key,
  user_id uuid not null,
  pick_id uuid not null references picks(id) on delete cascade,
  odds_taken integer not null,
  stake numeric not null,
  result text not null check (result in ('pending','win','loss','push')),
  clv_achieved numeric,
  profit_loss numeric,
  created_at timestamptz not null default now()
);

create index if not exists idx_picks_sport_active on picks(sport, is_active, created_at desc);
create index if not exists idx_line_movements_pick_time on line_movements(pick_id, timestamp desc);
create index if not exists idx_user_picks_user_time on user_picks(user_id, created_at desc);
