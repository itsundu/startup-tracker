"""
Uses Groq's free-tier API (OpenAI-compatible endpoint, no cost for supported
models) to read a batch of raw article snippets and pull out structured
startup / funding records as JSON.

Get a free key at https://console.groq.com/keys and set it as the
GROQ_API_KEY environment variable / GitHub secret.
"""

import json
import os
import time
import requests

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.3-70b-versatile"  # free-tier model on Groq as of writing

SYSTEM_PROMPT = """You extract structured data about startup companies from news snippets.

You will be given a numbered list of article title+summary snippets. For each snippet that is
ACTUALLY about a specific startup (new company launch, funding round, product launch by an
early-stage company) in technology, telecommunications, AI, engineering, or software services,
output one JSON object. Ignore snippets about large public companies, general commentary,
opinion pieces, or anything not about a specific identifiable startup.

Return ONLY a JSON array (no markdown fences, no preamble, no explanation). Each object must have
exactly these fields, using null when truly unknown -- never invent facts that aren't in the text:

{
  "company_name": string,
  "business_idea": string,          // one clear sentence: what the company does
  "sector": string,                 // e.g. "AI", "Fintech", "Telecom", "Enterprise Software", "Robotics"
  "location": string or null,       // city/country if mentioned
  "funding_stage": string or null,  // e.g. "Seed", "Series A", "Pre-seed", "Series B", "Unfunded"
  "funding_amount": string or null, // e.g. "$4.2M" -- as stated in the text, do not convert
  "investors": string or null,      // comma-separated investor/VC names if mentioned
  "source_index": integer           // the number of the snippet this came from
}

If no snippet in the batch qualifies, return an empty array: []
"""


def _call_groq(messages, retries=3):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 4000,
    }
    for attempt in range(retries):
        try:
            resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"[warn] Groq rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"[warn] Groq call failed (attempt {attempt + 1}): {e}")
            time.sleep(3)
    return None


def _clean_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def extract_startups(articles, batch_size=12):
    """articles: list of {title, summary, link, published, source_name}
    Returns list of extracted startup dicts, each with 'source_url' and 'source_name' attached.
    """
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY environment variable is not set")

    results = []
    for i in range(0, len(articles), batch_size):
        batch = articles[i:i + batch_size]
        snippet_lines = []
        for idx, art in enumerate(batch):
            snippet = f"{idx + 1}. TITLE: {art['title']}\n   SUMMARY: {art['summary'][:400]}"
            snippet_lines.append(snippet)

        user_prompt = "Snippets:\n\n" + "\n\n".join(snippet_lines)

        content = _call_groq([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ])
        if not content:
            continue

        try:
            parsed = json.loads(_clean_json(content))
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
