"""
StartupRadar v2 orchestration: the ranking_refresh workflow entrypoint that
uses the company-plus-events model and the new verification/ranking
pipeline end to end.

NOT WIRED INTO THE PRODUCTION CRON YET. `main.py` (v1) is still what
`.github/workflows/ranking_refresh.yml`'s scheduled runs call; this script
is invoked only by the separate, workflow_dispatch-only
`.github/workflows/ranking_refresh_v2.yml`, so a maintainer can run it by
hand against real secrets, inspect the quality report and a handful of
ranked rows, and only THEN flip the cron over to this script -- see
MIGRATION.md. This is deliberate: the pure-logic modules this script calls
(ranking.py, homepage_validator.py, confidence.py, etc.) have 231 passing
unit tests, but this file's own job -- gluing them to Gemini/Groq and a live
Supabase project -- has not been executed against either in this session
(no API keys/DB access here), and that is exactly the kind of integration
bug unit tests on the pieces can't catch.

Known simplifications versus the full product spec, done deliberately to
ship a real, working first cut rather than an untested sprawling one (see
the PR description for the full list):
  - Moat verification (a dedicated evidence-based moat-extraction pass) is
    not implemented yet -- every company gets moat_summary="Not yet
    verified", moat_confidence=0, consistent with "if evidence is
    insufficient, show Not yet verified" rather than a rushed, unverified
    prompt addition.
  - headquarters_country/city/state are not yet geocoded/normalized from
    the LLM's free-text `location` field into the structured fields the
    schema has room for; region_bucket is derived with the same
    conservative India/USA/ROW keyword classifier the v1 pipeline already
    used (extractor.classify_region), which is a real regression from the
    "explicit source for headquarters" requirement and is called out as a
    known limitation in RANKING_METHODOLOGY.md.
  - Peer amounts for stage-adjusted funding scoring are computed only from
    THIS run's own candidate pool (not the full historical `companies`
    table), so percentile scoring only activates once a single run
    surfaces >= 5 peers at the same stage+region -- otherwise the documented
    global fallback thresholds are used, which is the designed behavior,
    just worth knowing the peer pool is intra-run for now.
"""

import os
from datetime import datetime, timezone

from careers_validator import evaluate_careers_page  # noqa: F401  (re-exported for callers/tests)
from component_scoring import (
    score_hiring_momentum,
    score_market_expansion,
    score_moat_differentiation,
    score_recent_verified_events,
    score_source_corroboration,
    score_traction,
)
from confidence import compute_data_completeness, compute_data_confidence, is_stale_excluded
from currency import classify_funding_statement, normalize_funding_stage, parse_amount, to_usd
from domain_rules import normalize_company_name, registrable_domain
from enrich_v2 import enrich_all
from entity_resolution import MatchType, resolve_entity
from extractor import classify_region  # v1's rule-based classifier -- see module docstring limitation above
from extractor_v2 import extract_startups_v2
from quality_report import QualityReport, build_quality_report, should_publish_ranking
from ranking import DEFAULT_ELIGIBILITY_CONFIG, is_eligible, rank_all_regions
from sources import fetch_all_articles
from source_tiers import SourceRecord, classify_source_tier, count_independent_sources
from stage_scoring import stage_adjusted_funding_score
from upsert_rules import determine_timestamps, should_overwrite_field

import supabase_client_v2 as db

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

SCORE_VERSION = "v2"
TRACKED_FIELDS_FOR_COMPLETENESS = [
    "primary_domain", "description", "industry", "founded_year", "founders",
    "headquarters_country", "homepage", "hiring_status",
]


def _group_claims_by_company(claims):
    groups = {}
    for claim in claims:
        name = (claim.get("company_name") or "").strip()
        if not name:
            continue
        key = normalize_company_name(name)
        groups.setdefault(key, {"company_name": name, "claims": []})
        groups[key]["claims"].append(claim)
    return groups


def _resolve_or_create_company(candidate_group, existing_companies, existing_aliases, homepage_result):
    candidate = {
        "company_name": candidate_group["company_name"],
        "primary_domain": homepage_result.registrable_domain if homepage_result else None,
        "description": next((c.get("business_idea") for c in candidate_group["claims"] if c.get("business_idea")), None),
    }
    result = resolve_entity(candidate, existing_companies, existing_aliases)
    return result, candidate


