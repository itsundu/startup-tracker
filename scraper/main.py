import os
import requests

from sources import fetch_all_articles
from extractor import extract_startups, classify_region, parse_funding_usd
from enrich import enrich_all
from scoring import fetch_weights, compute_momentum_score, compute_data_confidence

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")


def deduplicate_by_company(records):
    """The same company can legitimately get extracted more than once in a single run (e.g.
    covered by both TechCrunch and Hacker News in the same week). Postgres's
    ON CONFLICT DO UPDATE can't affect the same target row twice within one upsert command,
    so sending two rows with the same company_name in one batch 500s the whole upsert. Dedupe
    here, before enrichment even runs, so we don't waste enrichment work on the duplicate
    either. Keeps whichever record has the longer business_idea, as a simple proxy for
    "the richer extraction" when two exist.
    """
    best_by_name = {}
    for r in records:
        name = (r.get("company_name") or "").strip()
        if not name:
            continue
        key = name.lower()
        existing = best_by_name.get(key)
        if existing is None or len(r.get("business_idea") or "") > len(existing.get("business_idea") or ""):
            best_by_name[key] = r
    return list(best_by_name.values())


def upsert_startups(records):
    """Upserts into `startups` and returns the upserted rows (with their real `id`s),
    so callers can write company_snapshots keyed by a stable UUID rather than by name.
    """
    if not records:
        print("No startup records to upsert.")
        return []

    url = f"{SUPABASE_URL}/rest/v1/startups"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        # Upsert on the unique company_name column; update the row if it already exists.
        # return=representation gives back the full upserted rows (incl. id) instead of nothing.
        "Prefer": "resolution=merge-duplicates,return=representation",
    }
    params = {"on_conflict": "company_name"}

    cleaned = []
    for r in records:
        if not r.get("company_name"):
            continue
        cleaned.append({
            "company_name": r.get("company_name"),
            "location": r.get("location"),
            "region": r.get("region"),
            "year_founded": r.get("year_founded"),
            "founder_name": r.get("founder_name"),
            "founder_linkedin": r.get("founder_linkedin"),
            "industry": r.get("industry"),
            "business_idea": r.get("business_idea"),
            "unique_moat": r.get("unique_moat"),
            "funding_stage": r.get("funding_stage"),
            "funding_amount": r.get("funding_amount"),
            "funding_amount_usd": r.get("funding_amount_usd"),
            "momentum_score": r.get("momentum_score"),
            "data_confidence": r.get("data_confidence"),
            "investors": r.get("investors"),
            "contact_email": r.get("contact_email"),
            "hiring_status": r.get("hiring_status"),
            "homepage": r.get("homepage"),
            "source_url": r.get("source_url"),
            "source_name": r.get("source_name"),
        })

    if not cleaned:
        return []

    resp = requests.post(url, headers=headers, params=params, json=cleaned, timeout=30)
    if resp.status_code not in (200, 201, 204):
        print(f"[error] Supabase upsert failed: {resp.status_code} {resp.text}")
        resp.raise_for_status()

    try:
        return resp.json()
    except ValueError:
        return []


def write_snapshots(upserted_rows):
    """One company_snapshots row per upserted startup -- the raw material for a
    momentum trajectory over time. See RADAR_SCORE_SPEC.md."""
    rows = [
        {
            "startup_id": r["id"],
            "momentum_score": r.get("momentum_score"),
            "data_confidence": r.get("data_confidence"),
        }
        for r in upserted_rows if r.get("id")
    ]
    if not rows:
        return 0

    url = f"{SUPABASE_URL}/rest/v1/company_snapshots"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    try:
        resp = requests.post(url, headers=headers, json=rows, timeout=30)
        if resp.status_code not in (200, 201, 204):
            print(f"[warn] Failed to write company_snapshots: {resp.status_code} {resp.text[:300]}")
            return 0
    except Exception as e:
        print(f"[warn] Failed to write company_snapshots: {e}")
        return 0
    return len(rows)


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

    print("Extracting startup records (phase 1, Gemini primary / Groq fallback)...")
    records, batch_count, failed_batches = extract_startups(articles)
    print(f"Extracted {len(records)} candidate startup records ({failed_batches}/{batch_count} batches failed).")

    if batch_count > 0 and failed_batches == batch_count:
        raise RuntimeError(
            f"Every extraction batch failed on both Gemini and Groq ({failed_batches}/{batch_count}). "
            "Treating this as a hard failure so the job exits non-zero, GitHub Actions flags the run "
            "red, and you get notified -- instead of silently upserting nothing. Check API keys/quota "
            "for both providers."
        )

    records = deduplicate_by_company(records)
    print(f"Deduplicated to {len(records)} unique companies.")

    print("Enriching records from company websites + LLM refine pass (phase 2)...")
    records = enrich_all(records, articles)

    print("Scoring records (Intelligence Engine: momentum score + data confidence)...")
    weights = fetch_weights(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    for r in records:
        r["region"] = classify_region(r.get("location"))
        r["funding_amount_usd"] = parse_funding_usd(r.get("funding_amount"))
        r["momentum_score"] = compute_momentum_score(r, weights)
        r["data_confidence"] = compute_data_confidence(r)

    upserted = upsert_startups(records)
    print(f"Upserted {len(upserted)} rows into Supabase.")

    snapshot_count = write_snapshots(upserted)
    print(f"Wrote {snapshot_count} company_snapshots rows.")

    log_run(len(articles), len(upserted))


if __name__ == "__main__":
    main()
