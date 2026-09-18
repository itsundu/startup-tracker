from source_tiers import (
    SourceRecord,
    TIER_1_AUTHORITATIVE,
    TIER_2_JOURNALISM,
    TIER_3_DISCOVERY,
    canonicalize_url,
    classify_source_tier,
    content_hash,
    count_independent_sources,
)


# ---- 25. Source-tier rules ----

def test_own_company_domain_is_tier_1():
    result = classify_source_tier("https://acmerobotics.ai/news/we-raised-seed", is_own_company_domain=True)
    assert result.tier == TIER_1_AUTHORITATIVE


def test_government_source_is_tier_1():
    result = classify_source_tier("https://sec.gov/filing/123", is_government_or_regulator=True)
    assert result.tier == TIER_1_AUTHORITATIVE


def test_investor_announcement_is_tier_1():
    result = classify_source_tier("https://sequoiacap.com/article/we-invested", is_investor_announcement=True)
    assert result.tier == TIER_1_AUTHORITATIVE


def test_known_publication_is_tier_2():
    result = classify_source_tier("https://techcrunch.com/2026/01/01/acme-raises")
    assert result.tier == TIER_2_JOURNALISM


def test_hacker_news_is_tier_3():
    result = classify_source_tier("https://news.ycombinator.com/item?id=12345")
    assert result.tier == TIER_3_DISCOVERY


def test_unknown_domain_defaults_to_tier_3_not_tier_2():
    # An unrecognized domain must NOT be assumed authoritative/journalistic
    # by default -- it has to earn a higher tier explicitly.
    result = classify_source_tier("https://some-random-blog.example/post")
    assert result.tier == TIER_3_DISCOVERY


def test_flags_override_domain_classification():
    # Even a Tier-3-looking domain becomes Tier 1 when it IS the company's
    # own verified domain (e.g. a startup posting on a personal-blog-style domain).
    result = classify_source_tier("https://some-random-blog.example/post", is_own_company_domain=True)
    assert result.tier == TIER_1_AUTHORITATIVE


# ---- 24. Duplicate and syndicated-source detection ----

def test_canonicalize_url_strips_tracking_params():
    a = canonicalize_url("https://Example.com/article?utm_source=twitter&id=5")
    b = canonicalize_url("https://example.com/article?id=5")
    assert a == b


def test_canonicalize_url_strips_fragment_and_trailing_slash():
    a = canonicalize_url("https://example.com/article/#comments")
    b = canonicalize_url("https://example.com/article")
    assert a == b


def test_content_hash_identical_for_same_normalized_text():
    h1 = content_hash("Acme raises $5M", "Full article body here.")
    h2 = content_hash("acme raises $5m", "full   article body here.")
    assert h1 == h2


def test_content_hash_differs_for_different_text():
    h1 = content_hash("Acme raises $5M", "Full article body here.")
    h2 = content_hash("Beta raises $8M", "Different article body.")
    assert h1 != h2


def test_syndicated_copies_count_as_one_independent_source():
    original = SourceRecord(url="https://prnewswire.com/release/acme-raises-5m", title="Acme raises $5M", body="Acme Robotics announced today it raised $5M.")
    mirror_1 = SourceRecord(url="https://some-blog.example/acme-raises-5m?utm_source=rss", title="Acme raises $5M", body="Acme Robotics announced today it raised $5M.")
    mirror_2 = SourceRecord(url="https://another-mirror.example/copy", title="Acme raises $5M", body="Acme Robotics announced today it raised $5M.")

    assert count_independent_sources([original, mirror_1, mirror_2]) == 1


def test_genuinely_different_reports_count_as_independent():
    techcrunch = SourceRecord(url="https://techcrunch.com/acme-raises", title="Acme raises $5M seed", body="TechCrunch's own reporting and analysis of the round.")
    yourstory = SourceRecord(url="https://yourstory.com/acme-raises", title="Acme secures funding", body="YourStory's independent reporting with different quotes and detail.")

    assert count_independent_sources([techcrunch, yourstory]) == 2


def test_same_domain_reporting_twice_is_not_two_independent_sources():
    first = SourceRecord(url="https://techcrunch.com/article-one", title="Acme raises seed", body="Initial coverage of the round.")
    followup = SourceRecord(url="https://techcrunch.com/article-two", title="More on Acme's seed round", body="A follow-up with more detail from the same publication.")

    assert count_independent_sources([first, followup]) == 1


def test_empty_source_list_has_zero_independent_sources():
    assert count_independent_sources([]) == 0
