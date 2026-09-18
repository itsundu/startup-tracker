from domain_rules import (
    has_asset_extension,
    is_denylisted_domain,
    is_non_html_content_type,
    is_tracking_url,
    registrable_domain,
)


def test_registrable_domain_strips_www_and_scheme():
    assert registrable_domain("https://www.acmerobotics.ai/about") == "acmerobotics.ai"


def test_registrable_domain_handles_two_part_public_suffix():
    assert registrable_domain("https://startup.co.in") == "startup.co.in"


def test_registrable_domain_handles_bare_domain_no_scheme():
    assert registrable_domain("acmerobotics.ai") == "acmerobotics.ai"


def test_registrable_domain_none_for_empty_input():
    assert registrable_domain("") is None
    assert registrable_domain(None) is None


def test_has_asset_extension_true_for_known_asset_types():
    for ext in (".js", ".css", ".png", ".avif", ".woff2", ".pdf"):
        assert has_asset_extension(f"https://example.com/file{ext}") is True


def test_has_asset_extension_false_for_normal_page():
    assert has_asset_extension("https://example.com/about-us") is False


def test_has_asset_extension_ignores_query_string():
    assert has_asset_extension("https://example.com/page?ref=hero.png") is False


def test_is_non_html_content_type():
    assert is_non_html_content_type("image/avif") is True
    assert is_non_html_content_type("application/javascript") is True
    assert is_non_html_content_type("text/css") is True
    assert is_non_html_content_type("text/html; charset=utf-8") is False
    assert is_non_html_content_type(None) is False


def test_is_tracking_url():
    assert is_tracking_url("https://example.com/gtm.js") is True
    assert is_tracking_url("https://example.com/page?utm_source=twitter") is True
    assert is_tracking_url("https://example.com/about") is False


def test_is_denylisted_domain_matches_subdomains():
    assert is_denylisted_domain("ads.doubleclick.net") is True
    assert is_denylisted_domain("doubleclick.net") is True
    assert is_denylisted_domain("acmerobotics.ai") is False
