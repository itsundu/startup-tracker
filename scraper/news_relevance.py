"""
Deterministic relevance filter for the daily news sidebar feed.

The daily job intentionally does NOT call an LLM (see news_job.py's module
docstring -- that's what keeps it cheap enough to run daily), so relevance
filtering here has to be a fast, free, keyword-based heuristic rather than a
classification model. It is intentionally conservative: it REMOVES clear
noise (event/webinar promotions, generic non-startup commentary, pure stock-
market/macro pieces with no startup angle) rather than trying to positively
confirm relevance -- a heuristic false-negative (dropping a relevant item)
is a worse failure mode for a discovery feed than an occasional false
positive (keeping a borderline item), so the exclude list is deliberately
narrow and high-precision.
"""

import re

EXCLUDE_TITLE_PATTERNS = [
    r"\bwebinar\b", r"\bregister now\b", r"\bcall for (speakers|papers|applications)\b",
    r"\bearly[- ]bird (tickets|pricing)\b", r"\bsponsored\b", r"\bpromo(tion)?\b",
    r"\bwatch now\b", r"\blive stream\b", r"\bpodcast episode\b", r"\bhoroscope\b",
    r"\brecipe\b", r"\bmovie review\b", r"\btv review\b", r"\bnow streaming\b",
    r"\bjob openings? at\b",  # a "we're hiring at X" listicle, not startup news
    r"\bdiscount code\b", r"\bcoupon\b", r"\bblack friday\b", r"\bcyber monday\b",
]

# Pure macro/markets pieces with no discernible startup angle -- title
# contains ONLY these terms and none of the startup-signal terms below.
GENERIC_MARKET_ONLY_PATTERNS = [
    r"\bfed(eral reserve)? (raises|cuts|holds) rates\b", r"\bstock market (rises|falls|closes)\b",
    r"\bdow jones\b", r"\bnasdaq (closes|rises|falls)\b", r"\bs&p 500\b",
]

STARTUP_SIGNAL_TERMS = [
    # Deliberately excludes bare "raises"/"raised" -- too generic on their
    # own ("Fed raises rates", "report raises concerns") and would defeat
    # the whole point of the GENERIC_MARKET_ONLY_PATTERNS check below.
    "startup", "funding round", "seed round", "series a", "series b",
    "series c", "venture capital", "vc firm", "valuation", "co-founder",
    "acquisition", "product launch", "ai company", "fintech", "proptech",
    "unicorn",
]

_EXCLUDE_RE = re.compile("|".join(EXCLUDE_TITLE_PATTERNS), re.IGNORECASE)
_MARKET_ONLY_RE = re.compile("|".join(GENERIC_MARKET_ONLY_PATTERNS), re.IGNORECASE)


def is_relevant(item):
    """`item` is a dict with at least "title" (and optionally "summary").
    Returns False for items that match a clear noise pattern; True
    otherwise (the conservative default -- see module docstring)."""
    title = (item.get("title") or "").strip()
    if not title:
        return False

    haystack = f"{title} {item.get('summary', '')}"

    if _EXCLUDE_RE.search(haystack):
        return False

    if _MARKET_ONLY_RE.search(title) and not any(term in haystack.lower() for term in STARTUP_SIGNAL_TERMS):
        return False

    return True


def filter_relevant(items):
    return [item for item in items if is_relevant(item)]
