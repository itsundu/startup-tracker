"""
Homepage-validation tests, built around the exact production failure modes
described in the migration brief: an .avif image, a GTM script, a CSS asset,
a DoubleClick ad URL, a publication homepage, and (as the positive case) a
legitimate company homepage. No real network calls -- `fetch_fn` is always
injected.
"""

from homepage_validator import FetchResult, pick_best_homepage, validate_homepage_candidate


def _never_called(url):
    raise AssertionError(f"fetch_fn should not have been called for a candidate rejected pre-fetch: {url}")


# ---- 18. URL asset rejection ----

def test_rejects_avif_image():
    result = validate_homepage_candidate(
        "https://cdn.example-press.com/wp-content/uploads/2026/01/hero.avif",
        company_name="Acme Robotics",
        fetch_fn=_never_called,
    )
    assert result.valid is False
    assert "asset" in " ".join(result.reasons).lower()


def test_rejects_gtm_javascript_file():
    result = validate_homepage_candidate(
        "https://www.googletagmanager.com/gtm.js?id=GTM-XXXX",
        company_name="Acme Robotics",
        fetch_fn=_never_called,
    )
    assert result.valid is False


def test_rejects_css_asset():
    result = validate_homepage_candidate(
        "https://example-press.com/assets/main.min.css",
        company_name="Acme Robotics",
        fetch_fn=_never_called,
    )
    assert result.valid is False
    assert "asset" in " ".join(result.reasons).lower()


# ---- 19. CDN and advertising-domain rejection ----

def test_rejects_doubleclick_advertising_url():
    result = validate_homepage_candidate(
        "https://ad.doubleclick.net/ddm/trackclk/N123.456/B789",
        company_name="Acme Robotics",
        fetch_fn=_never_called,
    )
    assert result.valid is False
    assert "doubleclick.net" in " ".join(result.reasons)


def test_rejects_known_cdn_domain():
    result = validate_homepage_candidate(
        "https://d111111abcdef8.cloudfront.net/assets/logo-fallback",
        company_name="Acme Robotics",
        fetch_fn=_never_called,
    )
    assert result.valid is False


def test_rejects_social_domain():
    result = validate_homepage_candidate(
        "https://www.linkedin.com/company/acme-robotics",
        company_name="Acme Robotics",
        fetch_fn=_never_called,
    )
    assert result.valid is False


# ---- publication homepage should never be accepted as a company homepage ----

def test_rejects_publication_homepage():
    result = validate_homepage_candidate(
        "https://techcrunch.com/2026/01/15/acme-robotics-raises-seed/",
        company_name="Acme Robotics",
        fetch_fn=_never_called,
    )
    assert result.valid is False
    assert "techcrunch.com" in " ".join(result.reasons)


# ---- 20. Non-HTML homepage rejection (by actual Content-Type, not just extension) ----

def test_rejects_non_html_content_type_even_without_asset_extension_in_url():
    def fetch(url):
        return FetchResult(status_code=200, final_url=url, content_type="application/pdf", text="")
    result = validate_homepage_candidate(
        "https://files.example.com/download",
        company_name="Acme Robotics",
        fetch_fn=fetch,
    )
    assert result.valid is False
    assert "content-type" in " ".join(result.reasons).lower()


def test_rejects_unreachable_url():
    def fetch(url):
        return None
    result = validate_homepage_candidate(
        "https://this-domain-does-not-resolve.example",
        company_name="Acme Robotics",
        fetch_fn=fetch,
    )
    assert result.valid is False


def test_rejects_http_error_status():
    def fetch(url):
        return FetchResult(status_code=404, final_url=url, content_type="text/html", text="")
    result = validate_homepage_candidate(
        "https://acmerobotics.ai/old-page",
        company_name="Acme Robotics",
        fetch_fn=fetch,
    )
    assert result.valid is False


# ---- 21. Redirect validation ----

def test_redirect_to_denylisted_domain_is_rejected():
    def fetch(url):
        # A lapsed startup domain, parked and redirected to an ad network.
        return FetchResult(status_code=200, final_url="https://doubleclick.net/parked", content_type="text/html", text="parked domain")
    result = validate_homepage_candidate(
        "https://old-startup-domain.example",
        company_name="Acme Robotics",
        fetch_fn=fetch,
    )
    assert result.valid is False


def test_redirect_to_legitimate_different_final_domain_is_accepted():
    # A launch/marketing domain that 301-redirects to the company's real
    # product domain -- final-domain validation must use the domain AFTER
    # following the redirect, not the one originally requested.
    def fetch(url):
        return FetchResult(
            status_code=200,
            final_url="https://acmerobotics.ai/",
            content_type="text/html; charset=utf-8",
            text="<html><head><title>Acme Robotics -- industrial automation</title></head></html>",
        )
    result = validate_homepage_candidate(
        "https://tryacmerobotics.com",
        company_name="Acme Robotics",
        fetch_fn=fetch,
    )
    assert result.valid is True
    assert result.registrable_domain == "acmerobotics.ai"
    assert any("redirect" in r.lower() for r in result.reasons)


# ---- Legitimate company homepage: the positive case ----

def test_accepts_legitimate_company_homepage():
    def fetch(url):
        return FetchResult(
            status_code=200,
            final_url="https://www.acmerobotics.ai/",
            content_type="text/html; charset=utf-8",
            text=(
                "<html><head><title>Acme Robotics | AI-powered industrial robots</title></head>"
                "<body>Acme Robotics builds autonomous warehouse robots.</body></html>"
            ),
        )
    result = validate_homepage_candidate(
        "https://www.acmerobotics.ai",
        company_name="Acme Robotics",
        fetch_fn=fetch,
    )
    assert result.valid is True
    assert result.confidence >= 50
    assert any("company-identifying text" in r for r in result.reasons)


def test_pick_best_homepage_never_just_takes_the_first_candidate():
    """The exact bug being fixed: the old code took candidates[0] from the
    RSS summary. Here the first candidate is a tracking pixel, the second is
    a CSS asset, and the third (last) is the real homepage -- the validator
    must still find and return the real one."""
    def fetch(url):
        if url == "https://acmerobotics.ai":
            return FetchResult(
                status_code=200, final_url=url, content_type="text/html",
                text="<title>Acme Robotics</title>",
            )
        return FetchResult(status_code=200, final_url=url, content_type="text/html", text="")

    candidates = [
        "https://googleads.g.doubleclick.net/pagead/viewthroughconversion/123",
        "https://example-press.com/wp-content/theme/style.css",
        "https://acmerobotics.ai",
    ]
    best = pick_best_homepage(candidates, company_name="Acme Robotics", fetch_fn=fetch)
    assert best is not None
    assert best.url == "https://acmerobotics.ai"


def test_pick_best_homepage_returns_none_when_nothing_is_valid():
    candidates = [
        "https://ads.doubleclick.net/x",
        "https://example.com/logo.png",
    ]
    best = pick_best_homepage(candidates, company_name="Acme Robotics", fetch_fn=_never_called)
    assert best is None


def test_https_preferred_over_http_all_else_equal():
    def fetch(url):
        return FetchResult(status_code=200, final_url=url, content_type="text/html", text="<title>Acme Robotics</title>")

    https_result = validate_homepage_candidate("https://acmerobotics.ai", "Acme Robotics", fetch_fn=fetch)
    http_result = validate_homepage_candidate("http://acmerobotics.ai", "Acme Robotics", fetch_fn=fetch)
    assert https_result.confidence >= http_result.confidence
