"""
Phase 2: for each candidate startup from phase 1, try to find its own website
and pull founder / contact / hiring signals from it.

Email, LinkedIn, and hiring-page detection are done with plain regex against
fetched pages -- free, no LLM tokens spent. Founder name, year founded, and a
one-line "unique moat" summary need judgement, so those go through one batched
LLM call (Gemini primary, Groq fallback -- see llm.py) across all enriched
companies.

Many startups simply don't publish a public email or LinkedIn link anywhere,
even on their own site -- those fields will legitimately stay null, and that's
expected rather than a bug.
"""

import json
import re
import requests

from gemini_client import clean_json
from llm import call_llm

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StartupTrackerBot/1.0)"}

DENYLIST_DOMAINS = {
    "twitter.com", "x.com", "linkedin.com", "facebook.com", "instagram.com",
    "youtube.com", "medium.com", "crunchbase.com", "techcrunch.com",
    "venturebeat.com", "fastcompany.com", "yourstory.com", "inc42.com",
    "entrackr.com", "eu-startups.com", "siliconcanals.com", "techinasia.com",
    "prnewswire.com", "businesswire.com", "news.ycombinator.com", "github.com",
    "wikipedia.org", "apple.com", "google.com", "play.google.com", "apps.apple.com",
}

HIRING_KEYWORDS = [
    "we're hiring", "were hiring", "open positions", "join our team",
    "open roles", "current openings", "we are hiring",
]


def _extract_candidate_links(html):
    if not html:
        return []
    urls = re.findall(r'href=["\'](https?://[^"\']+)["\']', html)
    candidates = []
    for u in urls:
        m = re.match(r'https?://(?:www\.)?([^/]+)', u)
        if not m:
            continue
        domain = m.group(1).lower()
        if any(domain == d or domain.endswith("." + d) for d in DENYLIST_DOMAINS):
            continue
        candidates.append(u.split("?")[0])
    return candidates


def _fetch(url, timeout=10):
    if not url:
        return None
    try:
        r = requests.get(url, timeout=timeout, headers=HEADERS)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return None


def _guess_homepage(company_name, rss_summary_html, article_link):
    for html in (rss_summary_html, _fetch(article_link)):
        candidates = _extract_candidate_links(html)
        if candidates:
            return candidates[0]

    slug = re.sub(r'[^a-z0-9]', '', (company_name or "").lower())
    if not slug:
        return None
    for tld in ("com", "io", "ai"):
        url = f"https://www.{slug}.{tld}"
        try:
            r = requests.head(url, timeout=6, allow_redirects=True, headers=HEADERS)
            if r.status_code < 400:
                return url
        except Exception:
            continue
    return None


def _find_linkedin(html):
    m = re.search(r'https?://(?:www\.)?linkedin\.com/in/[\w\-/]+', html or "")
    return m.group(0) if m else None


def _find_email(html):
    m = re.search(r'mailto:([\w.\-+]+@[\w\-]+\.[\w.\-]+)', html or "", re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'\b([\w.\-+]+@[\w\-]+\.[a-z]{2,})\b', html or "", re.IGNORECASE)
    return m.group(1) if m else None


def _find_hiring(combined_text_lower, homepage):
    if any(k in combined_text_lower for k in HIRING_KEYWORDS):
        return "Likely hiring"
    for path in ("/careers", "/jobs"):
        if _fetch(homepage.rstrip("/") + path):
            return "Likely hiring"
    return "Unknown"


def _strip_tags(html):
    text = re.sub(r'<script[\s\S]*?</script>', ' ', html or "", flags=re.IGNORECASE)
    text = re.sub(r'<style[\s\S]*?</style>', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _enrich_one(company_name, source_url, rss_summary_html):
    homepage = _guess_homepage(company_name, rss_summary_html, source_url)
    if not homepage:
        return {"homepage": None, "founder_linkedin": None, "contact_email": None,
                "hiring_status": "Unknown", "about_text": ""}

    home_html = _fetch(homepage) or ""
    about_html = _fetch(homepage.rstrip("/") + "/about") or _fetch(homepage.rstrip("/") + "/about-us") or ""
    team_html = _fetch(homepage.rstrip("/") + "/team") or ""
    combined = " ".join([home_html, about_html, team_html])

    return {
        "homepage": homepage,
        "founder_linkedin": _find_linkedin(combined),
        "contact_email": _find_email(combined),
        "hiring_status": _find_hiring(combined.lower(), homepage),
        "about_text": _strip_tags(combined)[:3000],
    }


REFINE_SYSTEM_PROMPT = """For each numbered company below, you're given its one-line business
description and (if available) raw text scraped from its own website. Using ONLY the given text,
extract:

{
  "founder_name": string or null,   // founder(s)/CEO name if stated, comma-separated if multiple
  "year_founded": string or null,   // 4-digit year, only if explicitly stated or unambiguous
  "unique_moat": string or null     // one sentence on what differentiates this company; null if the text gives no real basis for one
}

Never invent facts not present in the given text. Return ONLY a JSON array, same order as input,
one object per company, no markdown fences."""


def _refine_with_llm(records, batch_size=8):
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        lines = []
        for idx, r in enumerate(batch):
            lines.append(
                f"{idx + 1}. COMPANY: {r.get('company_name')}\n"
                f"   DESCRIPTION: {r.get('business_idea') or ''}\n"
                f"   WEBSITE TEXT: {r.get('_about_text') or '(none found)'}"
            )
        user_prompt = "Companies:\n\n" + "\n\n".join(lines)

        content, _provider = call_llm(REFINE_SYSTEM_PROMPT, user_prompt, max_output_tokens=2000)
        if not content:
            continue
        try:
            parsed = json.loads(clean_json(content))
        except json.JSONDecodeError:
            print("[warn] Could not parse enrichment JSON, skipping batch")
            continue
        if len(parsed) != len(batch):
            print("[warn] Enrichment batch size mismatch, skipping batch")
            continue
        for r, extra in zip(batch, parsed):
            r["founder_name"] = extra.get("founder_name")
            r["year_founded"] = extra.get("year_founded")
            r["unique_moat"] = extra.get("unique_moat")


def enrich_all(records, articles):
    article_by_link = {a["link"]: a for a in articles if a.get("link")}

    for r in records:
        art = article_by_link.get(r.get("source_url"))
        summary_html = art["summary"] if art else ""
        info = _enrich_one(r.get("company_name"), r.get("source_url"), summary_html)
        r["homepage"] = info["homepage"]
        r["founder_linkedin"] = info["founder_linkedin"]
        r["contact_email"] = info["contact_email"]
        r["hiring_status"] = info["hiring_status"]
        r["_about_text"] = info["about_text"]

    _refine_with_llm(records)

    for r in records:
        r.pop("_about_text", None)

    return records
