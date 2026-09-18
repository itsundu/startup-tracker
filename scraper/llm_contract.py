"""
Strict schema validation for LLM extraction output.

The LLM is an extraction assistant, not a factual source (product principle
#1) -- so its output is never trusted just because it parsed as JSON. Every
record must additionally: cite a valid source index, include an evidence
excerpt for any material claim, have that excerpt actually traceable to the
cited source text, use a supported event_type/status vocabulary, and carry a
confidence in range. A record failing any check is REJECTED (dropped from
this run), never silently repaired or defaulted -- a rejected record simply
doesn't overwrite anything (see upsert_rules.py for how that interacts with
previously verified data).
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

EVENT_TYPES = {
    "funding_round_completed", "funding_round_announced", "proposed_funding",
    "valuation_report", "acquisition", "product_launch", "major_product_release",
    "customer_win", "partnership", "geographic_expansion", "executive_change",
    "hiring_growth", "layoffs", "shutdown", "other",
}

# Event types that make a material, fact-like claim and therefore REQUIRE a
# non-empty, source-traceable evidence excerpt. Non-material types ("other")
# are still required to carry the field, but empty evidence there is a
# rejection too -- there's no such thing as an evidence-free record.
MATERIAL_EVENT_TYPES = {
    "funding_round_completed", "funding_round_announced", "proposed_funding",
    "valuation_report", "acquisition", "customer_win", "layoffs", "shutdown",
}

STATUS_VALUES = {"completed", "proposed", "rumored", "abandoned", "ambiguous"}

REQUIRED_FIELDS = {"event_type", "value", "evidence_excerpt", "source_index", "confidence", "status"}

# Minimum token-overlap ratio between the claimed evidence_excerpt and the
# actual cited source text for the excerpt to count as "traceable." Not 1.0
# (exact substring) because the LLM may normalize whitespace/quotes when
# quoting; not much lower, or a fabricated excerpt could slip through on
# common-word overlap alone.
MIN_EVIDENCE_OVERLAP = 0.6


@dataclass
class ExtractionValidationResult:
    valid: bool
    errors: List[str] = field(default_factory=list)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+", _normalize(text)))


def evidence_overlap_ratio(evidence_excerpt: str, source_text: str) -> float:
    """Fraction of the evidence excerpt's word tokens that also appear in
    the source text. 1.0 for an exact quote; degrades gracefully for minor
    paraphrasing; near 0 for a fabricated/unrelated excerpt."""
    ev_tokens = _tokens(evidence_excerpt)
    if not ev_tokens:
        return 0.0
    src_tokens = _tokens(source_text)
    if not src_tokens:
        return 0.0
    return len(ev_tokens & src_tokens) / len(ev_tokens)


def validate_extraction_record(
    record: Dict,
    batch_size: int,
    source_texts: Optional[List[str]] = None,
) -> ExtractionValidationResult:
    """`source_texts` is the batch's original article texts, 1-indexed by
    `source_index` (i.e. source_texts[source_index - 1]). When provided,
    evidence-excerpt traceability is enforced; when omitted, that one check
    is skipped (useful for validating shape alone, e.g. in the LLM-facing
    unit tests where only the contract shape is under test).
    """
    errors: List[str] = []

    missing = REQUIRED_FIELDS - set(record.keys())
    if missing:
        errors.append(f"missing required field(s): {sorted(missing)}")

    event_type = record.get("event_type")
    if event_type not in EVENT_TYPES:
        errors.append(f"unsupported event_type '{event_type}'")

    status = record.get("status")
    if status not in STATUS_VALUES:
        errors.append(f"unsupported status '{status}' (must be one of {sorted(STATUS_VALUES)})")

    source_index = record.get("source_index")
    if not isinstance(source_index, int) or not (1 <= source_index <= max(batch_size, 1)):
        errors.append(f"source_index {source_index!r} is not a valid 1-based index into a batch of size {batch_size}")

    confidence = record.get("confidence")
    if confidence is None or not isinstance(confidence, (int, float)) or not (0 <= confidence <= 100):
        errors.append(f"confidence {confidence!r} is not a number in [0, 100]")

    evidence = (record.get("evidence_excerpt") or "").strip()
    if event_type in MATERIAL_EVENT_TYPES and not evidence:
        errors.append(f"event_type '{event_type}' is material and requires a non-empty evidence_excerpt")
    elif not evidence and "evidence_excerpt" in record:
        errors.append("evidence_excerpt is empty")

    if evidence and source_texts and isinstance(source_index, int) and 1 <= source_index <= len(source_texts):
        source_text = source_texts[source_index - 1] or ""
        ratio = evidence_overlap_ratio(evidence, source_text)
        if ratio < MIN_EVIDENCE_OVERLAP:
            errors.append(
                f"evidence_excerpt does not appear to come from the cited source "
                f"(token overlap {ratio:.2f} < {MIN_EVIDENCE_OVERLAP})"
            )

    if status == "ambiguous" and event_type in {"funding_round_completed", "acquisition"}:
        errors.append(
            f"event_type '{event_type}' claims a completed transaction but status is 'ambiguous' -- "
            "funding semantics must not be ambiguous for a *_completed event_type"
        )

    return ExtractionValidationResult(valid=len(errors) == 0, errors=errors)


def validate_extraction_batch(
    records: List[Dict],
    batch_size: int,
    source_texts: Optional[List[str]] = None,
) -> List[Dict]:
    """Returns only the records that pass validation, each annotated with
    `_validation` (the ExtractionValidationResult) for logging/quality-report
    purposes. Rejected records are dropped, not repaired."""
    accepted = []
    for record in records:
        result = validate_extraction_record(record, batch_size, source_texts)
        if result.valid:
            record = dict(record)
            record["_validation"] = result
            accepted.append(record)
    return accepted
