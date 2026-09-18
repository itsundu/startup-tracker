-- StartupRadar v2 migration.
--
-- Run this AFTER supabase_schema.sql (the original v1 schema) has already
-- been applied at least once. Safe to re-run: every statement is
-- idempotent (create-if-not-exists / add-column-if-not-exists / drop-and-
-- recreate for views and policies only, never for tables or data).
--
-- What this does:
--   1. Creates the v2 entity model (companies, company_aliases,
--      company_events, company_sources) alongside the existing `startups`
--      table -- `startups` is NOT dropped or altered destructively, so the
--      site keeps working on v1 data throughout.
--   2. Backfills `companies` from the current `startups` rows so the new
--      tables aren't empty on day one, explicitly marking backfilled data
--      as unverified-by-v2-rules (data_confidence recomputed to 0 pending a
--      real v2 run; homepage_confidence null; region_bucket derived from
--      the old free-text `region` field with a documented, conservative
--      mapping -- anything that doesn't clearly say US/India maps to NULL,
--      i.e. "not yet verified," rather than guessed).
--   3. Extends `company_snapshots` with the new versioned-scoring columns
--      (additive only -- existing snapshot rows are untouched).
--   4. Adds `scan_runs`, a richer run-tracking table alongside the existing
--      `scan_log` (which is left in place for backward compatibility; nothing
--      currently reads it will break).
--   5. Creates `v_regional_rankings`, a stable view the NEW frontend reads
--      from -- built off `companies`, so it returns the backfilled v1 data
--      immediately after this migration runs, and starts returning properly
--      verified/ranked v2 data as soon as the new scraper pipeline's first
--      run completes. This is what "keep the frontend functional during
--      migration" means concretely: there is no moment where the view is
--      empty.
--   6. Enables RLS with PUBLIC READ-ONLY policies on `companies`,
--      `company_aliases`, `company_events`, `company_snapshots`, and the
--      view. `company_sources` and `scan_runs` get RLS enabled with NO
--      public policy at all -- only the service_role key (which bypasses
--      RLS) can read them, since they can carry extraction/administrative
--      detail not meant for public consumption.
--
-- Rollback: see MIGRATION.md in the repo root for the full rollback
-- sequence (drop the new objects; `startups`/`company_snapshots`/`scan_log`
-- are never touched destructively by this file, so a rollback of this
-- migration cannot lose any v1 data).

create extension if not exists pgcrypto;

-- ============================================================================
-- 1. companies
-- ============================================================================

