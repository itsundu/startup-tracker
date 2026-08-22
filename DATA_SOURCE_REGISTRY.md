# Data Source Registry

Every source this project could plausibly use, its status, and why. Per the "legal/compliance"
principle in the original spec: **do not scrape or redistribute a source unless its terms
actually permit the intended use.** Sources here are marked `enabled` only if they're free, public,
and reading them the way we do (RSS/API, feeding text to an LLM, storing structured output) doesn't
require a license we don't have.

| source_id | provider | type | status | refresh | notes |
|---|---|---|---|---|---|
| techcrunch_startups | TechCrunch | public RSS | enabled | weekly | `sources.py: fetch_rss_sources` |
| techcrunch_venture | TechCrunch | public RSS | enabled | weekly | " |
| venturebeat | VentureBeat | public RSS | enabled | weekly | " |
| fastcompany_tech | Fast Company | public RSS | enabled | weekly | " |
| yourstory | YourStory | public RSS | enabled | weekly | India coverage |
| inc42 | Inc42 | public RSS | enabled | weekly | India coverage |
| entrackr | Entrackr | public RSS | enabled | weekly | India coverage |
| eu_startups | EU-Startups | public RSS | enabled | weekly | rest-of-world coverage |
| silicon_canals | Silicon Canals | public RSS | enabled | weekly | rest-of-world coverage |
| tech_in_asia | Tech in Asia | public RSS | enabled | weekly | rest-of-world coverage |
| yc_blog | Y Combinator | public RSS (`ycombinator.com/blog/rss`) | enabled | weekly | YC's own public blog/press, NOT their structured `/companies` directory (that's a proprietary dataset, not a public feed -- see below) |
| hacker_news | Hacker News (Algolia API) | public API, no key | enabled | weekly + daily | `sources.py: fetch_hn_funding_stories` |
| company_homepage | (each startup's own site) | direct HTTP fetch | enabled | weekly | `enrich.py` -- fetches homepage/about/careers pages already publicly served by the company itself |
| topstartups_io | topstartups.io | proprietary directory | **disabled** | — | Their own aggregated dataset/product, not a public feed. No published methodology; scraping it would mean cloning a competitor's proprietary data, not reading press. Not built. |
| yc_companies_directory | Y Combinator | proprietary structured directory (`/companies`) | **disabled** | — | This is YC's internal structured database (funding, batch, status per company), distinct from their public blog. Scraping/republishing it wholesale is a different act from reading press RSS and isn't something we do. |
| github_api | GitHub | free public API (token required, generous free rate limit) | **not yet built** (Phase 2) | — | Would give a real, free "product/tech activity" signal (stars/commits/releases) for startups with an identifiable public org. No licensing issue -- just not implemented yet. |
| sec_edgar | U.S. SEC | free public API | **not yet built** (Phase 2, low priority) | — | Free and legitimate, but most tracked companies are pre-IPO/private and won't have EDGAR filings. More useful once tracking later-stage/public-adjacent companies. |
| usaspending | USAspending.gov | free public API | **not yet built** (low priority) | — | Useful only for gov-contract-facing startups; a small slice of what we track. |
| data_gov_in | data.gov.in | free public datasets | **not yet built** (low priority) | — | India government open data; would need a concrete use case identified first. |
| crunchbase | Crunchbase | licensed commercial API | **disabled — requires paid license** | — | Not self-serve; would need a commercial agreement. Do not integrate without one. |
| dealroom | Dealroom | licensed commercial API | **disabled — requires paid license** | — | Same. |
| tracxn | Tracxn | licensed commercial API | **disabled — requires paid license** | — | Same. |
| similarweb | Similarweb | licensed commercial API | **disabled — requires paid license** | — | Web-traffic signal; not free. |
| builtwith | BuiltWith | licensed commercial API | **disabled — requires paid license** | — | Technology-stack signal; not free. |
| sensor_tower | Sensor Tower / data.ai | licensed commercial API | **disabled — requires paid license** | — | App-traction signal; not free. |
| greenhouse_lever_adzuna | Greenhouse / Lever / Adzuna | job board APIs | **not yet built** | — | Would improve hiring-signal precision beyond the current "does a /careers page exist" heuristic; needs per-company setup (most companies use these on their own subdomain, not a global searchable index) so this is nontrivial even though the APIs themselves are accessible. |

## Fields, per source

- **RSS/HN sources** → title, summary, link, published date, source name. Fed to the LLM
  extraction step; nothing structured is trusted from the feed itself except the link/date/source
  (those are used as-is; company/funding/etc. fields are LLM-extracted from the text).
- **company_homepage** → raw HTML of homepage/about/team/careers pages, regex-parsed for a
  LinkedIn URL, an email address, and a hiring signal; separately fed (as plain text) to the LLM
  refine pass for founder name / year founded / unique moat.

## Source priority (when two sources disagree)

Currently there's exactly one path to each field (the LLM extraction/enrichment pipeline), so
there's no real conflict-resolution problem yet — this project doesn't ingest the same field from
two independent sources. If/when a second source is added for an overlapping field (e.g. GitHub
confirming a company's status alongside press coverage), the priority order should be:

1. The company's own official material (its homepage, its own press release) — already how
   `enrich.py` works.
2. Direct news reporting of a specific event (funding, hiring) — already how `extractor.py` works.
3. Any future licensed/structured provider, if one is ever added with a valid license.
4. LLM inference beyond what's explicitly stated in 1-3 — never used to invent facts (see
   `extractor.py`/`enrich.py` prompts: "never invent facts that aren't in the text").
