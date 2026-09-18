> **Superseded by `RANKING_METHODOLOGY.md` (v2).** This document is kept as a historical record of
> the v1 formula that shipped first and is still what `scraper/main.py` (the workflow currently on
> the scheduled `ranking_refresh.yml` cron) computes. See `RANKING_METHODOLOGY.md` for the v2
> company-plus-events model, regional independence, recency decay, stage-adjusted funding, source
> tiers, and confidence/completeness split — implemented in `scraper/main_v2.py`, not yet wired
> into the scheduled workflow (see `MIGRATION.md`).

# Momentum Score Spec (v1)

Implemented in `scraper/scoring.py`. Replaces the earlier 1-5 `signal_score` heuristic with a
0-100 `momentum_score` computed from **configurable weights** (stored in Supabase's
`score_weights` table, not hardcoded), per the "transparent, not black-box" principle.

## Honesty constraint

This is a heuristic built entirely from data we actually have — funding stage, disclosed funding
amount, whether any investor was named, and whether a hiring signal was detected. **It is not a
valuation, not a success prediction, and not comparable to a real multi-source intelligence score
built on licensed data.** A company with no disclosed funding info gets the most conservative
score for that component — that reflects missing information, not a negative judgment.

## Components (v1 — only what we can actually compute today)

| component | maps to (original spec's language) | how it's scored (0-100) | default weight |
|---|---|---|---|
| `funding_stage` | Funding Momentum | tiered by disclosed stage: unstated/none=0, pre-seed=20, seed=40, series A=60, series B=80, series C+/growth=100 | 0.35 |
| `funding_amount` | Funding Momentum / Traction | bucketed by `funding_amount_usd`: none=0, <$1M=30, $1-5M=50, $5-20M=70, $20-100M=90, ≥$100M=100 | 0.30 |
| `investor_presence` | Investor Signal | binary: any investor named=100, none=0 | 0.15 |
| `hiring_signal` | Hiring Momentum | binary: `hiring_status == "Likely hiring"`=100, else 0 | 0.20 |

Weights sum to 1.0. `momentum_score = round(Σ component_score × weight)`, clamped to [0, 100].

## Components explicitly NOT implemented yet (weight = 0, not in the table above)

Product Momentum, Market/Expansion Momentum, Technology Signal, News/Event Momentum, Founder/Team
Signal — the original spec's other named categories. Each needs a data source we don't have yet
(GitHub activity, structured news-event tracking, licensed tech/traffic data, etc.). Adding a row
to `score_weights` for one of these with a nonzero weight should only happen alongside the code
that actually computes that component — never assign weight to a signal we don't measure.

## Data confidence (0-100)

Deterministic completeness measure — **not** a claim about how "sure" we are the company will
succeed. Checks 8 key fields: `funding_stage, funding_amount, investors, founder_name,
year_founded, homepage, contact_email, hiring_status`.

```
data_confidence = round(100 × (# of the 8 fields that are non-null) / 8)
```

A company with only `funding_stage` and `homepage` populated gets confidence 25, honestly
reflecting that most fields are unknown — not because anything is "wrong," but because that's
what's actually been found.

## `score_weights` table

```sql
create table score_weights (
  component text primary key,
  weight numeric not null,
  updated_at timestamptz default now()
);
```

Seeded with the v1 defaults above. `scraper/scoring.py::fetch_weights()` reads this table at the
start of each run; if it's empty or unreachable, it falls back to the same defaults in code (so a
misconfigured table never breaks the whole run).

## What's deliberately deferred (needs real historical depth we don't have yet)

- **Score decay** (`signal_weight = base_weight × exp(-lambda × age_days)`): meaningless until
  there are actual dated events to decay — right now every field is "as of the most recent scan,"
  there's no per-event timestamp to decay against.
- **Velocity/acceleration** (score change over 7/30/90 days): requires weeks of
  `company_snapshots` history to exist first. The snapshot table starts recording now; this
  becomes possible once there's several weeks of data, not before.
- **Breakout detection, relative/peer-comparison scoring, precision/recall model evaluation**: all
  require either historical depth or a much richer signal set than four heuristic components.
  Building these against zero real history would fabricate a rigor the system doesn't have.

## `company_snapshots` table

```sql
create table company_snapshots (
  id uuid primary key default gen_random_uuid(),
  startup_id uuid not null references startups(id) on delete cascade,
  momentum_score int,
  data_confidence int,
  recorded_at timestamptz default now()
);
```

One row per company per run. This is what eventually lets a trajectory graph (61 → 68 → 73 → 82 →
94, per the original spec's example) become real — it just needs time to accumulate. Expect it to
look flat/sparse for the first several weeks; that's correct, not a bug.
