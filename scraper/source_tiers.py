"""
Configurable source-tier classification and syndication/duplicate detection.

Tier definitions are data (a dict), not a hardcoded if/else chain, so a
maintainer can re-tier a domain by editing TIER_DOMAINS without touching
logic -- matching the "all ranking components and weights must be
transparent and configurable" principle applied to sources too.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from domain_rules import registrable_domain

TIER_1_AUTHORITATIVE = 1   # company newsroom, official investor announcement, gov filing/regulator
TIER_2_JOURNALISM = 2      # established tech/business/regional publications
TIER_3_DISCOVERY = 3       # HN, aggregators, syndicated mirrors, blogs, unverified directories

# Domains classified as Tier 2 (established journalism) by default. This is
# the ranking-relevant subset from DATA_SOURCE_REGISTRY.md; anything not
# listed here defaults to Tier 3 (discovery) rather than being assumed
# authoritative -- an unknown domain must earn a higher tier explicitly.
TIER_2_DOMAINS = {
    "techcrunch.com", "venturebeat.com", "fastcompany.com", "theverge.com",
    "wired.com", "bloomberg.com", "reuters.com", "forbes.com", "axios.com",
    "cnbc.com", "wsj.com", "nytimes.com", "businessinsider.com",
    "yourstory.com", "inc42.com", "entrackr.com", "economictimes.indiatimes.com",
    "livemint.com", "moneycontrol.com", "eu-startups.com", "siliconcanals.com",
    "techinasia.com", "sifted.eu", "tech.eu",
}

# Domains classified as Tier 3 (discovery-only) explicitly, even though they
# are "syndicated press-release mirrors" rather than unknowns -- these are
# common enough sources to name explicitly so they never get silently
# promoted by an "unknown = tier 2" default.
TIER_3_DOMAINS = {
    "news.ycombinator.com", "prnewswire.com", "businesswire.com",
    "globenewswire.com", "medium.com", "substack.com", "reddit.com",
}

# Domains that, when they are the company's OWN registrable domain, count as
# Tier 1 (official company material). Checked by caller passing
# is_own_domain=True, since "own domain" is relative to which company we're
# scoring, not a fixed list.


@dataclass
class SourceTierResult:
    tier: int
    label: str
    reasons: List[str]


def classify_source_tier(
    source_url: str,
    is_own_company_domain: bool = False,
    is_investor_announcement: bool = False,
    is_government_or_regulator: bool = False,
    is_accelerator_controlled_profile: bool = False,
) -> SourceTierResult:
    """`is_own_company_domain` should be computed by the caller by comparing
    the source's registrable domain against the company's verified
    `primary_domain` -- this module has no notion of "which company" on its
    own."""
    if is_government_or_regulator:
        return SourceTierResult(TIER_1_AUTHORITATIVE, "government/regulator filing", ["government or regulator source"])
    if is_own_company_domain:
        return SourceTierResult(TIER_1_AUTHORITATIVE, "company newsroom/official site", ["source is the company's own verified domain"])
    if is_investor_announcement:
        return SourceTierResult(TIER_1_AUTHORITATIVE, "official investor announcement", ["source is a named investor's own announcement"])
    if is_accelerator_controlled_profile:
        return SourceTierResult(TIER_1_AUTHORITATIVE, "accelerator-controlled company profile", ["source is a reputable accelerator's own company profile"])

    domain = registrable_domain(source_url)
    if domain and domain in TIER_2_DOMAINS:
        return SourceTierResult(TIER_2_JOURNALISM, "established journalism", [f"'{domain}' is a recognized Tier 2 publication"])
    if domain and domain in TIER_3_DOMAINS:
        return SourceTierResult(TIER_3_DISCOVERY, "discovery source", [f"'{domain}' is a recognized Tier 3 discovery/syndication source"])

    return SourceTierResult(TIER_3_DISCOVERY, "unclassified (defaults to discovery)", [f"'{domain}' is not in the Tier 1/2 registry; defaulting to Tier 3"])


# ---- Syndication / duplicate detection -------------------------------------------------

_TRACKING_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src",
}


def canonicalize_url(url: str) -> str:
    """Strips tracking query params and fragment, lowercases scheme/host, so
    the same article shared via different tracking links canonicalizes to
    one URL."""
    if not url:
        return ""
    parsed = urlparse(url.strip())
    query = [(k, v) for k, v in parse_qsl(parsed.query) if k.lower() not in _TRACKING_QUERY_KEYS]
    query.sort()
    rebuilt = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        query=urlencode(query),
        fragment="",
    )
    path = rebuilt.path.rstrip("/") or "/"
    rebuilt = rebuilt._replace(path=path)
    return urlunparse(rebuilt)


def content_hash(title: str, body: str) -> str:
    """Stable hash of normalized title+body text, used to detect the same
    press release/article mirrored verbatim across multiple domains (a
    syndicated copy), which must NOT count as independent corroboration."""
    normalized = re.sub(r"\s+", " ", f"{title or ''} {body or ''}".lower()).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass
class SourceRecord:
    url: str
    title: str = ""
    body: str = ""
    domain: Optional[str] = None


def count_independent_sources(records: List[SourceRecord]) -> int:
    """Counts sources that are independent of one another: same canonical
    URL or identical content hash across different domains counts once, not
    N times -- syndicated wire copies of one press release are one source,
    not several independent confirmations."""
    seen_urls = set()
    seen_hashes = set()
    seen_domains = set()
    independent = 0

    for rec in records:
        canon = canonicalize_url(rec.url)
        chash = content_hash(rec.title, rec.body)
        domain = rec.domain or registrable_domain(rec.url)

        if canon in seen_urls:
            continue
        if chash and chash in seen_hashes:
            # Same content, different URL/domain -- a syndicated mirror, not
            # an independent source.
            seen_urls.add(canon)
            continue
        if domain and domain in seen_domains:
            # Same publication reporting twice (a follow-up article) is not
            # a second independent source either.
            seen_urls.add(canon)
            continue

        seen_urls.add(canon)
        if chash:
            seen_hashes.add(chash)
        if domain:
            seen_domains.add(domain)
        independent += 1

    return independent
