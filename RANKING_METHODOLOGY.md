# Ranking Methodology (v2)

**Startup Radar ranks recent, publicly verifiable momentum signals. It is a discovery tool, not
investment advice or a prediction of company success.** A high momentum score means "several
verifiable things happened recently, corroborated by decent sources" — nothing more. It is not a
valuation, not a signal of future returns, and not comparable to a licensed data provider built on
proprietary financial records.

This document describes the **v2** methodology, implemented in `scraper/ranking.py`,
`scraper/component_scoring.py`, `scraper/stage_scoring.py`, `scraper/decay.py`,
`scraper/confidence.py`, and `scraper/entity_resolution.py`. It supersedes `RADAR_SCORE_SPEC.md`
(the v1 formula), which is kept in the repo as a historical record of what shipped before this
migration — see that file's own note at the top.

## Scope

Up to 150 companies total, as three **independently ranked** regional lists of up to 50 each:

- Top 50 United States
- Top 50 India
- Top 50 Rest of World

150 is a target, not a guarantee. **If fewer than 50 companies in a region meet the eligibility
bar, the list shows fewer than 50** — nothing is invented to fill a quota. The frontend's empty
state and the quality report's `shortfall_by_region` both surface this honestly.

## Eligibility (`ranking.is_eligible`)

A company enters a regional list only if **all** of the following hold:

1. Its headquarters region is verified as US, India, or Rest of World (not merely guessed).
2. Its primary domain has passed the verified homepage-resolution pipeline
   (`homepage_validator.py`) with confidence ≥ 50.
3. It belongs to a supported industry (AI, Enterprise & Developer Tech, FinTech, PropTech, Real
   Estate Technology, or closely related Technology).
4. It has at least one qualifying event within the **candidate event window** (default 180 days).
5. Its data confidence is at least the **minimum ranking confidence** (default 60/100).
6. Its evidence isn't based solely on an uncorroborated Tier 3 (discovery-only) source — it needs
   either one Tier 1 (authoritative) source, or at least the **minimum corroborated sources**
   (default 1 *additional* independent source beyond the first).
7. It is not shut down, acquired, or a large public incumbent.
8. Its identity is not ambiguous or unresolved (`entity_resolution.py` didn't flag it
   `NEEDS_REVIEW` — e.g. a generic name like "Clay" or "Warp" with no domain/HQ corroboration).
