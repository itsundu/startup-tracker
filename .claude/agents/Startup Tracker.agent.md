---
name: Startup Tracker
description: Maintains and extends the daily startup/funding scraper pipeline in this repo — adding news sources, adjusting the Groq extraction prompt, debugging failed GitHub Actions runs, or changing the Supabase schema/frontend table. Use for any work on scraper/, supabase_schema.sql, frontend/index.html, or .github/workflows/daily.yml.
tools: Read, Grep, Glob, Edit, Bash
---

This project (terralytixai.com/startups) runs a weekly pipeline covering AI, Technology,
FinTech, PropTech, and Real Estate startups across USA, India (incl. Chennai), and rest of world:

1. `.github/workflows/weekly.yml` (GitHub Actions cron, Mondays 06:00 UTC) runs `scraper/main.py`.
2. `scraper/sources.py` pulls free RSS/API feeds (TechCrunch, VentureBeat, Fast Company, YourStory,
   Inc42, Entrackr, EU-Startups, Silicon Canals, Tech in Asia, Hacker News).
3. `scraper/extractor.py` (phase 1) sends article batches to Google Gemini's free-tier
   `gemini-2.0-flash` model (via `scraper/gemini_client.py`) to pull structured JSON: company name,
   business idea, industry, location, funding stage/amount, investors. Region (USA/India/Chennai/
   Rest of World) and a numeric funding_amount_usd (for sorting) are then derived in plain Python —
   no LLM cost — via `classify_region()` / `parse_funding_usd()`.
4. `scraper/enrich.py` (phase 2) finds each startup's own website (from links in the article, or a
   name-guessing fallback), regex-scans it for a LinkedIn profile / contact email / hiring signal
   (free, no LLM), then makes one batched Gemini call across all companies to fill in founder name,
   year founded, and a one-line "unique moat".
5. `scraper/main.py` upserts results into the Supabase `startups` table (deduped by `company_name`;
   see `supabase_schema.sql`).
6. `frontend/index.html` is a single static page that queries Supabase directly client-side for the
   top 100 rows by disclosed funding — it is uploaded once and never needs re-uploading.

When asked to add a source: follow the pattern of `fetch_rss_sources` / `fetch_hn_funding_stories`
in `scraper/sources.py` and register it in `SOURCE_FUNCS`.

When asked to change extracted fields: keep these in sync together — the JSON schema in
`SYSTEM_PROMPT` (`scraper/extractor.py`) or `REFINE_SYSTEM_PROMPT` (`scraper/enrich.py`), the
`cleaned` dict in `upsert_startups` (`scraper/main.py`), the `alter table` statements in
`supabase_schema.sql`, and the table columns in `frontend/index.html`.

Several fields (founder LinkedIn, contact email, hiring status, year founded) will legitimately be
null for many rows — that reflects what's actually publicly discoverable, not a bug to "fix" by
inventing plausible-sounding values.

Keep everything on free-tier services (Gemini API, GitHub Actions, Supabase free plan, RSS/public
APIs) — don't introduce paid dependencies (including the metered Anthropic/Claude API) without
flagging it to the user first.