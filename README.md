# StartupRadar — weekly top-100 startup intelligence tracker for terralytixai.com

An automated pipeline that scans public news/tech feeds weekly, uses free-tier LLMs to pull out
structured startup data (industry, location, founding year, founder, funding stage/amount,
investors, unique moat, contact/hiring signals), computes a transparent **Momentum Score**, stores
it all in Supabase, and displays it on a live top-100 table page you upload once to your site.

Scope: AI, Technology, FinTech, PropTech, and Real Estate startups, globally, with dedicated
coverage of the USA, India (including Chennai specifically), and rest-of-world news sources.

**This project was scoped down from a much larger "full startup intelligence platform" spec**
(licensed data providers, entity resolution, auth/subscriptions, a Next.js/FastAPI rewrite, etc.)
to what's actually buildable free-tier-only. See `STARTUPRADAR_ARCHITECTURE.md` for the full
reasoning, `DATA_SOURCE_REGISTRY.md` for every source considered (enabled, deferred, or
deliberately not built), and `RADAR_SCORE_SPEC.md` for the scoring model.

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
  │                    LLM call for founder name /
  │                    year founded / unique moat
  ├─ scoring.py     → Intelligence Engine: momentum_score
  │                    + data_confidence from configurable
  │                    score_weights (see RADAR_SCORE_SPEC.md)
        │
        ▼
Supabase "startups" table (upsert, deduped by company_name, real UUIDs)
        │
        ├──▶ "company_snapshots" (one row/company/run -- momentum trajectory over time)
        ▼
frontend/index.html  →  fetches top 100 (by Momentum Score) + the news sidebar,
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

### 2. Get free API keys: Gemini (primary) + Groq (fallback)

1. Gemini: go to https://aistudio.google.com/app/apikey and sign up (free) → create an
   API key → this is `GEMINI_API_KEY`. The free tier comfortably covers one weekly run.
2. Groq: go to https://console.groq.com/keys and sign up (free) → create an API key →
   this is `GROQ_API_KEY`. This one is a **fallback only** — see "Reliability" below. The
   job still runs fine without it, but a total Gemini outage would then fail loudly with
   nothing to fall back to, instead of quietly recovering.

### 3. Push this code to GitHub

1. Create a new GitHub repo (can be private).
2. Push everything in this folder to it.
3. Go to repo → Settings → Secrets and variables → Actions → New repository secret, and add:
   - `GEMINI_API_KEY`
   - `GROQ_API_KEY`
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
2. `.github/workflows/deploy_frontend.yml` auto-deploys `frontend/index.html` and
   `frontend/images/` over SFTP any time a push to `main` touches `frontend/**` — no manual
   FTP upload needed. One-time setup, in repo → Settings → Secrets and variables → Actions:
   - Generate a dedicated SSH keypair for CI (don't reuse your personal one), e.g.
     `ssh-keygen -t ed25519 -f deploy_key -C "startup-tracker-ci"`.
   - Add the **public** key (`deploy_key.pub`) to your host's SSH authorized keys (in cPanel:
     Security → SSH Access → Manage SSH Keys → Import Key, then Authorize).
   - Add repo secrets:
     - `SFTP_HOST` — your host's SSH/SFTP hostname
     - `SFTP_PORT` — usually `22`
     - `SFTP_USERNAME` — the cPanel/SSH username
     - `SFTP_PRIVATE_KEY` — contents of the **private** key (`deploy_key`)
     - `SFTP_REMOTE_PATH` — target directory, e.g. `/home/<user>/public_html/startups`
   - If your host only supports password auth instead of SSH keys, edit the workflow to swap
     `key: ${{ secrets.SFTP_PRIVATE_KEY }}` for `password: ${{ secrets.SFTP_PASSWORD }}`.
   - You can also trigger a redeploy anytime from the Actions tab → "Deploy Frontend" →
     "Run workflow", without needing a new commit.
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
| Momentum Score (0-100) | Configurable weighted formula in `scraper/scoring.py`, from funding stage + amount + investor presence + hiring signal | **Not a valuation or a prediction** — see `RADAR_SCORE_SPEC.md` |
| Data Confidence (0-100) | % of 8 key fields that are actually populated | Honest completeness measure, not a "will this company succeed" score |

This is news-driven and website-driven, not a Crunchbase/LinkedIn-grade database — treat it
as a discovery feed, and spot-check anything before treating it as fact. See
`RADAR_SCORE_SPEC.md` for the full Momentum Score formula and what it deliberately does NOT
claim (no decay, no breakout detection, no peer comparison — all need historical depth this
project doesn't have yet).

## Reliability: how production failures are handled

This pipeline has broken in production before (Google renaming/retiring Gemini models out
from under it), so three layers guard against it recurring silently:

1. **Self-healing model selection on both providers** (`scraper/gemini_client.py` and
   `scraper/groq_client.py`): rather than a hardcoded model id, each asks its provider's
   model-listing endpoint what's actually callable, and re-discovers if a call 404s.
   Gemini's client additionally tries both the `v1beta` and `v1` API versions, and — if a
   404 response names a replacement model ("...use models/X instead") — switches straight
   to it. Groq's discovery also excludes narrow/regional/audio models (it once picked a
   small Arabic-language model with a tiny free-tier token budget, which 413'd on every
   call) and prefers larger general-purpose chat models; a 413 is treated the same as a
   404 — try a different model, don't just retry the same oversized request. This alone
   fixes most future renames/bad-picks automatically, with no code change needed (this has
   already happened three times in production: twice on Gemini, once on Groq).
