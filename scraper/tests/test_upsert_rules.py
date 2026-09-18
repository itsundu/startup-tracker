from datetime import datetime, timezone

from upsert_rules import determine_timestamps, should_overwrite_field


# ---- 16. Preservation of verified values during upsert ----

def test_new_null_never_overwrites_existing_value():
    decision = should_overwrite_field(existing_value="Jane Doe", new_value=None)
    assert decision.apply_new_value is False


def test_new_unknown_string_never_overwrites_existing_value():
    decision = should_overwrite_field(existing_value="Seed", new_value="Unknown")
    # "Unknown" is a null-equivalent sentinel via is_populated(), so it must
    # not overwrite either -- but should_overwrite_field itself only checks
    # is_populated(new_value), and "Unknown" IS caught by is_populated.
    assert decision.apply_new_value is False


def test_existing_null_accepts_new_value_regardless_of_confidence():
    decision = should_overwrite_field(
        existing_value=None, new_value="Seed",
        existing_confidence=None, new_confidence=20,
    )
    assert decision.apply_new_value is True


def test_higher_confidence_existing_value_is_preserved_against_lower_confidence_new_value():
    decision = should_overwrite_field(
        existing_value="Series B", new_value="Series A",
        existing_confidence=90, new_confidence=40,
        existing_source_tier=1, new_source_tier=3,
    )
    assert decision.apply_new_value is False
    assert decision.store_as_contradiction is True


def test_equal_or_better_authority_overwrites():
    decision = should_overwrite_field(
        existing_value="Series A", new_value="Series B",
        existing_confidence=50, new_confidence=50,
        existing_source_tier=3, new_source_tier=1,  # new source is MORE authoritative (tier 1 < tier 3)
    )
    assert decision.apply_new_value is True


def test_equal_or_better_confidence_overwrites():
    decision = should_overwrite_field(
        existing_value="Series A", new_value="Series B",
        existing_confidence=40, new_confidence=80,
        existing_source_tier=2, new_source_tier=2,
    )
    assert decision.apply_new_value is True


def test_reconfirmation_of_same_value_is_accepted():
    decision = should_overwrite_field(existing_value="Seed", new_value="Seed")
    assert decision.apply_new_value is True


def test_clearly_newer_evidence_flag_forces_overwrite():
    decision = should_overwrite_field(
        existing_value="Series A", new_value="Series B",
        existing_confidence=90, new_confidence=40,
        existing_source_tier=1, new_source_tier=3,
        new_evidence_is_clearly_newer=True,
    )
    assert decision.apply_new_value is True


# ---- 17. Correct update of last_seen and last_verified ----

def test_last_seen_bumps_on_rediscovery_even_without_verification():
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    result = determine_timestamps(
        previous_last_seen="2026-01-01T00:00:00+00:00",
        previous_last_verified="2026-01-01T00:00:00+00:00",
        rediscovered_now=True,
        material_fact_verified_now=False,
        now=now,
    )
    assert result.last_seen == now.isoformat()
    assert result.last_verified == "2026-01-01T00:00:00+00:00"  # unchanged -- nothing verified this run


def test_last_verified_only_bumps_when_something_was_actually_verified():
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    result = determine_timestamps(
        previous_last_seen="2026-01-01T00:00:00+00:00",
        previous_last_verified="2026-01-01T00:00:00+00:00",
        rediscovered_now=True,
        material_fact_verified_now=True,
        now=now,
    )
    assert result.last_verified == now.isoformat()


def test_not_rediscovered_preserves_previous_last_seen():
    result = determine_timestamps(
        previous_last_seen="2026-01-01T00:00:00+00:00",
        previous_last_verified="2026-01-01T00:00:00+00:00",
        rediscovered_now=False,
        material_fact_verified_now=False,
    )
    assert result.last_seen == "2026-01-01T00:00:00+00:00"


def test_brand_new_company_gets_now_for_both_timestamps():
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    result = determine_timestamps(
        previous_last_seen=None, previous_last_verified=None,
        rediscovered_now=True, material_fact_verified_now=True, now=now,
    )
    assert result.last_seen == now.isoformat()
    assert result.last_verified == now.isoformat()
