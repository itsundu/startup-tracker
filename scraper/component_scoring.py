"""
Turns a company's verified company_events (plus hiring/moat fields already
resolved elsewhere) into the 7 v2 ranking components (see ranking.py for the
weights that combine them). Kept separate from main_v2.py's orchestration so
these formulas -- the most judgment-heavy, least-precedented part of this
whole rewrite -- are unit-testable in isolation and easy to retune later
without touching orchestration/IO code.

Every formula here is a documented, configurable v2.0 starting point, not a
claim of precision -- exactly the same honesty standard the existing
RADAR_SCORE_SPEC.md already holds its v1 weights to. Expect these to be
retuned once real historical snapshot data exists to evaluate them against.
"""

from datetime import date, datetime, timezone
from typing import Dict, List, Optional

from decay import DEFAULT_HALF_LIFE_DAYS, decayed_weight

# "Positive momentum" base values per event_type, used by both the
# recent-events and traction components (each pulls a different subset --
# see EVENT_TYPES_FOR_RECENT_EVENTS / EVENT_TYPES_FOR_TRACTION below).
# layoffs/shutdown are deliberately absent: they are not positive momentum
# signals, so they contribute 0 rather than a negative number -- this
# module scores momentum, it does not build a separate penalty system.
EVENT_BASE_VALUES = {
    "funding_round_completed": 100,
    "funding_round_announced": 70,
    "acquisition": 90,
    "major_product_release": 70,
    "product_launch": 55,
    "customer_win": 60,
    "partnership": 50,
    "geographic_expansion": 50,
    "executive_change": 25,
    "hiring_growth": 35,
}

EVENT_TYPES_FOR_RECENT_EVENTS = {
    "funding_round_completed", "funding_round_announced", "acquisition",
    "executive_change", "hiring_growth",
}
EVENT_TYPES_FOR_TRACTION = {
    "product_launch", "major_product_release", "customer_win",
}
EVENT_TYPES_FOR_MARKET_EXPANSION = {
    "partnership", "geographic_expansion",
}

# Only events with this verification_status count toward momentum at all --
# proposed/rumored/abandoned/ambiguous claims may still be stored (and shown
# in the frontend's evidence view) but must not move the score, per "only
# completed or clearly announced funding rounds may contribute."
COUNTABLE_STATUSES = {"completed"}
# funding_round_announced is allowed to count even at "proposed"-adjacent
# confidence IF its own status is "completed" (i.e. the announcement itself,
# not the money, is confirmed) -- handled by COUNTABLE_STATUSES already
# covering "completed" uniformly; funding_round_announced events should be
# extracted with status "completed" when the announcement itself is real,
# reserving "proposed" for funding_round_completed claims that are actually
# just proposals (a schema/extraction-time distinction, not scored here).


def _age_days(event_date_value, now: Optional[datetime] = None) -> float:
    now = now or datetime.now(timezone.utc)
    if event_date_value is None:
        return 9999.0
    if isinstance(event_date_value, datetime):
        d = event_date_value.date()
    elif isinstance(event_date_value, date):
        d = event_date_value
    else:
        try:
            d = datetime.fromisoformat(str(event_date_value).replace("Z", "+00:00")).date()
        except ValueError:
            return 9999.0
    return max(0.0, (now.date() - d).days)


def _decayed_event_sum(events: List[Dict], allowed_types: set, half_life_days: float, now: Optional[datetime]) -> float:
    total = 0.0
    for event in events:
        if event.get("verification_status") not in COUNTABLE_STATUSES:
            continue
        event_type = event.get("event_type")
        if event_type not in allowed_types:
            continue
        base = EVENT_BASE_VALUES.get(event_type, 0)
        age = _age_days(event.get("event_date") or event.get("published_at"), now)
        total += decayed_weight(base, age, half_life_days)
    return total


def score_recent_verified_events(events: List[Dict], half_life_days: float = DEFAULT_HALF_LIFE_DAYS, now: Optional[datetime] = None) -> int:
    """0-100. Multiple qualifying events compound (a funding round AND a
    hire spree in the same window both count), but the total is capped at
    100 -- a company can't exceed "maximum recent momentum" no matter how
    many events it has."""
    return max(0, min(100, round(_decayed_event_sum(events, EVENT_TYPES_FOR_RECENT_EVENTS, half_life_days, now))))


def score_traction(events: List[Dict], half_life_days: float = DEFAULT_HALF_LIFE_DAYS, now: Optional[datetime] = None) -> int:
    return max(0, min(100, round(_decayed_event_sum(events, EVENT_TYPES_FOR_TRACTION, half_life_days, now))))


def score_market_expansion(events: List[Dict], half_life_days: float = DEFAULT_HALF_LIFE_DAYS, now: Optional[datetime] = None) -> int:
    return max(0, min(100, round(_decayed_event_sum(events, EVENT_TYPES_FOR_MARKET_EXPANSION, half_life_days, now))))


HIRING_STATUS_BASE = {
    "actively_hiring": 90,
    "limited_hiring": 50,
    "no_verified_openings": 10,
    "unknown": 0,
}


def score_hiring_momentum(hiring_status: Optional[str], hiring_confidence: Optional[int]) -> int:
    """Scaled by hiring_confidence so a low-confidence "actively_hiring"
    read (e.g. only a job-count regex matched, no ATS link) doesn't score
    identically to a confidently-verified one."""
    base = HIRING_STATUS_BASE.get(hiring_status or "unknown", 0)
    confidence_factor = (hiring_confidence or 0) / 100.0
    return max(0, min(100, round(base * confidence_factor if base else 0)))


def score_moat_differentiation(moat_confidence: Optional[int]) -> int:
    """moat_confidence is produced elsewhere (moat verification is not yet
    implemented in this pass -- see main_v2.py's docstring) and defaults to
    0 ("Not yet verified"), which correctly yields a 0 component score
    rather than a fabricated one."""
    return max(0, min(100, round(moat_confidence or 0)))


def score_source_corroboration(independent_source_count: int) -> int:
    """0 sources -> 0. 1 -> 40. 2 -> 80. 3+ -> 100. Deliberately steep early
    (going from 0 to 1 independent source is the biggest credibility jump)
    and flat after 3 (a 6th mirror of the same story isn't materially more
    corroborating than a 3rd)."""
    count = independent_source_count or 0
    if count <= 0:
        return 0
    if count == 1:
        return 40
    if count == 2:
        return 80
    return 100
