-- Run this once in Supabase: Dashboard -> SQL Editor -> New query -> paste -> Run

create extension if not exists pgcrypto;

create table if not exists startups (
  id uuid primary key default gen_random_uuid(),
  company_name text not null,
  domain text,
  business_idea text,
  sector text,
  location text,
  funding_stage text,
  funding_amount text,
  investors text,
  source_url text,
  source_name text,
  first_seen date default current_date,
  last_updated timestamptz default now(),
  unique (company_name)
);

create index if not exists idx_startups_last_updated on startups (last_updated desc);
create index if not exists idx_startups_sector on startups (sector);

-- Track each daily run so the frontend can show "last scanned" honestly
create table if not exists scan_log (
  id bigint generated always as identity primary key,
  run_at timestamptz default now(),
  articles_scanned int,
  startups_found int
);

-- Row Level Security: the site (using the public "anon" key) may only READ.
-- Writing happens only from GitHub Actions using the "service_role" key,
-- which bypasses RLS entirely -- so no write policy is needed or wanted here.
alter table startups enable row level security;
alter table scan_log enable row level security;

create policy "Public read access" on startups
  for select using (true);

create policy "Public read access" on scan_log
  for select using (true);
