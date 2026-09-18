# Operations Runbook

Quick reference for running, debugging, and configuring StartupRadar day to day. See
`MIGRATION.md` for the one-time v1→v2 cutover sequence, and `RANKING_METHODOLOGY.md` for what the
numbers mean.

## Required secrets (GitHub → Settings → Secrets and variables → Actions)

| Secret | Used by | Notes |
|---|---|---|
| `GEMINI_API_KEY` | `ranking_refresh.yml` | Free tier at aistudio.google.com |
| `GROQ_API_KEY` | same | Fallback only; job still runs without it but loses redundancy |
| `SUPABASE_URL` | all scraper workflows | Your project's REST URL |
| `SUPABASE_SERVICE_KEY` | all scraper workflows | **Secret** — full write access, GitHub Actions only, never the browser |
| `SFTP_HOST` / `SFTP_PORT` / `SFTP_USERNAME` / `SFTP_PRIVATE_KEY` / `SFTP_REMOTE_PATH` | `deploy_frontend.yml` | Frontend deploy target |
| (frontend, hardcoded) `SUPABASE_ANON_KEY` | both frontend files | Public read-only key, safe to expose — see RLS policies in `supabase_schema.sql`/`supabase_migration_v2.sql` |

None of the values above are printed by any workflow. `ranking_refresh.yml`'s artifact upload
contains only human-readable result lines (`Run complete...`, `Regional counts...`) — never
article text, prompts, or secret values.

## Running things manually

- **Ranking refresh (v2, scheduled)**: Actions → "Ranking Refresh" → Run workflow. This is the
  live scheduled pipeline (`scraper/main_v2.py`) — running it manually just forces an extra run
  outside the Mon/Wed/Fri schedule. Requires `supabase_migration_v2.sql` to have been applied.
- **Ranking refresh (v1, no longer scheduled)**: run `python main.py` locally in `scraper/` with
  the same secrets as env vars, or temporarily edit `ranking_refresh.yml`'s `run:` line back to
  `python main.py` if you need a v1 refresh again.
- **News refresh**: Actions → "Daily News Refresh" → Run workflow.
- **Redeploy frontend without a new commit**: Actions → "Deploy Frontend" → Run workflow.
- **Run the test suite locally**:
  ```bash
  cd scraper
  pip install -r requirements-dev.txt
  python -m pytest tests/ -v
  ```

## Changing the ranking schedule

Edit the `cron:` line in `.github/workflows/ranking_refresh.yml` (currently
`"0 6 * * 1,3,5"` — Mon/Wed/Fri 06:00 UTC). Cron is always UTC regardless of where you are.

## Tuning the ranking model

Everything is configurable, none of it requires touching ranking logic itself:

- **Component weights**: `scraper/ranking.py::DEFAULT_WEIGHTS_V2`. Must sum to 1.0 —
  `validate_weights` will raise otherwise, failing the run loudly rather than silently
  mis-weighting.
- **Eligibility thresholds** (recency windows, min confidence, min sources, staleness): construct a
  custom `ranking.EligibilityConfig(...)` and pass it to `is_eligible`/wire it into `main_v2.py` in
  place of `DEFAULT_ELIGIBILITY_CONFIG`.
- **Recency decay half-life**: `scraper/decay.py::DEFAULT_HALF_LIFE_DAYS` (currently 45).
- **Stage funding thresholds** (used only when a run has <5 peers at a stage+region):
  `scraper/stage_scoring.py::GLOBAL_STAGE_FALLBACK_THRESHOLDS_USD`.
- **Source tiers**: `scraper/source_tiers.py::TIER_2_DOMAINS` / `TIER_3_DOMAINS` — add a domain here
  to change its default tier; anything unlisted defaults to Tier 3.
- **Homepage denylist**: `scraper/domain_rules.py` — `CDN_DOMAINS`, `AD_TRACKING_DOMAINS`,
  `SOCIAL_DOMAINS`, `PUBLICATION_DOMAINS`, `PLATFORM_LISTING_DOMAINS`.

Whenever you change a weight or threshold meaningfully, bump `ranking.SCORE_VERSION` so historical
snapshots stay interpretable as "scored under the old rules."

## Adding a news/data source

Add a fetch function in `scraper/sources.py` following `fetch_rss_sources`'s pattern, and add it to
`SOURCE_FUNCS`. No other changes needed — both the daily news job and the ranking refresh already
call `fetch_all_articles()`.

## Debugging a failed ranking run

1. Check the workflow's step summary first (GitHub Actions run page) — `ranking_refresh.yml`
   writes one every run. A red/failed run there can mean either a crash OR a quality-gate decline
   (see the file's own comments) — the summary and step 2 below tell you which.
2. Query the full quality report:
   ```sql
   select finished_at, run_status, regional_result_counts, quality_report
   from scan_runs order by started_at desc limit 5;
   ```
   `quality_report.threshold_failures` names exactly which launch threshold(s) failed.
3. "Every extraction batch failed on both providers" means both Gemini and Groq are unreachable —
   check API key validity/quota for both.
4. A region showing 0 or few companies isn't necessarily a bug — check
   `rejected_candidate_count_by_reason` in the quality report; it's often "no qualifying event
   within window" or "data confidence below threshold," which is the system working as intended
   (see `RANKING_METHODOLOGY.md`'s eligibility section), not a failure.

## Debugging the frontend

- Browser console errors mentioning `PGRST205` / "Could not find the table" against
  `v_regional_rankings` mean `supabase_migration_v2.sql` hasn't been applied yet to the project the
  frontend's `SUPABASE_URL` points at.
- A region tab showing "No qualified `<region>` companies yet" is the correct, intended empty
  state (not a bug) whenever that region has zero companies passing eligibility — check
  `scan_runs.regional_result_counts` to confirm whether that's expected.
- The "Last scan" indicator in the top bar reads `scan_runs` (v2) — it will say "No v2 scans yet"
  until `main_v2.py` has run successfully at least once, even if v1's `scan_log` has recent runs.

## Security notes for anyone touching this code

- Never put the service-role key anywhere except GitHub Actions secrets. It must never appear in
  frontend code, client-side JS, or a committed file.
- Any new place a source-derived URL is rendered as an `href`/`src` in the frontend must go through
  `isSafeHref`/`safeAttrsOrHide` (see `frontend/index_v2.html`) — never interpolate a URL directly.
- Any new public-facing Supabase table needs an explicit RLS policy (public SELECT only, never
  public INSERT/UPDATE/DELETE) or it defaults to fully inaccessible via the anon key, which is the
  safe default but will silently look like "no data" to the frontend if you forget the policy.
