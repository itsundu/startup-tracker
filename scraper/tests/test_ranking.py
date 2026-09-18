from datetime import date, timedelta

import pytest

from ranking import (
    DEFAULT_ELIGIBILITY_CONFIG,
    DEFAULT_WEIGHTS_V2,
    EligibilityConfig,
    WeightValidationError,
    compute_momentum_score,
    is_eligible,
    rank_all_regions,
    rank_region,
    validate_weights,
    why_ranked,
)


def _company(name, region, momentum_components, **overrides):
    base = {
        "canonical_name": name,
        "region_bucket": region,
        "component_scores": momentum_components,
        "data_confidence": 75,
        "independent_source_count": 2,
        "last_high_impact_event_at": "2026-01-01",
        "previous_regional_rank": None,
    }
    base.update(overrides)
    return base


FULL_COMPONENTS = {
    "recent_verified_events": 80,
    "traction": 70,
    "stage_adjusted_funding": 60,
    "hiring_momentum": 50,
    "market_expansion": 40,
    "moat_differentiation": 30,
    "source_corroboration": 20,
}


# ---- 10 & 27. Weight validation + score component sum/bounds ----

def test_validate_weights_accepts_default():
    validate_weights(DEFAULT_WEIGHTS_V2)  # should not raise


def test_validate_weights_rejects_wrong_sum():
    bad = dict(DEFAULT_WEIGHTS_V2)
    bad["traction"] += 0.5
    with pytest.raises(WeightValidationError):
        validate_weights(bad)


def test_validate_weights_rejects_missing_component():
    bad = dict(DEFAULT_WEIGHTS_V2)
    del bad["hiring_momentum"]
    with pytest.raises(WeightValidationError):
        validate_weights(bad)


def test_validate_weights_rejects_negative_weight():
    bad = dict(DEFAULT_WEIGHTS_V2)
    bad["traction"] = -0.1
    bad["moat_differentiation"] += 0.3  # keep sum at 1.0 to isolate the negativity check
    with pytest.raises(WeightValidationError):
        validate_weights(bad)


def test_momentum_score_is_bounded_0_to_100():
    score = compute_momentum_score(FULL_COMPONENTS)
    assert 0 <= score <= 100


def test_momentum_score_all_max_components_is_100():
    all_max = {k: 100 for k in DEFAULT_WEIGHTS_V2}
    assert compute_momentum_score(all_max) == 100


def test_momentum_score_all_zero_components_is_0():
    all_zero = {k: 0 for k in DEFAULT_WEIGHTS_V2}
    assert compute_momentum_score(all_zero) == 0


def test_momentum_score_matches_manual_weighted_sum():
    expected = round(sum(FULL_COMPONENTS[k] * DEFAULT_WEIGHTS_V2[k] for k in DEFAULT_WEIGHTS_V2))
    assert compute_momentum_score(FULL_COMPONENTS) == expected


def test_momentum_score_rejects_out_of_bounds_component():
    bad = dict(FULL_COMPONENTS)
    bad["traction"] = 150
    with pytest.raises(ValueError):
        compute_momentum_score(bad)


# ---- 8 & 28. Regional ranking independence / exactly independent pools ----

def test_regions_are_ranked_independently_not_globally_split():
    # A mediocre India candidate must be able to rank #1 in India even
    # though several US candidates score higher in absolute terms -- because
    # each region is ranked against its OWN pool, never a combined global
    # list divided afterward.
    us_companies = [
        _company(f"US-{i}", "US", {**FULL_COMPONENTS, "recent_verified_events": 95})
        for i in range(5)
    ]
    india_companies = [
        _company("India-Only", "INDIA", {**FULL_COMPONENTS, "recent_verified_events": 40}),
    ]

    ranked = rank_all_regions({"US": us_companies, "INDIA": india_companies})

    assert ranked["INDIA"][0]["canonical_name"] == "India-Only"
    assert ranked["INDIA"][0]["regional_rank"] == 1
    # The India company's momentum score is lower than every US company's,
    # yet it is still regional_rank 1 for India -- proof the pools don't
    # cross-contaminate.
    assert ranked["INDIA"][0]["momentum_score"] < ranked["US"][0]["momentum_score"]


def test_ranking_never_pads_a_short_region_with_fabricated_entries():
    only_two = [_company("A", "ROW", FULL_COMPONENTS), _company("B", "ROW", FULL_COMPONENTS)]
    ranked = rank_region(only_two, top_n=50)
    assert len(ranked) == 2  # not padded to 50


def test_ranking_truncates_to_top_n():
    many = [_company(f"C{i}", "US", {**FULL_COMPONENTS, "traction": i}) for i in range(60)]
    ranked = rank_region(many, top_n=50)
    assert len(ranked) == 50
    assert ranked[0]["regional_rank"] == 1
    assert ranked[-1]["regional_rank"] == 50


# ---- 9. Deterministic tie-breaking ----

def test_tie_break_by_data_confidence():
    a = _company("Alpha", "US", FULL_COMPONENTS, data_confidence=90)
    b = _company("Beta", "US", FULL_COMPONENTS, data_confidence=60)
    ranked = rank_region([a, b])
    assert [c["canonical_name"] for c in ranked] == ["Alpha", "Beta"]


