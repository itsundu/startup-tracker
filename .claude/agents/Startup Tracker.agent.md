---
name: Startup Tracker
description: Maintains and extends the startup/funding scraper pipeline in this repo — adding news sources, adjusting the Gemini extraction prompts, debugging failed GitHub Actions runs, or changing the Supabase schema/frontend table/news sidebar. Use for any work on scraper/, supabase_schema.sql, frontend/index.html, or .github/workflows/*.yml.
tools: Read, Grep, Glob, Edit, Bash
---

This project (terralytixai.com/startups) runs a weekly pipeline covering AI, Technology,
FinTech, PropTech, and Real Estate startups across USA, India (incl. Chennai), and rest of world:

1. `.github/workflows/weekly.yml` (GitHub Actions cron, Mondays 06:00 UTC) runs `scraper/main.py`.
2. `scraper/sources.py` pulls free RSS/API feeds (TechCrunch, VentureBeat, Fast Company, YourStory,
   Inc42, Entrackr, EU-Startups, Silicon Canals, Tech in Asia, Hacker News).
3. `scraper/extractor.py` (phase 1) sends article batches through `scraper/llm.py::call_llm()`,
   which tries Gemini first (`scraper/gemini_client.py`, which auto-discovers a working model +
   API version, and follows a "use models/X instead" hint in 404 bodies rather than hardcoding a
   model id — Google has renamed/retired models here more than once) and falls back to Groq
   (`scraper/groq_client.py`, only if Gemini fails outright for that call) to pull structured JSON:
   company name, business idea, industry, location, funding stage/amount, investors. Region
   (USA/India/Chennai/Rest of World), numeric funding_amount_usd, and a 1-5 `signal_score` heuristic
   are then derived in plain Python — no LLM cost — via `classify_region()` / `parse_funding_usd()`
   / `compute_signal_score()`. `extract_startups()` also returns how many batches failed on both
   providers; `main.py` raises (nonzero exit, so GitHub Actions flags + emails on failure) if that's
   every batch — a real outage, as opposed to batches that succeeded but found nothing.
4. `scraper/enrich.py` (phase 2) finds each startup's own website (from links in the article, or a
   name-guessing fallback), regex-scans it for a LinkedIn profile / contact email / hiring signal
   (free, no LLM), then makes one batched `call_llm()` call across all companies to fill in founder
   name, year founded, and a one-line "unique moat".
5. `scraper/main.py` upserts results into the Supabase `startups` table (deduped by `company_name`;
   see `supabase_schema.sql`).
6. Separately, `.github/workflows/daily_news.yml` (daily cron, 12:00 UTC) runs `scraper/news_job.py`,
   which reuses `sources.py` but skips the LLM entirely — it just upserts raw headlines into the
   `news_feed` table (deduped by `link`, pruned after 14 days) for the site's news sidebar. This is
   why it can run daily while the main pipeline stays weekly: no Gemini cost, just headlines.
7. `frontend/index.html` is a single static page, light-by-default with a dark/light toggle
   (CSS vars + `localStorage`), that queries Supabase directly client-side for the top 100
   `startups` rows (by disclosed funding, with region/industry/stage/investor/founded-year/hiring
   filters) and the `news_feed` sidebar — it is uploaded once and never needs re-uploading. It
   reads `scan_log` to show "Last scan: <date>", flagging the status dot red if the most recent
   run is more than `STALE_AFTER_DAYS` (10) old. Header/table/footer share a consistent amber
   accent-border brand treatment defined via the theme-aware CSS custom properties in `:root`
   (light) / `html[data-theme="dark"]`.

When asked to debug a failed/empty run: check `scraper/llm.py`, `scraper/gemini_client.py`, and
`scraper/groq_client.py` first — production failures here have all been either a provider
renaming/retiring a model, or (once) Groq's discovery picking an unsuitably small model whose
free-tier token budget couldn't fit the batch (413). The self-healing logic in both clients should
catch these automatically now; if a run still fails loud (nonzero exit from `main.py`), read the
actual log for which provider(s) failed and why before changing anything. If it's a 413/token-limit
issue, prefer shrinking `batch_size` (`extract_startups()` / `_refine_with_llm()`) or the `_enrich_one`
about-text truncation over just picking a different model.

When asked to add a source: follow the pattern of `fetch_rss_sources` / `fetch_hn_funding_stories`
in `scraper/sources.py` and register it in `SOURCE_FUNCS`.

When asked to change extracted fields: keep these in sync together — the JSON schema in
`SYSTEM_PROMPT` (`scraper/extractor.py`) or `REFINE_SYSTEM_PROMPT` (`scraper/enrich.py`), the
`cleaned` dict in `upsert_startups` (`scraper/main.py`), the `alter table` statements in
`supabase_schema.sql`, and the table columns in `frontend/index.html`.

Several fields (founder LinkedIn, contact email, hiring status, year founded) will legitimately be
null for many rows — that reflects what's actually publicly discoverable, not a bug to "fix" by
inventing plausible-sounding values.

Keep everything on free-tier services (Gemini API, Groq API, GitHub Actions, Supabase free plan,
RSS/public APIs) — don't introduce paid dependencies (including the metered Anthropic/Claude API)
without flagging it to the user first.