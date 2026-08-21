# Startup Signal — weekly top-100 startup tracker for terralytixai.com

An automated pipeline that scans public news/tech feeds weekly, uses Google's free-tier
Gemini API to pull out structured startup data (industry, location, founding year, founder,
funding stage/amount, investors, unique moat, contact/hiring signals), stores it in Supabase,
and displays it on a live top-100 table page you upload once to your site.

Scope: AI, Technology, FinTech, PropTech, and Real Estate startups, globally, with dedicated
coverage of the USA, India (including Chennai specifically), and rest-of-world news sources.

## How it works

```
GitHub Actions (weekly cron, Mondays 06:00 UTC)          GitHub Actions (daily cron, 12:00 UTC)
        │                                                        │
        ▼                                                        ▼
scraper/main.py                                          scraper/news_job.py
  ├─ sources.py     → RSS feeds + Hacker News (free)        (reuses sources.py, no LLM call --
  ├─ extractor.py   → phase 1: Gemini extracts               just raw headlines, cheap enough
  │                    company/idea/industry/location/       to run daily)
  │                    funding/investors                           │
  ├─ enrich.py      → phase 2: finds each startup's own            ▼
  │                    website, pulls founder LinkedIn /    Supabase "news_feed" table
  │                    contact email / hiring signal via    (upsert by link, pruned after 14 days)
  │                    regex (free), then one batched
  │                    Gemini call for founder name /
  │                    year founded / unique moat
        │
        ▼
Supabase "startups" table (upsert, deduped by company_name)
        │
        ▼
frontend/index.html  →  fetches top 100 (by disclosed funding) + the news sidebar,
                         both directly from Supabase client-side
        │
        ▼
you upload this ONE file, once, to terralytixai.com/startups/index.html
```

The key thing: **you only upload the frontend file once.** It queries Supabase live on
every page load, so as the weekly and daily jobs update their tables, the page just shows
the new data — no re-upload needed. The main startup table refreshes weekly (it does the
expensive Gemini extraction + website enrichment); the news sidebar refreshes daily (it's
just headlines, no LLM involved, so daily is cheap).

## Setup steps

### 1. Supabase

1. Open your Supabase project → SQL Editor → New query.
2. Paste the contents of `supabase_schema.sql` and run it.
   (Safe to run even if you already created the old version of this table — it upgrades
   the existing table in place instead of dropping data.)
3. Go to Project Settings → API and copy:
   - `Project URL` → this is `SUPABASE_URL`
   - `anon public` key → this is `SUPABASE_ANON_KEY` (safe to expose publicly, read-only)
   - `service_role` key → this is `SUPABASE_SERVICE_KEY` (**secret**, gives full write access — never put this in the frontend)

### 2. Get a free Gemini API key

1. Go to https://aistudio.google.com/app/apikey and sign up (free).
2. Create an API key → this is `GEMINI_API_KEY`.
   The free tier comfortably covers one weekly run over ~150–300 articles plus the
   per-company enrichment pass; if you ever hit a rate limit, the job logs a warning,
   retries with backoff, and moves on — nothing breaks the whole run.

### 3. Push this code to GitHub

