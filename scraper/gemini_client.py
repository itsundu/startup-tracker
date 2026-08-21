"""
Shared client for Google's free-tier Gemini API (Google AI Studio).

Get a free key at https://aistudio.google.com/app/apikey and set it as the
GEMINI_API_KEY environment variable / GitHub secret. The free tier covers a
weekly run of this scraper comfortably.
"""

import os
import time
import requests

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL = "gemini-2.0-flash"  # free-tier model as of writing -- check https://ai.google.dev/pricing if Google renames it
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


def call_gemini(system_prompt, user_prompt, retries=3, max_output_tokens=4000):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY environment variable is not set")

    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
        },
    }

    for attempt in range(retries):
        try:
            resp = requests.post(GEMINI_URL, params={"key": GEMINI_API_KEY}, json=payload, timeout=60)
            if resp.status_code == 429:
                wait = 10 * (attempt + 1)
                print(f"[warn] Gemini rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            print(f"[warn] Gemini call failed (attempt {attempt + 1}): {e}")
            time.sleep(3)
    return None


def clean_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()
