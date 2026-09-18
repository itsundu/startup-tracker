"""
Thin REST helpers for the v2 tables (companies, company_aliases,
company_events, company_sources, company_snapshots, scan_runs).

Deliberately does NOT use PostgREST's `on_conflict=` merge-duplicates upsert
for `companies` (the way the v1 pipeline's `main.py` does for `startups`):
`companies` has no single plain-column unique constraint suitable as a
PostgREST conflict target (its uniqueness is expression-based -- see
supabase_migration_v2.sql -- which PostgREST's upsert mechanism can't target
directly). More importantly, a blind whole-row upsert can't express
per-field preservation (upsert_rules.should_overwrite_field) -- that
requires reading the existing row, deciding per-field in Python, and
issuing a targeted PATCH with only the fields that should change. So entity
resolution + field-level PATCH/POST is the mechanism here, not a database-
level upsert.

Every function takes `base_url`/`service_key` explicitly rather than
reading environment variables itself, so it's easy to point at a different
project (or mock the whole module) in a script without env-var juggling.
"""

import requests

TIMEOUT = 30


def _headers(service_key):
    return {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }


def fetch_companies_for_resolution(base_url, service_key):
    """All companies' identity-relevant fields, for entity_resolution.py.
    Paginated via PostgREST's Range header would be needed past ~1000 rows;
    fine for this project's target scale (<=150 ranked + history)."""
    resp = requests.get(
        f"{base_url}/rest/v1/companies",
        headers=_headers(service_key),
        params={"select": "id,canonical_name,normalized_name,primary_domain,headquarters_country,description"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_aliases(base_url, service_key):
    resp = requests.get(
        f"{base_url}/rest/v1/company_aliases",
        headers=_headers(service_key),
        params={"select": "company_id,normalized_alias"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def insert_company(base_url, service_key, fields):
    """Inserts a brand-new company row. Returns the inserted row (with id)."""
    headers = _headers(service_key)
    headers["Prefer"] = "return=representation"
    resp = requests.post(f"{base_url}/rest/v1/companies", headers=headers, json=fields, timeout=TIMEOUT)
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


def patch_company(base_url, service_key, company_id, fields):
    """Applies ONLY the fields the caller has already decided (via
    upsert_rules.should_overwrite_field) should change. Never sends a field
    that should be preserved -- the caller filters, not this function."""
    if not fields:
        return None
    headers = _headers(service_key)
    headers["Prefer"] = "return=representation"
    resp = requests.patch(
        f"{base_url}/rest/v1/companies",
        headers=headers,
        params={"id": f"eq.{company_id}"},
        json=fields,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


def insert_alias(base_url, service_key, company_id, alias, normalized_alias, source_url=None):
    headers = _headers(service_key)
    headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
    payload = {
        "company_id": company_id, "alias": alias,
        "normalized_alias": normalized_alias, "source_url": source_url,
    }
    resp = requests.post(
        f"{base_url}/rest/v1/company_aliases",
        headers=headers,
        params={"on_conflict": "company_id,normalized_alias"},
        json=payload,
        timeout=TIMEOUT,
    )
    if resp.status_code not in (200, 201, 204):
        print(f"[warn] Failed to insert alias: {resp.status_code} {resp.text[:300]}")


def fetch_company_events(base_url, service_key, company_id):
    """ALL historical events for one company, not just ones extracted this
    run -- scoring/eligibility/funding-aggregate computation must consider a
    company's full recent history (e.g. a funding round found two runs ago
    is still within the 90-day high-impact window today even if this run's
    articles don't happen to mention it again), not just today's batch."""
    resp = requests.get(
        f"{base_url}/rest/v1/company_events",
        headers=_headers(service_key),
        params={"company_id": f"eq.{company_id}", "select": "*", "order": "published_at.desc"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def insert_company_event(base_url, service_key, event_fields):
    headers = _headers(service_key)
    headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
    resp = requests.post(
        f"{base_url}/rest/v1/company_events",
        headers=headers,
        params={"on_conflict": "company_id,source_url,event_type"},
        json=event_fields,
        timeout=TIMEOUT,
    )
    if resp.status_code not in (200, 201, 204):
        print(f"[warn] Failed to insert company_event: {resp.status_code} {resp.text[:300]}")


def insert_company_source(base_url, service_key, source_fields):
    headers = _headers(service_key)
    headers["Prefer"] = "return=minimal"
    resp = requests.post(f"{base_url}/rest/v1/company_sources", headers=headers, json=source_fields, timeout=TIMEOUT)
    if resp.status_code not in (200, 201, 204):
        print(f"[warn] Failed to insert company_source: {resp.status_code} {resp.text[:300]}")


def insert_company_snapshot(base_url, service_key, snapshot_fields):
    headers = _headers(service_key)
    headers["Prefer"] = "return=minimal"
    resp = requests.post(f"{base_url}/rest/v1/company_snapshots", headers=headers, json=snapshot_fields, timeout=TIMEOUT)
    if resp.status_code not in (200, 201, 204):
        print(f"[warn] Failed to insert company_snapshot: {resp.status_code} {resp.text[:300]}")


def insert_scan_run(base_url, service_key, run_fields):
    headers = _headers(service_key)
    headers["Prefer"] = "return=representation"
    resp = requests.post(f"{base_url}/rest/v1/scan_runs", headers=headers, json=run_fields, timeout=TIMEOUT)
    resp.raise_for_status()
    rows = resp.json()
    return rows[0]["id"] if rows else None


def fetch_recent_successful_scan_runs(base_url, service_key, limit=1):
    """Most recent scan_runs with run_status in ('success',
    'success_with_warnings'), newest first -- used to compare this run's
    quality report against the last one that actually published a ranking,
    so should_publish_ranking can detect a material regression."""
    resp = requests.get(
        f"{base_url}/rest/v1/scan_runs",
        headers=_headers(service_key),
        params={
            "select": "id,finished_at,quality_report,run_status",
            "run_status": "in.(success,success_with_warnings)",
            "order": "finished_at.desc",
            "limit": str(limit),
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def update_scan_run(base_url, service_key, run_id, fields):
    headers = _headers(service_key)
    headers["Prefer"] = "return=minimal"
    resp = requests.patch(
        f"{base_url}/rest/v1/scan_runs",
        headers=headers,
        params={"id": f"eq.{run_id}"},
        json=fields,
        timeout=TIMEOUT,
    )
    if resp.status_code not in (200, 201, 204):
        print(f"[warn] Failed to update scan_run: {resp.status_code} {resp.text[:300]}")