1. Create a new GitHub repo (can be private).
2. Push everything in this folder to it.
3. Go to repo → Settings → Secrets and variables → Actions → New repository secret, and add:
   - `GEMINI_API_KEY`
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`

That's it — `.github/workflows/weekly.yml` will now run automatically every Monday at
06:00 UTC, and `.github/workflows/daily_news.yml` (same secrets, no `GEMINI_API_KEY`
needed) runs every day at 12:00 UTC to refresh the news sidebar. You can also trigger
either one manually anytime from the repo's "Actions" tab → pick the workflow → "Run
workflow", which is the fastest way to test them end-to-end the first time.

### 4. Publish the frontend

1. Open `frontend/index.html` and fill in near the bottom of the file:
   ```js
   const SUPABASE_URL = "https://xxxxx.supabase.co";
   const SUPABASE_ANON_KEY = "eyJ...";      // the anon public key, NOT service_role
   ```
2. Upload `frontend/index.html` **and** the `frontend/images/` folder together via FTP/cPanel
   to wherever `www.terralytixai.com/startups` should point (e.g. `startups/index.html` and
   `startups/images/...`) — the logo files are referenced with a relative `images/` path, so
   they need to keep sitting next to the HTML file.
3. Done — visit the page any time and it'll show whatever is currently in the table.

## What gets extracted, and how reliable each field is

| Field | Source | Reliability |
|---|---|---|
| Company name, business idea, industry | News article, via Gemini | Good — this is what press coverage is built around |
| Funding stage / amount / investors | News article, via Gemini | Good when a round was actually reported; null otherwise |
| Location, region (USA/India/Chennai/Rest of World) | News article + rule-based classification | Good |
| Year founded | Article or company's own About page, via Gemini | Partial — many companies don't state this anywhere public |
| Founder name | Article or company's own About/Team page, via Gemini | Partial |
| Founder LinkedIn | Regex scan of the company's own website | **Often null** — many sites don't link a personal LinkedIn |
| Contact email | Regex scan for `mailto:`/email patterns on the company's own website | **Often null** — most startups don't publish one |
| Hiring status | Detects a `/careers` or `/jobs` page, or "we're hiring" language | Best-effort; "Unknown" is common, not a bug |
| Signal (★) | Rule-based score (1-5) computed in `compute_signal_score()` from funding stage + disclosed amount + whether investors were named | **Not a valuation or a prediction** — see below |

This is news-driven and website-driven, not a Crunchbase/LinkedIn-grade database — treat it
as a discovery feed, and spot-check anything before treating it as fact.

### About the "Signal" star rating

There's no free source of real startup valuations, so the Signal column is a transparent,
rule-based heuristic — not a valuation, and not a claim about which startups will succeed.
It's computed once per row in `scraper/extractor.py::compute_signal_score()` from three things
we actually have: funding stage (later stage scores higher), disclosed amount (larger rounds add
a point), and whether any investor was named (a small bump for early/unstated-stage rows). A
startup with an unstated funding stage gets the lowest, most conservative score — that reflects
missing information, not a negative judgment on the company.

## Notes and honest limitations

- **Data quality**: pulls from public news (TechCrunch, VentureBeat, Fast Company, YourStory,
  Inc42, Entrackr, EU-Startups, Silicon Canals, Tech in Asia, Hacker News). It will not catch
  every startup that raises money globally — only ones that got press coverage.
- **"Top 100"**: the frontend shows the top 100 rows ranked by disclosed funding amount
  (highest first, undisclosed-amount rows sorted by recency). It is not a verified,
  authoritative ranking — it's what the pipeline has found and can rank so far.
- **Free tier**: Gemini's free tier has rate limits (requests/minute and requests/day). One
  weekly run comfortably fits within it even with the two-phase extraction + enrichment.
- **Adding sources**: to add a new feed (e.g. a regional tech blog, an accelerator's news
  page), add a small fetch function in `scraper/sources.py` and follow the pattern of
  `fetch_rss_sources`.
- **Changing the schedule**: edit the `cron` line in `.github/workflows/weekly.yml`
  (it's in UTC).
- **Claude/Anthropic note**: Claude Pro (the claude.ai chat subscription) has no API access
  and can't run this job. The separate Anthropic API is metered pay-per-token — an option
  worth considering later for extraction quality, but not a free-tier fit like Gemini/Groq.

## The frontend: theme + news sidebar

- **Dark / light toggle**: the sun/moon button in the top bar switches themes instantly;
  the choice is remembered per-visitor via `localStorage` (falls back to dark on first visit
  or if storage is blocked). The logo swaps automatically too: `frontend/images/StartupRadar_logo_white_noBG.png`
  in dark mode, `frontend/images/StartupRadar_logo_blue_noBG.png` in light mode.
- **News sidebar**: reads the `news_feed` table (populated daily, see above) and shows the
  latest ~20 headlines with source + relative time, independent of the weekly startup table.

## Files

- `supabase_schema.sql` — run once in Supabase (upgrade-safe)
- `scraper/sources.py` — free RSS/API news sources (USA, India/Chennai, rest of world)
- `scraper/extractor.py` — phase 1: Gemini extraction + region/funding-amount parsing
- `scraper/enrich.py` — phase 2: company-website discovery + regex parsing + Gemini refine pass
- `scraper/main.py` — orchestrates the weekly run and upserts into `startups`
- `scraper/news_job.py` — orchestrates the daily headline refresh into `news_feed` (no LLM)
- `.github/workflows/weekly.yml` — free weekly scheduler for the startup table
- `.github/workflows/daily_news.yml` — free daily scheduler for the news sidebar
- `frontend/index.html` — the page you upload to your site
