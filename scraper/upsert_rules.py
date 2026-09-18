"""
Field-preservation and timestamp rules for upserting a re-discovered company.

Fixes the old behavior (`Prefer: resolution=merge-duplicates` blindly
replacing the whole row every run, and a single `last_updated =
default now()` conflating "we saw this company again" with "we verified a
fact about it"). Two timestamps now carry distinct meaning:

  - last_seen:     bumped every time the company is rediscovered by any run,
                    regardless of whether any field changed.
  - last_verified: bumped ONLY when at least one material fact was
                    successfully verified (accepted a new or reconfirmed
                    value) during this run.

And no single field is ever silently regressed: a new run's null, or a
lower-authority/lower-confidence value, never overwrites an existing
higher-quality value -- it's recorded as contradictory evidence instead (see
`company_sources`), not discarded and not applied.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from confidence import is_populated


@dataclass
class FieldUpdateDecision:
    apply_new_value: bool
    reason: str
    store_as_contradiction: bool = False


def should_overwrite_field(
    existing_value,
    new_value,
    existing_confidence: Optional[int] = None,
    new_confidence: Optional[int] = None,
    existing_source_tier: Optional[int] = None,
    new_source_tier: Optional[int] = None,
    new_evidence_is_clearly_newer: bool = False,
) -> FieldUpdateDecision:
    """Lower `source_tier` number means MORE authoritative (Tier 1 beats
    Tier 3) -- matches source_tiers.py's TIER_1_AUTHORITATIVE = 1 convention.
    """
    if not is_populated(new_value):
        return FieldUpdateDecision(False, "new value is null/unknown; preserving existing value")

    if not is_populated(existing_value):
        return FieldUpdateDecision(True, "existing value was null/unknown; accepting new value")

    if new_value == existing_value:
        return FieldUpdateDecision(True, "new value matches existing value (reconfirmation)")

    equal_or_better_authority = (
        new_source_tier is not None
        and existing_source_tier is not None
        and new_source_tier <= existing_source_tier
    )
    equal_or_better_confidence = (
        new_confidence is not None
        and existing_confidence is not None
        and new_confidence >= existing_confidence
    )

    if equal_or_better_authority or equal_or_better_confidence or new_evidence_is_clearly_newer:
        return FieldUpdateDecision(
            True,
            "new value has equal/higher authority, equal/higher confidence, or is clearly newer evidence",
        )

    return FieldUpdateDecision(
        False,
        "existing value has higher authority/confidence and new evidence isn't clearly newer; "
        "preserving existing value and recording the new one as contradictory evidence",
        store_as_contradiction=True,
    )


@dataclass
class TimestampDecision:
    last_seen: str
    last_verified: str


def determine_timestamps(
    previous_last_seen: Optional[str],
    previous_last_verified: Optional[str],
    rediscovered_now: bool,
    material_fact_verified_now: bool,
    now: Optional[datetime] = None,
) -> TimestampDecision:
    """Returns ISO-8601 timestamps for last_seen/last_verified given this
    run's outcome. `rediscovered_now` should be True whenever the company
    appeared in this run's candidate set at all (even if every field
    matched and nothing changed). `material_fact_verified_now` should be
    True only if at least one field passed `should_overwrite_field` with
    apply_new_value=True (a fresh accept OR a reconfirmation), or an
    existing field was independently reconfirmed by a Tier 1/2 source this
    run.
    """
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()

    last_seen = now_iso if rediscovered_now else (previous_last_seen or now_iso)
    last_verified = now_iso if material_fact_verified_now else (previous_last_verified or now_iso)

    return TimestampDecision(last_seen=last_seen, last_verified=last_verified)
