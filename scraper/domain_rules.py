"""
Maintainable denylists used by homepage_validator.py and source_tiers.py.

Nothing here calls the network -- pure data + pure functions, so it's fully
unit-testable without mocking. Keep new entries alphabetized within their
section so diffs stay small and reviewable.
"""

import re
from urllib.parse import urlparse

# File extensions that can never be a company homepage, regardless of what
# domain they sit on. Checked against the URL path (case-insensitive) AND,
# when available, the response Content-Type.
ASSET_EXTENSIONS = {
    ".js", ".mjs", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".avif", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".pdf",
    ".mp4", ".mp3", ".zip", ".json", ".xml", ".csv",
}

# Content-Type prefixes/values that disqualify a homepage candidate even if
# the URL itself looks clean (e.g. a tracking pixel served at a bare path).
NON_HTML_CONTENT_TYPES = (
    "image/", "font/", "video/", "audio/", "application/javascript",
    "text/javascript", "text/css", "application/json", "application/pdf",
    "application/octet-stream", "application/zip",
)

# CDN / asset-hosting domains. A URL resolving here is serving someone's
# static assets, not a company's own site.
CDN_DOMAINS = {
    "cloudfront.net", "akamaized.net", "akamaihd.net", "fastly.net",
    "cloudflare.com", "jsdelivr.net", "unpkg.com", "gstatic.com",
    "googleusercontent.com", "wp.com", "imgix.net", "cdninstagram.com",
    "bootstrapcdn.com", "jquery.com", "polyfill.io",
}

# Advertising / tag-manager / tracking endpoints. These show up constantly in
# article body HTML and RSS summaries as the "first link" -- exactly the bug
# this module exists to fix.
AD_TRACKING_DOMAINS = {
    "doubleclick.net", "googlesyndication.com", "googletagmanager.com",
    "googletagservices.com", "google-analytics.com", "googleadservices.com",
    "adservice.google.com", "adnxs.com", "taboola.com", "outbrain.com",
    "criteo.com", "scorecardresearch.com", "quantserve.com", "hotjar.com",
    "segment.io", "segment.com", "mixpanel.com", "amplitude.com",
    "chartbeat.com", "moatads.com", "amazon-adsystem.com", "pubmatic.com",
    "rubiconproject.com", "openx.net", "bidswitch.net", "adsrvr.org",
}

# Social networks. Legitimate company presences, never a company's own
# registrable homepage.
SOCIAL_DOMAINS = {
    "twitter.com", "x.com", "facebook.com", "instagram.com", "linkedin.com",
    "youtube.com", "tiktok.com", "pinterest.com", "reddit.com", "threads.net",
    "mastodon.social", "medium.com", "substack.com",
}

# Publications, aggregators, and other third-party sources this project
# itself reads from -- a homepage candidate resolving to one of these is
# almost always the article's own domain leaking through as "the first
# link," not the startup's site.
PUBLICATION_DOMAINS = {
    "techcrunch.com", "venturebeat.com", "fastcompany.com", "yourstory.com",
    "inc42.com", "entrackr.com", "eu-startups.com", "siliconcanals.com",
    "techinasia.com", "prnewswire.com", "businesswire.com", "globenewswire.com",
    "news.ycombinator.com", "ycombinator.com", "github.com", "wikipedia.org",
    "crunchbase.com", "bloomberg.com", "reuters.com", "forbes.com",
    "theverge.com", "wired.com", "axios.com", "cnbc.com", "wsj.com",
    "nytimes.com", "economictimes.indiatimes.com", "livemint.com",
    "moneycontrol.com", "apple.com", "play.google.com", "apps.apple.com",
    "google.com", "amazon.com",
}

# App-store / platform links that resolve fine and serve HTML, but aren't a
# *company* homepage -- they're a listing on someone else's platform.
PLATFORM_LISTING_DOMAINS = {
    "apps.apple.com", "play.google.com", "chrome.google.com",
    "producthunt.com", "angel.co", "wellfound.com", "glassdoor.com",
    "indeed.com", "ziprecruiter.com",
}

