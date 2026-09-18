"""
Versioned, configurable ranking engine (score_version "v2").

Three independent regional pools (US / INDIA / ROW) are ranked separately --
`rank_region` is called once per region on that region's own candidate list.
Never rank a combined global list and split it afterward (that would let a
weak Indian candidate's rank be an artifact of how many strong US candidates
happen to exist, rather than how it compares to its actual regional peers).

Momentum measures recent, evidence-backed movement -- NOT company size or
lifetime success. Data confidence, completeness, AI relevance, source
quality, and eligibility are computed elsewhere (confidence.py,
eligibility below) and displayed alongside the score, never folded into it.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional

SCORE_VERSION = "v2"

# Versioned component weights. Sum must equal 1.0 -- validate_weights()
# enforces this and is called by rank_region() before scoring anything, so a
# misconfigured weight set fails loudly instead of silently under/over-
# weighting the score.
DEFAULT_WEIGHTS_V2 = {
    "recent_verified_events": 0.25,
    "traction": 0.20,
    "stage_adjusted_funding": 0.15,
    "hiring_momentum": 0.15,
    "market_expansion": 0.10,
    "moat_differentiation": 0.10,
    "source_corroboration": 0.05,
}

COMPONENT_LABELS = {
    "recent_verified_events": "recent verified events",
    "traction": "product/customer traction",
    "stage_adjusted_funding": "stage-adjusted funding momentum",
    "hiring_momentum": "verified hiring momentum",
    "market_expansion": "market expansion/partnerships",
    "moat_differentiation": "technology differentiation/moat",
    "source_corroboration": "independent source corroboration",
}

SUPPORTED_REGIONS = {"US", "INDIA", "ROW"}

SUPPORTED_INDUSTRIES = {
    "AI", "Enterprise & Developer Tech", "FinTech", "PropTech",
    "Real Estate Technology", "Technology",
}

INELIGIBLE_STATUSES = {"shut_down", "acquired", "public_incumbent"}


class WeightValidationError(ValueError):
    pass


def validate_weights(weights: Dict[str, float]) -> None:
    """Raises WeightValidationError if `weights` doesn't have exactly the
    expected components summing to 1.0 (within floating-point tolerance)."""
    expected = set(DEFAULT_WEIGHTS_V2)
    got = set(weights)
    if got != expected:
        missing = expected - got
        extra = got - expected
        raise WeightValidationError(
            f"weight set mismatch. missing={sorted(missing)} unexpected={sorted(extra)}"
        )
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise WeightValidationError(f"weights must sum to 1.0, got {total}")
    if any(w < 0 for w in weights.values()):
        raise WeightValidationError("weights must be non-negative")


def compute_momentum_score(component_scores: Dict[str, float], weights: Dict[str, float] = None) -> int:
    """Each value in `component_scores` must already be a 0-100 score for
    that component (decay and stage-adjustment already applied upstream).
    Returns an int clamped to [0, 100]."""
    weights = weights or DEFAULT_WEIGHTS_V2
    validate_weights(weights)
    total = 0.0
    for component, weight in weights.items():
        score = component_scores.get(component, 0) or 0
        if not (0 <= score <= 100):
            raise ValueError(f"component '{component}' score {score} out of [0, 100] bounds")
        total += score * weight
    return max(0, min(100, round(total)))


# ---- Eligibility -------------------------------------------------------------------

@dataclass
class EligibilityConfig:
    candidate_event_window_days: int = 180
    high_impact_event_window_days: int = 90
    min_ranking_confidence: int = 60
    min_authoritative_or_corroborated_sources: int = 1
    max_stale_warn_days: int = 30
    max_stale_exclude_days: int = 90


DEFAULT_ELIGIBILITY_CONFIG = EligibilityConfig()


@dataclass
class EligibilityResult:
    eligible: bool
    reasons: List[str] = field(default_factory=list)


def is_eligible(company: Dict, config: EligibilityConfig = DEFAULT_ELIGIBILITY_CONFIG) -> EligibilityResult:
    """`company` is expected to carry pre-computed fields:
        region_bucket, primary_domain_verified (bool), industry,
        days_since_last_qualifying_event (int|None),
        data_confidence (int), independent_source_count (int),
        best_source_tier (int|None), company_status (str),
        entity_resolution_needs_review (bool),
        days_since_last_verified (int|None)
    """
    reasons = []

    if company.get("region_bucket") not in SUPPORTED_REGIONS:
        reasons.append("headquarters region is not verified as US/INDIA/ROW")

    if not company.get("primary_domain_verified"):
        reasons.append("primary domain is not verified")

    if company.get("industry") not in SUPPORTED_INDUSTRIES:
        reasons.append(f"industry '{company.get('industry')}' is not a supported category")

    days_since_event = company.get("days_since_last_qualifying_event")
    if days_since_event is None or days_since_event > config.candidate_event_window_days:
        reasons.append(
            f"no qualifying event within the {config.candidate_event_window_days}-day candidate window"
        )

    confidence = company.get("data_confidence") or 0
    if confidence < config.min_ranking_confidence:
        reasons.append(
            f"data confidence {confidence} below minimum ranking confidence {config.min_ranking_confidence}"
        )

    has_authoritative_source = company.get("best_source_tier") == 1
    independent_sources = company.get("independent_source_count") or 0
    if not has_authoritative_source and independent_sources < config.min_authoritative_or_corroborated_sources:
        reasons.append("evidence relies solely on a weak (Tier 3) discovery source with no corroboration")

    if company.get("company_status") in INELIGIBLE_STATUSES:
        reasons.append(f"company status '{company.get('company_status')}' is not rankable")

    if company.get("entity_resolution_needs_review"):
        reasons.append("entity identity is ambiguous / unresolved (possible confusion with a similarly named org)")

    days_since_verified = company.get("days_since_last_verified")
    if days_since_verified is not None and days_since_verified > config.max_stale_exclude_days:
        reasons.append(
            f"data has not been verified in {days_since_verified} days "
            f"(exceeds the {config.max_stale_exclude_days}-day staleness exclusion)"
        )

    return EligibilityResult(eligible=len(reasons) == 0, reasons=reasons)


# ---- Regional ranking with deterministic tie-breaking -------------------------------

def _sortable_date(value) -> date:
    """None / unparseable dates sort as the earliest possible date, so a
    company with no dated high-impact event never wins a tie-break against
    one that has one."""
    if value is None:
        return date.min
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return date.min


def _tie_break_key(company: Dict):
    """Deterministic descending sort key. Python sorts ascending, so every
    field that should rank "higher is better" is negated; canonical_name is
    the final ascending (alphabetical) tie-break."""
    return (
        -(company.get("momentum_score") or 0),
        -(company.get("data_confidence") or 0),
        _sortable_date(company.get("last_high_impact_event_at")).toordinal() * -1,
        -(company.get("independent_source_count") or 0),
        (company.get("canonical_name") or "").strip().lower(),
    )


def why_ranked(component_scores: Dict[str, float], weights: Dict[str, float] = None, top_n: int = 2) -> str:
    """A short, human-readable summary of the top contributing components,
    e.g. "Ranked mainly on recent verified events and verified hiring
    momentum." Deterministic: ties in weighted contribution break on the
    fixed component_labels iteration order (declaration order above)."""
    weights = weights or DEFAULT_WEIGHTS_V2
    contributions = [
        (component, (component_scores.get(component, 0) or 0) * weight)
        for component, weight in weights.items()
    ]
    contributions.sort(key=lambda pair: -pair[1])
    top = [c for c, _ in contributions[:top_n] if contributions]
    if not top or all(component_scores.get(c, 0) == 0 for c in top):
        return "Ranked on limited verified signal; see component scores."
    labels = [COMPONENT_LABELS.get(c, c) for c in top]
    return "Ranked mainly on " + " and ".join(labels) + "."


def rank_region(
    companies: List[Dict],
    weights: Dict[str, float] = None,
    top_n: int = 50,
) -> List[Dict]:
    """Ranks ONE region's candidate pool independently. `companies` should
    already be filtered to eligible companies in a single region_bucket
    (callers typically run `is_eligible` first and pass only the survivors).

    Each input dict must carry `component_scores` (a dict of the 7 v2
    components, each 0-100) plus the tie-break fields consumed by
    `_tie_break_key`. Returns a NEW list (does not mutate input dicts in
    place beyond what's returned), each entry augmented with:
        momentum_score, regional_rank, why_ranked, score_version,
        rank_change_since_previous_snapshot
    truncated to `top_n` entries -- if fewer than `top_n` are eligible, the
    shorter list is returned as-is (never padded with fabricated entries).
    """
    weights = weights or DEFAULT_WEIGHTS_V2
    validate_weights(weights)

    scored = []
    for company in companies:
        c = dict(company)
        c["momentum_score"] = compute_momentum_score(c.get("component_scores", {}), weights)
        scored.append(c)

    scored.sort(key=_tie_break_key)
    ranked = scored[:top_n]

    for idx, company in enumerate(ranked):
        company["regional_rank"] = idx + 1
        company["score_version"] = SCORE_VERSION
        company["why_ranked"] = why_ranked(company.get("component_scores", {}), weights)
        previous_rank = company.get("previous_regional_rank")
        company["rank_change_since_previous_snapshot"] = (
            (previous_rank - company["regional_rank"]) if previous_rank is not None else None
        )

    return ranked


def rank_all_regions(
    companies_by_region: Dict[str, List[Dict]],
    weights: Dict[str, float] = None,
    top_n: int = 50,
) -> Dict[str, List[Dict]]:
    """Convenience wrapper: ranks US/INDIA/ROW independently and returns a
    dict of region -> ranked list. Regions with fewer than `top_n` qualified
    companies simply return a shorter list -- see quality_report.py for
    surfacing the shortfall, never fabricate entries to pad it."""
    return {
        region: rank_region(pool, weights=weights, top_n=top_n)
        for region, pool in companies_by_region.items()
    }
