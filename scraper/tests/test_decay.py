import math

import pytest

from decay import DEFAULT_HALF_LIFE_DAYS, decayed_weight, half_value_days


# ---- 6. Recency decay ----

def test_decay_at_zero_age_is_full_value():
    assert decayed_weight(100, 0) == pytest.approx(100.0)


def test_decay_decreases_monotonically_with_age():
    values = [decayed_weight(100, age) for age in (0, 10, 30, 45, 90, 180)]
    assert values == sorted(values, reverse=True)
    assert all(v >= 0 for v in values)


def test_decay_half_value_point_matches_formula():
    hl = half_value_days(DEFAULT_HALF_LIFE_DAYS)
    value_at_half = decayed_weight(100, hl)
    assert value_at_half == pytest.approx(50.0, abs=0.5)


def test_decay_matches_exp_formula_directly():
    base, age, half_life = 80.0, 45.0, 45.0
    expected = base * math.exp(-age / half_life)
    assert decayed_weight(base, age, half_life) == pytest.approx(expected)


def test_decay_negative_age_clamped_to_zero():
    # An event dated in the future (clock skew) must never exceed full value.
    assert decayed_weight(100, -10) == pytest.approx(100.0)


def test_decay_none_base_value_is_zero():
    assert decayed_weight(None, 10) == 0.0


def test_decay_invalid_half_life_raises():
    with pytest.raises(ValueError):
        decayed_weight(100, 10, half_life_days=0)
    with pytest.raises(ValueError):
        decayed_weight(100, 10, half_life_days=-5)


def test_decay_default_half_life_is_within_documented_windows():
    # The default should sit meaningfully inside the 90-day high-impact
    # window and decay to a small tail by the 180-day candidate window --
    # see decay.py's module docstring for the reasoning.
    at_90 = decayed_weight(100, 90, DEFAULT_HALF_LIFE_DAYS)
    at_180 = decayed_weight(100, 180, DEFAULT_HALF_LIFE_DAYS)
    assert 5 < at_90 < 25
    assert at_180 < 5
