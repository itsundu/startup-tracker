# Startup Signal — weekly top-100 startup tracker for terralytixai.com

An automated pipeline that scans public news/tech feeds weekly, uses Google's free-tier
Gemini API to pull out structured startup data (industry, location, founding year, founder,
funding stage/amount, investors, unique moat, contact/hiring signals), stores it in Supabase,
and displays it on a live top-100 table page you upload once to your site.

Scope: AI, Technology, FinTech, PropTech, and Real Estate startups, globally, with dedicated
coverage of the USA, India (including Chennai specifically), and rest-of-world news sources.

## How it works

```
GitHub Actions (weekly cron, Mondays 06:00 UTC)
        │
        ▼
scraper/main.py
  ├─ sources.py     → pulls articles from RSS feeds + Hacker News (all free, no key)
  ├─ extractor.py   → phase 1: sends article batches to Gemini's free API,
  │                    gets back company/idea/industry/location/funding/investors
  ├─ enrich.py      → phase 2: finds each startup's own website, pulls founder
  │                    LinkedIn / contact email / hiring signal via regex (free,
  │                    no LLM cost), then one batched Gemini call to fill in
  │                    founder name / year founded / unique moat
        │
        ▼
Supabase "startups" table (upsert, deduped by company_name)
        │
        ▼
frontend/index.html  →  fetches top 100 (by disclosed funding) directly from
                         Supabase client-side, with region/industry/stage/hiring filters
        │
        ▼
you upload this ONE file, once, to terralytixai.com/startups/index.html
```

The key thing: **you only upload the frontend file once.** It queries Supabase live on
every page load, so as the weekly job updates rows, the page just shows them — no re-upload
needed.

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
06:00 UTC. You can also trigger it manually anytime from the repo's "Actions" tab →
"Weekly Startup Scan" → "Run workflow", which is the fastest way to test it end-to-end
the first time.

### 4. Publish the frontend

1. Open `frontend/index.html` and fill in near the bottom of the file:
   ```js
   const SUPABASE_URL = "https://xxxxx.supabase.co";
   const SUPABASE_ANON_KEY = "eyJ...";      // the anon public key, NOT service_role
   ```
2. Upload that one file via FTP/cPanel to wherever `www.terralytixai.com/startups`
   should point (e.g. as `startups/index.html`).
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

This is news-driven and website-driven, not a Crunchbase/LinkedIn-grade database — treat it
as a discovery feed, and spot-check anything before treating it as fact.

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

## Files

- `supabase_schema.sql` — run once in Supabase (upgrade-safe)
- `scraper/sources.py` — free RSS/API news sources (USA, India/Chennai, rest of world)
- `scraper/extractor.py` — phase 1: Gemini extraction + region/funding-amount parsing
- `scraper/enrich.py` — phase 2: company-website discovery + regex parsing + Gemini refine pass
- `scraper/main.py` — orchestrates the run and upserts into Supabase
- `.github/workflows/weekly.yml` — the free weekly scheduler (GitHub Actions)
- `frontend/index.html` — the page you upload to your site
