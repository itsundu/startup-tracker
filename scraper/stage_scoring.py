"""
Stage-adjusted funding scoring: a strong seed round should be able to
outrank an ordinary late-stage round, so raw funding_amount_usd is never
compared directly across stages.

Two modes:
  1. Robust percentile-within-stage-and-region, when the peer sample size in
     that (stage, region) cohort is large enough (>= MIN_PEER_SAMPLE) for a
     percentile to mean anything.
  2. A documented global-stage fallback threshold table, used when the peer
     sample is too small (a fresh region/stage cohort with only 1-2
     companies has no meaningful percentile distribution).

Either path returns a 0-100 score representing "how strong is this amount
relative to comparable peers at the same stage," which is what feeds the
ranking engine's stage-adjusted funding-momentum component (see ranking.py)
-- never the raw dollar amount itself.
"""

from typing import List, Optional, Sequence

MIN_PEER_SAMPLE = 5

# Global fallback thresholds (USD), reviewed 2026-01, used only when a
# (stage, region) cohort doesn't yet have enough peers for a real
# percentile. Each stage maps ascending amount thresholds to a 0-100 score;
# the score for an amount is the highest threshold's score that the amount
# meets or exceeds, or 10 (a token "raised something, but very small for
# this stage") if it's above zero but below every threshold.
GLOBAL_STAGE_FALLBACK_THRESHOLDS_USD = {
    "pre-seed": [(100_000, 40), (300_000, 60), (750_000, 80), (1_500_000, 100)],
    "seed":     [(500_000, 40), (1_500_000, 60), (3_000_000, 80), (6_000_000, 100)],
    "series a": [(3_000_000, 40), (8_000_000, 60), (18_000_000, 80), (35_000_000, 100)],
    "series b": [(10_000_000, 40), (25_000_000, 60), (50_000_000, 80), (90_000_000, 100)],
    "series c": [(25_000_000, 40), (60_000_000, 60), (120_000_000, 80), (250_000_000, 100)],
    "growth":   [(75_000_000, 40), (150_000_000, 60), (300_000_000, 80), (600_000_000, 100)],
}


def _fallback_score(amount_usd: float, stage: str) -> int:
    thresholds = GLOBAL_STAGE_FALLBACK_THRESHOLDS_USD.get(stage)
    if not thresholds:
        return 0
    if amount_usd <= 0:
        return 0
    score = 10  # raised something at this stage, but below every named threshold
    for threshold, points in thresholds:
        if amount_usd >= threshold:
            score = points
    return score


def percentile_rank(value: float, peers: Sequence[float]) -> float:
    """% of peers with a value <= `value` (inclusive), i.e. this value's own
    percentile within the peer distribution. Peers should NOT include the
    value itself twice; pass the full comparable cohort excluding the
    current company, or including it -- either is fine as long as it's
    consistent, since with n>=5 the difference is marginal."""
    if not peers:
        return 0.0
    at_or_below = sum(1 for p in peers if p <= value)
    return 100.0 * at_or_below / len(peers)


def stage_adjusted_funding_score(
    amount_usd: Optional[float],
    stage: str,
    peer_amounts: Optional[List[float]] = None,
) -> int:
    """Returns 0-100. `stage` should already be normalized (see
    currency.normalize_funding_stage) to one of: pre-seed, seed, series a,
    series b, series c, growth, unknown. `peer_amounts` is the list of
    amount_usd for OTHER companies in the same normalized stage + region
    cohort (any Nones already filtered out by the caller).

    Returns 0 for stage == "unknown" or amount_usd is None/<=0 -- a company
    with no verified completed-funding amount gets the conservative score
    for this component, reflecting missing information, not a penalty for
    being early.
    """
    if amount_usd is None or amount_usd <= 0 or stage == "unknown":
        return 0

    peers = [p for p in (peer_amounts or []) if p is not None and p > 0]
    if len(peers) >= MIN_PEER_SAMPLE:
        return round(percentile_rank(amount_usd, peers))

    return _fallback_score(amount_usd, stage)
