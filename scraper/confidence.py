"""
Field-level and record-level confidence + completeness.

Two deliberately SEPARATE numbers (per the product spec):

  - data_completeness: how many useful fields are present. Says nothing
    about whether they're CORRECT.
  - data_confidence: how much the *populated* facts should be trusted, given
    source reliability, corroboration, freshness, entity/homepage
    resolution confidence, and contradictory evidence.

"Unknown" (or any of the UNKNOWN_SENTINELS) is never counted as populated in
either calculation -- a field literally holding the string "Unknown" carries
the same information as a null, and must not inflate completeness.
"""

from typing import Dict, Iterable, Optional

UNKNOWN_SENTINELS = {
    "unknown", "n/a", "na", "none", "not yet verified", "not available",
    "tbd", "",
}


def is_populated(value) -> bool:
    """A field counts as populated only if it has a real, non-placeholder
    value. Numeric 0 / False are populated (they are real answers, not
    unknowns) -- only None and unknown-sentinel strings are not."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in UNKNOWN_SENTINELS
    return True


def compute_data_completeness(fields: Dict, tracked_fields: Iterable[str]) -> int:
    """% of `tracked_fields` present in `fields` that are populated
    (0-100). Purely a coverage measure -- see module docstring."""
    tracked = list(tracked_fields)
    if not tracked:
        return 0
    populated = sum(1 for f in tracked if is_populated(fields.get(f)))
    return round(100 * populated / len(tracked))


# Confidence sub-weights. These are the SUB-components of one field-level or
# record-level confidence score, not the ranking weights (see ranking.py for
# those) -- kept separate and documented so they can be tuned without
# touching ranking math.
CONFIDENCE_WEIGHTS = {
    "source_tier": 0.30,
    "corroboration": 0.20,
    "freshness": 0.15,
    "completeness": 0.15,
    "resolution_confidence": 0.10,
    "extraction_confidence": 0.10,
}
assert abs(sum(CONFIDENCE_WEIGHTS.values()) - 1.0) < 1e-9

STALE_WARN_DAYS = 30
STALE_EXCLUDE_DAYS = 90


def _source_tier_score(best_tier: Optional[int]) -> float:
    if best_tier is None:
        return 0.0
    return {1: 100.0, 2: 65.0, 3: 30.0}.get(best_tier, 0.0)


def _corroboration_score(independent_source_count: int) -> float:
    if independent_source_count <= 0:
        return 0.0
    if independent_source_count == 1:
        return 50.0
    if independent_source_count == 2:
        return 80.0
    return 100.0


def _freshness_score(days_since_last_verified: Optional[int]) -> float:
    if days_since_last_verified is None:
        return 0.0
    if days_since_last_verified <= STALE_WARN_DAYS:
        return 100.0
    if days_since_last_verified >= STALE_EXCLUDE_DAYS:
        return 0.0
    # linear falloff between the warn and exclude thresholds
    span = STALE_EXCLUDE_DAYS - STALE_WARN_DAYS
    return 100.0 * (1 - (days_since_last_verified - STALE_WARN_DAYS) / span)


def compute_data_confidence(
    *,
    best_source_tier: Optional[int],
    independent_source_count: int,
    days_since_last_verified: Optional[int],
    field_completeness: int,
    homepage_confidence: Optional[int] = None,
    entity_resolution_confidence: Optional[int] = None,
    extraction_confidence: Optional[int] = None,
    has_contradictory_evidence: bool = False,
) -> int:
    """Returns 0-100. `homepage_confidence` and `entity_resolution_confidence`
    are averaged (whichever are available) into `resolution_confidence`;
    if neither is available that sub-score is 0, not skipped -- a record
    with no resolution confidence at all should not get a free pass.

    `has_contradictory_evidence` applies a flat penalty multiplier (0.7)
    rather than a subtraction, so it scales proportionally instead of
    potentially pushing a low score negative.
    """
    resolution_values = [v for v in (homepage_confidence, entity_resolution_confidence) if v is not None]
    resolution_score = sum(resolution_values) / len(resolution_values) if resolution_values else 0.0
    extraction_score = float(extraction_confidence) if extraction_confidence is not None else 0.0

    components = {
        "source_tier": _source_tier_score(best_source_tier),
        "corroboration": _corroboration_score(independent_source_count),
        "freshness": _freshness_score(days_since_last_verified),
        "completeness": float(field_completeness),
        "resolution_confidence": resolution_score,
        "extraction_confidence": extraction_score,
    }

    total = sum(components[k] * CONFIDENCE_WEIGHTS[k] for k in CONFIDENCE_WEIGHTS)

    if has_contradictory_evidence:
        total *= 0.7

    return max(0, min(100, round(total)))


def is_stale_warning(days_since_last_verified: Optional[int]) -> bool:
    return days_since_last_verified is not None and days_since_last_verified > STALE_WARN_DAYS


def is_stale_excluded(days_since_last_verified: Optional[int]) -> bool:
    return days_since_last_verified is not None and days_since_last_verified > STALE_EXCLUDE_DAYS
