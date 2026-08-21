"""
Free, no-paid-key data sources for startup / funding news.

Each function returns a list of dicts:
  {"title": str, "summary": str, "link": str, "published": str, "source_name": str}

Everything here uses either a public RSS feed or a free/no-key public API.
Add more sources by writing a similar function and adding it to SOURCE_FUNCS.
"""

import feedparser
import requests

RSS_FEEDS = [
    ("TechCrunch - Startups", "https://techcrunch.com/category/startups/feed/"),
    ("TechCrunch - Venture", "https://techcrunch.com/category/venture/feed/"),
    ("VentureBeat", "https://venturebeat.com/feed/"),
    ("Fast Company - Tech", "https://www.fastcompany.com/technology/rss"),
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StartupTrackerBot/1.0)"}


def fetch_rss_sources():
    items = []
    for source_name, url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:25]:
                items.append({
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", entry.get("description", "")),
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "source_name": source_name,
                })
        except Exception as e:
            print(f"[warn] Failed to fetch {source_name}: {e}")
    return items


def fetch_hn_funding_stories():
    """Hacker News via the free Algolia HN Search API (no key required)."""
    items = []
    queries = ["raises seed", "raises Series A", "raises funding", "launches startup"]
    for q in queries:
        try:
            resp = requests.get(
                "https://hn.algolia.com/api/v1/search_by_date",
                params={"query": q, "tags": "story", "hitsPerPage": 20},
                headers=HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            for hit in resp.json().get("hits", []):
                title = hit.get("title") or ""
                url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                if not title:
                    continue
                items.append({
                    "title": title,
                    "summary": title,
                    "link": url,
                    "published": hit.get("created_at", ""),
                    "source_name": "Hacker News",
                })
        except Exception as e:
            print(f"[warn] Failed to fetch HN query '{q}': {e}")
    return items


SOURCE_FUNCS = [fetch_rss_sources, fetch_hn_funding_stories]


def fetch_all_articles():
    all_items = []
    for fn in SOURCE_FUNCS:
        all_items.extend(fn())

    # Dedupe by link
    seen = set()
    deduped = []
    for item in all_items:
        link = item.get("link")
        if link and link not in seen:
            seen.add(link)
            deduped.append(item)
    return deduped