def test_tie_break_by_more_recent_high_impact_event():
    a = _company("Alpha", "US", FULL_COMPONENTS, data_confidence=70, last_high_impact_event_at="2026-01-01")
    b = _company("Beta", "US", FULL_COMPONENTS, data_confidence=70, last_high_impact_event_at="2025-06-01")
    ranked = rank_region([a, b])
    assert ranked[0]["canonical_name"] == "Alpha"


def test_tie_break_by_independent_source_count():
    a = _company("Alpha", "US", FULL_COMPONENTS, data_confidence=70,
                 last_high_impact_event_at="2026-01-01", independent_source_count=5)
    b = _company("Beta", "US", FULL_COMPONENTS, data_confidence=70,
                 last_high_impact_event_at="2026-01-01", independent_source_count=1)
    ranked = rank_region([a, b])
    assert ranked[0]["canonical_name"] == "Alpha"


def test_tie_break_final_fallback_is_canonical_name_alphabetical():
    a = _company("Zeta", "US", FULL_COMPONENTS, data_confidence=70,
                  last_high_impact_event_at="2026-01-01", independent_source_count=2)
    b = _company("Alpha", "US", FULL_COMPONENTS, data_confidence=70,
                  last_high_impact_event_at="2026-01-01", independent_source_count=2)
    ranked = rank_region([a, b])
    assert [c["canonical_name"] for c in ranked] == ["Alpha", "Zeta"]


def test_ranking_is_deterministic_across_repeated_runs():
    companies = [
        _company("Zeta", "US", FULL_COMPONENTS, data_confidence=70, independent_source_count=2),
        _company("Alpha", "US", FULL_COMPONENTS, data_confidence=70, independent_source_count=2),
        _company("Mid", "US", {**FULL_COMPONENTS, "traction": 99}, data_confidence=85),
    ]
    first = [c["canonical_name"] for c in rank_region(list(companies))]
    second = [c["canonical_name"] for c in rank_region(list(reversed(companies)))]
    assert first == second


def test_company_with_no_last_high_impact_event_never_wins_tie_over_one_that_has_one():
    a = _company("HasEvent", "US", FULL_COMPONENTS, data_confidence=70, last_high_impact_event_at="2020-01-01")
    b = _company("NoEvent", "US", FULL_COMPONENTS, data_confidence=70, last_high_impact_event_at=None)
    ranked = rank_region([a, b])
    assert ranked[0]["canonical_name"] == "HasEvent"


# ---- why_ranked / rank_change ----

def test_why_ranked_names_top_components():
    summary = why_ranked(FULL_COMPONENTS)
    assert "recent verified events" in summary


def test_why_ranked_handles_all_zero_gracefully():
    summary = why_ranked({k: 0 for k in DEFAULT_WEIGHTS_V2})
    assert "limited verified signal" in summary


def test_rank_change_since_previous_snapshot():
    a = _company("Alpha", "US", FULL_COMPONENTS, previous_regional_rank=5)
    ranked = rank_region([a])
    assert ranked[0]["regional_rank"] == 1
    assert ranked[0]["rank_change_since_previous_snapshot"] == 4  # moved up from 5 to 1


def test_rank_change_none_when_no_previous_rank():
    a = _company("Alpha", "US", FULL_COMPONENTS, previous_regional_rank=None)
    ranked = rank_region([a])
    assert ranked[0]["rank_change_since_previous_snapshot"] is None


# ---- 29. Stale-company exclusion (via eligibility gate) ----

def _eligible_company(**overrides):
    base = dict(
        region_bucket="US",
        primary_domain_verified=True,
        industry="AI",
        days_since_last_qualifying_event=10,
        data_confidence=80,
        independent_source_count=2,
        best_source_tier=2,
        company_status="operating",
        entity_resolution_needs_review=False,
        days_since_last_verified=5,
    )
    base.update(overrides)
    return base


def test_eligible_company_passes():
    result = is_eligible(_eligible_company())
    assert result.eligible
    assert result.reasons == []


def test_stale_company_excluded_past_max_stale_days():
    company = _eligible_company(days_since_last_verified=120)
    result = is_eligible(company)
    assert not result.eligible
    assert any("stale" in r.lower() or "verified" in r.lower() for r in result.reasons)


def test_company_outside_candidate_event_window_excluded():
    company = _eligible_company(days_since_last_qualifying_event=200)
    result = is_eligible(company)
    assert not result.eligible


def test_company_below_min_confidence_excluded():
    company = _eligible_company(data_confidence=40)
    result = is_eligible(company)
    assert not result.eligible


def test_shutdown_company_excluded():
    company = _eligible_company(company_status="shut_down")
    result = is_eligible(company)
    assert not result.eligible


def test_acquired_company_excluded():
    company = _eligible_company(company_status="acquired")
    assert not is_eligible(company).eligible


def test_ambiguous_entity_excluded():
    company = _eligible_company(entity_resolution_needs_review=True)
    assert not is_eligible(company).eligible


def test_unverified_region_excluded():
    company = _eligible_company(region_bucket="UNKNOWN")
    assert not is_eligible(company).eligible


def test_weak_source_only_excluded():
    company = _eligible_company(best_source_tier=3, independent_source_count=0)
    assert not is_eligible(company).eligible


def test_custom_eligibility_config_is_honored():
    strict_config = EligibilityConfig(min_ranking_confidence=95)
    company = _eligible_company(data_confidence=80)
    assert is_eligible(company, strict_config).eligible is False
    assert is_eligible(company, DEFAULT_ELIGIBILITY_CONFIG).eligible is True