2. **Cross-provider fallback** (`scraper/llm.py`): every extraction/enrichment call tries
   Gemini first; if Gemini fails outright for that call (bad key, exhausted quota, every
   model 404ing, rate-limited past its retry budget), it automatically retries through Groq
   instead. Real redundancy against a full Gemini outage, not just a naming issue. There's
   also a pacing delay (4s) between batch calls in both phases, and batch sizes are kept
   modest (8 articles / 6 companies per call), since free-tier per-minute rate limits and
   token budgets are easy to trip when batches fire back-to-back or run too large.
3. **Fail loud instead of silently succeeding** (`scraper/main.py`): if literally every
   extraction batch fails on *both* providers, the job raises and exits non-zero — GitHub
   Actions marks the run red and (by default) emails the repo owner. Previously a total
   failure still "succeeded" with 0 rows upserted and no alert at all. A batch that succeeds
   but legitimately finds zero qualifying startups is *not* treated as a failure — only a
   batch where the LLM never returned usable output counts against this.

## Run time

The website-enrichment step (`scraper/enrich.py`, phase 2) runs company website
lookups **concurrently** (`ThreadPoolExecutor`, 10 workers) rather than one at a time, and
does fewer fetch attempts per company than it used to. A run with 100+ candidate startups
used to take close to two hours — nearly all of it sequential HTTP fetching of individual
company websites, several per company, each with a 10s timeout, run one company after
another. If a future change to `enrich.py` reintroduces a fully sequential loop over
companies, expect run time to blow back up the same way.

On top of that, the frontend shows **"Last scan: <date>"** read from the `scan_log` table
(not just the newest row in `startups`), and turns the status dot red with a "data may be
stale" note if the most recent run is more than 10 days old — so staleness is visible on the
site itself even if a GitHub Actions failure email gets missed.

## Notes and honest limitations

- **Data quality**: pulls from public news (TechCrunch, VentureBeat, Fast Company, YourStory,
  Inc42, Entrackr, EU-Startups, Silicon Canals, Tech in Asia, Y Combinator's own blog, Hacker
  News). It will not catch every startup that raises money globally — only ones that got press
  coverage. Deliberately does **not** scrape topstartups.io or YC's structured `/companies`
  directory — those are proprietary aggregated datasets, not public feeds, and scraping a
  competing directory product (or a company's internal database) to republish elsewhere is a
  different thing entirely from reading public press RSS.
- **"Top 100"**: the frontend shows the top 100 rows ranked by Momentum Score (funding amount,
  then recency, as tiebreakers). It is not a verified, authoritative ranking — it's what the
  pipeline has found and can score/rank so far.
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

## The frontend: design, filters, theme + news sidebar

- **Design**: a clean, light-by-default layout — sans-serif (Space Grotesk) for all UI chrome
  (nav, hero, controls, labels), monospace (IBM Plex Mono) reserved for data-dense areas (table
  cells, the ticker) where fixed-width alignment actually helps readability. Nav bar, filter
  toolbar, table, and news panel are all styled as consistent cards with the same radius/shadow.
- **Dark / light toggle**: the sun/moon button in the top bar switches themes instantly (light
  is the default); the choice is remembered per-visitor via `localStorage`. The logo swaps
  automatically too: `frontend/images/StartupRadar_logo_blue_noBG.png` in light mode,
  `frontend/images/StartupRadar_logo_white_noBG.png` in dark mode.
- **Filters**: region, industry, funding stage, **investor** (parsed from the comma-separated
  `investors` field), **founded year**, and hiring status — plus free-text search and click-to-sort
  on every column. The investor/founded-year filters and the inline company-website link (using
  the `homepage` field already collected by `enrich.py`, but not previously shown anywhere) were
  added after comparing against topstartups.io's field/filter set — its own data-sourcing
  methodology isn't publicly documented, but its field structure was a useful reference point.
- **News sidebar**: reads the `news_feed` table (populated daily, see above) and shows the
  latest ~20 headlines with source + relative time, independent of the weekly startup table.

## Files

- `STARTUPRADAR_ARCHITECTURE.md` — current architecture, what was scoped out and why, phase plan
- `DATA_SOURCE_REGISTRY.md` — every source considered: enabled, deferred (free but not built yet),
  or deliberately not built (requires a paid license)
- `RADAR_SCORE_SPEC.md` — the Momentum Score formula, components, and what it doesn't claim
- `supabase_schema.sql` — run once in Supabase (upgrade-safe)
- `scraper/sources.py` — free RSS/API news sources (USA, India/Chennai, rest of world, YC blog)
- `scraper/gemini_client.py` — Gemini API client with self-healing model/version discovery
- `scraper/groq_client.py` — Groq API client, used only as a fallback (see Reliability above)
- `scraper/llm.py` — tries Gemini then Groq for every LLM call; extractor.py/enrich.py use this
- `scraper/extractor.py` — Data Engine, phase 1: LLM extraction + region/funding-amount parsing
- `scraper/enrich.py` — Data Engine, phase 2: company-website discovery + regex + LLM refine pass
- `scraper/scoring.py` — Intelligence Engine: momentum_score + data_confidence from `score_weights`
- `scraper/main.py` — orchestrates the weekly run, upserts into `startups`, writes
  `company_snapshots`, fails loud on total outage
- `scraper/news_job.py` — orchestrates the daily headline refresh into `news_feed` (no LLM)
- `.github/workflows/weekly.yml` — free weekly scheduler for the startup table
- `.github/workflows/daily_news.yml` — free daily scheduler for the news sidebar
- `.github/workflows/deploy_frontend.yml` — auto-deploys `frontend/` to terralytixai.com over
  SFTP on every push to `main` that touches it
- `frontend/index.html` — the page that gets deployed to your site
