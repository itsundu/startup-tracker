"""
End-to-end integration test for main_v2.process_run(), with every I/O
boundary mocked (no real network, no real LLM, no real Supabase). This is
NOT a substitute for a real dry run against live services (see main_v2.py's
docstring) -- it exists to catch orchestration bugs in the glue code itself
(wrong field names, KeyErrors, type mismatches between modules) that unit
tests on the individual pure modules can't see, since each of those is
tested in isolation from how main_v2 actually calls it.
"""

from datetime import datetime, timezone

import main_v2


class FakeDB:
    """In-memory stand-in for supabase_client_v2, recording every call so
    the test can assert on what main_v2 actually tried to write."""

    def __init__(self):
        self.companies = {}
        self.aliases = []
        self.events = []
        self.sources = []
        self.snapshots = []
        self.scan_runs = {}
        self._next_id = 1

    def _new_id(self, prefix):
        cid = f"{prefix}-{self._next_id}"
        self._next_id += 1
        return cid

    def insert_scan_run(self, base_url, key, fields):
        run_id = self._new_id("run")
        self.scan_runs[run_id] = dict(fields)
        return run_id

    def update_scan_run(self, base_url, key, run_id, fields):
        self.scan_runs[run_id].update(fields)

    def fetch_recent_successful_scan_runs(self, base_url, key, limit=1):
        return []  # no prior run for this test -- first-ever run

    def fetch_companies_for_resolution(self, base_url, key):
        return list(self.companies.values())

    def fetch_aliases(self, base_url, key):
        return list(self.aliases)

    def insert_company(self, base_url, key, fields):
        cid = self._new_id("co")
        row = {"id": cid, **fields}
        self.companies[cid] = row
        return row

    def patch_company(self, base_url, key, company_id, fields):
        if company_id in self.companies:
            self.companies[company_id].update(fields)
            return self.companies[company_id]
        return None

    def insert_alias(self, *a, **kw):
        pass

    def insert_company_event(self, base_url, key, fields):
        self.events.append(fields)

    def fetch_company_events(self, base_url, key, company_id):
        return [e for e in self.events if e["company_id"] == company_id]

    def insert_company_source(self, base_url, key, fields):
        self.sources.append(fields)

    def insert_company_snapshot(self, base_url, key, fields):
        self.snapshots.append(fields)


FAKE_ARTICLES = [
    {"title": "Acme Robotics raises $8M Series A", "summary": "Acme Robotics announced today it raised $8 million in a Series A round led by Acme Ventures.", "link": "https://techcrunch.com/acme-raises", "source_name": "TechCrunch", "published_iso": "2026-02-20T00:00:00+00:00"},
    {"title": "Beta Fintech launches new product", "summary": "Beta Fintech launched a new payments product for small businesses in India.", "link": "https://yourstory.com/beta-launch", "source_name": "YourStory", "published_iso": "2026-02-15T00:00:00+00:00"},
]

FAKE_CLAIMS = [
    {
        "company_name": "Acme Robotics", "business_idea": "Builds autonomous warehouse robots.",
        "industry": "AI", "location": "San Francisco, USA",
        "event_type": "funding_round_completed", "value": "Series A, $8 million",
        "investors": "Acme Ventures", "evidence_excerpt": "raised $8 million in a Series A round led by Acme Ventures",
        "source_index": 1, "confidence": 90, "status": "completed", "notes": None,
        "source_url": "https://techcrunch.com/acme-raises", "source_name": "TechCrunch",
        "published_at": "2026-02-20T00:00:00+00:00", "extraction_provider": "gemini",
    },
    {
        "company_name": "Beta Fintech", "business_idea": "Payments product for small businesses.",
        "industry": "FinTech", "location": "Mumbai, India",
        "event_type": "product_launch", "value": None,
        "investors": None, "evidence_excerpt": "launched a new payments product for small businesses in India",
        "source_index": 2, "confidence": 80, "status": "completed", "notes": None,
        "source_url": "https://yourstory.com/beta-launch", "source_name": "YourStory",
        "published_at": "2026-02-15T00:00:00+00:00", "extraction_provider": "gemini",
    },
]

FAKE_ENRICHMENT = {
    "acme robotics": {"homepage": "https://acmerobotics.ai", "homepage_confidence": 90, "primary_domain": "acmerobotics.ai", "hiring_status": "actively_hiring", "hiring_confidence": 80, "verified_open_role_count": 5},
    "beta fintech": {"homepage": "https://betafintech.in", "homepage_confidence": 85, "primary_domain": "betafintech.in", "hiring_status": "unknown", "hiring_confidence": 0, "verified_open_role_count": None},
}


