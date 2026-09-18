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

## Step 2 — Test the v2 pipeline manually (does not affect production)

1. In GitHub → Actions → "Ranking Refresh v2 (manual test)" → **Run workflow**. This calls
   `scraper/main_v2.py` against your real secrets, but only writes to the *new* tables — it never
   touches `startups`, so v1/the live site are still unaffected even while this runs.
2. Watch the run. It should finish with a step summary showing `should_publish=True` (or `False`
   with a clear reason — see "Reading a failed run" below).
3. Query the result:
   ```sql
   select region_bucket, count(*) from v_regional_rankings group by 1;
   select * from scan_runs order by started_at desc limit 1;
   ```
4. Repeat this a few times over a week or two (it's `workflow_dispatch`-only — it won't run on a
   schedule until you do Step 4) until you're comfortable with the quality of what it's finding.
   Every run's quality report is preserved in `scan_runs.quality_report` for comparison.

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

## Step 4 — Switch the scheduled ranking refresh to v2

Only after Steps 2–3 have been running smoothly:

1. Edit `.github/workflows/ranking_refresh.yml`: change `run: python main.py` to
   `run: python main_v2.py`.
2. Optionally delete `.github/workflows/ranking_refresh_v2.yml` at this point (its job is done), or
   keep it around as a manual re-test tool.
3. Commit and push. The Monday/Wednesday/Friday 06:00 UTC schedule is unchanged — only which script
   it runs changes.

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
