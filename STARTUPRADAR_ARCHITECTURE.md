> **Status update:** the "Phase 2" items below around entity resolution, score decay, and a richer
> data model have been implemented as **v2** (`scraper/main_v2.py`, on the `claude/startup-radar-v2`
> branch as of this writing) — see `RANKING_METHODOLOGY.md` for the model and `MIGRATION.md` for
> how it coexists with the "Current architecture" described below during the transition. This
> document's description of the free-tier constraint and what's deliberately out of scope
> (licensed data providers, a hosted backend, auth/subscriptions) still holds for v2 — it's a
> richer pipeline within the same zero-paid-infrastructure constraint, not a departure from it.

# StartupRadar — Architecture (free-tier scope)

This document exists because a much larger product spec was proposed for this project — one
describing a Crunchbase/Harmonic/PitchBook-class platform (Next.js + FastAPI, Postgres + Redis +
ClickHouse, licensed Crunchbase/Dealroom/Tracxn/Similarweb/BuiltWith/Sensor Tower data, Stripe
subscriptions, OAuth, entity resolution, admin dashboards, SEC/GitHub/hiring connectors, weekly
newsletters, SEO page factories...). That's a legitimate multi-person, multi-year, funded
engineering roadmap. **It is not what this document describes.**

This project has been explicitly free-tier-only from day one: GitHub Actions (free cron), Supabase
(free Postgres), a static HTML file on Hostinger shared hosting, and free-tier LLMs (Gemini +
Groq). This doc describes the version of "StartupRadar" that's actually being built inside that
constraint, and draws a hard line around what's deliberately **not** being built until/unless that
constraint changes (see "Deliberately out of scope" below).

## What "StartupRadar" means here

Same north star as the original vision — **discover, track, score, and explain startup
momentum** — built with only free, public, self-serve data sources, no server process, and no
paid infrastructure.

## Current architecture

```
GitHub Actions (weekly cron)              GitHub Actions (daily cron)
        │                                          │
        ▼                                          ▼
scraper/main.py                           scraper/news_job.py
  ├─ sources.py    → RSS/HN/YC blog          (same sources.py, no LLM --
  ├─ extractor.py  → LLM extraction           just headlines, cheap enough
  │  (Data Engine: raw → structured fields)   to run daily)
  ├─ enrich.py     → website enrichment              │
  ├─ scoring.py    → Intelligence Engine:             ▼
  │  momentum score + confidence,          Supabase "news_feed" table
  │  from configurable weights
  └─ main.py       → upsert + snapshot
        │
        ▼
Supabase (Postgres, free tier)
  ├─ startups           (current state per company)
  ├─ company_snapshots  (momentum_score over time -- the "trajectory")
  ├─ score_weights       (configurable scoring formula, not hardcoded)
  ├─ scan_log
  └─ news_feed
        │
        ▼
frontend/index.html  →  static page, reads Supabase directly client-side
        │
        ▼
Hostinger (static file hosting) -- one file, uploaded once
```

### Layers, mapped to what actually exists

- **Layer A — Data Engine**: `scraper/sources.py` (fetch) + `scraper/extractor.py` (LLM
  extraction into structured fields) + `scraper/enrich.py` (website-derived fields). This is
  "raw → normalized," same idea as the original spec's ingestion layer, just without a
  multi-connector framework — one connector type (RSS/API + LLM) rather than many.
- **Layer B — Intelligence Engine**: `scraper/scoring.py`. Converts the row's own fields into a
  `momentum_score` (0-100) and a `data_confidence` (0-100), using weights read from the
  `score_weights` table (not hardcoded), and writes a snapshot row so history accumulates over
  time. No decay function, no breakout-detection model, no multi-signal independence checks yet
  — those need real historical depth (weeks/months of snapshots) to mean anything, and we're
  starting from zero history.
- **Layer C — Product Experience**: `frontend/index.html`. One static page: search/filter/sort
  table + news sidebar. No company profile pages, no sector/investor pages, no "Ask Radar," no
  watchlists/alerts/auth yet — see Phase 2/3 below.

## What changed in this pass (Phase 1)

1. `signal_score` (1-5, hardcoded heuristic) → `momentum_score` (0-100, weights read from
   `score_weights`) — same underlying signals (funding stage, funding amount, investor presence)
   but now on a 0-100 scale with a configuration table instead of hardcoded constants, per the
   spec's "transparent, not black-box" principle.
2. Added `data_confidence` (0-100): a deterministic measure of how complete the row's data is
   (what fraction of key fields are populated), not a fabricated "how sure are we this company
   will succeed" number. See `RADAR_SCORE_SPEC.md`.
3. Added `company_snapshots`: one row per company per weekly run, so a trajectory becomes visible
   over time. It will be nearly empty/flat for the first several weeks — that's expected, not a
   bug; there's no way to backfill history that was never recorded.
4. Upsert now requests `return=representation` from Supabase so we get real row `id`s back,
   letting snapshots reference `startups.id` (a stable UUID) rather than joining on company name
   — matches the spec's "don't use names as keys" principle, and we already had the UUID, just
   weren't using it for this.

## Deliberately out of scope (this is a decision, not an oversight)

- **Any licensed data connector** (Crunchbase, Dealroom, Tracxn, Similarweb, BuiltWith, Sensor
  Tower). These require paid, often sales-gated commercial licenses. See
  `DATA_SOURCE_REGISTRY.md` — they're documented as disabled/licensed, not built.
- **Next.js/FastAPI rewrite, Redis, ClickHouse, Elasticsearch.** No server process exists in this
  project; Hostinger (as configured) serves static files only. Adding a real backend is a hosting
  decision with real monthly cost — revisit only if that trade-off is explicitly chosen.
- **Auth, subscriptions, Stripe, multi-tenant orgs, roles.** No business entity/payment processing
  exists for this project; building subscription logic before that's true is pure waste.
- **Company profile pages, sector/investor pages, "Ask Radar," watchlists/alerts, admin
  dashboard.** All genuinely good ideas from the original spec, but all need either routing (this
  is a single static HTML page) or an authenticated backend. Candidates for Phase 2 once the
  data/scoring layer has run long enough to have something worth showing.
- **SEC EDGAR, GitHub signals, hiring-signal connectors (Greenhouse/Lever/Adzuna), government
  data (USAspending, data.gov.in).** All genuinely free and worth adding — deferred to Phase 2 as
  new sources in the existing `sources.py` pattern, not because they cost money, but to keep this
  pass reviewable and testable rather than one giant change.
- **Score decay, breakout detection, relative/peer-comparison scoring, model evaluation
  (precision/recall against real outcomes).** All require weeks-to-months of real historical
  snapshot data to be meaningful. Building them against zero history would just be fabricating
  precision we don't have.

## Phase 2 (candidates, not committed)

- GitHub API as a free "product/tech signal" source (stars/commits/releases velocity) for
  startups with an identifiable public org.
- Sector and investor rollup views (SQL views over `startups`/`company_snapshots`) surfaced as new
  frontend pages/sections.
- A simple company-detail view (even without full routing — e.g. a modal or query-param-based
  detail panel within the existing single page) showing the snapshot trajectory once there's
  enough history to plot.
- Score decay once there are enough weeks of `company_snapshots` for it to be meaningful.

## Phase 3 (only if the budget/business conversation happens)

Everything in the original spec that requires real money: licensed data providers, a real
backend + hosting, auth/subscriptions, an admin dashboard, "Ask Radar" natural-language search,
API/enterprise access. Not started.
