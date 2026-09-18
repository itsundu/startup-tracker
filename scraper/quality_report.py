"""
Post-run quality report + launch-threshold gate.

This module only aggregates and judges numbers the pipeline hands it -- it
does not compute confidence/homepage validity/etc. itself (see
confidence.py, homepage_validator.py, source_tiers.py for that). Keeping it
pure aggregation makes it trivially testable against synthetic run stats,
without needing a live scraper run.

The report is "sanitized" by construction: it contains only counts,
percentages, and category labels -- never raw extraction prompts,
administrative notes, article text, or provider request/response bodies. Do
not add a field here that could leak such content; anything free-text should
be a short reason label (e.g. "content type not HTML"), not raw payloads.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

TARGET_COMPANIES_PER_REGION = 50

# Suggested minimum launch thresholds from the ranking methodology. A run
# that fails these does not overwrite the currently published ranking (see
# `passed_launch_thresholds` / `threshold_failures` below and main.py's use
# of them) -- it still records the run (scan_runs) and surfaces a warning.
LAUNCH_THRESHOLDS = {
    "min_pct_verified_regional_assignment": 100.0,
    "min_pct_valid_primary_domain": 100.0,
    "min_pct_material_funding_claims_with_evidence": 100.0,
    "max_asset_or_cdn_homepage_count": 0,
    "min_pct_confidence_ge_70": 80.0,
    "min_pct_with_recent_verified_event": 70.0,
    "min_pct_two_sources_or_one_authoritative": 60.0,
    "max_valuation_shown_as_funding_count": 0,
}


def _pct(numerator: int, denominator: int) -> float:
    if not denominator:
        return 0.0
    return round(100.0 * numerator / denominator, 1)


def confidence_distribution(confidence_values: List[int]) -> Dict[str, int]:
    """Buckets a list of per-company data_confidence scores into 0-19 /
    20-39 / 40-59 / 60-79 / 80-100 bands."""
    bands = {"0-19": 0, "20-39": 0, "40-59": 0, "60-79": 0, "80-100": 0}
    for v in confidence_values:
        v = max(0, min(100, v or 0))
        if v < 20:
            bands["0-19"] += 1
        elif v < 40:
            bands["20-39"] += 1
        elif v < 60:
            bands["40-59"] += 1
        elif v < 80:
            bands["60-79"] += 1
        else:
            bands["80-100"] += 1
    return bands


@dataclass
class QualityReport:
    generated_at: str
    score_version: str
    run_status: str

    qualified_company_count_by_region: Dict[str, int]
    shortfall_by_region: Dict[str, int]

    rejected_candidate_count_by_reason: Dict[str, int]
    missingness_by_field: Dict[str, float]
    source_tier_distribution: Dict[str, int]

    invalid_homepage_count: int
    ambiguous_funding_count: int
    valuation_shown_as_funding_count: int
    stale_record_count: int
    duplicate_candidates_detected: int

    confidence_distribution: Dict[str, int]

    pct_ranked_with_confidence_ge_70: float
    pct_ranked_with_two_sources_or_authoritative: float
    pct_ranked_with_verified_headquarters: float
    pct_ranked_with_verified_funding_dates: float
    pct_ranked_with_verified_employee_ranges: float
    pct_ranked_with_verified_hiring_data: float
    pct_ranked_with_recent_verified_event: float
    pct_ranked_with_verified_regional_assignment: float
    pct_ranked_with_valid_primary_domain: float
    pct_material_funding_claims_with_evidence: float

    llm_extraction_failure_rate: float
    provider_fallback_usage: Dict[str, int]

    passed_launch_thresholds: bool = field(default=False)
    threshold_failures: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)


def _check_thresholds(report_fields: Dict) -> (bool, List[str]):
    failures = []

    if report_fields["pct_ranked_with_verified_regional_assignment"] < LAUNCH_THRESHOLDS["min_pct_verified_regional_assignment"]:
        failures.append(
            f"regional assignment coverage {report_fields['pct_ranked_with_verified_regional_assignment']}% "
            f"< required {LAUNCH_THRESHOLDS['min_pct_verified_regional_assignment']}%"
        )
    if report_fields["pct_ranked_with_valid_primary_domain"] < LAUNCH_THRESHOLDS["min_pct_valid_primary_domain"]:
        failures.append(
            f"valid primary domain coverage {report_fields['pct_ranked_with_valid_primary_domain']}% "
            f"< required {LAUNCH_THRESHOLDS['min_pct_valid_primary_domain']}%"
        )
    if report_fields["pct_material_funding_claims_with_evidence"] < LAUNCH_THRESHOLDS["min_pct_material_funding_claims_with_evidence"]:
        failures.append(
            f"material funding claims with evidence {report_fields['pct_material_funding_claims_with_evidence']}% "
            f"< required {LAUNCH_THRESHOLDS['min_pct_material_funding_claims_with_evidence']}%"
        )
    if report_fields["invalid_homepage_count"] > LAUNCH_THRESHOLDS["max_asset_or_cdn_homepage_count"]:
        failures.append(
            f"{report_fields['invalid_homepage_count']} invalid (asset/CDN) homepage(s) found "
            f"(max allowed {LAUNCH_THRESHOLDS['max_asset_or_cdn_homepage_count']})"
        )
    if report_fields["pct_ranked_with_confidence_ge_70"] < LAUNCH_THRESHOLDS["min_pct_confidence_ge_70"]:
        failures.append(
            f"only {report_fields['pct_ranked_with_confidence_ge_70']}% of ranked records have confidence >= 70 "
            f"(required {LAUNCH_THRESHOLDS['min_pct_confidence_ge_70']}%)"
        )
    if report_fields["pct_ranked_with_recent_verified_event"] < LAUNCH_THRESHOLDS["min_pct_with_recent_verified_event"]:
        failures.append(
            f"only {report_fields['pct_ranked_with_recent_verified_event']}% have a verified recent event "
            f"(required {LAUNCH_THRESHOLDS['min_pct_with_recent_verified_event']}%)"
        )
    if report_fields["pct_ranked_with_two_sources_or_authoritative"] < LAUNCH_THRESHOLDS["min_pct_two_sources_or_one_authoritative"]:
        failures.append(
            f"only {report_fields['pct_ranked_with_two_sources_or_authoritative']}% have 2+ sources or 1 "
            f"authoritative source (required {LAUNCH_THRESHOLDS['min_pct_two_sources_or_one_authoritative']}%)"
        )
    if report_fields["valuation_shown_as_funding_count"] > LAUNCH_THRESHOLDS["max_valuation_shown_as_funding_count"]:
        failures.append(
            f"{report_fields['valuation_shown_as_funding_count']} record(s) show a valuation as funding raised"
        )

    return len(failures) == 0, failures


def build_quality_report(
    *,
    score_version: str,
    run_status: str,
    qualified_company_count_by_region: Dict[str, int],
    rejected_candidate_count_by_reason: Dict[str, int],
    missingness_by_field: Dict[str, float],
    source_tier_distribution: Dict[str, int],
    invalid_homepage_count: int,
    ambiguous_funding_count: int,
    valuation_shown_as_funding_count: int,
    stale_record_count: int,
    duplicate_candidates_detected: int,
    confidence_values: List[int],
    pct_ranked_with_two_sources_or_authoritative: float,
    pct_ranked_with_verified_headquarters: float,
    pct_ranked_with_verified_funding_dates: float,
    pct_ranked_with_verified_employee_ranges: float,
    pct_ranked_with_verified_hiring_data: float,
    pct_ranked_with_recent_verified_event: float,
    pct_ranked_with_verified_regional_assignment: float,
    pct_ranked_with_valid_primary_domain: float,
    pct_material_funding_claims_with_evidence: float,
    llm_extraction_failure_rate: float,
    provider_fallback_usage: Dict[str, int],
    generated_at: Optional[str] = None,
) -> QualityReport:
    dist = confidence_distribution(confidence_values)
    total = len(confidence_values) or 1
    pct_confidence_ge_70 = _pct(sum(1 for v in confidence_values if (v or 0) >= 70), total)

    shortfall_by_region = {
        region: max(0, TARGET_COMPANIES_PER_REGION - count)
        for region, count in qualified_company_count_by_region.items()
    }

    fields = dict(
        generated_at=generated_at or datetime.now(timezone.utc).isoformat(),
        score_version=score_version,
        run_status=run_status,
        qualified_company_count_by_region=qualified_company_count_by_region,
        shortfall_by_region=shortfall_by_region,
        rejected_candidate_count_by_reason=rejected_candidate_count_by_reason,
        missingness_by_field=missingness_by_field,
        source_tier_distribution=source_tier_distribution,
        invalid_homepage_count=invalid_homepage_count,
        ambiguous_funding_count=ambiguous_funding_count,
        valuation_shown_as_funding_count=valuation_shown_as_funding_count,
        stale_record_count=stale_record_count,
        duplicate_candidates_detected=duplicate_candidates_detected,
        confidence_distribution=dist,
        pct_ranked_with_confidence_ge_70=pct_confidence_ge_70,
        pct_ranked_with_two_sources_or_authoritative=pct_ranked_with_two_sources_or_authoritative,
        pct_ranked_with_verified_headquarters=pct_ranked_with_verified_headquarters,
        pct_ranked_with_verified_funding_dates=pct_ranked_with_verified_funding_dates,
        pct_ranked_with_verified_employee_ranges=pct_ranked_with_verified_employee_ranges,
        pct_ranked_with_verified_hiring_data=pct_ranked_with_verified_hiring_data,
        pct_ranked_with_recent_verified_event=pct_ranked_with_recent_verified_event,
        pct_ranked_with_verified_regional_assignment=pct_ranked_with_verified_regional_assignment,
        pct_ranked_with_valid_primary_domain=pct_ranked_with_valid_primary_domain,
        pct_material_funding_claims_with_evidence=pct_material_funding_claims_with_evidence,
        llm_extraction_failure_rate=llm_extraction_failure_rate,
        provider_fallback_usage=provider_fallback_usage,
    )

    passed, failures = _check_thresholds(fields)
    return QualityReport(passed_launch_thresholds=passed, threshold_failures=failures, **fields)


def should_publish_ranking(report: QualityReport, previous_report: Optional[QualityReport] = None) -> (bool, str):
    """Decides whether this run's ranking should replace the currently
    published one. A run that fails launch thresholds still gets recorded
    (scan_runs) but must NOT overwrite a better existing ranking -- 'do not
    replace the currently published ranking with a lower-quality result.'
    """
    if not report.passed_launch_thresholds:
        return False, "run failed launch quality thresholds: " + "; ".join(report.threshold_failures)

    if previous_report is not None:
        regressed = (
            report.pct_ranked_with_confidence_ge_70 < previous_report.pct_ranked_with_confidence_ge_70 - 10
            or sum(report.qualified_company_count_by_region.values())
            < sum(previous_report.qualified_company_count_by_region.values()) * 0.5
        )
        if regressed:
            return False, "run quality regressed materially versus the previously published run"

    return True, "run passed all launch thresholds"
