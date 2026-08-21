-- Run this once in Supabase: Dashboard -> SQL Editor -> New query -> paste -> Run
-- Safe to run whether this is a fresh project OR you already ran an earlier
-- version of this schema -- it upgrades in place without losing data.

create extension if not exists pgcrypto;

create table if not exists startups (
  id uuid primary key default gen_random_uuid(),
  company_name text not null,
  unique (company_name)
);

alter table startups add column if not exists location text;
alter table startups add column if not exists region text;
alter table startups add column if not exists year_founded text;
alter table startups add column if not exists founder_name text;
alter table startups add column if not exists founder_linkedin text;
alter table startups add column if not exists industry text;
alter table startups add column if not exists business_idea text;
alter table startups add column if not exists unique_moat text;
alter table startups add column if not exists funding_stage text;
alter table startups add column if not exists funding_amount text;
alter table startups add column if not exists funding_amount_usd numeric;
alter table startups add column if not exists signal_score int;
alter table startups add column if not exists investors text;
alter table startups add column if not exists contact_email text;
alter table startups add column if not exists hiring_status text;
alter table startups add column if not exists homepage text;
alter table startups add column if not exists source_url text;
alter table startups add column if not exists source_name text;
alter table startups add column if not exists first_seen date default current_date;
alter table startups add column if not exists last_updated timestamptz default now();

-- Upgrade path from the original (v1) schema, which had "sector" and "domain"
-- instead of the fields above.
do $$
begin
  if exists (select 1 from information_schema.columns where table_name = 'startups' and column_name = 'sector') then
    update startups set industry = coalesce(industry, sector);
    alter table startups drop column sector;
  end if;
  if exists (select 1 from information_schema.columns where table_name = 'startups' and column_name = 'domain') then
    alter table startups drop column domain;
  end if;
end $$;

create index if not exists idx_startups_last_updated on startups (last_updated desc);
create index if not exists idx_startups_industry on startups (industry);
create index if not exists idx_startups_region on startups (region);
create index if not exists idx_startups_funding_usd on startups (funding_amount_usd desc nulls last);

-- Track each weekly run so the frontend can show "last scanned" honestly
create table if not exists scan_log (
  id bigint generated always as identity primary key,
  run_at timestamptz default now(),
  articles_scanned int,
  startups_found int
);

-- Latest AI/startup headlines for the site's news sidebar. Populated by a
-- separate DAILY job (scraper/news_job.py) -- no LLM involved, just raw
-- headlines from the same free feeds, so it's cheap enough to run daily
-- even though the main startups table only refreshes weekly.
create table if not exists news_feed (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  link text not null,
  source_name text,
  published text,
  fetched_at timestamptz default now(),
  unique (link)
);

create index if not exists idx_news_feed_published on news_feed (published desc nulls last);
create index if not exists idx_news_feed_fetched on news_feed (fetched_at desc);

-- Row Level Security: the site (using the public "anon" key) may only READ.
-- Writing happens only from GitHub Actions using the "service_role" key,
-- which bypasses RLS entirely -- so no write policy is needed or wanted here.
alter table startups enable row level security;
alter table scan_log enable row level security;
alter table news_feed enable row level security;

drop policy if exists "Public read access" on startups;
create policy "Public read access" on startups for select using (true);

drop policy if exists "Public read access" on scan_log;
create policy "Public read access" on scan_log for select using (true);

drop policy if exists "Public read access" on news_feed;
create policy "Public read access" on news_feed for select using (true);
