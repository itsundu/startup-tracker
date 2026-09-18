"""
v2 enrichment: verified homepage resolution + verified careers-page
evaluation, replacing enrich.py's "take the first extracted link" /
"a 200 response means hiring" behavior.

Network access (requests.get) happens only in this module's default fetch
functions; homepage_validator.py and careers_validator.py themselves never
touch the network, so this is the one place production HTTP behavior lives
for v2 enrichment -- and the one place that would need a live run to fully
verify (not done in this session; see PR description).
"""

import re
from concurrent.futures import ThreadPoolExecutor

import requests

from careers_validator import evaluate_careers_page
from homepage_validator import FetchResult, pick_best_homepage

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StartupRadarBot/2.0; +https://terralytixai.com)"}
MAX_WORKERS = 10
FETCH_TIMEOUT = 8

_LINK_RE = re.compile(r'href=["\'](https?://[^"\']+)["\']')


def extract_candidate_links(html):
    """All http(s) links in `html`, query strings stripped, order
    preserved -- deliberately returns EVERY candidate (not just the first)
    so pick_best_homepage can evaluate them all."""
    if not html:
        return []
    return [u.split("?")[0] for u in _LINK_RE.findall(html)]


def _fetch(url, timeout=FETCH_TIMEOUT):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    except Exception:
        return None
    return FetchResult(
        status_code=resp.status_code,
        final_url=resp.url,
        content_type=resp.headers.get("Content-Type"),
        text=resp.text[:20000] if resp.text else "",
    )


def resolve_homepage(company_name, rss_summary_html, article_link, product_names=None):
    """Returns a homepage_validator.HomepageCandidateResult or None.
    Candidates are gathered from the RSS summary first (cheap), then --
    only if that yields nothing valid -- from the full article page (one
    extra fetch), matching enrich.py's existing cost-conscious ordering.
    Never just returns candidates[0]; see homepage_validator.pick_best_homepage.
    """
    candidates = extract_candidate_links(rss_summary_html)
    best = pick_best_homepage(candidates, company_name, product_names, fetch_fn=_fetch) if candidates else None
    if best:
        return best

    article_html = _fetch(article_link)
    article_candidates = extract_candidate_links(article_html.text if article_html else "")
    return pick_best_homepage(article_candidates, company_name, product_names, fetch_fn=_fetch) if article_candidates else None


def evaluate_hiring(homepage_url):
    """Fetches /careers and /jobs off the verified homepage and returns the
    BEST (highest-confidence) careers_validator.CareersPageResult found, or
    an "unknown" result if neither resolves. Never treats a bare HTTP 200 as
    hiring evidence -- see careers_validator.evaluate_careers_page."""
    if not homepage_url:
        return evaluate_careers_page(None)

    base = homepage_url.rstrip("/")
    results = []
    for path in ("/careers", "/jobs", "/about/careers"):
        fetched = _fetch(base + path)
        if fetched and fetched.status_code < 400:
            results.append(evaluate_careers_page(fetched.text, url=base + path))

    if not results:
        return evaluate_careers_page(None)

    status_rank = {"actively_hiring": 3, "limited_hiring": 2, "no_verified_openings": 1, "unknown": 0}
    results.sort(key=lambda r: (status_rank.get(r.status, 0), r.confidence), reverse=True)
    return results[0]


def enrich_one(company_name, source_url, rss_summary_html, product_names=None):
    homepage_result = resolve_homepage(company_name, rss_summary_html, source_url, product_names)
    homepage_url = homepage_result.url if homepage_result else None
    careers_result = evaluate_hiring(homepage_url)
    return {
        "homepage": homepage_url,
        "homepage_confidence": homepage_result.confidence if homepage_result else None,
        "primary_domain": homepage_result.registrable_domain if homepage_result else None,
        "hiring_status": careers_result.status,
        "hiring_confidence": careers_result.confidence,
        "verified_open_role_count": careers_result.open_role_count,
    }


def enrich_all(records, articles):
    """Parallel across companies (pure network I/O, no shared state) --
    same rationale as enrich.py's existing ThreadPoolExecutor use."""
    article_by_link = {a["link"]: a for a in articles if a.get("link")}

    def _run(r):
        art = article_by_link.get(r.get("source_url"))
        summary_html = art["summary"] if art else ""
        info = enrich_one(r.get("company_name"), r.get("source_url"), summary_html)
        r.update(info)
        return r

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        return list(pool.map(_run, records))
