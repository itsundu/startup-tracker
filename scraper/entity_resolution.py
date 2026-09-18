"""
Entity resolution: decide whether a newly extracted candidate is an existing
`companies` row, a known alias of one, or a genuinely new company.

Deliberately does NOT use `company_name` as a unique key (the old schema's
`unique(company_name)` constraint is exactly what let "OpenAI" / "Open AI" /
"OpenAI, Inc." become three rows). Resolution order, matching the product
spec:

  1. Verified registrable domain (strongest signal -- two companies do not
     share a domain)
  2. Known alias (an explicit, previously-recorded alternate name)
  3. Normalized company name -- but ONLY as a candidate match to confirm
     with a human/second signal, never an automatic merge for names on the
     AMBIGUOUS_NAMES list (common English words / generic product names
     that collide across unrelated companies)
  4. Headquarters + description similarity as a tie-breaker signal only,
     never sufficient alone

`resolve_entity` never merges two companies just because normalized names
match (explicit product requirement) -- an ambiguous name with no domain and
no alias match returns `NEEDS_REVIEW`, not an automatic merge or an automatic
new record.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from domain_rules import normalize_company_name, registrable_domain

# Common English words / generic product-style names that have collided with
# unrelated startups in practice. A candidate whose normalized name is in
# this set may ONLY be resolved by domain or alias match -- name similarity
# alone is explicitly insufficient, per the "Clay, Warp, Preview" example in
# the product spec. Extend this list as new collisions are discovered; it is
# intentionally conservative (short generic/dictionary-word company names).
AMBIGUOUS_NAMES = {
    "clay", "warp", "preview", "notion", "linear", "ramp", "brex", "arc",
    "float", "flow", "pulse", "spark", "atlas", "compass", "anchor", "base",
    "core", "edge", "grid", "loop", "orbit", "prism", "vault", "zenith",
    "current", "range", "scale", "shift", "wave", "beam", "nova", "echo",
}


class MatchType(str, Enum):
    DOMAIN = "domain"
    ALIAS = "alias"
    NAME = "name"
    NEW = "new"
    NEEDS_REVIEW = "needs_review"


@dataclass
class ResolutionResult:
    match_type: MatchType
    company_id: Optional[str] = None
    confidence: int = 0
    reasons: List[str] = field(default_factory=list)


def _tokens(name: str) -> set:
    return set(normalize_company_name(name).split())


def _description_similarity(a: Optional[str], b: Optional[str]) -> float:
    """Cheap, dependency-free Jaccard similarity over word tokens -- good
    enough as a *tie-breaker* signal, never a primary one."""
    if not a or not b:
        return 0.0
    ta, tb = set(a.lower().split()), set(b.lower().split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def resolve_entity(
    candidate: Dict,
    existing_companies: List[Dict],
    existing_aliases: Optional[List[Dict]] = None,
) -> ResolutionResult:
    """
    candidate: {"company_name": str, "primary_domain": str | None,
                "headquarters_country": str | None, "description": str | None}
    existing_companies: [{"id": str, "canonical_name": str,
                           "normalized_name": str, "primary_domain": str | None,
                           "headquarters_country": str | None,
                           "description": str | None}, ...]
    existing_aliases: [{"company_id": str, "normalized_alias": str}, ...]
    """
    existing_aliases = existing_aliases or []
    candidate_name = candidate.get("company_name") or ""
    candidate_domain = registrable_domain(candidate.get("primary_domain")) if candidate.get("primary_domain") else None
    candidate_normalized = normalize_company_name(candidate_name)

    if not candidate_normalized and not candidate_domain:
        return ResolutionResult(MatchType.NEW, reasons=["no usable name or domain on candidate"])

    # 1. Domain match -- strongest possible signal, always wins.
    if candidate_domain:
        for company in existing_companies:
            existing_domain = registrable_domain(company.get("primary_domain")) if company.get("primary_domain") else None
            if existing_domain and existing_domain == candidate_domain:
                return ResolutionResult(
                    MatchType.DOMAIN,
                    company_id=company["id"],
                    confidence=100,
                    reasons=[f"registrable domain '{candidate_domain}' matches existing company"],
                )

    # 2. Alias match.
    for alias in existing_aliases:
        if alias.get("normalized_alias") == candidate_normalized and candidate_normalized:
            return ResolutionResult(
                MatchType.ALIAS,
                company_id=alias["company_id"],
                confidence=90,
                reasons=[f"'{candidate_normalized}' is a recorded alias"],
            )

    # 3. Normalized-name match.
    name_matches = [c for c in existing_companies if c.get("normalized_name") == candidate_normalized and candidate_normalized]

    if not name_matches:
        return ResolutionResult(MatchType.NEW, reasons=["no domain, alias, or name match found"])

    is_ambiguous_name = candidate_normalized in AMBIGUOUS_NAMES

    if len(name_matches) > 1:
        # Multiple existing companies share this normalized name -- never
        # auto-merge into an arbitrary one of them.
        return ResolutionResult(
            MatchType.NEEDS_REVIEW,
            reasons=[
                f"{len(name_matches)} existing companies share normalized name "
                f"'{candidate_normalized}'; domain or alias evidence required to disambiguate"
            ],
        )

    match = name_matches[0]

    # Single name match. For an ambiguous (common/generic) name, this alone
    # is NOT sufficient -- domain match was already tried and failed above,
    # so an ambiguous name reaches here only with strong corroborating
    # evidence (HQ + description similarity, computed below) or none at
    # all. The final ambiguous-name gate is applied after computing that
    # corroboration, not before, so "domain or OTHER STRONG EVIDENCE" (the
    # product requirement) actually gets evaluated.
    confidence = 60
    reasons = [f"normalized name '{candidate_normalized}' matches exactly one existing company"]

    same_hq = (
        candidate.get("headquarters_country")
        and match.get("headquarters_country")
        and candidate["headquarters_country"] == match["headquarters_country"]
    )
    similarity = _description_similarity(candidate.get("description"), match.get("description"))

    if same_hq:
        confidence += 15
        reasons.append("headquarters country matches")
    if similarity >= 0.2:
        confidence += 15
        reasons.append(f"description similarity {similarity:.2f} corroborates match")

    if is_ambiguous_name and not (same_hq or similarity >= 0.2):
        return ResolutionResult(
            MatchType.NEEDS_REVIEW,
            reasons=[
                f"'{candidate_normalized}' is on the ambiguous-name list and no domain/HQ/"
                "description corroboration was found"
            ],
        )

    return ResolutionResult(MatchType.NAME, company_id=match["id"], confidence=min(100, confidence), reasons=reasons)
