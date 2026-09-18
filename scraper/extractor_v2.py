"""
v2 extraction: the strengthened LLM contract described in the migration
brief. The LLM is asked for evidence, event semantics, and confidence for
each claim -- not just bare fields -- and every record is run through
llm_contract.validate_extraction_record before being trusted at all.

This module builds the prompt and parses/validates the response; it does
NOT decide funding semantics (currency.classify_funding_statement, called by
the caller against the evidence_excerpt) or compute scores -- kept as thin
LLM plumbing so the actual judgment stays in deterministic, unit-tested
Python, per the "LLM is an extraction assistant, not a factual source"
principle.
"""

import json
import time

from gemini_client import clean_json
from llm import call_llm
from llm_contract import validate_extraction_batch

PACING_SECONDS = 4

SYSTEM_PROMPT_V2 = """You extract structured, evidence-backed claims about startup companies from
news snippets, for a system that treats you as an extraction assistant, not a source of truth.
Every claim you output MUST be traceable to an exact quoted excerpt from the given text.

You will be given a numbered list of article title+summary snippets. For each snippet that is
ACTUALLY about a specific startup (funding, product launch, hiring, partnership, acquisition, or
similar) in one of these categories: AI, Enterprise & Developer Tech, FinTech, PropTech, Real
Estate Technology, or closely related Technology -- output one JSON object per material claim in
that snippet (a single snippet can produce zero, one, or multiple claims).

Return ONLY a JSON array (no markdown fences, no preamble). Each object must have exactly these
fields:

{
  "company_name": string,
  "business_idea": string or null,
  "industry": string or null,
  "location": string or null,
  "event_type": one of "funding_round_completed", "funding_round_announced", "proposed_funding",
      "valuation_report", "acquisition", "product_launch", "major_product_release", "customer_win",
      "partnership", "geographic_expansion", "executive_change", "hiring_growth", "layoffs",
      "shutdown", "other",
  "value": string or null,          // the raw claimed value AS STATED (amount, stage, headcount, etc.) -- do not convert units or currency yourself
  "investors": string or null,      // comma-separated if multiple
  "evidence_excerpt": string,       // an EXACT quoted excerpt (not a paraphrase) from the snippet supporting this claim -- required, non-empty
  "source_index": integer,          // the number of the snippet this came from
  "confidence": integer,            // 0-100, your own confidence this claim is accurately extracted
  "status": one of "completed", "proposed", "rumored", "abandoned", "ambiguous",  // is the underlying event/transaction completed, merely proposed/in talks, rumored, called off, or unclear?
  "notes": string or null           // any contradiction or uncertainty worth flagging, else null
}

Rules:
- NEVER invent a fact not present in the text. If a field is unknown, use null.
- NEVER report a valuation, an acquisition price, a fund's own size, or a total-addressable-market
  figure as if it were funding the company itself raised -- use event_type "valuation_report" or
  "acquisition" (or omit entirely if it's a fund/TAM figure with no company-funding claim) instead
  of "funding_round_completed" for those.
- Use "funding_round_completed" ONLY when the text clearly states the round closed/was raised, not
  merely proposed, sought, or rumored -- use "proposed_funding" or status "proposed"/"rumored" for
  those instead.
- evidence_excerpt must be an exact substring-quality quote from the snippet, not a rewording.
- If nothing in the batch qualifies, return an empty array: []
"""


def _source_texts_for_batch(batch):
    return [f"{a.get('title', '')} {a.get('summary', '')}" for a in batch]


def extract_startups_v2(articles, batch_size=8):
    """Returns (accepted_records, batch_count, failed_batch_count,
    rejected_count). `accepted_records` have already passed
    llm_contract.validate_extraction_record (including evidence-excerpt
    traceability against the batch's own source text) and carry
    `source_url`/`source_name` resolved from `source_index`.
    """
    accepted = []
    batch_count = 0
    failed_batches = 0
    rejected_count = 0

    for i in range(0, len(articles), batch_size):
        if batch_count > 0:
            time.sleep(PACING_SECONDS)

        batch = articles[i:i + batch_size]
        batch_count += 1
        snippet_lines = [
            f"{idx + 1}. TITLE: {art['title']}\n   SUMMARY: {art['summary'][:400]}"
            for idx, art in enumerate(batch)
        ]
        user_prompt = "Snippets:\n\n" + "\n\n".join(snippet_lines)

        content, provider = call_llm(SYSTEM_PROMPT_V2, user_prompt, max_output_tokens=6000)
        if not content:
            failed_batches += 1
            continue

        try:
            parsed = json.loads(clean_json(content))
        except json.JSONDecodeError:
            print(f"[warn] Could not parse JSON from {provider} output, skipping batch")
            failed_batches += 1
            continue

        if not isinstance(parsed, list):
            print("[warn] Extraction output was not a JSON array, skipping batch")
            failed_batches += 1
            continue

        source_texts = _source_texts_for_batch(batch)
        valid_records = validate_extraction_batch(parsed, batch_size=len(batch), source_texts=source_texts)
        rejected_count += len(parsed) - len(valid_records)

        for record in valid_records:
            idx = record.get("source_index")
            if isinstance(idx, int) and 1 <= idx <= len(batch):
                record["source_url"] = batch[idx - 1]["link"]
                record["source_name"] = batch[idx - 1]["source_name"]
                record["published_at"] = batch[idx - 1].get("published_iso")
            record["extraction_provider"] = provider
            accepted.append(record)

    return accepted, batch_count, failed_batches, rejected_count
