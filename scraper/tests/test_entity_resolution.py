import pytest

from domain_rules import normalize_company_name
from entity_resolution import MatchType, resolve_entity


# ---- 13. Case-insensitive name normalization ----

@pytest.mark.parametrize("a,b", [
    ("OpenAI", "openai"),
    ("OPENAI", "OpenAI"),
    ("Open AI, Inc.", "open ai inc"),
])
def test_normalize_company_name_is_case_insensitive(a, b):
    assert normalize_company_name(a) == normalize_company_name(b)


def test_normalize_company_name_strips_legal_suffixes_and_punctuation():
    assert normalize_company_name("OpenAI, Inc.") == normalize_company_name("OpenAI")
    assert normalize_company_name("Acme Technologies LLC") == normalize_company_name("Acme")


EXISTING = [
    {
        "id": "co-1",
        "canonical_name": "OpenAI",
        "normalized_name": normalize_company_name("OpenAI"),
        "primary_domain": "openai.com",
        "headquarters_country": "US",
        "description": "builds large language models and AI research",
    },
    {
        "id": "co-2",
        "canonical_name": "Clay (design tools)",
        "normalized_name": normalize_company_name("Clay"),
        "primary_domain": "clay.com",
        "headquarters_country": "US",
        "description": "sales prospecting and enrichment platform",
    },
    {
        "id": "co-3",
        "canonical_name": "Warp (terminal)",
        "normalized_name": normalize_company_name("Warp"),
        "primary_domain": "warp.dev",
        "headquarters_country": "US",
        "description": "modern terminal application for developers",
    },
]

ALIASES = [
    {"company_id": "co-1", "normalized_alias": normalize_company_name("Open AI")},
]


# ---- 14. Domain-based entity resolution ----

def test_domain_match_resolves_to_existing_company_even_with_different_name_spelling():
    candidate = {"company_name": "Open A.I.", "primary_domain": "https://openai.com/blog/post"}
    result = resolve_entity(candidate, EXISTING, ALIASES)
    assert result.match_type == MatchType.DOMAIN
    assert result.company_id == "co-1"
    assert result.confidence == 100


def test_no_duplicate_created_for_openai_variants():
    variants = ["OpenAI", "Open AI", "OpenAI, Inc."]
    for variant in variants:
        candidate = {"company_name": variant, "primary_domain": "openai.com"}
        result = resolve_entity(candidate, EXISTING, ALIASES)
        assert result.company_id == "co-1"


def test_alias_match_resolves_without_domain():
    candidate = {"company_name": "Open AI"}
    result = resolve_entity(candidate, EXISTING, ALIASES)
    assert result.match_type == MatchType.ALIAS
    assert result.company_id == "co-1"


def test_unrelated_new_company_is_not_matched():
    candidate = {"company_name": "Totally New Startup Co", "primary_domain": "totallynewstartup.io"}
    result = resolve_entity(candidate, EXISTING, ALIASES)
    assert result.match_type == MatchType.NEW


# ---- 15. Ambiguous company names ----

def test_ambiguous_name_without_domain_needs_review_not_auto_merged():
    # "Clay" collides with an existing, unrelated "Clay" -- no domain given,
    # so this must NOT silently merge into co-2.
    candidate = {"company_name": "Clay"}
    result = resolve_entity(candidate, EXISTING, ALIASES)
    assert result.match_type == MatchType.NEEDS_REVIEW
    assert result.company_id is None


def test_ambiguous_name_with_matching_domain_resolves_confidently():
    candidate = {"company_name": "Clay", "primary_domain": "clay.com"}
    result = resolve_entity(candidate, EXISTING, ALIASES)
    assert result.match_type == MatchType.DOMAIN
    assert result.company_id == "co-2"


def test_ambiguous_name_with_different_domain_is_a_new_company_not_a_merge():
    # A genuinely different "Warp" (e.g. a fintech) at a different domain
    # must be treated as a new company, never merged into the terminal-app Warp.
    candidate = {"company_name": "Warp", "primary_domain": "warp-payments.io"}
    result = resolve_entity(candidate, EXISTING, ALIASES)
    assert result.match_type != MatchType.NAME or result.company_id != "co-3"
    # Since its domain doesn't match any existing company, it should not
    # resolve to co-3 at all.
    assert result.company_id != "co-3"


def test_ambiguous_name_with_corroborating_hq_and_description_resolves():
    candidate = {
        "company_name": "Warp",
        "headquarters_country": "US",
        "description": "modern terminal application built for developers",
    }
    result = resolve_entity(candidate, EXISTING, ALIASES)
    assert result.match_type == MatchType.NAME
    assert result.company_id == "co-3"


def test_never_merges_solely_because_normalized_names_match_when_ambiguous():
    candidate = {"company_name": "Preview"}  # on the ambiguous list, no existing "Preview" row at all
    result = resolve_entity(candidate, EXISTING, ALIASES)
    assert result.match_type == MatchType.NEW  # no existing row to (wrongly) merge into


def test_multiple_existing_matches_require_review():
    duplicated_existing = EXISTING + [{
        "id": "co-4",
        "canonical_name": "OpenAI Robotics (unrelated)",
        "normalized_name": normalize_company_name("OpenAI"),
        "primary_domain": "openai-robotics.example",
        "headquarters_country": "DE",
        "description": "industrial robotics",
    }]
    candidate = {"company_name": "OpenAI"}
    result = resolve_entity(candidate, duplicated_existing, [])
    assert result.match_type == MatchType.NEEDS_REVIEW
