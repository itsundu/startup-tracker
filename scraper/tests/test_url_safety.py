from url_safety import is_safe_href


# ---- 30. Frontend URL sanitization (shared logic with frontend's isSafeHref) ----

def test_https_is_safe():
    assert is_safe_href("https://acmerobotics.ai/careers") is True


def test_javascript_scheme_is_rejected():
    assert is_safe_href("javascript:alert(document.cookie)") is False


def test_data_scheme_is_rejected():
    assert is_safe_href("data:text/html,<script>alert(1)</script>") is False


def test_vbscript_scheme_is_rejected():
    assert is_safe_href("vbscript:msgbox(1)") is False


def test_bare_http_without_allowlist_is_rejected_by_default():
    assert is_safe_href("http://example.com") is False


def test_empty_or_none_is_rejected():
    assert is_safe_href("") is False
    assert is_safe_href(None) is False


def test_whitespace_padded_dangerous_scheme_is_rejected():
    assert is_safe_href("  javascript:alert(1)") is False


def test_mailto_is_rejected_not_in_allowlist():
    # mailto: is common for contact_email links, but is intentionally NOT in
    # DANGEROUS_SCHEMES nor treated as safe by this generic helper -- the
    # frontend handles mailto: via its own dedicated `mailto:` string
    # construction (not user/source-controlled scheme selection), so this
    # helper's job is only to gate arbitrary source-derived hrefs.
    assert is_safe_href("mailto:hello@acme.com") is False
