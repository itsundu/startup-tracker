import os
import requests

from sources import fetch_all_articles
from extractor import extract_startups

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")


def upsert_startups(records):
    if not records:
        print("No startup records to upsert.")
        return 0

    url = f"{SUPABASE_URL}/rest/v1/startups"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        # Upsert on the unique company_name column; update the row if it already exists
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    params = {"on_conflict": "company_name"}

    # Supabase/PostgREST wants a clean list of dicts matching table columns
    cleaned = []
    for r in records:
        if not r.get("company_name"):
            continue
        cleaned.append({
            "company_name": r.get("company_name"),
            "business_idea": r.get("business_idea"),
            "sector": r.get("sector"),
            "location": r.get("location"),
            "funding_stage": r.get("funding_stage"),
            "funding_amount": r.get("funding_amount"),
            "investors": r.get("investors"),
            "source_url": r.get("source_url"),
            "source_name": r.get("source_name"),
        })

    if not cleaned:
        return 0

    resp = requests.post(url, headers=headers, params=params, json=cleaned, timeout=30)
    if resp.status_code not in (200, 201, 204):
        print(f"[error] Supabase upsert failed: {resp.status_code} {resp.text}")
        resp.raise_for_status()
    return len(cleaned)


def log_run(articles_count, startups_count):
    url = f"{SUPABASE_URL}/rest/v1/scan_log"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    payload = {"articles_scanned": articles_count, "startups_found": startups_count}
    try:
        requests.post(url, headers=headers, json=payload, timeout=15)
    except Exception as e:
        print(f"[warn] Failed to write scan_log: {e}")


def main():
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")

    print("Fetching articles from all sources...")
    articles = fetch_all_articles()
    print(f"Fetched {len(articles)} unique articles.")

    print("Extracting startup records with Groq...")
    records = extract_startups(articles)
    print(f"Extracted {len(records)} candidate startup records.")

    written = upsert_startups(records)
    print(f"Upserted {written} rows into Supabase.")

    log_run(len(articles), written)


if __name__ == "__main__":
    main()
