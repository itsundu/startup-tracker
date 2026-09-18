from datetime import datetime, timezone

from component_scoring import (
    score_hiring_momentum,
    score_market_expansion,
    score_moat_differentiation,
    score_recent_verified_events,
    score_source_corroboration,
    score_traction,
)

NOW = datetime(2026, 3, 1, tzinfo=timezone.utc)


def _event(event_type, days_ago, status="completed"):
    event_date = (NOW.date().toordinal() - days_ago)
    from datetime import date as _date
    return {
        "event_type": event_type,
        "event_date": _date.fromordinal(event_date).isoformat(),
        "verification_status": status,
    }


def test_no_events_scores_zero():
    assert score_recent_verified_events([], now=NOW) == 0
    assert score_traction([], now=NOW) == 0
    assert score_market_expansion([], now=NOW) == 0


def test_recent_completed_funding_scores_high():
    events = [_event("funding_round_completed", days_ago=1)]
    assert score_recent_verified_events(events, now=NOW) > 90


def test_old_event_decays_toward_zero():
    events = [_event("funding_round_completed", days_ago=400)]
    assert score_recent_verified_events(events, now=NOW) < 5


def test_uncountable_status_does_not_contribute():
    events = [_event("funding_round_completed", days_ago=1, status="proposed")]
    assert score_recent_verified_events(events, now=NOW) == 0


def test_layoffs_never_contribute_positively():
    events = [_event("layoffs", days_ago=1)]
    assert score_recent_verified_events(events, now=NOW) == 0


def test_score_capped_at_100_even_with_many_events():
    events = [_event("funding_round_completed", days_ago=0) for _ in range(10)]
    assert score_recent_verified_events(events, now=NOW) == 100


def test_traction_only_counts_traction_event_types():
    events = [_event("customer_win", days_ago=1)]
    assert score_traction(events, now=NOW) > 0
    assert score_recent_verified_events(events, now=NOW) == 0  # not a "recent events" type


def test_market_expansion_counts_partnership_and_geo_expansion():
    events = [_event("partnership", days_ago=1)]
    assert score_market_expansion(events, now=NOW) > 0


def test_hiring_momentum_scales_with_confidence():
    high_conf = score_hiring_momentum("actively_hiring", 90)
    low_conf = score_hiring_momentum("actively_hiring", 20)
    assert high_conf > low_conf


def test_hiring_momentum_unknown_status_is_zero():
    assert score_hiring_momentum("unknown", 100) == 0
    assert score_hiring_momentum(None, None) == 0


def test_hiring_momentum_no_verified_openings_scores_low():
    assert score_hiring_momentum("no_verified_openings", 100) <= 15


def test_moat_differentiation_defaults_to_zero_not_fabricated():
    assert score_moat_differentiation(None) == 0


def test_moat_differentiation_uses_given_confidence():
    assert score_moat_differentiation(80) == 80


def test_source_corroboration_steps():
    assert score_source_corroboration(0) == 0
    assert score_source_corroboration(1) == 40
    assert score_source_corroboration(2) == 80
    assert score_source_corroboration(3) == 100
    assert score_source_corroboration(10) == 100
