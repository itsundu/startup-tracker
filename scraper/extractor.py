"""
Phase 1: reads batches of article snippets and asks Gemini's free tier to
identify which ones are about an actual AI / Tech / FinTech / PropTech /
Real Estate startup, returning structured core fields.

Region classification and funding-amount parsing happen afterward in plain
Python (no LLM cost) via classify_region() / parse_funding_usd().
"""

import json
import re

from gemini_client import call_gemini, clean_json

SYSTEM_PROMPT = """You extract structured data about startup companies from news snippets.

You will be given a numbered list of article title+summary snippets. For each snippet that is
ACTUALLY about a specific startup (new company launch, funding round, or notable product launch
by an early/growth-stage private company) in one of these categories: AI, general Technology/
Software, FinTech, PropTech, or Real Estate -- output one JSON object. Ignore snippets about large
public companies, general commentary/opinion pieces, or anything not about one specific identifiable
private startup.

Return ONLY a JSON array (no markdown fences, no preamble). Each object must have exactly these
fields, using null when truly unknown -- never invent facts that aren't in the text:

{
  "company_name": string,
  "business_idea": string,          // one clear sentence: what the company does
  "industry": string,               // one of: "AI", "Technology", "FinTech", "PropTech", "Real Estate"
  "location": string or null,       // city/country if mentioned
  "funding_stage": string or null,  // e.g. "Pre-seed", "Seed", "Series A", "Series B"
  "funding_amount": string or null, // e.g. "$4.2M" -- as stated in the text, do not convert
  "investors": string or null,      // comma-separated investor/VC names if mentioned
  "source_index": integer           // the number of the snippet this came from
}

If no snippet in the batch qualifies, return an empty array: []
"""


def extract_startups(articles, batch_size=12):
    """articles: list of {title, summary, link, published, source_name}
    Returns list of extracted startup dicts, each with 'source_url'/'source_name' attached.
    """
    results = []
    for i in range(0, len(articles), batch_size):
        batch = articles[i:i + batch_size]
        snippet_lines = []
        for idx, art in enumerate(batch):
            snippet = f"{idx + 1}. TITLE: {art['title']}\n   SUMMARY: {art['summary'][:400]}"
            snippet_lines.append(snippet)

        user_prompt = "Snippets:\n\n" + "\n\n".join(snippet_lines)

        content = call_gemini(SYSTEM_PROMPT, user_prompt)
        if not content:
            continue

        try:
            parsed = json.loads(clean_json(content))
        except json.JSONDecodeError:
            print("[warn] Could not parse JSON from model output, skipping batch")
            continue

        for record in parsed:
            src_idx = record.pop("source_index", None)
            if src_idx and 1 <= src_idx <= len(batch):
                record["source_url"] = batch[src_idx - 1]["link"]
                record["source_name"] = batch[src_idx - 1]["source_name"]
            else:
                record["source_url"] = None
                record["source_name"] = None
            results.append(record)

    return results


INDIA_TERMS = [
    "india", "bangalore", "bengaluru", "mumbai", "new delhi", "delhi", "hyderabad",
    "pune", "gurgaon", "gurugram", "noida", "kolkata", "ahmedabad", "chennai",
]
USA_TERMS = [
    "usa", "u.s.", "united states", "california", "new york", "san francisco",
    "seattle", "boston", "chicago", "austin", "los angeles", "miami",
    "silicon valley", "texas", "washington", "denver", "atlanta",
]


def classify_region(location):
    """Rule-based, zero-LLM-cost region tagging used for the site's region filter."""
    if not location:
        return "Unknown"
    loc = location.lower()
    if "chennai" in loc:
        return "India (Chennai)"
    if any(t in loc for t in INDIA_TERMS):
        return "India"
    if any(t in loc for t in USA_TERMS):
        return "USA"
    return "Rest of World"


def parse_funding_usd(text):
    """Best-effort '$4.2M' / '$500K' / '$1.1B' -> numeric USD, used only for sorting the top-100 view."""
    if not text:
        return None
    m = re.search(r'\$\s*([\d,]+(?:\.\d+)?)\s*([kmb])\b', text, re.IGNORECASE)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    mult = {"k": 1e3, "m": 1e6, "b": 1e9}[m.group(2).lower()]
    return int(num * mult)


def compute_signal_score(funding_stage, funding_amount_usd, investors):
    """A 1-5 heuristic 'how much momentum does this round signal' score, built only from
    funding stage / disclosed amount / whether investors were named -- NOT a valuation, and
    not a prediction of future success. It exists so the table can be sorted/skimmed by
    strength of signal without pretending to know something no public source stated.
    """
    stage = (funding_stage or "").lower()

    if "pre-seed" in stage or "preseed" in stage or "pre seed" in stage:
        score = 1
    elif "seed" in stage:
        score = 2
    elif "series a" in stage:
        score = 3
    elif "series b" in stage:
        score = 4
    elif any(s in stage for s in ("series c", "series d", "series e", "series f", "growth", "late stage")):
        score = 5
    else:
        score = 1  # stage unstated -- most conservative, since we genuinely don't know

    if funding_amount_usd:
        if funding_amount_usd >= 100_000_000 and score < 5:
            score += 1
        elif funding_amount_usd >= 20_000_000 and score < 4:
            score += 1

    if investors and score < 3:
        score += 1  # a named backer nudges an early/unlabeled round up slightly

    return max(1, min(5, score))
