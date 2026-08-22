"""
Intelligence Engine (Layer B): turns a row's own extracted fields into a
Momentum Score (0-100) and a Data Confidence (0-100). See RADAR_SCORE_SPEC.md
for the full spec -- this module should always match that document exactly.

This is a transparent, config-driven heuristic built only from data we
actually collect (funding stage/amount, investor presence, hiring signal) --
NOT a valuation, NOT a success prediction. Weights live in Supabase's
score_weights table (fetched once per run) rather than being hardcoded, so
the formula can be tuned without a code change. If that table is empty or
unreachable, DEFAULT_WEIGHTS below is used instead -- a bad edit to the table
can never break a whole run.
"""

import requests

DEFAULT_WEIGHTS = {
    "funding_stage": 0.35,
    "funding_amount": 0.30,
    "investor_presence": 0.15,
    "hiring_signal": 0.20,
}


def fetch_weights(supabase_url, supabase_service_key):
    """Reads score_weights from Supabase; falls back to DEFAULT_WEIGHTS on any problem."""
    try:
        resp = requests.get(
            f"{supabase_url}/rest/v1/score_weights",
            headers={
                "apikey": supabase_service_key,
                "Authorization": f"Bearer {supabase_service_key}",
            },
            params={"select": "component,weight"},
            timeout=15,
        )
        resp.raise_for_status()
        rows = resp.json()
        weights = {r["component"]: float(r["weight"]) for r in rows if r.get("component")}
        if weights:
            return weights
        print("[warn] score_weights table is empty, using DEFAULT_WEIGHTS")
    except Exception as e:
        print(f"[warn] Could not fetch score_weights, using DEFAULT_WEIGHTS: {e}")
    return dict(DEFAULT_WEIGHTS)


def _funding_stage_score(funding_stage):
    stage = (funding_stage or "").lower()
    if "pre-seed" in stage or "preseed" in stage or "pre seed" in stage:
        return 20
    if "seed" in stage:
        return 40
    if "series a" in stage:
        return 60
    if "series b" in stage:
        return 80
    if any(s in stage for s in ("series c", "series d", "series e", "series f", "growth", "late stage")):
        return 100
    return 0


def _funding_amount_score(funding_amount_usd):
    if not funding_amount_usd:
        return 0
    if funding_amount_usd >= 100_000_000:
        return 100
    if funding_amount_usd >= 20_000_000:
        return 90
    if funding_amount_usd >= 5_000_000:
        return 70
    if funding_amount_usd >= 1_000_000:
        return 50
    return 30


def _investor_presence_score(investors):
    return 100 if investors else 0


def _hiring_signal_score(hiring_status):
    return 100 if hiring_status == "Likely hiring" else 0


def compute_momentum_score(record, weights):
    components = {
        "funding_stage": _funding_stage_score(record.get("funding_stage")),
        "funding_amount": _funding_amount_score(record.get("funding_amount_usd")),
        "investor_presence": _investor_presence_score(record.get("investors")),
        "hiring_signal": _hiring_signal_score(record.get("hiring_status")),
    }
    total = sum(components[k] * weights.get(k, 0) for k in components)
    return max(0, min(100, round(total)))


# The 8 fields checked for data_confidence -- see RADAR_SCORE_SPEC.md.
CONFIDENCE_FIELDS = [
    "funding_stage", "funding_amount", "investors", "founder_name",
    "year_founded", "homepage", "contact_email", "hiring_status",
]


def compute_data_confidence(record):
    populated = sum(1 for f in CONFIDENCE_FIELDS if record.get(f))
    return round(100 * populated / len(CONFIDENCE_FIELDS))
