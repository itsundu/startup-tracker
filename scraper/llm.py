"""
Thin two-provider orchestration: tries Gemini first (primary), and only falls
back to Groq (secondary, see groq_client.py) if Gemini fails outright for that
specific call -- not as a routine alternate, but as real redundancy against a
full Gemini outage (bad key, exhausted quota, every model 404ing).

Every fallback use is logged, and extractor.py/enrich.py track which calls
failed on BOTH providers so main.py can fail the whole job loudly (nonzero
exit) if literally everything failed, instead of silently upserting nothing.
"""

from gemini_client import call_gemini
from groq_client import call_groq, GROQ_API_KEY


def call_llm(system_prompt, user_prompt, max_output_tokens=4000):
    """Returns (text, provider_name) on success, or (None, None) if both providers failed."""
    try:
        text = call_gemini(system_prompt, user_prompt, max_output_tokens=max_output_tokens)
    except Exception as e:
        print(f"[warn] Gemini raised an exception, treating as a failed call: {e}")
        text = None

    if text is not None:
        return text, "gemini"

    if not GROQ_API_KEY:
        print("[warn] Gemini failed and GROQ_API_KEY is not set -- no fallback provider available")
        return None, None

    print("[warn] Gemini failed for this call -- falling back to Groq")
    try:
        text = call_groq(system_prompt, user_prompt, max_output_tokens=max_output_tokens)
    except Exception as e:
        print(f"[warn] Groq raised an exception too: {e}")
        text = None

    return (text, "groq") if text is not None else (None, None)
