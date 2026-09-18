"""
Regenerates docs/examples/sample_quality_report.json from
scraper/quality_report.py against representative SYNTHETIC fixture data --
not a real run's output (this repo's v2 pipeline has not yet been run
against live services; see main_v2.py's module docstring).

The fixture numbers model a plausible early real run: regions well short of
the 50-company target (a first run naturally hasn't discovered everyone
yet), and a confidence distribution that intentionally FAILS the
min_pct_confidence_ge_70 launch threshold -- demonstrating that
should_publish_ranking's gate actually withholds a mediocre run rather than
rubber-stamping it (see MIGRATION.md's "Reading a failed/low-quality run").

Run: python docs/examples/generate_sample_quality_report.py
(from the repo root; adds scraper/ to sys.path itself)
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scraper"))

from quality_report import build_quality_report, should_publish_ranking  # noqa: E402

report = build_quality_report(
    score_version="v2",
    run_status="success",
    qualified_company_count_by_region={"US": 38, "INDIA": 24, "ROW": 19},
    rejected_candidate_count_by_reason={
        "no qualifying event within the 180-day candidate window": 41,
        "data confidence 52 below minimum ranking confidence 60": 19,
        "headquarters region is not verified as US/INDIA/ROW": 12,
        "primary domain is not verified": 7,
        "evidence relies solely on a weak (Tier 3) discovery source with no corroboration": 5,
    },
    missingness_by_field={"founders": 34.0, "employee_count_min": 61.0, "moat_summary": 100.0, "headquarters_city": 22.0},
    source_tier_distribution={"1": 14, "2": 156, "3": 203},
    invalid_homepage_count=0,
    ambiguous_funding_count=3,
    valuation_shown_as_funding_count=0,
    stale_record_count=2,
    duplicate_candidates_detected=6,
    confidence_values=[78, 82, 65, 91, 58, 73, 69, 85, 60, 77, 55, 88, 72, 63, 80, 90, 45, 76, 81, 67,
                        74, 86, 59, 71, 83, 66, 79, 62, 89, 70, 75, 84, 61, 87, 68, 64, 92, 57, 73, 80],
    pct_ranked_with_two_sources_or_authoritative=68.3,
    pct_ranked_with_verified_headquarters=100.0,
    pct_ranked_with_verified_funding_dates=71.6,
    pct_ranked_with_verified_employee_ranges=39.0,
    pct_ranked_with_verified_hiring_data=54.2,
    pct_ranked_with_recent_verified_event=100.0,
    pct_ranked_with_verified_regional_assignment=100.0,
    pct_ranked_with_valid_primary_domain=100.0,
    pct_material_funding_claims_with_evidence=100.0,
    llm_extraction_failure_rate=1.8,
    provider_fallback_usage={"gemini": 214, "groq": 9},
    generated_at="2026-02-21T06:00:00+00:00",  # pinned for a reproducible example file
)

should_publish, reason = should_publish_ranking(report)

out_path = os.path.join(os.path.dirname(__file__), "sample_quality_report.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(report.to_dict(), f, indent=2, default=str)
    f.write("\n")

print(f"Wrote {out_path}")
print(f"passed_launch_thresholds={report.passed_launch_thresholds}  should_publish={should_publish} ({reason})")
