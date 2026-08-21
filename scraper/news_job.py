"""
Daily news-sidebar refresh: pulls the same free RSS/HN sources used by the
weekly startup scan, but does NOT call any LLM -- it just stores raw
headlines (title, link, source, published date) into the "news_feed" table.

This runs daily precisely because it's cheap: no extraction, no enrichment,
just fetch + upsert + prune. The weekly deep-extraction pipeline (main.py)
is unaffected and keeps running on its own schedule.
"""

import os
from datetime import datetime, timedelta, timezone

import requests

from sources import fetch_all_articles

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

MAX_ITEMS = 60
KEEP_DAYS = 14


def upsert_news(items):
    if not items:
        print("No news items to upsert.")
        return 0

    url = f"{SUPABASE_URL}/rest/v1/news_feed"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    params = {"on_conflict": "link"}

    cleaned = [{
        "title": it["title"],
        "link": it["link"],
        "source_name": it.get("source_name"),
        "published": it.get("published_iso") or it.get("published") or None,
    } for it in items if it.get("title") and it.get("link")]

    if not cleaned:
        return 0

    resp = requests.post(url, headers=headers, params=params, json=cleaned, timeout=30)
    if resp.status_code not in (200, 201, 204):
        print(f"[error] Supabase news upsert failed: {resp.status_code} {resp.text}")
        resp.raise_for_status()
    return len(cleaned)


def prune_old_news():
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
    url = f"{SUPABASE_URL}/rest/v1/news_feed"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    }
    try:
        requests.delete(url, headers=headers, params={"fetched_at": f"lt.{cutoff}"}, timeout=15)
    except Exception as e:
        print(f"[warn] Failed to prune old news: {e}")


def main():
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")

    print("Fetching latest news...")
    articles = fetch_all_articles()
    articles.sort(key=lambda a: a.get("published_iso") or "", reverse=True)
    top = articles[:MAX_ITEMS]

    written = upsert_news(top)
    print(f"Upserted {written} news items.")

    prune_old_news()


if __name__ == "__main__":
    main()
