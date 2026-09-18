> **Current status of this repo:** Step 1 (database migration) and Step 4 (scheduled workflow
> now runs `main_v2.py`) below are **done**. Step 3 (frontend cutover) has **not** happened yet —
> `frontend/index.html` still reads v1's `startups` table, deliberately, until the v2 pipeline has
> a few successful scheduled runs to look at. `ranking_refresh_v2.yml` (the originally-documented
> manual-test-only workflow) was removed once the schedule switched over to it, since
> `ranking_refresh.yml` already supports `workflow_dispatch` for on-demand runs — keeping both
> would have meant two workflows doing the same job, the same duplication problem this migration
> already fixed once for the old dueling deploy workflows.

# Migrating to StartupRadar v2

This is the exact sequence for moving from the v1 pipeline (`scraper/main.py`, the `startups`
table, `frontend/index.html`) to v2 (`scraper/main_v2.py`, the `companies`+`company_events` model,
`frontend/index_v2.html`) **without any downtime and without a moment where the live site shows
nothing**.

Read `RANKING_METHODOLOGY.md` first if you haven't — it explains *what* changed; this document is
only the *how*.

## Before you start

- This entire migration lives on the `claude/startup-radar-v2` branch until you decide to merge it.
  Nothing here has been applied to your production Supabase project or deployed to
  terralytixai.com by this work.
- You'll need: access to your Supabase project's SQL editor, and (only for step 3) your existing
  `GEMINI_API_KEY` / `GROQ_API_KEY` / `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` GitHub secrets — no
  new secrets are required for v2.

## Step 1 — Apply the database migration

1. Supabase Dashboard → SQL Editor → New query.
2. Paste the full contents of `supabase_migration_v2.sql` and run it.
3. It is idempotent — safe to re-run if anything errors partway through a first attempt. It does
   **not** touch `startups`, `company_snapshots`' existing rows, or `scan_log` destructively; it
   only adds new tables/columns and backfills `companies` from `startups`.
4. Confirm the backfill worked:
   ```sql
   select count(*) from companies;
   select count(*) from v_regional_rankings;  -- expected: 0 right now, see below
   ```
   `companies` should have roughly as many rows as `startups`. `v_regional_rankings` will be
   **empty** at this point — that's correct: backfilled rows start at `data_confidence = 0` and
   `regional_rank = null` (not eligible for ranking) until the v2 pipeline actually verifies them.
   This is what "a backfilled row existing is not the same claim as it being verified" means
   concretely.
5. **At this point, `frontend/index.html` (the live site) is completely unaffected** — it still
   reads from `startups`, which this migration never touched.

### Rollback for step 1

Nothing here can lose v1 data (no `drop table`, no destructive `alter`), so "rollback" just means
removing the new objects if you decide not to proceed:

```sql
drop view if exists v_regional_rankings;
drop table if exists company_sources;
drop table if exists company_events;
drop table if exists company_aliases;
drop table if exists companies cascade;  -- cascade needed: company_snapshots.company_id references it
drop table if exists scan_runs;

alter table company_snapshots drop column if exists company_id;
alter table company_snapshots drop column if exists region_bucket;
alter table company_snapshots drop column if exists rank;
alter table company_snapshots drop column if exists regional_rank;
alter table company_snapshots drop column if exists score_version;
alter table company_snapshots drop column if exists component_scores;
alter table company_snapshots drop column if exists verified_open_job_count;
alter table company_snapshots drop column if exists total_verified_funding_usd;
alter table company_snapshots drop column if exists data_completeness;
```

`startups`, the pre-existing `company_snapshots` rows/columns, and `scan_log` are all still
exactly as they were.

## Step 2 — Verify the v2 pipeline's results (does not affect production)

In this repo, the scheduled workflow already runs `main_v2.py` (see the status note above), so
this step is about **checking** its runs rather than triggering a one-off test — though you can
still force an extra run anytime via GitHub → Actions → "Ranking Refresh" → **Run workflow**
(`workflow_dispatch` works independent of the Mon/Wed/Fri schedule). Either way, it only writes to
the *new* tables — it never touches `startups`, so v1/the live site are unaffected regardless.

