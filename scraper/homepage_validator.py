"""
Verified homepage-resolution pipeline.

Replaces the old "take the first extracted link" behavior in enrich.py. A
homepage candidate is only accepted after passing every gate below, and it
always carries a confidence score + evidence (reasons) explaining the
decision -- never a bare accept/reject.

Network access is injected via `fetch_fn` (default `_default_fetch`, backed
by `requests`) so this module is fully unit-testable with mocked responses --
no real HTTP calls happen in tests. See tests/test_homepage_validator.py for
the fixture set (an .avif image, a GTM script, a CSS asset, a DoubleClick ad
URL, a publication homepage, and a legitimate company homepage).
"""

from dataclasses import dataclass, field
from typing import Callable, List, Optional
from urllib.parse import urlparse

from domain_rules import (
    has_asset_extension,
    is_denylisted_domain,
    is_non_html_content_type,
    is_tracking_url,
    normalize_company_name,
    registrable_domain,
)

MIN_ACCEPT_CONFIDENCE = 50


@dataclass
class FetchResult:
    status_code: int
    final_url: str
    content_type: Optional[str]
    text: str


@dataclass
class HomepageCandidateResult:
    url: str
    valid: bool
    confidence: int
    reasons: List[str] = field(default_factory=list)
    final_url: Optional[str] = None
    registrable_domain: Optional[str] = None


def _default_fetch(url: str, timeout: int = 8) -> Optional[FetchResult]:
    import requests  # local import: keeps this module importable without requests during pure unit tests that always inject fetch_fn

    headers = {"User-Agent": "Mozilla/5.0 (compatible; StartupRadarBot/2.0; +https://terralytixai.com)"}
    try:
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    except Exception:
        return None
    return FetchResult(
        status_code=resp.status_code,
        final_url=resp.url,
        content_type=resp.headers.get("Content-Type"),
        text=resp.text[:20000] if resp.text else "",
    )


def _reject(url, confidence, reasons):
    return HomepageCandidateResult(url=url, valid=False, confidence=confidence, reasons=reasons)


def _name_tokens(name: str) -> List[str]:
    normalized = normalize_company_name(name)
    return [t for t in normalized.split() if len(t) >= 3]


def _title_matches_company(text: str, company_name: str, product_names: Optional[List[str]] = None) -> bool:
    if not text:
        return False
    haystack = text.lower()
    candidates = [company_name] + list(product_names or [])
    for candidate in candidates:
        tokens = _name_tokens(candidate)
        if not tokens:
            continue
        # Require the strongest token (usually the most distinctive one --
        # take the longest) to appear, rather than every token, since
        # taglines/branding rarely repeat generic words ("labs", "tech").
        strongest = max(tokens, key=len)
        if strongest in haystack:
            return True
    return False


def validate_homepage_candidate(
    url: str,
    company_name: str,
    product_names: Optional[List[str]] = None,
    fetch_fn: Optional[Callable[[str], Optional[FetchResult]]] = None,
) -> HomepageCandidateResult:
    """Runs the full verified-domain-resolution pipeline against one
    candidate URL. Never raises on a bad candidate -- always returns a
    HomepageCandidateResult with `valid` and `confidence` set, so callers can
    compare multiple candidates and keep the best one instead of the first.
    """
    reasons: List[str] = []
    if not url or not url.strip():
        return _reject(url or "", 0, ["empty URL"])

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return _reject(url, 0, [f"unsupported scheme '{parsed.scheme}'"])

    if has_asset_extension(url):
        return _reject(url, 0, ["URL path looks like a static asset (image/script/style/font/pdf)"])

    if is_tracking_url(url):
        return _reject(url, 0, ["URL path matches a known tracking/tag-manager pattern"])

    domain = registrable_domain(url)
    if not domain:
        return _reject(url, 0, ["could not determine a registrable domain"])

    if is_denylisted_domain(domain):
        return _reject(url, 0, [f"'{domain}' is a known CDN/ad/social/publication/platform domain"])

    fetch = fetch_fn or _default_fetch
    fetched = fetch(url)
    if fetched is None:
        return _reject(url, 0, ["URL did not resolve (network error or timeout)"])

    if fetched.status_code >= 400:
        return _reject(url, 0, [f"resolved with HTTP {fetched.status_code}"])

    if is_non_html_content_type(fetched.content_type):
        return _reject(url, 0, [f"Content-Type '{fetched.content_type}' is not HTML"])

    # Redirect / final-domain validation: a candidate can start clean and
    # redirect to a denylisted or asset domain (e.g. a lapsed startup domain
    # parked and redirected to an ad network).
    final_domain = registrable_domain(fetched.final_url) or domain
    if is_denylisted_domain(final_domain):
        return _reject(
            url, 0,
            [f"redirected to '{final_domain}', a known CDN/ad/social/publication/platform domain"],
        )

    confidence = 40  # baseline once it resolves as real HTML on a non-denylisted domain
    reasons.append(f"resolved as HTML on registrable domain '{final_domain}'")

    if parsed.scheme == "https":
        confidence += 5
        reasons.append("served over HTTPS")

    domain_root = final_domain.split(".")[0]
    name_tokens = _name_tokens(company_name)
    if name_tokens:
        strongest = max(name_tokens, key=len)
        if strongest and (strongest in domain_root or domain_root in strongest):
            confidence += 25
            reasons.append(f"registrable domain '{domain_root}' plausibly matches company name")

    if _title_matches_company(fetched.text, company_name, product_names):
        confidence += 20
        reasons.append("page text/title contains company-identifying text")

    if final_domain != domain:
        reasons.append(f"followed redirect from '{domain}' to '{final_domain}'")

    confidence = max(0, min(100, confidence))
    valid = confidence >= MIN_ACCEPT_CONFIDENCE

    if not valid:
        reasons.append(
            f"confidence {confidence} below minimum acceptance threshold {MIN_ACCEPT_CONFIDENCE}"
        )

    return HomepageCandidateResult(
        url=url,
        valid=valid,
        confidence=confidence,
        reasons=reasons,
        final_url=fetched.final_url,
        registrable_domain=final_domain,
    )


def pick_best_homepage(
    candidates: List[str],
    company_name: str,
    product_names: Optional[List[str]] = None,
    fetch_fn: Optional[Callable[[str], Optional[FetchResult]]] = None,
) -> Optional[HomepageCandidateResult]:
    """Evaluates every candidate (never just the first) and returns the
    highest-confidence *valid* result, or None if nothing passes. Ties break
    on shorter URL (prefers a bare domain over a deep path)."""
    results = [
        validate_homepage_candidate(c, company_name, product_names, fetch_fn=fetch_fn)
        for c in dict.fromkeys(candidates or [])  # de-dupe, preserve order
    ]
    valid = [r for r in results if r.valid]
    if not valid:
        return None
    valid.sort(key=lambda r: (-r.confidence, len(r.url)))
    return valid[0]
