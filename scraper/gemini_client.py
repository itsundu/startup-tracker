"""
Shared client for Google's free-tier Gemini API (Google AI Studio).

Get a free key at https://aistudio.google.com/app/apikey and set it as the
GEMINI_API_KEY environment variable / GitHub secret. The free tier covers a
weekly run of this scraper comfortably.

Model selection is auto-discovered rather than hardcoded: Google periodically
renames/retires model ids (e.g. "gemini-2.0-flash" 404'd after being valid
earlier), so on first use this asks the API which models the key can actually
call and picks a "flash"-class one that supports generateContent. Set
GEMINI_MODEL as an env var / repo secret to force a specific model instead.
"""

import os
import time
import requests

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

_resolved_model = os.environ.get("GEMINI_MODEL") or None


def _discover_model():
    try:
        resp = requests.get(f"{GEMINI_BASE}/models", params={"key": GEMINI_API_KEY}, timeout=30)
        resp.raise_for_status()
        models = resp.json().get("models", [])
        usable = [m for m in models if "generateContent" in m.get("supportedGenerationMethods", [])]
        flash = [
            m for m in usable
            if "flash" in m.get("name", "").lower() and "preview" not in m.get("name", "").lower()
        ]
        pick = flash or usable
        if pick:
            name = pick[0]["name"].split("/")[-1]
            print(f"[info] Using Gemini model: {name}")
            return name
        print("[warn] Gemini API key returned no usable models")
    except Exception as e:
        print(f"[warn] Could not list Gemini models: {e}")
    return "gemini-2.5-flash"  # last-resort guess if discovery itself fails


def _get_model(force_refresh=False):
    global _resolved_model
    if force_refresh or not _resolved_model:
        _resolved_model = _discover_model()
    return _resolved_model


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

    model = _get_model()
    already_retried_model = False

    for attempt in range(retries):
        url = f"{GEMINI_BASE}/models/{model}:generateContent"
        try:
            resp = requests.post(url, params={"key": GEMINI_API_KEY}, json=payload, timeout=60)

            if resp.status_code == 404 and not already_retried_model:
                print(f"[warn] Model '{model}' not found (404); re-discovering an available model")
                model = _get_model(force_refresh=True)
                already_retried_model = True
                continue

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