1. After a scheduled or manual run, check its step summary. It should show `should_publish=True`
   (or `False` with a clear reason — see "Reading a failed run" below).
2. Query the result:
   ```sql
   select region_bucket, count(*) from v_regional_rankings group by 1;
   select * from scan_runs order by started_at desc limit 1;
   ```
3. Check back after each of the next few scheduled runs (Mon/Wed/Fri) until you're comfortable
   with the quality of what it's finding. Every run's quality report is preserved in
   `scan_runs.quality_report` for comparison across runs.

### Reading a failed/low-quality run

If `should_publish=False`, the run still recorded itself in `scan_runs` (with `run_status =
success_with_warnings`) but **left whatever ranking was already published untouched** — company
field-level data (homepage, hiring status, etc.) still gets verified and updated where the
per-field preservation rules allow it, but `regional_rank`/`momentum_score` are not overwritten
with a worse result. Check `scan_runs.quality_report.threshold_failures` for exactly why.

## Step 3 — Cut the frontend over (only after Step 2 looks good)

Once `v_regional_rankings` has a reasonable number of companies per region you're happy with:

```bash
git mv frontend/index.html frontend/index_v1_backup.html   # keep it, don't delete it
git mv frontend/index_v2.html frontend/index.html
git commit -m "Cut frontend over to v2 (regional rankings)"
```

Pushing this to `main` triggers `deploy_frontend.yml` as usual and deploys the new experience.
`frontend/index_v1_backup.html` stays in the repo (not deployed — `deploy_frontend.yml` only
uploads whatever is at `frontend/index.html` plus `frontend/images/`) as an easy rollback: to
revert, just swap the two filenames back and push again.

## Step 4 — Switch the scheduled ranking refresh to v2 (**done** in this repo)

`.github/workflows/ranking_refresh.yml` already runs `scraper/main_v2.py` on the Monday/Wednesday/
Friday 06:00 UTC schedule — this step was done ahead of Step 3 in this repo's actual history (a
deliberate choice: it only affects the new tables, not the live site, so there's no reason to wait
for the frontend cutover first). The workflow exits non-zero (shows red, notifies the repo owner)
whenever a run's quality report fails the launch thresholds, per "fail when quality thresholds are
breached" — that is a *quality-gate decline*, not a crash: all the run's safe writes (field-level
verification, `scan_runs`, the quality report) still happened; it just means `regional_rank`/
`momentum_score` weren't overwritten with a worse result. Check the run's step summary or
`scan_runs.quality_report.threshold_failures` before assuming something is broken.

If you ever want to revert this specific step without touching anything else, edit
`ranking_refresh.yml`'s `run:` line back to `python main.py`.

## Step 5 — Retire v1 (optional, do this later, not as part of the cutover)

Once you're confident in v2 (a few weeks of good runs), you can:
- Stop reading `startups`/`score_weights` anywhere (nothing in v2 does).
- Leave `startups` and the old `company_snapshots` rows in place as historical record — they cost
  nothing to keep and this migration was explicitly designed never to need to delete them.

## FAQ

**Can I run v1 and v2 side by side indefinitely?** Yes. They write to entirely separate tables
(`startups` vs. `companies`/`company_events`/etc.) and nothing in this migration makes v1 stop
working. The only shared table is `company_snapshots`, which is extended additively (new nullable
columns) — v1's existing writes to it are unaffected.

**What if I want to go back to v1 after switching the frontend?** Swap
`frontend/index.html`/`frontend/index_v1_backup.html` back (Step 3, reversed) and push. The
`startups` table was never touched, so v1 data is exactly as fresh as it was when you left it
running (which, if you kept `ranking_refresh.yml` on v1 during the test period, means it never
stopped being current).

**Does this require new GitHub secrets?** No — `main_v2.py` uses the exact same
`GEMINI_API_KEY`/`GROQ_API_KEY`/`SUPABASE_URL`/`SUPABASE_SERVICE_KEY` secrets as `main.py`.
