"""
URL-scheme allowlist shared conceptually with the frontend's `isSafeHref()`
(see frontend/index.html) -- kept here as the documented, testable source of
truth for the rule: only `https:` and explicitly approved `http:` hosts may
ever be placed in an `href`/`src`. `javascript:`, `data:`, `vbscript:`, and
anything else is rejected outright, regardless of what a source or an LLM
extraction returned.

Python and the browser are two different runtimes, so this can't be
imported by the frontend directly -- but this module's tests pin down the
exact rule the JS re-implements, so a regression in either place is a
one-line diff to spot against the other.
"""

from urllib.parse import urlparse

# Empty by default: no http: exceptions are pre-approved. Add a hostname
# here only for a specific, reviewed reason (documented inline at the call
# site), never as a blanket allowance.
ALLOWED_HTTP_HOSTS = set()

DANGEROUS_SCHEMES = {"javascript", "data", "vbscript", "file"}


def is_safe_href(url) -> bool:
    """True only for https: URLs, or http: URLs whose host is explicitly in
    ALLOWED_HTTP_HOSTS. Anything unparseable, schemeless-but-not-relative,
    or using a dangerous scheme returns False."""
    if not url or not isinstance(url, str):
        return False
    value = url.strip()
    if not value:
        return False

    low = value.lower()
    for scheme in DANGEROUS_SCHEMES:
        if low.startswith(scheme + ":"):
            return False

    try:
        parsed = urlparse(value)
    except ValueError:
        return False

    scheme = parsed.scheme.lower()
    if scheme == "https":
        return True
    if scheme == "http":
        return parsed.hostname in ALLOWED_HTTP_HOSTS
    return False