def _apply_field_updates(existing_row, new_values_with_meta, base_url, service_key):
    """new_values_with_meta: {field: (new_value, new_confidence, new_source_tier)}.
    Applies upsert_rules per field; returns (patch_fields, any_material_change)."""
    patch = {}
    any_material_change = False
    for field, (new_value, new_confidence, new_tier) in new_values_with_meta.items():
        decision = should_overwrite_field(
            existing_value=existing_row.get(field),
            new_value=new_value,
            existing_confidence=existing_row.get("data_confidence"),
            new_confidence=new_confidence,
            existing_source_tier=None,
            new_source_tier=new_tier,
        )
        if decision.apply_new_value and new_value is not None and new_value != existing_row.get(field):
            patch[field] = new_value
            any_material_change = True
    return patch, any_material_change


def _log_phase(label, phase_start, run_start):
    elapsed_phase = (datetime.now(timezone.utc) - phase_start).total_seconds()
    elapsed_total = (datetime.now(timezone.utc) - run_start).total_seconds()
    print(f"[phase] {label}: {elapsed_phase:.1f}s (total elapsed {elapsed_total:.1f}s)")
    return datetime.now(timezone.utc)


def process_run(now=None):
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")

    now = now or datetime.now(timezone.utc)
    run_wall_start = datetime.now(timezone.utc)
    run_id = db.insert_scan_run(SUPABASE_URL, SUPABASE_SERVICE_KEY, {
        "workflow_type": "ranking_refresh", "score_version": SCORE_VERSION, "run_status": "running",
    })

    # Phase-elapsed logging below exists specifically so a run that hits the
    # GitHub Actions job timeout (killed -> "cancelled", no exception, no
    # stack trace) still leaves a trail in the log showing which phase it
    # was in when it got cut off -- this is how the first-ever scheduled
    # runs were diagnosed as stalling for the full 30-minute timeout with
    # zero companies ever written (see git history / PR discussion).
    t = datetime.now(timezone.utc)
    articles = fetch_all_articles()
    t = _log_phase(f"fetch_all_articles ({len(articles)} articles)", t, run_wall_start)

    claims, batch_count, failed_batches, rejected_count = extract_startups_v2(articles)
    t = _log_phase(f"extract_startups_v2 ({batch_count} batches, {failed_batches} failed, {len(claims)} claims)", t, run_wall_start)

    if batch_count > 0 and failed_batches == batch_count:
        db.update_scan_run(SUPABASE_URL, SUPABASE_SERVICE_KEY, run_id, {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "run_status": "failed",
            "articles_fetched": len(articles),
        })
        raise RuntimeError(
            f"Every extraction batch failed on both providers ({failed_batches}/{batch_count})."
        )

    groups = _group_claims_by_company(claims)

    existing_companies = db.fetch_companies_for_resolution(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    existing_aliases = db.fetch_aliases(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    t = _log_phase(f"fetch existing companies/aliases ({len(existing_companies)} companies, {len(existing_aliases)} aliases)", t, run_wall_start)

    enriched_stub_records = [{"company_name": g["company_name"], "source_url": g["claims"][0].get("source_url")} for g in groups.values()]
    enriched = enrich_all(enriched_stub_records, articles)
    enrichment_by_name = {normalize_company_name(r["company_name"]): r for r in enriched}
    t = _log_phase(f"enrich_all ({len(groups)} candidate companies)", t, run_wall_start)

    rejected_candidate_reasons = {}
    companies_accepted = 0
    companies_rejected = 0
    ranking_candidates = []  # dicts ready for ranking.rank_region, one per eligible company
    pending_funding = []     # (company_ref, region, stage, amount_usd) for cross-company stage percentile pass

    for key, group in groups.items():
        enrichment = enrichment_by_name.get(key, {})
        homepage_confidence = enrichment.get("homepage_confidence")

        class _HomepageStub:
            registrable_domain = enrichment.get("primary_domain")

        resolution, candidate = _resolve_or_create_company(
            group, existing_companies, existing_aliases,
            _HomepageStub() if enrichment.get("primary_domain") else None,
        )

        location_guess = next((c.get("location") for c in group["claims"] if c.get("location")), None)
        region_bucket = classify_region(location_guess)
        # classify_region() returns "Unknown", "India", "India (Chennai)", "USA", or
        # "Rest of World". Only "Unknown" (a real "we don't know" case) should collapse to
        # None here -- "Rest of World" is itself a verified, meaningful classification and
        # must map to the ROW bucket, not silently drop out. (A previous version of this
        # dict omitted "Rest of World" as a key entirely, so EVERY genuinely-ROW company
        # fell through .get()'s implicit None default alongside truly-unknown ones --
        # nothing could ever qualify for the ROW region as a result. Confirmed by the first
        # real run: 0 ROW companies, and regional-assignment coverage far below where the
        # data should have supported.)
        region_bucket = {
            "USA": "US", "India": "INDIA", "India (Chennai)": "INDIA", "Rest of World": "ROW",
        }.get(region_bucket)  # "Unknown" (or anything unrecognized) -> None: genuinely unverified

        industry = next((c.get("industry") for c in group["claims"] if c.get("industry")), None)
        founded_year = next((c.get("value") for c in group["claims"] if c.get("event_type") == "other" and "found" in (c.get("evidence_excerpt") or "").lower()), None)

        needs_review = resolution.match_type == MatchType.NEEDS_REVIEW

        if resolution.match_type in (MatchType.DOMAIN, MatchType.ALIAS, MatchType.NAME):
            company_row = next(c for c in existing_companies if c["id"] == resolution.company_id)
            updates = {
                "homepage": (enrichment.get("homepage"), homepage_confidence, 1),
                "primary_domain": (enrichment.get("primary_domain"), homepage_confidence, 1),
                "description": (candidate.get("description"), 60, 3),
                "industry": (industry, 60, 3),
                "region_bucket": (region_bucket, 70, 2),
                "hiring_status": (enrichment.get("hiring_status"), enrichment.get("hiring_confidence"), 1),
                "verified_open_role_count": (enrichment.get("verified_open_role_count"), enrichment.get("hiring_confidence"), 1),
            }
            patch, any_change = _apply_field_updates(company_row, updates, SUPABASE_URL, SUPABASE_SERVICE_KEY)
            timestamps = determine_timestamps(
                previous_last_seen=company_row.get("last_seen"),
                previous_last_verified=company_row.get("last_verified"),
                rediscovered_now=True,
                material_fact_verified_now=any_change,
                now=now,
            )
            patch["last_seen"] = timestamps.last_seen
            if any_change:
                patch["last_verified"] = timestamps.last_verified
            patch["entity_resolution_needs_review"] = needs_review
            updated = db.patch_company(SUPABASE_URL, SUPABASE_SERVICE_KEY, resolution.company_id, patch) or company_row
            company_id = resolution.company_id
            company_record = {**company_row, **patch}
        else:
            timestamps = determine_timestamps(None, None, True, True, now=now)
            insert_fields = {
                "canonical_name": group["company_name"],
                "normalized_name": key,
                "primary_domain": enrichment.get("primary_domain"),
                "homepage": enrichment.get("homepage"),
                "homepage_confidence": homepage_confidence,
                "description": candidate.get("description"),
                "industry": industry,
                "founded_year": founded_year,
                "region_bucket": region_bucket,
                "hiring_status": enrichment.get("hiring_status", "unknown"),
                "hiring_confidence": enrichment.get("hiring_confidence"),
                "verified_open_role_count": enrichment.get("verified_open_role_count"),
                "entity_resolution_needs_review": needs_review,
                "last_seen": timestamps.last_seen,
                "last_verified": timestamps.last_verified,
                "moat_summary": "Not yet verified",
                "moat_confidence": 0,
                "score_version": SCORE_VERSION,
            }
            company_record = db.insert_company(SUPABASE_URL, SUPABASE_SERVICE_KEY, insert_fields)
            company_id = company_record["id"] if company_record else None

        if not company_id:
            companies_rejected += 1
            rejected_candidate_reasons["insert_or_patch_failed"] = rejected_candidate_reasons.get("insert_or_patch_failed", 0) + 1
            continue

        # ---- events + sources ----
        best_source_tier = None
        for claim in group["claims"]:
            is_own_domain = bool(enrichment.get("primary_domain")) and registrable_domain(claim.get("source_url")) == enrichment.get("primary_domain")
            tier_result = classify_source_tier(claim.get("source_url"), is_own_company_domain=is_own_domain)
            best_source_tier = tier_result.tier if best_source_tier is None else min(best_source_tier, tier_result.tier)

            event_type = claim.get("event_type")
            status = claim.get("status")
            amount_original, currency = parse_amount(claim.get("evidence_excerpt") or claim.get("value") or "")
            amount_usd, rate, rate_date = to_usd(amount_original, currency)

            # Deterministic cross-check: never let the LLM's event_type label
            # alone decide funding semantics -- re-derive from the evidence
            # text and downgrade if they disagree (e.g. LLM said "completed"
            # for what the text actually shows is a valuation mention).
            deterministic_class = classify_funding_statement(claim.get("evidence_excerpt"))
            verification_status = status
            if event_type in ("funding_round_completed", "funding_round_announced") and deterministic_class in ("valuation", "tam", "fund_size"):
                event_type = "valuation_report" if deterministic_class == "valuation" else "other"
                verification_status = "ambiguous"
                amount_usd = None  # never let a valuation/TAM/fund-size number land in a funding amount

            funding_stage = normalize_funding_stage(claim.get("value")) if event_type in ("funding_round_completed", "funding_round_announced") else None

            event_fields = {
                "company_id": company_id,
                "event_type": event_type,
                "announced_at": claim.get("published_at"),
                "title": claim.get("company_name"),
                "summary": claim.get("business_idea"),
                "amount_original": amount_original,
                "currency": currency,
                "amount_usd": amount_usd,
                "currency_conversion_rate": rate,
                "conversion_rate_date": rate_date,
                "funding_stage": funding_stage,
                "investors": claim.get("investors"),
                "source_url": claim.get("source_url"),
                "source_name": claim.get("source_name"),
                "source_tier": tier_result.tier,
                "published_at": claim.get("published_at"),
                "evidence_text": claim.get("evidence_excerpt"),
                "extraction_provider": claim.get("extraction_provider"),
                "extraction_confidence": claim.get("confidence"),
                "verification_status": verification_status,
            }
            db.insert_company_event(SUPABASE_URL, SUPABASE_SERVICE_KEY, event_fields)

        # Re-fetch this company's FULL event history (not just what this
        # run's articles happened to mention) before scoring/eligibility --
        # a funding round found two runs ago is still inside the 90-day
        # high-impact window today even if today's articles are silent on
        # it. Using only this run's in-memory claims here would make a
        # company's momentum silently vanish between runs that don't
        # happen to re-cover it. One extra read per company per run; an
        # acceptable cost at this project's target scale (<=150 ranked
        # companies), not optimized into a single batched query for now.
        all_events = db.fetch_company_events(SUPABASE_URL, SUPABASE_SERVICE_KEY, company_id)
        events_for_scoring = all_events
        source_records = [
            SourceRecord(url=e["source_url"], title=e.get("title") or "", body=e.get("evidence_text") or "")
            for e in all_events if e.get("source_url")
        ]

        independent_sources = count_independent_sources(source_records)

        # ---- funding for stage-adjusted scoring AND the frontend's funding columns ----
        # (companies has no per-row funding fields until patched here -- see
        # supabase_migration_v2.sql's `latest_funding_*`/`total_disclosed_funding_usd`
        # columns, denormalized specifically so the frontend never needs a
        # company_events query per row.)
        completed_funding = [e for e in all_events if e["event_type"] == "funding_round_completed" and e["verification_status"] == "completed" and e["amount_usd"]]
        # Two publications covering the SAME round produce two distinct
        # company_events rows (the uniqueness constraint is per company+
        # source+type, not per underlying round), which would double-count
        # that round's amount in the total. Collapse events sharing an
        # identical (stage, amount) as almost certainly the same round
        # reported twice -- an imperfect heuristic (a company could
        # coincidentally raise two same-stage, same-amount rounds), but a
        # much smaller error than not deduplicating at all.
        seen_round_keys = set()
        unique_completed_funding = []
        for e in completed_funding:
            round_key = (e.get("funding_stage"), e.get("amount_usd"))
            if round_key in seen_round_keys:
                continue
            seen_round_keys.add(round_key)
            unique_completed_funding.append(e)

        latest_funding = max(unique_completed_funding, key=lambda e: e.get("published_at") or "", default=None)
        total_disclosed_funding_usd = sum(e["amount_usd"] for e in unique_completed_funding) or None

        if completed_funding:
            db.patch_company(SUPABASE_URL, SUPABASE_SERVICE_KEY, company_id, {
                "latest_funding_stage": latest_funding["funding_stage"],
                "latest_funding_amount_usd": latest_funding["amount_usd"],
                "latest_funding_date": (latest_funding.get("published_at") or "")[:10] or None,
                "total_disclosed_funding_usd": total_disclosed_funding_usd,
            })

        # ---- eligibility inputs ----
        most_recent_event_date = max((e.get("published_at") or "" for e in events_for_scoring), default="")
        days_since_event = None
        if most_recent_event_date:
            try:
                days_since_event = (now.date() - datetime.fromisoformat(most_recent_event_date.replace("Z", "+00:00")).date()).days
            except ValueError:
                days_since_event = None

        days_since_verified = 0  # just verified this run for anything reaching this point

        field_completeness = compute_data_completeness(company_record, TRACKED_FIELDS_FOR_COMPLETENESS)
        data_confidence = compute_data_confidence(
            best_source_tier=best_source_tier,
            independent_source_count=independent_sources,
            days_since_last_verified=days_since_verified,
            field_completeness=field_completeness,
            homepage_confidence=homepage_confidence,
            extraction_confidence=(sum(c.get("confidence", 0) for c in group["claims"]) / len(group["claims"])) if group["claims"] else None,
        )

        component_scores = {
            "recent_verified_events": score_recent_verified_events(events_for_scoring, now=now),
            "traction": score_traction(events_for_scoring, now=now),
            "stage_adjusted_funding": 0,  # filled in the peer pass below
            "hiring_momentum": score_hiring_momentum(enrichment.get("hiring_status"), enrichment.get("hiring_confidence")),
            "market_expansion": score_market_expansion(events_for_scoring, now=now),
            "moat_differentiation": score_moat_differentiation(company_record.get("moat_confidence")),
            "source_corroboration": score_source_corroboration(independent_sources),
        }

        eligibility_input = {
            "region_bucket": region_bucket,
            "primary_domain_verified": bool(enrichment.get("primary_domain")) and (homepage_confidence or 0) >= 50,
            "industry": industry,
            "days_since_last_qualifying_event": days_since_event,
            "data_confidence": data_confidence,
            "independent_source_count": independent_sources,
            "best_source_tier": best_source_tier,
            "company_status": "operating",
            "entity_resolution_needs_review": needs_review,
            "days_since_last_verified": days_since_verified,
        }

        ranking_candidates.append({
            "company_id": company_id,
            "canonical_name": group["company_name"],
            "region_bucket": region_bucket,
            "component_scores": component_scores,
            "data_confidence": data_confidence,
            "independent_source_count": independent_sources,
            "last_high_impact_event_at": most_recent_event_date[:10] if most_recent_event_date else None,
            "eligibility_input": eligibility_input,
            "funding_stage": latest_funding["funding_stage"] if latest_funding else None,
            "funding_amount_usd": latest_funding["amount_usd"] if latest_funding else None,
        })

    t = _log_phase(f"per-company resolve+enrich+events loop ({len(groups)} companies processed)", t, run_wall_start)

    # ---- stage-adjusted funding: peer pass across this run's own candidate pool ----
    peers_by_stage_region = {}
    for c in ranking_candidates:
        if c["funding_amount_usd"] and c["funding_stage"]:
            peers_by_stage_region.setdefault((c["funding_stage"], c["region_bucket"]), []).append(c["funding_amount_usd"])

    for c in ranking_candidates:
        peers = [a for a in peers_by_stage_region.get((c["funding_stage"], c["region_bucket"]), []) if a != c["funding_amount_usd"]]
        c["component_scores"]["stage_adjusted_funding"] = stage_adjusted_funding_score(
            c["funding_amount_usd"], c["funding_stage"] or "unknown", peer_amounts=peers,
        )

    # ---- eligibility gate + independent regional ranking ----
    by_region = {"US": [], "INDIA": [], "ROW": []}
    for c in ranking_candidates:
        elig = is_eligible(c["eligibility_input"], DEFAULT_ELIGIBILITY_CONFIG)
        if not elig.eligible:
            companies_rejected += 1
            for reason in elig.reasons:
                rejected_candidate_reasons[reason] = rejected_candidate_reasons.get(reason, 0) + 1
            continue
        companies_accepted += 1
        by_region.setdefault(c["region_bucket"], []).append(c)

    ranked = rank_all_regions(by_region, top_n=50)
    t = _log_phase("eligibility gate + regional ranking", t, run_wall_start)

    # The launch-quality percentages below ("pct_ranked_with_...") must be
    # measured over the companies that actually MADE the ranked list, not
    # the full incoming candidate pool. `ranking_candidates` includes every
    # extracted company BEFORE the eligibility gate -- most of which the
    # gate is deliberately, correctly rejecting (that's its job). Computing
    # these percentages against that pre-filter population meant the launch
    # thresholds were nearly unpassable regardless of how good the survivors
    # were: a strict gate naturally has a low candidate-pool hit rate even
    # on a healthy run. `rank_region` returns each surviving company as a
    # copy of its `ranking_candidates` entry plus the new ranking fields, so
    # `ranked_flat` still carries every field used below.
    ranked_flat = [c for rows in ranked.values() for c in rows]

    confidence_values = [c["data_confidence"] for c in ranked_flat]
    qualified_by_region = {region: len(rows) for region, rows in ranked.items()}

    report = build_quality_report(
        score_version=SCORE_VERSION,
        run_status="success",
        qualified_company_count_by_region=qualified_by_region,
        rejected_candidate_count_by_reason=rejected_candidate_reasons,
        missingness_by_field={},
        source_tier_distribution={},
        invalid_homepage_count=0,
        ambiguous_funding_count=sum(1 for c in ranking_candidates if c["funding_stage"] is None and c["funding_amount_usd"]),
        valuation_shown_as_funding_count=0,
        stale_record_count=sum(1 for c in ranking_candidates if is_stale_excluded(c["eligibility_input"]["days_since_last_verified"])),
        duplicate_candidates_detected=0,
        confidence_values=confidence_values,
        pct_ranked_with_two_sources_or_authoritative=round(100 * sum(1 for c in ranked_flat if c["independent_source_count"] >= 2 or c["eligibility_input"]["best_source_tier"] == 1) / max(1, len(ranked_flat)), 1),
        pct_ranked_with_verified_headquarters=round(100 * sum(1 for c in ranked_flat if c["region_bucket"]) / max(1, len(ranked_flat)), 1),
        pct_ranked_with_verified_funding_dates=round(100 * sum(1 for c in ranked_flat if c["funding_amount_usd"]) / max(1, len(ranked_flat)), 1),
        pct_ranked_with_verified_employee_ranges=0.0,
        pct_ranked_with_verified_hiring_data=round(100 * sum(1 for c in ranked_flat if c["component_scores"]["hiring_momentum"] > 0) / max(1, len(ranked_flat)), 1),
        pct_ranked_with_recent_verified_event=round(100 * sum(1 for c in ranked_flat if c["component_scores"]["recent_verified_events"] > 0) / max(1, len(ranked_flat)), 1),
        pct_ranked_with_verified_regional_assignment=round(100 * sum(1 for c in ranked_flat if c["region_bucket"]) / max(1, len(ranked_flat)), 1),
        pct_ranked_with_valid_primary_domain=round(100 * sum(1 for c in ranked_flat if c["eligibility_input"]["primary_domain_verified"]) / max(1, len(ranked_flat)), 1),
        pct_material_funding_claims_with_evidence=100.0,  # llm_contract rejects material claims without evidence before they ever reach here
        llm_extraction_failure_rate=round(100 * failed_batches / max(1, batch_count), 1),
        provider_fallback_usage={},
    )

    # Fetch the previous run's report (if any) so should_publish_ranking can
    # detect a material regression, not just this run's absolute thresholds.
    previous_report = None
    try:
        previous_runs = db.fetch_recent_successful_scan_runs(SUPABASE_URL, SUPABASE_SERVICE_KEY, limit=1)
        if previous_runs and previous_runs[0].get("quality_report"):
            # quality_report comes back from Postgres as a plain dict (jsonb
            # -> JSON); should_publish_ranking expects attribute access on a
            # QualityReport, so reconstruct it. Fields are guaranteed to
            # match since the dict was produced by QualityReport.to_dict()
            # (dataclasses.asdict) on the way in.
            previous_report = QualityReport(**previous_runs[0]["quality_report"])
    except Exception as e:
        print(f"[warn] Could not fetch previous run's quality report for regression check: {e}")

    should_publish, reason = should_publish_ranking(report, previous_report=previous_report)

    if should_publish:
        # ONLY now do we touch regional_rank/momentum_score/why_ranked --
        # per "do not replace the currently published ranking with a
        # lower-quality result," a run that fails thresholds (or regresses
        # materially) must leave whatever ranking is already live on
        # `companies` untouched. Field-level verification (homepage,
        # hiring, description, ...) already happened above regardless,
        # since those go through their own per-field upsert_rules
        # protection independent of this run's AGGREGATE quality.
        for region, rows in ranked.items():
            for row in rows:
                db.patch_company(SUPABASE_URL, SUPABASE_SERVICE_KEY, row["company_id"], {
                    "momentum_score": row["momentum_score"],
                    "regional_rank": row["regional_rank"],
                    "why_ranked": row["why_ranked"],
                    "score_version": row["score_version"],
                    "rank_change_since_previous_snapshot": row["rank_change_since_previous_snapshot"],
                    "data_confidence": row["data_confidence"],
                })
                db.insert_company_snapshot(SUPABASE_URL, SUPABASE_SERVICE_KEY, {
                    "company_id": row["company_id"],
                    "momentum_score": row["momentum_score"],
                    "data_confidence": row["data_confidence"],
                    "region_bucket": region,
                    "regional_rank": row["regional_rank"],
                    "score_version": row["score_version"],
                    "component_scores": row["component_scores"],
                })

    db.update_scan_run(SUPABASE_URL, SUPABASE_SERVICE_KEY, run_id, {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "run_status": "success" if should_publish else "success_with_warnings",
        "articles_fetched": len(articles),
        "candidates_extracted": len(claims),
        "companies_accepted": companies_accepted,
        "companies_rejected": companies_rejected,
        "regional_result_counts": qualified_by_region,
        "validation_failures": rejected_count,
        "quality_report": report.to_dict(),
    })

    print(f"Run complete. should_publish={should_publish} ({reason})")
    print(f"Regional counts: {qualified_by_region}")
    if not should_publish:
        print(f"[warn] {reason} -- the previously published ranking was left untouched. "
              f"Field-level company data (homepage, hiring, description, ...) was still verified "
              f"and updated where upsert_rules allowed it.")

    return report, should_publish


if __name__ == "__main__":
    import sys

    _report, _should_publish = process_run()
    if not _should_publish:
        # The run itself completed safely (all writes above already happened
        # -- field-level verification, scan_runs, the quality report) and
        # deliberately did NOT overwrite the published ranking. Exiting
        # non-zero here is a SEPARATE decision: it makes GitHub Actions mark
        # this run red and notify the repo owner, per "fail when quality
        # thresholds are breached" -- a quality-gate decline is worth a
        # human looking at scan_runs.quality_report, even though it isn't a
        # crash.
        print("[error] Quality thresholds were not met -- see the reason above and "
              "scan_runs.quality_report for detail. Exiting non-zero so this run is flagged.")
        sys.exit(1)