9. Its data hasn't gone stale: no material fact verified within the **maximum stale period**
   (default 90 days) excludes it from ranking (a 30-day staleness *warning* is shown well before
   that, per the frontend's stale-data icon).

All of these thresholds are configurable (`ranking.EligibilityConfig`), not hardcoded constants
scattered through the codebase.

## Three independent regional pools

`ranking.rank_region` is called **once per region**, on that region's own eligible candidate list.
`rank_all_regions` never computes one global top-150 and splits it afterward — a company's rank in
India reflects only how it compares to other eligible India candidates, regardless of how many
stronger US candidates exist that week.

## Scoring components (v2, `ranking.DEFAULT_WEIGHTS_V2`)

| Component | Weight | What it measures |
|---|---|---|
| Recent verified events | 25% | Decayed strength of completed funding/acquisition/executive/hiring-growth events |
| Product & customer traction | 20% | Decayed strength of product launches, major releases, and customer wins |
| Stage-adjusted funding momentum | 15% | Latest completed round's amount, scored **relative to peers at the same stage and region** |
| Verified hiring momentum | 15% | Careers-page-verified hiring status, scaled by hiring confidence |
| Market expansion & partnerships | 10% | Decayed strength of partnership/geographic-expansion events |
| Technology differentiation / moat | 10% | Evidence-backed moat confidence (0 / "Not yet verified" until a moat-verification pass exists — see Known Limitations) |
| Independent source corroboration | 5% | How many genuinely independent sources corroborate this company's events |

Weights sum to exactly 1.0 (`ranking.validate_weights` enforces this and is called before every
scoring run). `momentum_score = round(Σ component_score × weight)`, clamped to [0, 100].

**Explicitly kept OUTSIDE the momentum score**, shown separately: data confidence, data
completeness, AI relevance, source quality, and eligibility status. A company's momentum score
says nothing about how *sure* the system is of the underlying facts — that's `data_confidence`.

## Recency decay

Event-based components apply `decayed_weight = base_value * exp(-age_days / 45)` (see
`decay.py`). A half-life of 45 days means:

| Age | Contribution |
|---|---|
| 0 days | 100% |
| ~31 days | 50% |
| 45 days | ~37% |
| 90 days | ~14% |
| 180 days | ~2% (effectively expired) |

45 was chosen so an event at the edge of the 90-day "high-impact" window still contributes a
meaningful tail (~14%), while one at the edge of the 180-day candidate window is nearly zero — old
but still-technically-eligible events don't get full weight, but aren't a hard cliff either.

## Stage-adjusted funding

Raw funding amounts are never compared directly across stages — a $6M seed round and a $60M Series
C round are not on the same scale of "impressive." `stage_scoring.stage_adjusted_funding_score`
computes a 0–100 score two ways:

- **Percentile-within-stage-and-region**, when at least 5 peer companies exist at the same
  normalized stage in the same region this run (a real distribution to compare against).
- **Documented global fallback thresholds** otherwise (a fresh region/stage cohort with only 1–2
  companies has no meaningful percentile) — see `stage_scoring.GLOBAL_STAGE_FALLBACK_THRESHOLDS_USD`.

This is exactly why **a strong seed round can outrank an ordinary late-stage round**.

## Funding semantics

Six distinct concepts are never conflated:

- **Latest completed funding round** (stage, amount, date) — only `funding_round_completed` events
  with `verification_status = "completed"` count.
- **Total disclosed funding** — sum of completed rounds, deduplicated when two sources report an
  identical (stage, amount) pair (almost certainly the same round covered twice — see
  `main_v2.py`'s documented limitation on this heuristic).
- **Valuation** — never counted as funding raised. A statement like "raised its Series B at a $500M
  valuation" is classified `valuation_report` for the valuation number, even if a completed round
  is *also* real; `currency.classify_funding_statement` independently re-derives this from the
  evidence text, so an LLM mislabeling valuation language as completed funding gets caught and
  downgraded (verified by a dedicated test).
- **Acquisition price** — classified `acquisition`, never `funding_round_completed`.
- **Proposed / rumored financing** — `status` in `proposed`/`rumored`/`abandoned` never counts
  toward the funding component, regardless of the amount mentioned.
- **A VC fund's own size / a market's TAM** — classified `fund_size`/`tam`, not attributed to the
  startup as funding it raised.

Currency: original amount and currency are stored separately from the USD conversion.
`currency.to_usd` uses documented static reference rates (`currency.REFERENCE_RATES_PER_USD`,
reviewed periodically) and stores the rate and its as-of date; if the currency isn't in that table,
`amount_usd` stays `null` rather than guessing.

## Confidence vs. completeness

Two deliberately separate numbers (`confidence.py`):

- **`data_completeness`** — what fraction of tracked fields are populated. Says nothing about
  correctness. A field holding the literal string `"Unknown"` is never counted as populated.
- **`data_confidence`** — a weighted blend of source-tier reliability (30%), corroboration (20%),
  freshness (15%), field completeness (15%), homepage/entity-resolution confidence (10%), and LLM
  extraction confidence (10%), with a flat 0.7× penalty when contradictory evidence exists for a
  field.

## Source tiers (`source_tiers.py`)

| Tier | Examples | Rule |
|---|---|---|
| 1 — Authoritative | Company's own verified domain, official investor announcement, government/regulator filing, a reputable accelerator's own controlled company profile | Sufficient alone for a material claim |
| 2 — Established journalism | TechCrunch, VentureBeat, YourStory, Inc42, EU-Startups, Bloomberg, Reuters, etc. (see `source_tiers.TIER_2_DOMAINS`) | Sufficient with at least the configured minimum corroboration |
| 3 — Discovery | Hacker News, press-release wire mirrors, blogs, unverified directories | Can discover a candidate; material facts need Tier 1 or corroboration |

An unrecognized domain defaults to Tier 3 — it must earn a higher tier explicitly, never the
reverse. Syndicated copies of the same press release (detected via canonical URL, content hash,
and same-domain-twice checks — `source_tiers.count_independent_sources`) count as **one** source,
not several.

## Deterministic tie-breaking

When two companies in the same region have equal momentum scores, `ranking.rank_region` breaks
ties, in order:

1. Higher data confidence
2. More recent verified high-impact event
3. More independent sources
4. Canonical company name (alphabetical) — the final, always-decisive tiebreaker, so ranking is
   100% deterministic given the same inputs (verified by `test_ranking.py`'s determinism test,
   which ranks the same set in two different input orders and asserts identical output).

## Entity resolution

No company is identified by name alone. Resolution order (`entity_resolution.resolve_entity`):

1. Verified registrable domain (strongest signal)
2. A recorded alias
3. Normalized name — but for names on the **ambiguous-name list** (`Clay`, `Warp`, `Preview`, and
   other short/generic/dictionary-word company names), name similarity alone is **never**
   sufficient; a domain match or corroborating headquarters + description similarity is required.
4. Two or more existing companies sharing a normalized name never auto-resolve to either —
   `NEEDS_REVIEW` instead of a guess.

## Score versioning

`score_version` is stamped on every company row and every snapshot. Changing a weight, threshold,
or scoring formula should bump this string — `ranking.SCORE_VERSION` is currently `"v2"`. Old
snapshots keep their original version so historical trends remain interpretable even after a
methodology change.

## Known limitations

- **Moat verification is not yet implemented.** Every company currently gets
  `moat_summary = "Not yet verified"`, `moat_confidence = 0` — an honest placeholder, not a
  fabricated moat, but it means the moat component of every company's score is currently 0.
- **Headquarters structured fields (city/state/country) are not yet geocoded** from the LLM's
  free-text location mention; `region_bucket` currently reuses v1's keyword-based classifier
  (`extractor.classify_region`), which is a real regression from "require an explicit source for
  headquarters" and should be replaced with a verified geocoding/lookup step.
- **Stage-adjusted funding peer pools are intra-run only** — percentile scoring only activates once
  a *single run* surfaces ≥5 peers at the same stage+region; it doesn't yet query the full
  historical `companies` table for a larger peer pool.
- **This pipeline has not yet been run against live Gemini/Groq/Supabase** as of this writing — see
  the PR description for exactly what's unit/integration-tested (with every I/O boundary mocked)
  versus what still needs a live dry run before the scheduled workflow is switched to it.
- **News-driven discovery, not a licensed database.** A company with zero qualifying press coverage
  in the configured windows will not appear, regardless of its real-world momentum.

## Why this is not investment advice

Momentum here means "recent, evidence-backed, publicly reported activity" — it does not measure
product quality, market size, team strength, competitive dynamics, burn rate, runway, or any of
the dozens of other factors that actually determine whether a startup succeeds. Two companies with
identical momentum scores can have wildly different outcomes. Treat this list as a **discovery
feed** — a reason to look closer, never a substitute for due diligence.
