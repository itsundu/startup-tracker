"""
Deterministic currency/amount parsing and funding-semantics classification.

No network calls, no LLM -- this is the "use deterministic Python" layer the
spec calls for. The LLM's job (see llm_contract.py) is only to point at which
sentence contains a number and say whether it reads as completed/proposed/
rumored/valuation/acquisition; the actual amount parsing and USD conversion
happens here, against the LLM's quoted evidence text, so a hallucinated
number can't survive without matching the source text.

Static, documented reference rates are used for USD conversion because this
project has no live FX-rate API. Rates are approximate and intentionally
conservative (documented below) -- if a future run wants live rates, swap
`REFERENCE_RATES` for a fetched value AND record `conversion_rate_date`
accordingly. If a currency isn't in this table, USD conversion is left null
rather than guessed, per the "never invent a fact to complete a row" rule.
"""

import re
from datetime import date

# Approximate reference rates (units of currency per 1 USD), reviewed
# 2026-01. These are intentionally static/manual, not live-fetched -- so
# amount_usd is always a *reference-quality* conversion, and every event
# stores `conversion_rate_date` so a reader can judge staleness. Documented
# here rather than fetched to keep this module dependency-free and testable
# offline; revisit periodically and bump CONVERSION_RATE_DATE.
CONVERSION_RATE_DATE = date(2026, 1, 1)
REFERENCE_RATES_PER_USD = {
    "USD": 1.0,
    "EUR": 0.92,
    "GBP": 0.79,
    "INR": 83.0,
    "SGD": 1.34,
    "AUD": 1.52,
    "CAD": 1.36,
    "JPY": 149.0,
    "AED": 3.67,
    "CNY": 7.24,
}

CURRENCY_SYMBOLS = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "₹": "INR",
    "¥": "JPY",
}

MULTIPLIERS = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "mn": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
    "cr": 1e7, "crore": 1e7,          # Indian numbering
    "lakh": 1e5, "lac": 1e5,          # Indian numbering
}

_AMOUNT_RE = re.compile(
    r"""
    (?P<symbol>[$€£₹¥])?\s*
    (?P<code_pre>USD|EUR|GBP|INR|SGD|AUD|CAD|JPY|AED|CNY|Rs\.?)?\s*
    (?P<number>\d[\d,]*(?:\.\d+)?)\s*
    (?P<unit>thousand|million|mn|billion|bn|crore|cr|lakh|lac|k|m|b)?\b
    (?:\s*(?P<code_post>USD|EUR|GBP|INR|SGD|AUD|CAD|JPY|AED|CNY))?
    """,
    re.IGNORECASE | re.VERBOSE,
)


def parse_amount(text):
    """Extract (amount_original, currency) from free text like "$4.2M",
    "₹35 crore", "INR 2.5 Cr", "€500K". Returns (None, None) if nothing
    matches. Does not guess a currency when none is indicated -- a bare
    "50 million" with no symbol/code returns currency=None so callers can
    decide whether to trust it (see classify_funding_statement)."""
    if not text:
        return None, None

    m = _AMOUNT_RE.search(text)
    if not m or not m.group("number"):
        return None, None

    try:
        number = float(m.group("number").replace(",", ""))
    except ValueError:
        return None, None

    unit = (m.group("unit") or "").lower()
    mult = MULTIPLIERS.get(unit, 1.0)
    amount = number * mult

    currency = None
    if m.group("symbol"):
        currency = CURRENCY_SYMBOLS.get(m.group("symbol"))
    code = m.group("code_pre") or m.group("code_post")
    if code:
        code = code.upper().replace("RS.", "INR").replace("RS", "INR")
        currency = code if code in REFERENCE_RATES_PER_USD else currency
    # "Rs" / "₹" with no explicit unit, e.g. "Rs 50,000" already handled above.

    return amount, currency


def to_usd(amount, currency):
    """Returns (amount_usd, conversion_rate, conversion_rate_date) or
    (None, None, None) if the currency is unknown -- never guesses a rate."""
    if amount is None:
        return None, None, None
    if currency is None:
        # No currency marker at all -- most free-text funding mentions
        # without a symbol are still USD-denominated (US/global press
        # convention), but that is an assumption, so record it as such by
        # treating unmarked numbers as unconverted rather than silently
        # assuming USD. Callers that trust bare numbers as USD should pass
        # currency="USD" explicitly after their own validation.
        return None, None, None
    rate = REFERENCE_RATES_PER_USD.get(currency.upper())
    if not rate:
        return None, None, None
    return round(amount / rate, 2), rate, CONVERSION_RATE_DATE.isoformat()