def _fake_enrich_all(records, articles):
    out = []
    for r in records:
        from domain_rules import normalize_company_name
        key = normalize_company_name(r["company_name"])
        out.append({**r, **FAKE_ENRICHMENT.get(key, {})})
    return out


def test_process_run_end_to_end_with_mocked_io(monkeypatch):
    fake_db = FakeDB()
    monkeypatch.setattr(main_v2, "SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setattr(main_v2, "SUPABASE_SERVICE_KEY", "fake-service-key")
    monkeypatch.setattr(main_v2, "db", fake_db)
    monkeypatch.setattr(main_v2, "fetch_all_articles", lambda: FAKE_ARTICLES)
    monkeypatch.setattr(main_v2, "extract_startups_v2", lambda articles: (FAKE_CLAIMS, 1, 0, 0))
    monkeypatch.setattr(main_v2, "enrich_all", _fake_enrich_all)

    report = main_v2.process_run(now=datetime(2026, 2, 21, tzinfo=timezone.utc))

    # Two new companies should have been created.
    assert len(fake_db.companies) == 2
    names = {c["canonical_name"] for c in fake_db.companies.values()}
    assert names == {"Acme Robotics", "Beta Fintech"}

    # One company_events row per claim.
    assert len(fake_db.events) == 2
    acme_event = next(e for e in fake_db.events if e["title"] == "Acme Robotics")
    assert acme_event["event_type"] == "funding_round_completed"
    assert acme_event["amount_usd"] == 8_000_000
    assert acme_event["verification_status"] == "completed"

    # Region classification: Acme -> US, Beta -> INDIA.
    acme = next(c for c in fake_db.companies.values() if c["canonical_name"] == "Acme Robotics")
    beta = next(c for c in fake_db.companies.values() if c["canonical_name"] == "Beta Fintech")
    assert acme["region_bucket"] == "US"
    assert beta["region_bucket"] == "INDIA"

    # Funding aggregate fields denormalized onto the company row.
    assert acme["latest_funding_stage"] == "series a"
    assert acme["latest_funding_amount_usd"] == 8_000_000
    assert acme["total_disclosed_funding_usd"] == 8_000_000

    # Quality report shape sanity.
    assert report.score_version == "v2"
    assert isinstance(report.qualified_company_count_by_region, dict)

    # scan_runs was created and finalized.
    assert len(fake_db.scan_runs) == 1
    run = list(fake_db.scan_runs.values())[0]
    assert run["run_status"] in ("success", "success_with_warnings")
    assert run["candidates_extracted"] == 2


def test_process_run_downgrades_valuation_language_even_if_llm_mislabeled_it(monkeypatch):
    """The deterministic cross-check in main_v2 must catch an LLM that
    mislabels valuation language as a completed funding round."""
    fake_db = FakeDB()
    monkeypatch.setattr(main_v2, "SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setattr(main_v2, "SUPABASE_SERVICE_KEY", "fake-service-key")
    bad_claim = {
        "company_name": "Gamma AI", "business_idea": "AI research lab.",
        "industry": "AI", "location": "New York, USA",
        "event_type": "funding_round_completed", "value": "$2 billion valuation",
        "investors": None,
        "evidence_excerpt": "Gamma AI is now valued at $2 billion after its latest round.",
        "source_index": 1, "confidence": 70, "status": "completed", "notes": None,
        "source_url": "https://techcrunch.com/gamma-valuation", "source_name": "TechCrunch",
        "published_at": "2026-02-18T00:00:00+00:00", "extraction_provider": "gemini",
    }

    monkeypatch.setattr(main_v2, "db", fake_db)
    monkeypatch.setattr(main_v2, "fetch_all_articles", lambda: FAKE_ARTICLES[:1])
    monkeypatch.setattr(main_v2, "extract_startups_v2", lambda articles: ([bad_claim], 1, 0, 0))
    monkeypatch.setattr(main_v2, "enrich_all", lambda records, articles: [
        {**r, "homepage": "https://gamma.ai", "homepage_confidence": 90, "primary_domain": "gamma.ai",
         "hiring_status": "unknown", "hiring_confidence": 0, "verified_open_role_count": None}
        for r in records
    ])

    main_v2.process_run(now=datetime(2026, 2, 21, tzinfo=timezone.utc))

    assert len(fake_db.events) == 1
    event = fake_db.events[0]
    # Must have been downgraded away from funding_round_completed, and the
    # valuation number must NEVER land in amount_usd.
    assert event["event_type"] != "funding_round_completed"
    assert event["amount_usd"] is None
