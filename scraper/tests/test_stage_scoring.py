import pytest

from stage_scoring import percentile_rank, stage_adjusted_funding_score


# ---- 7. Stage-adjusted funding scoring ----

def test_no_amount_scores_zero():
    assert stage_adjusted_funding_score(None, "seed") == 0
    assert stage_adjusted_funding_score(0, "seed") == 0


def test_unknown_stage_scores_zero_even_with_amount():
    assert stage_adjusted_funding_score(5_000_000, "unknown") == 0


def test_fallback_thresholds_used_with_small_peer_sample():
    # Only 2 peers -- below MIN_PEER_SAMPLE, so the documented global
    # fallback table is used instead of a (meaningless) percentile.
    score = stage_adjusted_funding_score(2_000_000, "seed", peer_amounts=[1_000_000, 1_500_000])
    assert score == 60  # seed: >= 1.5M -> 60 per GLOBAL_STAGE_FALLBACK_THRESHOLDS_USD


def test_fallback_below_every_threshold_gets_token_score():
    score = stage_adjusted_funding_score(50_000, "seed")
    assert 0 < score < 40


def test_percentile_used_with_sufficient_peer_sample():
    peers = [1_000_000, 2_000_000, 3_000_000, 4_000_000, 5_000_000]
    score = stage_adjusted_funding_score(3_000_000, "seed", peer_amounts=peers)
    assert score == round(percentile_rank(3_000_000, peers))


def test_strong_seed_can_outrank_ordinary_late_stage():
    # A standout seed round vs. an unremarkable growth round -- the whole
    # point of stage adjustment is that the seed round can score higher.
    strong_seed = stage_adjusted_funding_score(6_000_000, "seed")     # top fallback bucket for seed
    ordinary_growth = stage_adjusted_funding_score(80_000_000, "growth")  # below every growth threshold
    assert strong_seed > ordinary_growth


def test_percentile_rank_boundaries():
    peers = [10, 20, 30, 40, 50]
    assert percentile_rank(50, peers) == 100.0
    assert percentile_rank(5, peers) == 0.0
    assert percentile_rank(30, peers) == 60.0


def test_percentile_rank_empty_peers():
    assert percentile_rank(100, []) == 0.0
