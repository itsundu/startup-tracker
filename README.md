# Startup Signal — daily startup/funding tracker for terralytixai.com

An automated pipeline that scans public news/tech feeds every day, uses a free-tier LLM
(Groq) to pull out structured startup data (idea, sector, location, funding stage/amount,
investors), stores it in Supabase, and displays it on a live table page you upload once
to your site.

## How it works

```
GitHub Actions (daily cron)
        │
        ▼
scraper/main.py
  ├─ sources.py    → pulls articles from RSS feeds + Hacker News (all free, no key)
  ├─ extractor.py  → sends article batches to Groq's free API, gets back structured JSON
        │
        ▼
Supabase "startups" table (upsert, deduped by company_name)
        │
        ▼
frontend/index.html  →  fetches directly from Supabase client-side
        │
        ▼
you upload this ONE file, once, to terralytixai.com/startups/index.html
```

The key thing: **you only upload the frontend file once.** It queries Supabase live on
every page load, so as the daily job adds rows, the page just shows them — no re-upload
needed.

## Setup steps

### 1. Supabase

1. Open your Supabase project → SQL Editor → New query.
2. Paste the contents of `supabase_schema.sql` and run it.
3. Go to Project Settings → API and copy:
   - `Project URL` → this is `SUPABASE_URL`
   - `anon public` key → this is `SUPABASE_ANON_KEY` (safe to expose publicly, read-only)
   - `service_role` key → this is `SUPABASE_SERVICE_KEY` (**secret**, gives full write access — never put this in the frontend)

### 2. Get a free Groq API key

1. Go to https://console.groq.com/keys and sign up (free).
2. Create an API key → this is `GROQ_API_KEY`.
   Groq's free tier is generous and fast; if you ever hit its rate limit, the job just
   logs a warning and retries — nothing breaks. (You can also swap in Gemini's free tier
   later by editing `scraper/extractor.py`.)

### 3. Push this code to GitHub

1. Create a new GitHub repo (can be private).
2. Push everything in this folder to it.
3. Go to repo → Settings → Secrets and variables → Actions → New repository secret, and add:
   - `GROQ_API_KEY`
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`

That's it — `.github/workflows/daily.yml` will now run automatically every day at 06:00 UTC.
You can also trigger it manually anytime from the repo's "Actions" tab → "Daily Startup Scan" → "Run workflow",
which is the fastest way to test it end-to-end the first time.

### 4. Publish the frontend

1. Open `frontend/index.html` and fill in near the bottom of the file:
   ```js
   const SUPABASE_URL = "https://xxxxx.supabase.co";
   const SUPABASE_ANON_KEY = "eyJ...";      // the anon public key, NOT service_role
   ```
2. Upload that one file via FTP/cPanel to wherever `www.terralytixai.com/startups`
   should point (e.g. as `startups/index.html`).
3. Done — visit the page any time and it'll show whatever is currently in the table.

## Notes and honest limitations

- **Data quality**: this pulls from public news (TechCrunch, VentureBeat, Fast Company,
  Hacker News). It will not catch every startup that raises money globally — only ones
  that got press coverage. Funding amounts/investors are only as accurate as what the
  article states.
- **Free tier**: Groq's free tier has rate limits (requests/minute and tokens/day). One
  daily run over ~100–150 articles comfortably fits within it. If you later want denser
  coverage (e.g. Crunchbase-level completeness), that requires a paid data API.
- **Adding sources**: to add a new feed (e.g. a regional tech blog, Product Hunt, an
  accelerator's news page), add a small fetch function in `scraper/sources.py` — follow
  the pattern of `fetch_rss_sources`.
- **Changing the schedule**: edit the `cron` line in `.github/workflows/daily.yml`
  (it's in UTC).

## Files

- `supabase_schema.sql` — run once in Supabase
- `scraper/` — the daily Python job
- `.github/workflows/daily.yml` — the free daily scheduler (GitHub Actions)
- `frontend/index.html` — the page you upload to your site