create table if not exists companies (
  id uuid primary key default gen_random_uuid(),
  canonical_name text not null,
  normalized_name text not null,
  primary_domain text,
  homepage text,
  homepage_confidence int,
  description text,
  industry text,
  subsector text,
  ai_relevance_score int,
  company_status text default 'operating',      -- operating | shut_down | acquired | public_incumbent
  founded_year text,
  headquarters_city text,
  headquarters_state text,
  headquarters_country text,
  region_bucket text,                            -- US | INDIA | ROW | null (unverified)
  founders text,
  employee_count_min int,
  employee_count_max int,
  employee_count_as_of date,
  employee_count_source_url text,
  first_seen timestamptz default now(),
  last_seen timestamptz default now(),
  last_verified timestamptz,
  active boolean default true,
  data_confidence int default 0,
  data_completeness int default 0,
  momentum_score int,
  regional_rank int,
  score_version text,
  -- v2-specific fields not in the original product-spec list, needed to
  -- make eligibility/ranking actually computable without re-deriving them
  -- from company_events on every read:
  moat_summary text,
  moat_evidence text,
  moat_confidence int,
  hiring_status text default 'unknown',          -- actively_hiring | limited_hiring | no_verified_openings | unknown
  hiring_confidence int,
  verified_open_role_count int,
  -- Denormalized latest/aggregate funding facts, kept in sync by main_v2.py
  -- whenever it processes a funding_round_completed event for this company.
  -- Denormalized deliberately: the frontend lists up to 150 companies per
  -- page load and must not issue a company_events query per row.
  latest_funding_stage text,
  latest_funding_amount_usd numeric,
  latest_funding_date date,
  total_disclosed_funding_usd numeric,
  entity_resolution_needs_review boolean default false,
  why_ranked text,
  rank_change_since_previous_snapshot int,
  legacy_startup_id uuid references startups(id) on delete set null,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create unique index if not exists idx_companies_normalized_name_domain
  on companies (normalized_name, coalesce(primary_domain, ''));
create unique index if not exists idx_companies_primary_domain
  on companies (primary_domain) where primary_domain is not null;
create index if not exists idx_companies_region_bucket on companies (region_bucket);
create index if not exists idx_companies_industry on companies (industry);
create index if not exists idx_companies_momentum on companies (momentum_score desc nulls last);
create index if not exists idx_companies_regional_rank on companies (region_bucket, regional_rank);
create index if not exists idx_companies_active on companies (active);
create index if not exists idx_companies_last_verified on companies (last_verified desc nulls last);

-- ============================================================================
-- 2. company_aliases
-- ============================================================================

create table if not exists company_aliases (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references companies(id) on delete cascade,
  alias text not null,
  normalized_alias text not null,
  source_url text,
  created_at timestamptz default now(),
  unique (company_id, normalized_alias)
);

create index if not exists idx_company_aliases_normalized on company_aliases (normalized_alias);

-- ============================================================================
-- 3. company_events
-- ============================================================================

create table if not exists company_events (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references companies(id) on delete cascade,
  event_type text not null,
  event_date date,
  announced_at timestamptz,
  title text,
  summary text,
  amount_original numeric,
  currency text,
  amount_usd numeric,
  currency_conversion_rate numeric,
  conversion_rate_date date,
  funding_stage text,
  investors text,
  source_url text not null,
  source_name text,
  source_tier int,                                -- 1 | 2 | 3
  published_at timestamptz,
  retrieved_at timestamptz default now(),
  evidence_text text not null,
  extraction_provider text,
  extraction_confidence int,
  verification_status text default 'unverified',  -- completed | proposed | rumored | abandoned | ambiguous | unverified
  independent_source_count int default 1,
  created_at timestamptz default now(),
  constraint chk_company_events_type check (event_type in (
    'funding_round_completed', 'funding_round_announced', 'proposed_funding',
    'valuation_report', 'acquisition', 'product_launch', 'major_product_release',
    'customer_win', 'partnership', 'geographic_expansion', 'executive_change',
    'hiring_growth', 'layoffs', 'shutdown', 'other'
  )),
  constraint chk_company_events_verification check (verification_status in (
    'completed', 'proposed', 'rumored', 'abandoned', 'ambiguous', 'unverified'
  ))
);

create index if not exists idx_company_events_company on company_events (company_id, event_date desc nulls last);
create index if not exists idx_company_events_type on company_events (event_type);
create index if not exists idx_company_events_source_tier on company_events (source_tier);
-- Prevents the same article being recorded twice against the same company.
create unique index if not exists idx_company_events_dedup
  on company_events (company_id, source_url, event_type);

-- ============================================================================
-- 4. company_sources (field-level provenance)
-- ============================================================================

create table if not exists company_sources (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references companies(id) on delete cascade,
  field_name text not null,
  field_value text,
  source_url text not null,
  source_name text,
  source_tier int,
  published_at timestamptz,
  retrieved_at timestamptz default now(),
  evidence_text text,
  verification_status text default 'unverified',
  confidence int,
  content_hash text,
  is_contradiction boolean default false,
  created_at timestamptz default now()
);

create index if not exists idx_company_sources_company_field on company_sources (company_id, field_name);
create index if not exists idx_company_sources_content_hash on company_sources (content_hash);

-- ============================================================================
-- 5. company_snapshots -- extend the EXISTING table, additive only
-- ============================================================================

alter table company_snapshots add column if not exists company_id uuid references companies(id) on delete cascade;
alter table company_snapshots add column if not exists region_bucket text;
alter table company_snapshots add column if not exists rank int;
alter table company_snapshots add column if not exists regional_rank int;
alter table company_snapshots add column if not exists score_version text;
alter table company_snapshots add column if not exists component_scores jsonb;
alter table company_snapshots add column if not exists verified_open_job_count int;
alter table company_snapshots add column if not exists total_verified_funding_usd numeric;
alter table company_snapshots add column if not exists data_completeness int;

create index if not exists idx_snapshots_company on company_snapshots (company_id, recorded_at desc);
create index if not exists idx_snapshots_region_rank on company_snapshots (region_bucket, regional_rank, recorded_at desc);

-- ============================================================================
-- 6. scan_runs (richer than the existing scan_log, which is left untouched)
-- ============================================================================

create table if not exists scan_runs (
  id uuid primary key default gen_random_uuid(),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  workflow_type text not null,                    -- ranking_refresh | news_refresh
  sources_attempted int,
  sources_succeeded int,
  articles_fetched int,
  candidates_extracted int,
  companies_accepted int,
  companies_rejected int,
  regional_result_counts jsonb,
  validation_failures int default 0,
  warnings jsonb,
  provider_usage jsonb,
  score_version text,
  run_status text default 'running',              -- running | success | success_with_warnings | failed
  quality_report jsonb,
  created_at timestamptz default now(),
  constraint chk_scan_runs_status check (run_status in ('running', 'success', 'success_with_warnings', 'failed'))
);

create index if not exists idx_scan_runs_started on scan_runs (started_at desc);
create index if not exists idx_scan_runs_status on scan_runs (run_status);

-- ============================================================================
-- 7. Backfill companies from the existing startups table
-- ============================================================================
-- Idempotent via the unique index on (normalized_name, primary_domain): a
-- second run of this migration inserts nothing new for rows already
-- backfilled. Uses a simple inline normalization (lowercase, strip a small
-- set of punctuation) matching scraper/domain_rules.py's normalize_company_name
-- closely enough for backfill purposes; the real pipeline re-resolves every
-- company properly on its first v2 run.

insert into companies (
  canonical_name, normalized_name, primary_domain, homepage, description,
  industry, founded_year, region_bucket, founders, first_seen, last_seen,
  active, momentum_score, legacy_startup_id
)
select
  s.company_name,
  lower(regexp_replace(regexp_replace(s.company_name, '[,\.]', ' ', 'g'), '\s+', ' ', 'g')),
  case when s.homepage is not null and s.homepage ~ '^https?://[^/]+'
       then regexp_replace(regexp_replace(s.homepage, '^https?://(www\.)?', ''), '/.*$', '')
       else null end,
  s.homepage,
  s.business_idea,
  s.industry,
  s.year_founded,
  case
    when s.region ilike '%india%' then 'INDIA'
    when s.region ilike '%usa%' or s.region = 'USA' then 'US'
    when s.region is not null and s.region <> 'Unknown' then 'ROW'
    else null
  end,
  s.founder_name,
  coalesce(s.first_seen::timestamptz, now()),
  coalesce(s.last_updated, now()),
  true,
  s.momentum_score,
  s.id
from startups s
where s.company_name is not null
-- Deliberately no conflict target: `companies` has TWO unique indexes
-- (normalized_name+domain, and primary_domain alone). A bare
-- `on conflict do nothing` suppresses a violation of EITHER one, which
-- matters here because pre-existing v1 data can legitimately have two
-- different `startups` rows resolving to the same homepage domain (e.g. a
-- duplicate news mention) -- targeting only one index would abort the
-- whole backfill on that row instead of just skipping it.
on conflict do nothing;

-- Backfilled rows start with data_confidence/data_completeness at 0 and
-- entity_resolution_needs_review left false but company_status 'operating'
-- by default -- they are NOT eligible for regional ranking (see
-- ranking.EligibilityConfig.min_ranking_confidence = 60) until the v2
-- pipeline actually verifies them. This is intentional: a backfilled row
-- existing is not the same claim as a backfilled row being VERIFIED.

-- ============================================================================
-- 8. Compatibility view for the frontend
-- ============================================================================

drop view if exists v_regional_rankings;
create view v_regional_rankings
  with (security_invoker = true)
  as
select
  id, canonical_name, normalized_name, primary_domain, homepage,
  homepage_confidence, description, industry, subsector, ai_relevance_score,
  company_status, founded_year, headquarters_city, headquarters_state,
  headquarters_country, region_bucket, founders, employee_count_min,
  employee_count_max, employee_count_as_of, moat_summary, moat_evidence,
  moat_confidence, hiring_status, hiring_confidence, verified_open_role_count,
  latest_funding_stage, latest_funding_amount_usd, latest_funding_date,
  total_disclosed_funding_usd,
  momentum_score, regional_rank, rank_change_since_previous_snapshot,
  data_confidence, data_completeness, why_ranked, score_version,
  last_seen, last_verified, active
from companies
where active = true
  and region_bucket is not null
  and regional_rank is not null
order by region_bucket, regional_rank;

-- ============================================================================
-- 9. Row Level Security
-- ============================================================================

alter table companies enable row level security;
alter table company_aliases enable row level security;
alter table company_events enable row level security;
alter table company_sources enable row level security;
alter table scan_runs enable row level security;

drop policy if exists "Public read access" on companies;
create policy "Public read access" on companies for select using (true);

drop policy if exists "Public read access" on company_aliases;
create policy "Public read access" on company_aliases for select using (true);

-- company_events is publicly readable in full, deliberately including
-- rumored/proposed/Tier-3 events -- the product principle "important claims
-- displayed on the site must be traceable to evidence" requires the
-- frontend's "Sources" column and evidence drawer to be able to show
-- EVERY event a claim is based on, not just the ones that made the ranking
-- cut. The frontend is responsible for labeling verification_status
-- clearly (e.g. "rumored, not counted toward ranking"), not RLS.
drop policy if exists "Public read access" on company_events;
create policy "Public read access" on company_events for select using (true);

-- company_sources and scan_runs intentionally get NO public select policy --
-- RLS is enabled with zero policies, which means the anon role can read
-- nothing from them at all. Only the service_role key (used exclusively by
-- GitHub Actions, never the browser) bypasses RLS and can read/write these.
-- This is where extraction provider metadata, raw evidence excerpts tied to
-- rejected/contradictory values, and per-run internal counts live -- never
-- exposed to public queries.

grant select on v_regional_rankings to anon, authenticated;