ALL_DENYLISTED_DOMAINS = (
    CDN_DOMAINS
    | AD_TRACKING_DOMAINS
    | SOCIAL_DOMAINS
    | PUBLICATION_DOMAINS
    | PLATFORM_LISTING_DOMAINS
)

# Path/query substrings that mark a URL as a tracking or tag-manager
# endpoint even on an otherwise-unknown domain (e.g. a publication's own
# first-party tracking pixel proxy).
TRACKING_PATH_MARKERS = (
    "/gtm.js", "/gtag/", "/analytics.js", "/pixel", "/track?", "/beacon",
    "/collect?", "/ga.js", "utm_source=", "fbclid=",
)

LEGAL_SUFFIXES = [
    r"\bincorporated\b", r"\binc\.?\b", r"\bltd\.?\b", r"\blimited\b",
    r"\bllc\b", r"\bllp\b", r"\bpvt\.?\b", r"\bpte\.?\b", r"\bcorp\.?\b",
    r"\bcorporation\b", r"\bco\.?\b", r"\bgmbh\b", r"\bplc\b", r"\bsas\b",
    r"\bpbc\b", r"\bholdings?\b", r"\bgroup\b", r"\btechnologies\b",
    r"\btechnology\b", r"\bsolutions\b", r"\blabs\b", r"\bai\b",
]


def registrable_domain(url_or_domain):
    """Best-effort eTLD+1 without a public-suffix-list dependency: strips
    scheme/path/port/www, then keeps the last two labels, except for a small
    set of common two-part public suffixes (co.in, co.uk, com.au, ...) where
    the last three labels are kept. Good enough for denylist/allowlist
    matching; not a substitute for a real PSL for edge-case ccTLDs.
    """
    if not url_or_domain:
        return None
    value = url_or_domain.strip().lower()
    if "://" not in value:
        value = "https://" + value
    host = urlparse(value).hostname
    if not host:
        return None
    host = host.split(":")[0]
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    two_part_suffixes = {
        "co.in", "co.uk", "co.jp", "com.au", "com.br", "com.sg", "com.cn",
        "co.nz", "co.za", "com.mx", "ne.jp", "or.jp", "org.uk", "net.in",
    }
    last_two = ".".join(labels[-2:])
    last_three = ".".join(labels[-3:])
    if last_two in two_part_suffixes and len(labels) >= 3:
        return last_three
    return last_two


def is_denylisted_domain(domain):
    """True if `domain` (or a subdomain of it) is a known CDN/ad/social/
    publication/platform domain."""
    if not domain:
        return False
    domain = domain.lower()
    for bad in ALL_DENYLISTED_DOMAINS:
        if domain == bad or domain.endswith("." + bad):
            return True
    return False


def has_asset_extension(path_or_url):
    if not path_or_url:
        return False
    path = urlparse(path_or_url).path if "://" in path_or_url else path_or_url
    path = path.lower().split("?")[0]
    return any(path.endswith(ext) for ext in ASSET_EXTENSIONS)


def is_non_html_content_type(content_type):
    if not content_type:
        return False
    ct = content_type.lower().split(";")[0].strip()
    return any(ct.startswith(bad) for bad in NON_HTML_CONTENT_TYPES)


def is_tracking_url(url):
    if not url:
        return False
    low = url.lower()
    return any(marker in low for marker in TRACKING_PATH_MARKERS)


def normalize_company_name(name):
    """Lowercase, strip legal suffixes/punctuation, collapse whitespace --
    used as the join key for entity resolution (never the sole key, see
    entity_resolution.py, but the first pass)."""
    if not name:
        return ""
    value = name.lower().strip()
    value = re.sub(r"[,.]", " ", value)
    for suffix in LEGAL_SUFFIXES:
        value = re.sub(suffix, " ", value)
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value
