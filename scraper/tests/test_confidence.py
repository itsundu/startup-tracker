import pytest

from confidence import (
    compute_data_completeness,
    compute_data_confidence,
    is_populated,
    is_stale_excluded,
    is_stale_warning,
)

TRACKED_FIELDS = ["funding_stage", "founder_name", "homepage", "hiring_status"]


# ---- 12. Unknown values must not count as complete/populated ----

@pytest.mark.parametrize("value", [None, "Unknown", "unknown", "N/A", "n/a", "", "  ", "Not yet verified"])
def test_unknown_style_values_are_not_populated(value):
    assert is_populated(value) is False


@pytest.mark.parametrize("value", ["Seed", 0, False, "0", "Series A"])
def test_real_values_including_falsy_numbers_are_populated(value):
    assert is_populated(value) is True


def test_completeness_excludes_unknown_fields():
    fields = {"funding_stage": "Seed", "founder_name": "Unknown", "homepage": None, "hiring_status": "Unknown"}
    # Only funding_stage counts as populated out of 4 tracked fields.
    assert compute_data_completeness(fields, TRACKED_FIELDS) == 25


def test_completeness_all_populated_is_100():
    fields = {"funding_stage": "Seed", "founder_name": "Jane Doe", "homepage": "https://acme.com", "hiring_status": "Actively hiring"}
    assert compute_data_completeness(fields, TRACKED_FIELDS) == 100


def test_completeness_no_tracked_fields_is_zero():
    assert compute_data_completeness({"a": "b"}, []) == 0


# ---- 11. Confidence calculation ----

def test_confidence_higher_with_authoritative_source():
    tier1 = compute_data_confidence(
        best_source_tier=1, independent_source_count=2, days_since_last_verified=5,
        field_completeness=80, homepage_confidence=90, entity_resolution_confidence=90,
        extraction_confidence=90,
    )
    tier3 = compute_data_confidence(
        best_source_tier=3, independent_source_count=2, days_since_last_verified=5,
        field_completeness=80, homepage_confidence=90, entity_resolution_confidence=90,
        extraction_confidence=90,
    )
    assert tier1 > tier3


def test_confidence_higher_with_more_corroboration():
    one_source = compute_data_confidence(
        best_source_tier=2, independent_source_count=1, days_since_last_verified=5,
        field_completeness=80,
    )
    three_sources = compute_data_confidence(
        best_source_tier=2, independent_source_count=3, days_since_last_verified=5,
        field_completeness=80,
    )
    assert three_sources > one_source


def test_confidence_penalized_by_contradictory_evidence():
    clean = compute_data_confidence(
        best_source_tier=1, independent_source_count=2, days_since_last_verified=5,
        field_completeness=90, has_contradictory_evidence=False,
    )
    contradicted = compute_data_confidence(
        best_source_tier=1, independent_source_count=2, days_since_last_verified=5,
        field_completeness=90, has_contradictory_evidence=True,
    )
    assert contradicted < clean


def test_confidence_degrades_with_staleness():
    fresh = compute_data_confidence(
        best_source_tier=2, independent_source_count=2, days_since_last_verified=1,
        field_completeness=70,
    )
    stale = compute_data_confidence(
        best_source_tier=2, independent_source_count=2, days_since_last_verified=95,
        field_completeness=70,
    )
    assert fresh > stale


def test_confidence_bounded_0_to_100():
    maxed = compute_data_confidence(
        best_source_tier=1, independent_source_count=10, days_since_last_verified=0,
        field_completeness=100, homepage_confidence=100, entity_resolution_confidence=100,
        extraction_confidence=100,
    )
    zeroed = compute_data_confidence(
        best_source_tier=None, independent_source_count=0, days_since_last_verified=None,
        field_completeness=0,
    )
    assert 0 <= maxed <= 100
    assert 0 <= zeroed <= 100
    assert zeroed < maxed


def test_no_resolution_confidence_available_scores_zero_for_that_component_not_skipped():
    with_resolution = compute_data_confidence(
        best_source_tier=2, independent_source_count=1, days_since_last_verified=5,
        field_completeness=50, homepage_confidence=100,
    )
    without_resolution = compute_data_confidence(
        best_source_tier=2, independent_source_count=1, days_since_last_verified=5,
        field_completeness=50,
    )
    assert with_resolution > without_resolution


def test_stale_warning_and_exclude_thresholds():
    assert is_stale_warning(31) is True
    assert is_stale_warning(30) is False
    assert is_stale_excluded(91) is True
    assert is_stale_excluded(90) is False
    assert is_stale_warning(None) is False
    assert is_stale_excluded(None) is False
