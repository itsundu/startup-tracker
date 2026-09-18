"""
Verified hiring-signal detection.

Replaces the old rule ("a /careers fetch returned any 200, or a keyword
string appears somewhere on the homepage" -> "Likely hiring"). A soft-404
(a SPA or static-site host that returns HTTP 200 with a generic "page not
found" or empty-shell body for every path) must never count as evidence of
hiring -- this module requires positive, structured evidence instead.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional

ATS_DOMAINS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
    "bamboohr.com", "smartrecruiters.com", "myworkdayjobs.com",
    "breezy.hr", "jazzhr.com", "recruitee.com", "rippling.com",
    "wellfound.com/company", "join.com",
)

JOB_SCHEMA_MARKERS = (
    '"@type":"jobposting"', '"@type": "jobposting"', "schema.org/jobposting",
)

OPEN_ROLE_PHRASES = (
    "open position", "open positions", "open role", "open roles",
    "current openings", "we're hiring", "we are hiring", "join our team",
    "apply now", "view all jobs", "view open roles", "current opportunities",
)

SOFT_404_MARKERS = (
    "page not found", "404 not found", "oops", "nothing here",
    "this page doesn't exist", "we couldn't find that page", "coming soon",
)

# A bare mention of these without any of the positive signals above (no ATS
# link, no job schema, no explicit "open positions"/"apply now" language)
# reads as marketing copy ("hiring is core to our culture") rather than an
# actual current listing, so it is NOT treated as a positive signal alone.
WEAK_MARKETING_PHRASES = (
    "we're always hiring", "always looking for talent", "hiring is in our dna",
)

JOB_COUNT_RE = re.compile(r"(\d+)\s+(?:open\s+)?(?:job|position|role)s?\b", re.IGNORECASE)


@dataclass
class CareersPageResult:
    status: str  # "actively_hiring" | "limited_hiring" | "no_verified_openings" | "unknown"
    confidence: int
    open_role_count: Optional[int]
    evidence: List[str] = field(default_factory=list)


def _is_soft_404(html: str) -> bool:
    if not html:
        return True
    low = html.lower()
    text_only = re.sub(r"<[^>]+>", " ", low)
    text_only = re.sub(r"\s+", " ", text_only).strip()
    # A very short body plus a soft-404 marker is the classic SPA-shell case.
    if any(marker in text_only for marker in SOFT_404_MARKERS) and len(text_only) < 2000:
        return True
    return False


def evaluate_careers_page(html: Optional[str], url: Optional[str] = None) -> CareersPageResult:
    """Evaluate a fetched careers/jobs page's HTML for genuine, current
    hiring evidence. `html` should be the raw fetched body (status 200
    already confirmed by the caller) -- this function judges CONTENT, not
    just reachability, which is the whole point.
    """
    if not html or not html.strip():
        return CareersPageResult("unknown", 0, None, ["no page content available"])

    if _is_soft_404(html):
        return CareersPageResult(
            "unknown", 10, None,
            ["page content looks like a soft-404 / empty shell, not a real careers page"],
        )

    low = html.lower()
    evidence = []
    confidence = 20  # page fetched, is real content, not a soft-404
    evidence.append("careers page fetched successfully and is not a soft-404")

    has_ats_link = any(d in low for d in ATS_DOMAINS)
    has_job_schema = any(m in low for m in JOB_SCHEMA_MARKERS)
    has_open_phrase = any(p in low for p in OPEN_ROLE_PHRASES)

    count_match = JOB_COUNT_RE.search(html)
    open_role_count = int(count_match.group(1)) if count_match else None

    if has_ats_link:
        confidence += 35
        evidence.append("links to a known applicant-tracking-system domain (Greenhouse/Lever/etc.)")
    if has_job_schema:
        confidence += 35
        evidence.append("page contains schema.org JobPosting structured data")
    if has_open_phrase:
        confidence += 15
        evidence.append("page contains explicit current-openings language")
    if open_role_count is not None:
        confidence += 10
        evidence.append(f"page states an explicit open-role count ({open_role_count})")

    confidence = max(0, min(100, confidence))

    if not (has_ats_link or has_job_schema or has_open_phrase or open_role_count):
        if any(p in low for p in WEAK_MARKETING_PHRASES):
            evidence.append("only generic 'always hiring' marketing language found, no current listings")
        return CareersPageResult("no_verified_openings", confidence, None, evidence)

    if open_role_count == 0:
        return CareersPageResult("no_verified_openings", confidence, 0, evidence)

    if has_ats_link or has_job_schema or (open_role_count and open_role_count >= 3):
        return CareersPageResult("actively_hiring", confidence, open_role_count, evidence)

    return CareersPageResult("limited_hiring", confidence, open_role_count, evidence)
