from news_relevance import filter_relevant, is_relevant


def test_startup_funding_news_is_relevant():
    assert is_relevant({"title": "Acme Robotics raises $8M Series A", "summary": "Funding round details."}) is True


def test_webinar_promotion_is_not_relevant():
    assert is_relevant({"title": "Free Webinar: Scaling Your SaaS in 2026", "summary": "Register now to join."}) is False


def test_generic_market_commentary_is_not_relevant():
    assert is_relevant({"title": "Fed raises rates amid inflation concerns", "summary": "General macro and monetary policy commentary."}) is False


def test_market_news_with_startup_angle_is_relevant():
    assert is_relevant({"title": "Fed raises rates: how it affects startup funding", "summary": "Impact on venture capital and seed rounds."}) is True


def test_empty_title_is_not_relevant():
    assert is_relevant({"title": "", "summary": "something"}) is False


def test_sponsored_content_is_not_relevant():
    assert is_relevant({"title": "Sponsored: The Future of FinTech", "summary": ""}) is False


def test_filter_relevant_drops_only_noise():
    items = [
        {"title": "Beta Fintech launches new product", "summary": ""},
        {"title": "Webinar: Join our free session on growth hacking", "summary": "Register now"},
        {"title": "Gamma AI acquired by BigCo", "summary": ""},
    ]
    filtered = filter_relevant(items)
    assert len(filtered) == 2
    assert all("Webinar" not in i["title"] for i in filtered)
