"""
Secondary LLM provider -- fallback only. Used by llm.py when Gemini fails
outright for a call (every model/API-version attempt 404'd, key dead, quota
exhausted, etc).

Free tier at https://console.groq.com/keys. Set GROQ_API_KEY as an env var /
repo secret to enable this fallback. If it's unset, call_groq() just returns
None immediately -- there's no fallback available, and main.py's fail-loud
check will surface that as a hard failure instead of a silent empty run.
"""

import os
import time
import requests

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.3-70b-versatile"


def call_groq(system_prompt, user_prompt, retries=3, max_output_tokens=4000):
    if not GROQ_API_KEY:
        return None

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": max_output_tokens,
    }

    for attempt in range(retries):
        try:
            resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"[warn] Groq rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                print(f"[warn] Groq call failed: {resp.status_code} {resp.text[:300]}")
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"[warn] Groq call failed (attempt {attempt + 1}): {e}")
            time.sleep(3)
    return None
