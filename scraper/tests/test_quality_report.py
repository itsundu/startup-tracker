from quality_report import LAUNCH_THRESHOLDS, build_quality_report, confidence_distribution, should_publish_ranking


def _good_report_kwargs(**overrides):
    kwargs = dict(
        score_version="v2",
        run_status="success",
        qualified_company_count_by_region={"US": 50, "INDIA": 42, "ROW": 35},
        rejected_candidate_count_by_reason={"invalid_homepage": 3, "stale": 1},
        missingness_by_field={"founder_name": 20.0, "employee_count_min": 40.0},
        source_tier_distribution={"1": 30, "2": 90, "3": 20},
        invalid_homepage_count=0,
        ambiguous_funding_count=2,
        valuation_shown_as_funding_count=0,
        stale_record_count=1,
        duplicate_candidates_detected=4,
        confidence_values=[85, 90, 75, 60, 95, 88, 72, 91],
        pct_ranked_with_two_sources_or_authoritative=75.0,
        pct_ranked_with_verified_headquarters=95.0,
        pct_ranked_with_verified_funding_dates=80.0,
        pct_ranked_with_verified_employee_ranges=50.0,
        pct_ranked_with_verified_hiring_data=60.0,
        pct_ranked_with_recent_verified_event=85.0,
        pct_ranked_with_verified_regional_assignment=100.0,
        pct_ranked_with_valid_primary_domain=100.0,
        pct_material_funding_claims_with_evidence=100.0,
        llm_extraction_failure_rate=2.5,
        provider_fallback_usage={"gemini": 120, "groq": 5},
    )
    kwargs.update(overrides)
    return kwargs


def test_confidence_distribution_buckets_correctly():
    dist = confidence_distribution([5, 25, 45, 65, 85, 99, 0, 100])
    assert dist["0-19"] == 2
    assert dist["20-39"] == 1
    assert dist["40-59"] == 1
    assert dist["60-79"] == 1
    assert dist["80-100"] == 3


def test_good_run_passes_all_launch_thresholds():
    report = build_quality_report(**_good_report_kwargs())
    assert report.passed_launch_thresholds is True
    assert report.threshold_failures == []


def test_run_with_invalid_homepages_fails_threshold():
    report = build_quality_report(**_good_report_kwargs(invalid_homepage_count=3))
    assert report.passed_launch_thresholds is False
    assert any("invalid" in f.lower() for f in report.threshold_failures)


def test_run_with_valuation_shown_as_funding_fails_threshold():
    report = build_quality_report(**_good_report_kwargs(valuation_shown_as_funding_count=1))
    assert report.passed_launch_thresholds is False
    assert any("valuation" in f.lower() for f in report.threshold_failures)


def test_run_with_low_regional_verification_fails_threshold():
    report = build_quality_report(**_good_report_kwargs(pct_ranked_with_verified_regional_assignment=90.0))
    assert report.passed_launch_thresholds is False


def test_run_with_low_confidence_coverage_fails_threshold():
    # Only 2/8 records at confidence >= 70 -- well under 80%.
    report = build_quality_report(**_good_report_kwargs(confidence_values=[10, 20, 30, 40, 50, 60, 65, 69]))
    assert report.passed_launch_thresholds is False
    assert any("confidence" in f.lower() for f in report.threshold_failures)


def test_should_publish_ranking_true_for_passing_report():
    report = build_quality_report(**_good_report_kwargs())
    should_publish, reason = should_publish_ranking(report)
    assert should_publish is True


def test_should_publish_ranking_false_for_failing_report():
    report = build_quality_report(**_good_report_kwargs(invalid_homepage_count=5))
    should_publish, reason = should_publish_ranking(report)
    assert should_publish is False
    assert "threshold" in reason.lower()


def test_should_publish_ranking_false_on_material_regression_vs_previous():
    previous = build_quality_report(**_good_report_kwargs())
    regressed = build_quality_report(**_good_report_kwargs(
        qualified_company_count_by_region={"US": 5, "INDIA": 3, "ROW": 2},
    ))
    should_publish, reason = should_publish_ranking(regressed, previous_report=previous)
    assert should_publish is False


def test_launch_thresholds_are_documented_and_nonzero_dict():
    assert LAUNCH_THRESHOLDS["min_pct_confidence_ge_70"] == 80.0
    assert LAUNCH_THRESHOLDS["max_asset_or_cdn_homepage_count"] == 0
