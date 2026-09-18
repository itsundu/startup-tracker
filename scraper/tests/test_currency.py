import pytest

from currency import (
    classify_funding_statement,
    normalize_funding_stage,
    parse_amount,
    to_usd,
)


# ---- 1. Funding-stage parsing ----

@pytest.mark.parametrize("raw,expected", [
    ("Pre-Seed", "pre-seed"),
    ("preseed", "pre-seed"),
    ("Seed round", "seed"),
    ("Series A", "series a"),
    ("series a extension", "series a"),
    ("Series B", "series b"),
    ("Series C", "series c"),
    ("Series D", "series c"),   # grouped into "series c" bucket (Series C+)
    ("Growth equity round", "growth"),
    ("Late-stage", "growth"),
    (None, "unknown"),
    ("", "unknown"),
    ("Debt financing", "unknown"),
])
def test_normalize_funding_stage(raw, expected):
    assert normalize_funding_stage(raw) == expected


# ---- 2 & 3. Currency parsing: dollar, euro, pound, rupee amounts ----

@pytest.mark.parametrize("text,expected_amount,expected_currency", [
    ("raised $4.2M in seed funding", 4_200_000, "USD"),
    ("a $500K pre-seed round", 500_000, "USD"),
    ("secured $1.1B in Series C", 1_100_000_000, "USD"),
    ("closed a €12 million round", 12_000_000, "EUR"),
    ("raised £3.5m", 3_500_000, "GBP"),
    ("raised ₹35 crore", 350_000_000, "INR"),
    ("raised INR 2.5 Cr", 25_000_000, "INR"),
    ("raised Rs 50 lakh", 5_000_000, "INR"),
    ("no amount mentioned here", None, None),
])
def test_parse_amount(text, expected_amount, expected_currency):
    amount, currency = parse_amount(text)
    assert amount == expected_amount
    assert currency == expected_currency


def test_parse_amount_bare_number_has_no_currency():
    # No symbol/code at all -- must NOT guess a currency.
    amount, currency = parse_amount("raised 50 million in funding")
    assert amount == 50_000_000
    assert currency is None


def test_to_usd_known_currency():
    amount_usd, rate, rate_date = to_usd(12_000_000, "EUR")
    assert amount_usd is not None
    assert amount_usd > 12_000_000  # EUR is worth more than USD at the reference rate
    assert rate is not None
    assert rate_date is not None


def test_to_usd_unknown_currency_returns_null_not_a_guess():
    amount_usd, rate, rate_date = to_usd(1_000_000, "ZWL")
    assert amount_usd is None
    assert rate is None
    assert rate_date is None


def test_to_usd_no_currency_does_not_assume_usd():
    amount_usd, rate, rate_date = to_usd(1_000_000, None)
    assert amount_usd is None


# ---- 4. Valuation vs completed funding classification ----

def test_classify_valuation_is_not_completed_funding():
    text = "The company is now valued at $2 billion after the round."
    assert classify_funding_statement(text) == "valuation"


def test_classify_completed_round_is_not_valuation():
    text = "The startup raised $10 million in a Series A round led by Acme Ventures."
    assert classify_funding_statement(text) == "completed"


def test_classify_valuation_wins_even_with_raised_nearby():
    # "raised" appears, but the sentence is fundamentally a valuation claim
    # -- valuation language must take priority so the number never lands in
    # the funding-amount field.
    text = "It raised its Series B at a $500 million valuation."
    assert classify_funding_statement(text) == "valuation"


def test_classify_acquisition_price_is_not_funding():
    text = "Acme Corp acquired the startup for $80 million."
    assert classify_funding_statement(text) == "acquisition"


def test_classify_tam_is_not_funding():
    text = "The company operates in a market with a total addressable market of $50 billion."
    assert classify_funding_statement(text) == "tam"


def test_classify_fund_size_is_not_company_funding():
    text = "The VC firm launched a new $200 million fund focused on fintech."
    assert classify_funding_statement(text) == "fund_size"


# ---- 5. Proposed vs completed financing ----

def test_classify_proposed_financing():
    text = "The startup is reportedly in talks to raise $20 million."
    result = classify_funding_statement(text)
    assert result in ("proposed", "rumored")
    assert result != "completed"


def test_classify_rumored_financing():
    text = "Sources say the company is seeking new funding at a higher valuation."
    assert classify_funding_statement(text) in ("rumored", "valuation")
    assert classify_funding_statement(text) != "completed"


def test_classify_abandoned_financing():
    text = "The proposed funding round was called off after due diligence concerns."
    assert classify_funding_statement(text) == "abandoned"


def test_classify_completed_vs_proposed_are_distinguishable():
    completed = classify_funding_statement("The company has raised $5 million in seed funding.")
    proposed = classify_funding_statement("The company is planning to raise $5 million in seed funding.")
    assert completed == "completed"
    assert proposed == "proposed"
    assert completed != proposed


def test_classify_empty_text_is_ambiguous():
    assert classify_funding_statement("") == "ambiguous"
    assert classify_funding_statement(None) == "ambiguous"
