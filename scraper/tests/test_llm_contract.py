from llm_contract import (
    evidence_overlap_ratio,
    validate_extraction_batch,
    validate_extraction_record,
)

SOURCE_TEXTS = [
    "Acme Robotics announced today that it has raised $5 million in a seed round led by Acme Ventures.",
    "Beta Corp is reportedly in talks to raise a new round at a higher valuation.",
]


def _valid_record(**overrides):
    record = {
        "event_type": "funding_round_completed",
        "value": "$5 million",
        "evidence_excerpt": "raised $5 million in a seed round led by Acme Ventures",
        "source_index": 1,
        "confidence": 85,
        "status": "completed",
    }
    record.update(overrides)
    return record


# ---- 26. Missing evidence rejection ----

def test_material_claim_missing_evidence_is_rejected():
    record = _valid_record(evidence_excerpt="")
    result = validate_extraction_record(record, batch_size=2, source_texts=SOURCE_TEXTS)
    assert result.valid is False
    assert any("evidence" in e.lower() for e in result.errors)


def test_non_material_event_still_requires_some_evidence_field():
    record = _valid_record(event_type="other", evidence_excerpt="")
    result = validate_extraction_record(record, batch_size=2, source_texts=SOURCE_TEXTS)
    assert result.valid is False


def test_valid_material_record_with_evidence_passes():
    record = _valid_record()
    result = validate_extraction_record(record, batch_size=2, source_texts=SOURCE_TEXTS)
    assert result.valid is True
    assert result.errors == []


def test_fabricated_evidence_not_present_in_source_is_rejected():
    record = _valid_record(evidence_excerpt="raised $500 million from SoftBank at a huge valuation")
    result = validate_extraction_record(record, batch_size=2, source_texts=SOURCE_TEXTS)
    assert result.valid is False
    assert any("does not appear to come from" in e for e in result.errors)


# ---- Schema/contract validation: source index, event type, status ----

def test_invalid_source_index_rejected():
    record = _valid_record(source_index=99)
    result = validate_extraction_record(record, batch_size=2, source_texts=SOURCE_TEXTS)
    assert result.valid is False
    assert any("source_index" in e for e in result.errors)


def test_zero_source_index_rejected_one_based():
    record = _valid_record(source_index=0)
    result = validate_extraction_record(record, batch_size=2)
    assert result.valid is False


def test_unsupported_event_type_rejected():
    record = _valid_record(event_type="ipo_announcement")
    result = validate_extraction_record(record, batch_size=2)
    assert result.valid is False
    assert any("event_type" in e for e in result.errors)


def test_unsupported_status_rejected():
    record = _valid_record(status="confirmed")
    result = validate_extraction_record(record, batch_size=2)
    assert result.valid is False
    assert any("status" in e for e in result.errors)


def test_confidence_out_of_range_rejected():
    record = _valid_record(confidence=150)
    result = validate_extraction_record(record, batch_size=2)
    assert result.valid is False


def test_missing_required_field_rejected():
    record = _valid_record()
    del record["confidence"]
    result = validate_extraction_record(record, batch_size=2)
    assert result.valid is False
    assert any("missing" in e.lower() for e in result.errors)


def test_completed_event_type_cannot_have_ambiguous_status():
    record = _valid_record(event_type="funding_round_completed", status="ambiguous")
    result = validate_extraction_record(record, batch_size=2, source_texts=SOURCE_TEXTS)
    assert result.valid is False
    assert any("ambiguous" in e.lower() for e in result.errors)


def test_proposed_funding_with_proposed_status_is_valid():
    record = _valid_record(
        event_type="proposed_funding",
        status="proposed",
        evidence_excerpt="reportedly in talks to raise a new round at a higher valuation",
        source_index=2,
    )
    result = validate_extraction_record(record, batch_size=2, source_texts=SOURCE_TEXTS)
    assert result.valid is True


def test_evidence_overlap_ratio_exact_quote_is_1():
    assert evidence_overlap_ratio(SOURCE_TEXTS[0], SOURCE_TEXTS[0]) == 1.0


def test_evidence_overlap_ratio_unrelated_text_is_low():
    ratio = evidence_overlap_ratio("completely unrelated made up sentence about spaceships", SOURCE_TEXTS[0])
    assert ratio < 0.3


def test_validate_extraction_batch_drops_invalid_and_keeps_valid():
    records = [
        _valid_record(),
        _valid_record(event_type="not_a_real_type"),
        _valid_record(source_index=2, evidence_excerpt="in talks to raise a new round at a higher valuation", event_type="proposed_funding", status="proposed"),
    ]
    accepted = validate_extraction_batch(records, batch_size=2, source_texts=SOURCE_TEXTS)
    assert len(accepted) == 2
    assert all("_validation" in r for r in accepted)
