"""
Secondary LLM provider -- fallback only. Used by llm.py when Gemini fails
outright for a call (every model/API-version attempt 404'd, key dead, quota
exhausted, etc).

Free tier at https://console.groq.com/keys. Set GROQ_API_KEY as an env var /
repo secret to enable this fallback. If it's unset, call_groq() just returns
None immediately -- there's no fallback available, and main.py's fail-loud
check will surface that as a hard failure instead of a silent empty run.

Model selection is auto-discovered (via Groq's OpenAI-compatible /models
endpoint) rather than hardcoded -- a hardcoded "llama-3.3-70b-versatile" 404'd
here once Groq retired it, the same class of problem gemini_client.py already
guards against on the Gemini side.

Naive "just take whatever's listed first" discovery isn't safe either: it
once picked "allam-2-7b" (a small Arabic-language model) whose free-tier
tokens-per-minute budget (6000) is too small for our batch prompts, causing
a 413 on every single call. So discovery here explicitly excludes narrow/
non-chat models and prefers larger general-purpose ones, and a 413 (like a
404) triggers picking a different model rather than pointlessly retrying the
identical oversized request against the same one. Set GROQ_MODEL as an env
var to force a specific model and skip all of this.
"""

import os
import time
import requests

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_BASE = "https://api.groq.com/openai/v1"

# Models that are unsuitable for general JSON-extraction chat use -- narrow/
# regional/audio/moderation models, often with much smaller free-tier limits.
DENYLIST_SUBSTRINGS = [
    "whisper", "tts", "allam", "guard", "moderation", "safety",
    "embed", "vision", "playai",
]
# Signals of a larger, general-purpose chat model -- preferred when available.
PREFERRED_SUBSTRINGS = [
    "70b", "120b", "maverick", "scout", "32b", "versatile",
    "qwen", "kimi", "mixtral", "gpt-oss",
]

_state = {"model": None}


def _list_models():
    resp = requests.get(
        f"{GROQ_BASE}/models",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def _pick_model(models, exclude=()):
    ids = [m.get("id", "") for m in models if m.get("id") and m["id"] not in exclude]
    candidates = [i for i in ids if not any(bad in i.lower() for bad in DENYLIST_SUBSTRINGS)]
    if not candidates:
        candidates = ids  # everything got filtered -- fall back to anything rather than nothing
    preferred = [i for i in candidates if any(p in i.lower() for p in PREFERRED_SUBSTRINGS)]
    pick = preferred or candidates
    return pick[0] if pick else None


def _discover(exclude=()):
    try:
        model = _pick_model(_list_models(), exclude=exclude)
        if model:
            print(f"[info] Using Groq model '{model}'")
            return model
        print("[warn] Groq API key returned no usable models")
    except Exception as e:
        print(f"[warn] Could not list Groq models: {e}")
    return None


def _ensure_resolved():
    if _state["model"]:
        return
    forced = os.environ.get("GROQ_MODEL")
    if forced:
        _state["model"] = forced
        return
    _state["model"] = _discover() or "llama-3.3-70b-versatile"


def call_groq(system_prompt, user_prompt, retries=3, max_output_tokens=4000):
    if not GROQ_API_KEY:
        return None

    _ensure_resolved()

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    tried_models = set()

    for attempt in range(retries):
        model = _state["model"]
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": max_output_tokens,
        }
        url = f"{GROQ_BASE}/chat/completions"
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=60)

            # 404 = model retired/renamed; 413 = this model's request/TPM ceiling is too
            # small for our payload. Either way, retrying the SAME model is pointless --
            # pick a different one (excluding ones already tried this call) and retry.
            if resp.status_code in (404, 413):
                print(f"[warn] Groq model '{model}' -> {resp.status_code}: {resp.text[:300]}")
                tried_models.add(model)
                new_model = _discover(exclude=tried_models)
                if new_model and new_model not in tried_models:
                    _state["model"] = new_model
                    continue
                print("[error] No alternative Groq model available to try")
                return None

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
