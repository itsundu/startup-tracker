"""
Shared client for Google's free-tier Gemini API (Google AI Studio).

Get a free key at https://aistudio.google.com/app/apikey and set it as the
GEMINI_API_KEY environment variable / GitHub secret. The free tier covers a
weekly run of this scraper comfortably.

Model AND API-version selection are auto-discovered rather than hardcoded, because Google
has retired model ids more than once here (gemini-2.0-flash, then gemini-2.5-flash both
404'd eventually -- the latter with a 404 body that literally said "no longer available to
new users, use models/gemini-3.6-flash instead"). Two layers of self-healing:

1. If a 404's response body names a replacement model ("use models/X"), switch straight to
   X and retry -- this is the most precise fix since Google is telling us the exact answer.
2. Otherwise, fall back to trying both "v1beta" and "v1" and re-discovering a model via
   ListModels, in case it's an API-version mismatch rather than a pure rename.

Set GEMINI_MODEL as an env var / repo secret to force a specific model id and skip all of
this (still tried against "v1beta" first).
"""

import os
import re
import time
import requests

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
API_VERSIONS = ["v1beta", "v1"]

_state = {"version": None, "model": None}


def _list_models(api_version):
    resp = requests.get(
        f"https://generativelanguage.googleapis.com/{api_version}/models",
        params={"key": GEMINI_API_KEY},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("models", [])


def _pick_model(models):
    usable = [m for m in models if "generateContent" in m.get("supportedGenerationMethods", [])]
    flash = [
        m for m in usable
        if "flash" in m.get("name", "").lower() and "preview" not in m.get("name", "").lower()
    ]
    pick = flash or usable
    if not pick:
        return None
    return pick[0]["name"].split("/")[-1]


def _discover(api_version):
    """Find a usable model id for a given API version ("v1beta" or "v1"). Returns None on failure."""
    try:
        model = _pick_model(_list_models(api_version))
        if model:
            print(f"[info] Using Gemini model '{model}' via API {api_version}")
            return model
        print(f"[warn] No usable Gemini models found via {api_version}")
    except Exception as e:
        print(f"[warn] Could not list Gemini models via {api_version}: {e}")
    return None


def _ensure_resolved():
    if _state["model"] and _state["version"]:
        return

    forced = os.environ.get("GEMINI_MODEL")
    if forced:
        _state["version"] = API_VERSIONS[0]
        _state["model"] = forced
        return

    for version in API_VERSIONS:
        model = _discover(version)
        if model:
            _state["version"] = version
            _state["model"] = model
            return

    # Discovery itself failed on both versions (e.g. network hiccup) -- last-resort guess.
    _state["version"] = API_VERSIONS[0]
    _state["model"] = "gemini-3.6-flash"


def _suggested_replacement(error_text):
    """Google's 404 bodies sometimes say '...use models/gemini-X-flash instead' when a
    model is retired. Pull that name out so we can switch to it directly."""
    m = re.search(r'use\s+models/([a-zA-Z0-9_.\-]+)', error_text or "", re.IGNORECASE)
    return m.group(1) if m else None


def call_gemini(system_prompt, user_prompt, retries=4, max_output_tokens=4000):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY environment variable is not set")

    _ensure_resolved()

    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
        },
    }

    tried_other_version = False

    for attempt in range(retries):
        version = _state["version"]
        model = _state["model"]
        url = f"https://generativelanguage.googleapis.com/{version}/models/{model}:generateContent"

        try:
            resp = requests.post(url, params={"key": GEMINI_API_KEY}, json=payload, timeout=60)

            if resp.status_code == 404:
                print(f"[warn] {version}/{model} -> 404: {resp.text[:300]}")

                replacement = _suggested_replacement(resp.text)
                if replacement and replacement != model:
                    print(f"[info] Google's error suggested a replacement model: '{replacement}'")
                    _state["model"] = replacement
                    continue

                if not tried_other_version:
                    tried_other_version = True
                    for v in API_VERSIONS:
                        if v == version:
                            continue
                        m = _discover(v)
                        if m:
                            _state["version"] = v
                            _state["model"] = m
                            break
                    if _state["version"] != version or _state["model"] != model:
                        continue
                print("[error] Gemini generateContent 404'd on every API version/model tried")
                return None

            if resp.status_code == 429:
                wait = 10 * (attempt + 1)
                print(f"[warn] Gemini rate limited, waiting {wait}s")
                time.sleep(wait)
                continue

            if resp.status_code >= 400:
                print(f"[warn] Gemini call failed: {resp.status_code} {resp.text[:300]}")

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
