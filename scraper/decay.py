"""
Recency decay for event-based ranking components.

decayed_weight = base_value * exp(-age_days / half_life_days)

Note this uses `half_life_days` as the divisor, not a decay-rate lambda, so
the parameter has a direct, documentable interpretation: DEFAULT_HALF_LIFE_DAYS
is the number of days after which an event's contribution has fallen to
exp(-1) ~= 36.8% of its original value (the classic "e-folding time"), which
in turn corresponds to a half-value point (contribution = 0.5x) at
`half_life_days * ln(2)` ~= 0.693 * half_life_days days.

Concretely, with the default of 45 days:
  - at 0 days old:   100% of base value
  - at ~31 days old: 50% of base value   (0.693 * 45 ~= 31)
  - at 45 days old:  ~37% of base value
  - at 90 days old:  ~14% of base value
  - at 180 days old: ~2% of base value (effectively expired)

45 was chosen because it sits inside the spec's own recency windows (90-day
"high-impact" window, 180-day "candidate" window): an event at the edge of
the high-impact window (90 days) has decayed to ~14%, and one at the edge of
the candidate window (180 days) is close to zero -- so old-but-still-eligible
events contribute a small, non-fabricated tail rather than either a hard
cliff or full weight.
"""

import math

DEFAULT_HALF_LIFE_DAYS = 45.0


def decayed_weight(base_value, age_days, half_life_days=DEFAULT_HALF_LIFE_DAYS):
    """Returns base_value * exp(-age_days / half_life_days).

    age_days < 0 (an event dated in the future, e.g. clock skew) is clamped
    to 0 so it never receives MORE than full weight. half_life_days <= 0
    raises ValueError -- a zero or negative half-life has no defined decay
    curve and almost certainly indicates a misconfiguration, not intent.
    """
    if half_life_days <= 0:
        raise ValueError("half_life_days must be > 0")
    if base_value is None:
        return 0.0
    age = max(0.0, float(age_days))
    return float(base_value) * math.exp(-age / float(half_life_days))


def half_value_days(half_life_days=DEFAULT_HALF_LIFE_DAYS):
    """Days until decayed_weight falls to exactly 50% of base_value."""
    return half_life_days * math.log(2)