# ---- Funding-statement semantics -------------------------------------------------

COMPLETED_MARKERS = [
    "raised", "closes", "closed", "secures", "secured", "completes",
    "completed", "announced today", "has raised",
]
PROPOSED_MARKERS = [
    "in talks", "in discussions", "is seeking", "looking to raise",
    "planning to raise", "reportedly seeking", "may raise", "considering",
    "exploring a raise", "hopes to raise", "targets a raise", "in the process of raising",
]
RUMORED_MARKERS = [
    "reportedly", "sources say", "according to sources", "rumored",
    "rumoured", "said to be", "understood to be",
]
ABANDONED_MARKERS = [
    "called off", "abandoned", "shelved", "fell through", "no longer pursuing",
]
VALUATION_MARKERS = [
    "valued at", "valuation of", "valuing the company", "post-money valuation",
    "pre-money valuation", "worth", "valuation reaches",
]
ACQUISITION_MARKERS = [
    "acquired", "acquires", "acquisition of", "to be acquired", "buys",
    "bought by", "acqui-hire",
]
TAM_MARKERS = [
    "total addressable market", "tam of", "market opportunity of",
    "market size of",
]
FUND_SIZE_MARKERS = [
    "fund of", "new fund", "raised a fund", "launches a", "vc fund",
    "debut fund", "flagship fund",
]


def classify_funding_statement(evidence_text):
    """Classify what kind of monetary statement `evidence_text` is, so the
    funding component of the ranking never confuses a valuation, an
    acquisition price, a proposed round, or a fund's own size with a
    completed funding round raised BY the startup.

    Returns one of: "completed", "proposed", "rumored", "abandoned",
    "valuation", "acquisition", "tam", "fund_size", "ambiguous".

    Priority matters: valuation/acquisition/TAM/fund-size language, if
    present, wins over a bare "raised" nearby (e.g. "raised at a $2B
    valuation" is a valuation statement about the same round, and the
    valuation number specifically must never land in amount_usd for the
    funding component -- see ranking.py / company_events.amount_usd).
    """
    text = (evidence_text or "").lower()
    if not text.strip():
        return "ambiguous"

    if any(m in text for m in VALUATION_MARKERS):
        return "valuation"
    if any(m in text for m in ACQUISITION_MARKERS):
        return "acquisition"
    if any(m in text for m in TAM_MARKERS):
        return "tam"
    if any(m in text for m in FUND_SIZE_MARKERS):
        return "fund_size"
    if any(m in text for m in ABANDONED_MARKERS):
        return "abandoned"
    if any(m in text for m in RUMORED_MARKERS):
        return "rumored"
    if any(m in text for m in PROPOSED_MARKERS):
        return "proposed"
    if any(m in text for m in COMPLETED_MARKERS):
        return "completed"
    return "ambiguous"


FUNDING_STAGE_ORDER = [
    "pre-seed", "seed", "series a", "series b", "series c", "series d",
    "series e", "series f", "growth", "unknown",
]


def normalize_funding_stage(stage_text):
    """Maps free-text stage mentions to a canonical stage bucket used for
    stage-adjusted scoring (see stage_scoring.py). Anything past Series C is
    bucketed to "series c" (grouped as "Series C+" in stage_scoring), and
    anything that reads as a growth/late-stage round with no explicit series
    letter maps to "growth"."""
    if not stage_text:
        return "unknown"
    s = stage_text.lower().strip()
    s = s.replace("pre seed", "pre-seed").replace("preseed", "pre-seed")
    if "pre-seed" in s:
        return "pre-seed"
    if "seed" in s:
        return "seed"
    if "series a" in s:
        return "series a"
    if "series b" in s:
        return "series b"
    if any(f"series {c}" in s for c in ("c", "d", "e", "f", "g")):
        return "series c"
    if "growth" in s or "late stage" in s or "late-stage" in s:
        return "growth"
    return "unknown"
