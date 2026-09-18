from careers_validator import evaluate_careers_page

SOFT_404_HTML = """
<html><body><div id="root"><h1>404</h1><p>Oops! Page not found.</p></div></body></html>
"""

SPA_SHELL_HTML = """
<html><body><div id="app"></div><script src="/static/js/main.abc123.js"></script></body></html>
"""

ATS_LINK_HTML = """
<html><body>
  <h1>Careers at Acme Robotics</h1>
  <p>We're growing fast. Check out our current openings below.</p>
  <a href="https://acmerobotics.greenhouse.io/jobs">View open roles</a>
</body></html>
"""

JOB_SCHEMA_HTML = """
<html><head>
<script type="application/ld+json">
{"@type": "JobPosting", "title": "Senior Backend Engineer", "datePosted": "2026-02-01"}
</script>
</head><body><h1>Open Positions</h1></body></html>
"""

EXPLICIT_COUNT_HTML = """
<html><body><h1>Careers</h1><p>We have 7 open positions across engineering and sales.</p></body></html>
"""

MARKETING_ONLY_HTML = """
<html><body><h1>Life at Acme</h1><p>We're always hiring great people who share our values.</p></body></html>
"""

NO_OPENINGS_HTML = """
<html><body><h1>Careers</h1><p>0 open positions right now. Check back soon!</p></body></html>
"""


# ---- 22. Soft-404 careers-page rejection ----

def test_soft_404_page_is_not_counted_as_hiring_evidence():
    result = evaluate_careers_page(SOFT_404_HTML)
    assert result.status == "unknown"
    assert result.open_role_count is None


def test_spa_shell_with_no_content_is_not_counted_as_hiring_evidence():
    result = evaluate_careers_page(SPA_SHELL_HTML)
    # A near-empty client-rendered shell has no real evidence either way.
    assert result.status in ("unknown", "no_verified_openings")


def test_empty_page_is_unknown_not_hiring():
    result = evaluate_careers_page("")
    assert result.status == "unknown"


def test_http_200_alone_is_insufficient_marketing_language_only():
    result = evaluate_careers_page(MARKETING_ONLY_HTML)
    assert result.status == "no_verified_openings"


# ---- 23. Verified job-page detection ----

def test_ats_link_detected_as_actively_hiring():
    result = evaluate_careers_page(ATS_LINK_HTML)
    assert result.status == "actively_hiring"
    assert result.confidence > 50
    assert any("applicant-tracking" in e for e in result.evidence)


def test_job_schema_detected_as_actively_hiring():
    result = evaluate_careers_page(JOB_SCHEMA_HTML)
    assert result.status == "actively_hiring"
    assert any("JobPosting" in e for e in result.evidence)


def test_explicit_open_role_count_extracted():
    result = evaluate_careers_page(EXPLICIT_COUNT_HTML)
    assert result.open_role_count == 7
    assert result.status == "actively_hiring"


def test_zero_open_positions_is_no_verified_openings_not_hiring():
    result = evaluate_careers_page(NO_OPENINGS_HTML)
    assert result.open_role_count == 0
    assert result.status == "no_verified_openings"


def test_none_html_is_unknown():
    result = evaluate_careers_page(None)
    assert result.status == "unknown"
