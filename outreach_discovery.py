"""
outreach_discovery.py — supplier discovery, scraping & vetting engine.

A faithful Python port of the Node service `server/services/searchService.js`
(plus `aiVerificationService.js`) from the standalone momira-suppliersearch-mail
app, so the outreach tool can live inside the Momira Travel Platform. The
platform runs on Streamlit, which is Python-only and cannot host the original
Express server - see the module note at the bottom of this docstring for what
changed and what deliberately did not.

Pipeline (unchanged from the original):
  1. build_queries()      -> source-targeted search queries
  2. run_provider_search() -> pluggable search API per query (Tavily/SerpAPI/mock)
  3. parse_signals()      -> rating / review count / handle out of raw text
  4. pre-filter           -> drop non-businesses (listicles, editorial, forums...)
  5. vet_candidates()     -> drop anything below the quality bar
  6. dedupe_candidates()  -> merge duplicate mentions of one business
  7. AI verification      -> optional Claude pass, judgment the rules can't do
  8. enrich_from_website() -> scrape for a direct email / contact / Instagram
  9. to_supplier_record() -> normalize for the review table

PORTING RULES FOLLOWED (this matters more than elegance here - these heuristics
were tuned against real search results over time, and a subtly different rating
parser silently changes which suppliers pass vetting):
  * Every regex, keyword list, threshold and ordering is carried over verbatim.
    The original's explanatory comments are kept alongside them, because they
    record WHY a rule exists - several document real false positives that a
    "cleaner" rule would reintroduce (e.g. "afar" matching inside "safari").
  * JavaScript/Python semantic differences that would silently change behaviour
    are handled explicitly and flagged inline:
      - JS `str.replace(re, '')` without /g replaces only the FIRST match;
        Python's re.sub replaces ALL. Ported with count=1.
      - JS `??` (nullish coalescing) falls through only on null/undefined, NOT
        on 0. A rating of 0 is valid here (the parser accepts 0-5), so those
        are ported as explicit `is None` checks, never `or`.
      - JS `new URL(x)` throws on a malformed URL and several callers
        deliberately catch and return a permissive default. Python's urlparse
        doesn't throw, so "malformed" is detected as a missing hostname and the
        same permissive default is returned.
      - `[...new Set(x)]` preserves insertion order; dict.fromkeys() is used
        rather than set(), which doesn't.
  * cheerio -> BeautifulSoup and axios -> requests, both already platform
    dependencies (web_extractor.py already scrapes this way).

WHAT CHANGED: the original's async fan-out (Promise.all over queries and
candidates) is sequential here. Streamlit renders synchronously and the search
already runs behind a progress indicator, so concurrency bought complexity
rather than perceived speed. Behaviour is identical; only wall-clock differs.
"""
import os
import re
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

REQUEST_TIMEOUT_S = 8  # REQUEST_TIMEOUT_MS = 8000 in the original


# ============================================================================
# CONFIG - read at call time (not import time) so Streamlit's secrets loading,
# which populates os.environ before the tool runs, is always picked up.
# ============================================================================
def _min_rating() -> float:
    try:
        return float(os.getenv("MIN_SUPPLIER_RATING") or "4.0")
    except ValueError:
        return 4.0


def _max_results() -> int:
    try:
        return int(os.getenv("MAX_SUPPLIER_RESULTS") or "20")
    except ValueError:
        return 20


def _max_candidates() -> int:
    """How many raw candidates get the (expensive) website-enrichment pass before the
    final trim to MAX_RESULTS. Some won't have a usable contact method and get dropped
    after enrichment, so this always needs headroom above MAX_RESULTS - enforced as a
    floor so an old, lower MAX_SUPPLIER_CANDIDATES value can't choke the final count."""
    try:
        configured = int(os.getenv("MAX_SUPPLIER_CANDIDATES") or "30")
    except ValueError:
        configured = 30
    return max(configured, _max_results() + 10)


# ============================================================================
# 1. QUERY BUILDING - one query per target source
# ============================================================================
def build_queries(country: str, keyword: str) -> List[Dict[str, Any]]:
    base = f"{keyword} {country}".strip()
    return [
        # domains: passed to the provider's real domain-restriction parameter
        # (Tavily's include_domains / SerpAPI's site: operator), NOT embedded as
        # "site:x.com" text in the query. Tavily does not treat "site:" text as a
        # strict filter - it just does loose keyword matching, which let totally
        # unrelated pages (retail stores, dictionary definitions) leak in.
        {"source": "tripadvisor", "query": f"{base} reviews", "domains": ["tripadvisor.com"], "max_results": 10},
        # Trustpilot / Viator / GetYourGuide are REVIEW-QUALITY signal sources
        # (rating/review-count text to feed vetting), same role as Tripadvisor above -
        # not places to find a contact email (OTA listing pages almost never expose
        # one). Small result budget each: secondary signal, not primary discovery.
        {"source": "trustpilot", "query": f"{base} reviews", "domains": ["trustpilot.com"], "max_results": 6},
        {"source": "viator", "query": f"{base} reviews", "domains": ["viator.com"], "max_results": 6},
        {"source": "getyourguide", "query": f"{base} reviews", "domains": ["getyourguide.com"], "max_results": 6},
        # "contact email" used to be baked into these two queries, which biased the
        # engine toward CONTACT subpages instead of the operator's actual homepage
        # (real evidence: supplier sites showing up titled "Contact Us" instead of
        # their business name). Enrichment already scrapes for an email afterward.
        {"source": "google", "query": f"{base} tour operator best reviews", "domains": [], "max_results": 10},
        # Aimed at the operator's OWN site rather than review/listicle content - more
        # likely to surface direct-supplier brands than aggregators. This is the
        # priority source (own website first, social secondary), so the largest budget.
        {"source": "website", "query": f"{base} official website", "domains": [], "max_results": 14},
        # Social is a distant second priority - a fallback contact channel, not a
        # primary discovery source, so a smaller budget.
        {"source": "instagram", "query": f"{base}", "domains": ["instagram.com"], "max_results": 5},
        # No dedicated Facebook query - results were overwhelmingly personal posts and
        # group discussions rather than business pages, even after profile-shape
        # filtering. A Facebook Page can still surface incidentally via the queries
        # above (see _is_facebook_url).
    ]


# ============================================================================
# 2. PLUGGABLE SEARCH PROVIDERS
# ============================================================================
def _search_with_tavily(query: str, domains: List[str], max_results: int) -> List[Dict[str, Any]]:
    payload = {
        "api_key": os.getenv("TAVILY_API_KEY"),
        "query": query,
        "search_depth": "advanced",
        "include_answer": False,
        "max_results": max_results,
    }
    if domains:
        payload["include_domains"] = domains
    res = requests.post("https://api.tavily.com/search", json=payload, timeout=REQUEST_TIMEOUT_S)
    res.raise_for_status()
    data = res.json()
    return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")}
            for r in (data.get("results") or [])]


def _search_with_serpapi(query: str, domains: List[str], max_results: int) -> List[Dict[str, Any]]:
    # SerpAPI proxies real Google search, where "site:" IS a genuine operator, so the
    # domain restriction is folded into the query text here (unlike Tavily above).
    scoped = f"{' OR '.join('site:' + d for d in domains)} {query}" if domains else query
    res = requests.get(
        "https://serpapi.com/search.json",
        params={"q": scoped, "api_key": os.getenv("SERPAPI_API_KEY"), "num": max_results},
        timeout=REQUEST_TIMEOUT_S,
    )
    res.raise_for_status()
    data = res.json()
    return [{"title": r.get("title"), "url": r.get("link"), "snippet": r.get("snippet") or ""}
            for r in (data.get("organic_results") or [])]


_MOCK_SEED_NAMES = [
    "Blue Horizon", "Coral Coast", "Sunset Bay", "Island Breeze", "Reef Runner",
    "Wanderlust", "True North", "Local Roots", "Wave Chaser", "Trailblazer",
]


def _search_with_mock_provider(source: str, country: str, keyword: str) -> List[Dict[str, Any]]:
    """Deterministic, clearly-labeled sample data, used only when no search API key is
    configured - keeps the whole wizard demonstrable out of the box."""
    results = []
    for i in range(3):
        name = re.sub(r"\s+", " ",
                      f"{_MOCK_SEED_NAMES[(len(source) + i) % len(_MOCK_SEED_NAMES)]} "
                      f"{keyword.split(' ')[0]} {country}").strip()
        slug = re.sub(r"[^a-z0-9]+", "", name.lower())
        rating = f"{4 + ((i + len(source)) % 10) / 10:.1f}"  # 4.0 - 4.9
        if source == "tripadvisor":
            url = f"https://www.tripadvisor.com/Attraction_Review-mock-{slug}.html"
        elif source == "instagram":
            url = f"https://www.instagram.com/{slug}/"
        elif source == "facebook":
            url = f"https://www.facebook.com/{slug}/"
        else:
            url = f"https://www.{slug}.com"
        results.append({
            "title": f"{name} - {rating} stars ({80 + i * 37} reviews) | {source}",
            "url": url,
            "snippet": (f"{name} offers {keyword} in {country}. Rated {rating}/5 based on "
                        f"{80 + i * 37} reviews. \"Absolutely fantastic experience, highly recommend!\" "
                        f"Contact: info@{slug}.com. Website: https://www.{slug}.com"),
            "isMock": True,
        })
    return results


def run_provider_search(source: str, query: str, country: str, keyword: str,
                        domains: Optional[List[str]] = None, max_results: int = 10) -> List[Dict[str, Any]]:
    domains = domains or []
    try:
        if os.getenv("TAVILY_API_KEY"):
            return _search_with_tavily(query, domains, max_results)
        if os.getenv("SERPAPI_API_KEY"):
            return _search_with_serpapi(query, domains, max_results)
        return _search_with_mock_provider(source, country, keyword)
    except Exception as e:
        print(f"[outreach_discovery] provider search failed for \"{query}\": {e}")
        return []


# ============================================================================
# 3. SIGNAL PARSING - rating / review count / handles out of raw text
# ============================================================================
RATING_PATTERNS = [
    re.compile(r"(\d(?:[.,]\d)?)\s*(?:/|out of|von)\s*5", re.I),              # "4.6/5", "4,6 von 5"
    re.compile(r"(\d(?:[.,]\d)?)\s*(?:of|out of)\s*5\s*bubbles", re.I),        # Tripadvisor "4.5 of 5 bubbles"
    re.compile(r"(\d(?:[.,]\d)?)\s*stars?", re.I),                             # "4.6 stars"
    re.compile(r"(\d(?:[.,]\d)?)\s*sterne?", re.I),                            # "4,6 Sterne" (German)
    re.compile(r"(\d(?:[.,]\d)?)\s*★", re.I),                                  # "4.6★"
    re.compile(r"rated\s*(\d(?:[.,]\d)?)", re.I),                              # "rated 4.6"
    re.compile(r"rating[:\s]+(\d(?:[.,]\d)?)", re.I),                          # "Rating: 4.6"
    re.compile(r"\((\d(?:[.,]\d)?)\)"),                                        # "(4.6)" next to a name
]

REVIEW_COUNT_PATTERN = re.compile(r"(\d[\d,]*)\+?\s*(?:google\s*)?reviews?", re.I)

POSITIVE_KEYWORDS = [
    "traveler's choice", "travelers choice", "certificate of excellence",
    "highly recommend", "highly rated", "highly reviewed", "well reviewed",
    "well-reviewed", "top rated", "top-rated", "5-star", "five star", "five-star",
    "great reviews", "great service", "excellent service", "excellent",
    "outstanding", "amazing experience", "wonderful experience",
    "best of the best", "best in", "loved by", "recommended by",
]

NEGATIVE_KEYWORDS = [
    "permanently closed", "closed down", "temporarily closed", "do not recommend",
    "wouldn't recommend", "would not recommend", "avoid this", "avoid at all",
    "scam", "terrible experience", "worst experience", "poor reviews",
    "bad reviews", "1 star", "2 star", "1-star", "2-star",
]


def parse_rating(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    for pattern in RATING_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                value = float(match.group(1).replace(",", "."))
            except ValueError:
                continue
            if 0 <= value <= 5:
                return value
    return None


def has_negative_signal(text: Optional[str]) -> bool:
    if not text:
        return False
    lower = text.lower()
    return any(kw in lower for kw in NEGATIVE_KEYWORDS)


def parse_review_count(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    match = REVIEW_COUNT_PATTERN.search(text)
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def has_positive_signal(text: Optional[str]) -> bool:
    if not text:
        return False
    lower = text.lower()
    return any(kw in lower for kw in POSITIVE_KEYWORDS)


# Generic words that show up as a title SEGMENT but are never themselves the
# business name - "Reviews - Oberoi Philae" should resolve to "Oberoi Philae".
TITLE_NOISE_SEGMENTS = [
    "reviews", "review", "photos", "photo", "tripadvisor", "facebook", "instagram",
    "home", "contact", "about", "best", "top", "deals", "deal", "welcome",
]

_STARS_IN_TITLE = re.compile(r"\d(\.\d)?\s*stars?", re.I)


def guess_company_name(title: str) -> str:
    """Titles aren't always "BusinessName - Reviews" - plenty are the reverse
    ("Reviews - BusinessName") or "Tripadvisor: BusinessName", so blindly taking the
    first segment cuts off the real name half the time. Take the first segment that
    actually looks like a name (long enough, not a generic noise word), falling back
    to the first segment."""
    # NOTE: JS `.replace(re, '')` without /g strips only the FIRST match - count=1
    # preserves that exactly (Python's default would strip every occurrence).
    cleaned = _STARS_IN_TITLE.sub("", title or "", count=1)
    segments = [s.strip() for s in re.split(r"[-|:]", cleaned) if s.strip()]
    if not segments:
        return cleaned.strip()
    best = next((seg for seg in segments
                 if len(seg) >= 4 and seg.lower() not in TITLE_NOISE_SEGMENTS), None)
    return (best or segments[0]).strip()


# Titles like "Instagram", "Log in" show up when a search engine falls back to a
# generic page title instead of the business name. Real evidence this was too narrow:
# a genuine supplier site got indexed on its Contact page, so the extracted "name"
# came out as "Contact Us" and would have shown as the business name.
GENERIC_NAME_BLOCKLIST = [
    "instagram", "facebook", "log in", "login", "sign up", "signup", "home",
    "error", "page not found", "see more", "welcome to facebook",
    "contact", "contact us", "about", "about us", "privacy policy", "privacy",
    "terms of service", "terms and conditions", "terms", "faq", "faqs", "blog",
    "sitemap", "cookie policy", "cookies", "menu", "search",
]

# Fragment names like "In" or "On" happen when a page's real title never loaded and
# the search API fell back to a stray body word - a single common short word is never
# an actual business name.
NAME_STOPWORDS = {
    "in", "on", "at", "by", "to", "of", "for", "and", "or", "is", "it", "a",
    "an", "the", "this", "that", "with", "from",
}


def is_generic_name(name: Optional[str]) -> bool:
    if not name:
        return True
    n = name.lower().strip()
    if len(n) < 3:
        return True
    if n in NAME_STOPWORDS:
        return True
    return any(n == g or n.startswith(g) for g in GENERIC_NAME_BLOCKLIST)


# Real evidence this is needed: a genuine supplier's own website got indexed on its
# "Contact Us" page, so the name came out generic and the WHOLE candidate used to be
# dropped - even though it's a real business with its own domain (often with a real
# scraped email). Fall back to a name derived from the domain instead. Only ever used
# on a candidate's OWN website URL, never an aggregator/social one.
GENERIC_DOMAIN_SEGMENTS = {
    "www", "com", "net", "org", "co", "travel", "info", "shop", "online", "web", "site",
}


def derive_name_from_url(url: str) -> Optional[str]:
    host = _hostname(url)
    if not host:
        return None
    host = re.sub(r"^www\.", "", host)
    primary = next((seg for seg in host.split(".") if seg not in GENERIC_DOMAIN_SEGMENTS), None)
    if not primary or len(primary) < 3:
        return None
    # Split on hyphens/underscores where the domain has them ("nile-cruise-ship.com"
    # -> "Nile Cruise Ship"). A solid run ("nilecruiseship.com") can't be reliably
    # split without a dictionary, so it stays one capitalized word - still usable.
    words = [w for w in re.split(r"[-_]+", primary) if w]
    return " ".join(w[0].upper() + w[1:] for w in words)


# Forum questions and listicle round-ups are informational CONTENT about a niche, not
# a single identifiable business - they should never become a candidate at all.
QUESTION_TITLE_PATTERN = re.compile(
    r"^(what|how|is|are|should|why|when|which|can|do|does|will|where)\b", re.I)
# Real evidence the old version was too narrow: "Best Nile River Cruises 2026/27"
# slipped through because it has no LEADING digit. A real business is never literally
# named "Best ... 2026", so requiring a year keeps this from misfiring on a genuine
# company name that happens to start with "Best" or "Top".
LISTICLE_TITLE_PATTERN = re.compile(
    r"^\d{1,3}\s*(best|top)\b|^(the\s+)?(best|top)\b.*\b(19|20)\d{2}(\s*[/\-]\s*\d{2,4})?\b", re.I)


def is_question_or_listicle_title(title: Optional[str]) -> bool:
    if not title:
        return False
    t = title.strip()
    if not t:
        return False
    if t.endswith("?"):
        return True
    if QUESTION_TITLE_PATTERN.search(t):
        return True
    if LISTICLE_TITLE_PATTERN.search(t):
        return True
    return False


# Travel magazines write ABOUT suppliers, they aren't one. An article title slipping
# through is a different failure mode than a listicle, so it needs its own check.
EDITORIAL_PUBLISHERS = [
    "afar", "lonely planet", "condé nast traveler", "travel + leisure",
    "national geographic", "the points guy", "fodor", "frommer",
    "smarter travel", "travel and leisure",
]

# Word-boundary matching, NOT a plain substring check. A naive "in" check has a nasty
# false positive: the publisher "afar" matches inside the ordinary word "safari"
# (s-AFAR-i), which would wrongly reject every desert/wildlife safari supplier -
# exactly what Momira searches for constantly.
EDITORIAL_PUBLISHER_PATTERNS = [
    re.compile(rf"\b{re.escape(p)}\b", re.I) for p in EDITORIAL_PUBLISHERS
]


def is_editorial_content(name: Optional[str], snippet: Optional[str]) -> bool:
    text = f"{name or ''} {snippet or ''}"
    return any(p.search(text) for p in EDITORIAL_PUBLISHER_PATTERNS)


# Tripadvisor URLs come in very different shapes: a single business's own review page
# (what we want), a forum thread, or a category/city aggregator listing dozens of
# businesses (which was showing up mislabeled as one company). Only the first is usable.
TRIPADVISOR_LISTING_PATH_PATTERN = re.compile(r"_review-", re.I)
TRIPADVISOR_NON_LISTING_PATH_PATTERN = re.compile(
    r"/(showtopic|showuserreviews|tourism-g|attractions-g|restaurants-g|hotels-g)", re.I)


def _hostname(url: Optional[str]) -> Optional[str]:
    """urlparse() doesn't raise on a malformed URL the way JS's `new URL()` does, so a
    missing hostname is what "malformed" looks like here. Callers replicate the
    original's catch-block defaults explicitly."""
    if not url:
        return None
    try:
        return (urlparse(url).hostname or "").lower() or None
    except ValueError:
        return None


def _pathname(url: str) -> str:
    try:
        return urlparse(url).path or ""
    except ValueError:
        return ""


def is_tripadvisor_listing_url(url: str) -> bool:
    host = _hostname(url)
    if not host:
        return True  # malformed - don't reject on this basis alone
    if not re.search(r"(^|\.)tripadvisor\.com$", host, re.I):
        return True  # not a Tripadvisor URL - this check doesn't apply
    path = _pathname(url)
    if TRIPADVISOR_NON_LISTING_PATH_PATTERN.search(path):
        return False
    return bool(TRIPADVISOR_LISTING_PATH_PATTERN.search(path))


# Momira wants DIRECT suppliers - the business that actually operates the product.
# Brand size isn't the signal (Oberoi, Jaz Group, Mövenpick are big AND direct
# suppliers, and should show up). DMCs are allowed through too. The only thing
# filtered here is a generic online marketplace/OTA, which isn't a supplier at all.
OTA_MARKETPLACE_BLOCKLIST = [
    "expedia", "booking.com", "viator", "getyourguide", "trip.com",
    "kayak", "skyscanner", "hotels.com", "agoda",
]


def is_ota_or_marketplace(name: Optional[str]) -> bool:
    if not name:
        return False
    n = name.lower()
    return any(b in n for b in OTA_MARKETPLACE_BLOCKLIST)


# Defense-in-depth: even with provider-side domain restriction, a loosely-matched
# result can slip through (a search engine surfacing an unrelated retail page because
# a query word overlapped). Require the candidate to actually mention the country or
# keyword somewhere before it goes anywhere near vetting.
def build_relevance_tokens(country: str, keyword: str) -> List[str]:
    return [tok for tok in re.split(r"[^a-z0-9]+", f"{country} {keyword}".lower()) if len(tok) >= 3]


def is_relevant_candidate(candidate: Dict[str, Any], relevance_tokens: List[str]) -> bool:
    if not relevance_tokens:
        return True
    haystack = (f"{candidate.get('name') or ''} {candidate.get('snippet') or ''} "
                f"{candidate.get('sourceUrl') or ''}").lower()
    return any(tok in haystack for tok in relevance_tokens)


def extract_username_from_profile_url(url: str) -> Optional[str]:
    """When Instagram gives a generic page title, fall back to the @username in the URL."""
    segments = [s for s in _pathname(url).split("/") if s]
    return segments[0] if segments else None


# Instagram/Facebook links often land on an individual POST or a group thread rather
# than the business's own profile - those aren't a "supplier", so only profile/page-
# shaped URLs are kept. The URL host is checked directly (not the query source), since
# a Facebook link can surface via a Google/Tripadvisor result too.
INSTAGRAM_NON_PROFILE_SEGMENTS = {
    "p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct",
}
FACEBOOK_NON_PAGE_PATH = re.compile(
    r"/(posts|permalink|groups|photo|photos|videos|watch|reel|story|notes|events|sharer|profile\.php)", re.I)


def is_instagram_url(url: str) -> bool:
    host = _hostname(url)
    return bool(host and re.search(r"(^|\.)instagram\.com$", host, re.I))


def is_facebook_url(url: str) -> bool:
    host = _hostname(url)
    return bool(host and re.search(r"(^|\.)facebook\.com$", host, re.I))


# Review-aggregator / listing domains. A URL on one of these is NOT the business's own
# website - it's a third-party page ABOUT the business. Treating it as "website" and
# scraping it for an email wastes the enrichment pass and was a major reason emails
# were going missing even for real, well-known suppliers.
AGGREGATOR_DOMAINS = [
    "tripadvisor.com", "yelp.com", "booking.com", "expedia.com", "viator.com",
    "getyourguide.com", "tourradar.com", "lonelyplanet.com", "trustpilot.com",
]


def is_aggregator_url(url: str) -> bool:
    host = _hostname(url)
    if not host:
        return False
    return any(host == d or host.endswith(f".{d}") for d in AGGREGATOR_DOMAINS)


def looks_like_social_profile(source: str, url: str) -> bool:
    host = _hostname(url)
    if not host:
        return True  # malformed - don't reject on this basis alone
    if is_instagram_url(url):
        segments = [s for s in _pathname(url).split("/") if s]
        return bool(segments) and segments[0].lower() not in INSTAGRAM_NON_PROFILE_SEGMENTS
    if is_facebook_url(url):
        return not FACEBOOK_NON_PAGE_PATH.search(_pathname(url))
    return True


def parse_signals(raw: Dict[str, Any], source: str) -> Dict[str, Any]:
    # NOTE: `??` in the original, not `||` - a rating of 0 is a valid parse result and
    # must not fall through to the title. Ported as an explicit None check.
    rating = parse_rating(raw.get("snippet"))
    if rating is None:
        rating = parse_rating(raw.get("title"))
    review_count = parse_review_count(raw.get("snippet"))
    positive = has_positive_signal(raw.get("snippet")) or has_positive_signal(raw.get("title"))
    name = guess_company_name(raw.get("title") or raw.get("url") or "")
    if source == "instagram" and is_generic_name(name):
        name = extract_username_from_profile_url(raw.get("url") or "") or name
    return {
        "id": str(uuid.uuid4()),
        "source": source,
        "name": name,
        "sourceUrl": raw.get("url"),
        "snippet": raw.get("snippet") or "",
        "rating": rating,
        "reviewCount": review_count,
        "hasPositiveSignal": positive,
        "isMock": bool(raw.get("isMock")),
    }


# ============================================================================
# 4. VETTING - keep only businesses with strong, verifiable review signals
# ============================================================================
def vet_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    min_rating = _min_rating()
    kept = []
    for c in candidates:
        rating = c.get("rating")
        # A confidently-parsed numeric rating is the strongest signal - enforce the bar.
        # bool is a subclass of int in Python, so it's excluded explicitly; JS's
        # `typeof x === 'number'` would never be true for a boolean.
        if isinstance(rating, (int, float)) and not isinstance(rating, bool):
            if rating >= min_rating:
                kept.append(c)
            continue
        # No numeric rating parsed. Real snippets often describe a business in prose
        # without spelling out a star rating, so treating "no parseable number" as
        # "reject" throws away most genuine results. Keep it unless the text has an
        # explicit red flag - or admit it anyway on an explicit positive signal.
        if c.get("hasPositiveSignal"):
            kept.append(c)
            continue
        if not has_negative_signal(c.get("snippet")) and not has_negative_signal(c.get("name")):
            kept.append(c)
    return kept


# ============================================================================
# 5. DEDUPE - merge multiple source-mentions of the same business
# ============================================================================
def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def dedupe_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_name: Dict[str, Dict[str, Any]] = {}
    for c in candidates:
        key = normalize_name(c.get("name") or "")
        url = c.get("sourceUrl") or ""
        if key not in by_name:
            # The original had a bug here where instagramUrl/facebookUrl/
            # websiteCandidate were only attached when a SECOND matching candidate
            # merged - meaning any candidate with no duplicate (the vast majority)
            # never got a usable contact field and could never pass the later "has a
            # contact method" check. Fixed there, preserved fixed here: the first
            # occurrence sets it too. Classified by the URL itself, not the query
            # source, since a social link can surface via a Google result.
            entry = dict(c)
            entry["sources"] = [{"source": c.get("source"), "url": url}]
            if is_instagram_url(url):
                entry["instagramUrl"] = url
            elif is_facebook_url(url):
                entry["facebookUrl"] = url
            elif is_aggregator_url(url):
                entry["aggregatorUrl"] = url  # a listing page - not the business's own site
            else:
                entry["websiteCandidate"] = url
            by_name[key] = entry
        else:
            existing = by_name[key]
            existing["sources"].append({"source": c.get("source"), "url": url})
            # Prefer the highest parsed rating and richest snippet across duplicates.
            rating = c.get("rating")
            if isinstance(rating, (int, float)) and not isinstance(rating, bool):
                if existing.get("rating") is None or rating > existing["rating"]:
                    existing["rating"] = rating
            if len(c.get("snippet") or "") > len(existing.get("snippet") or ""):
                existing["snippet"] = c.get("snippet")
            if is_instagram_url(url) and not existing.get("instagramUrl"):
                existing["instagramUrl"] = url
            if is_facebook_url(url) and not existing.get("facebookUrl"):
                existing["facebookUrl"] = url
            if is_aggregator_url(url) and not existing.get("aggregatorUrl"):
                existing["aggregatorUrl"] = url
            if (not is_instagram_url(url) and not is_facebook_url(url)
                    and not is_aggregator_url(url) and not existing.get("websiteCandidate")):
                existing["websiteCandidate"] = url
    return list(by_name.values())


# ============================================================================
# 6. ENRICHMENT - scrape a candidate's own website for direct contact info
# ============================================================================
EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
GENERIC_EMAIL_PREFIXES = ["noreply", "no-reply", "donotreply", "example", "test"]
CONTACT_TITLE_PATTERN = re.compile(
    r"(owner|founder|manager|director|ceo)[^a-zA-Z]{0,15}([A-Z][a-z]+\s[A-Z][a-z]+)")

# A homepage very rarely shows a direct email itself - it almost always lives one
# click away, on a "Contact us" page or (especially with any Germany/EU presence) an
# "Impressum" legal-notice page, which is legally required to list a contact email.
# Terms & Conditions is the second most common place.
CONTACT_LINK_PATTERN = re.compile(
    r"contact(\s|-|_)?us\b|\bcontact\b|\bkontakt\b|\bimpressum\b|\bimprint\b", re.I)
TERMS_LINK_PATTERN = re.compile(
    r"terms(\s|-|_)?(and|&)?(\s|-|_)?conditions|terms of (service|use)|\bterms\b|\bagb\b|\blegal\b", re.I)

_PREFERRED_EMAIL_PATTERN = re.compile(r"^(info|contact|bookings|reservations|hello)@", re.I)
_SCRAPE_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MomiraTravelBot/1.0)"}


def pick_best_email(emails: List[str]) -> Optional[str]:
    # dict.fromkeys, not set() - the original's [...new Set()] preserves insertion
    # order, and "first one found" is the documented fallback below.
    cleaned = [e for e in dict.fromkeys(emails)
               if not any(e.lower().startswith(p) for p in GENERIC_EMAIL_PREFIXES)]
    if not cleaned:
        return None
    preferred = next((e for e in cleaned if _PREFERRED_EMAIL_PATTERN.search(e)), None)
    return preferred or cleaned[0]


def _fetch_and_parse(url: str) -> BeautifulSoup:
    res = requests.get(url, timeout=REQUEST_TIMEOUT_S, headers=_SCRAPE_HEADERS)
    res.raise_for_status()
    return BeautifulSoup(res.text, "html.parser")


def extract_email_and_instagram_from_page(soup: BeautifulSoup) -> Dict[str, Any]:
    body = soup.body or soup
    body_text = body.get_text(" ", strip=True)
    mailto_emails = []
    for a in soup.select('a[href^="mailto:"]'):
        href = a.get("href") or ""
        mailto_emails.append(href.replace("mailto:", "").split("?")[0])
    text_emails = EMAIL_PATTERN.findall(body_text)
    email = pick_best_email(mailto_emails + text_emails)
    instagram = None
    for a in soup.select('a[href*="instagram.com"]'):
        if not instagram:
            instagram = a.get("href")
    return {"email": email, "instagram": instagram, "bodyText": body_text}


def find_contact_and_terms_links(soup: BeautifulSoup, base_url: str) -> List[str]:
    """Scans a homepage's own links for the best "Contact us" and "Terms/Impressum"
    links, resolved absolute and restricted to the same site (never wanders off).
    Returned in priority order - Contact first - matching how likely each is to list
    a direct email."""
    contact_link = None
    terms_link = None
    base_host = _hostname(base_url)
    if not base_host:
        return []
    for a in soup.select("a[href]"):
        if contact_link and terms_link:
            break
        href = a.get("href") or ""
        if not href or href.startswith("mailto:") or href.startswith("tel:") or href.startswith("#"):
            continue
        text = a.get_text(" ", strip=True) or ""
        hay = f"{href} {text}".lower()
        try:
            resolved = urljoin(base_url, href)
        except ValueError:
            continue
        if _hostname(resolved) != base_host:
            continue  # stay on the supplier's own site
        # if/elif exactly as the original: a link matching Contact when contact is
        # already set is NOT then tested against Terms.
        if not contact_link and CONTACT_LINK_PATTERN.search(hay):
            contact_link = resolved
        elif not terms_link and TERMS_LINK_PATTERN.search(hay):
            terms_link = resolved
    return [link for link in (contact_link, terms_link) if link]


# Looks for the supplier's own outbound "official website" link on a third-party
# listing page. Tripadvisor commonly links out this way; OTAs do it less often since
# they want travelers booking through them - but when they do expose it, this is how
# Momira gets the operator's REAL site instead of just the listing.
WEBSITE_LINK_TEXT_PATTERN = re.compile(
    r"official\s*website|visit\s*website|company\s*website|our\s*website|\bwebsite\b", re.I)


def find_outbound_website_link(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    base_host = _hostname(base_url)
    if not base_host:
        return None
    for a in soup.select("a[href]"):
        href = a.get("href") or ""
        if not href or href.startswith("mailto:") or href.startswith("tel:") or href.startswith("#"):
            continue
        text = a.get_text(" ", strip=True) or ""
        hay = f"{text} {a.get('aria-label') or ''}".lower()
        if not WEBSITE_LINK_TEXT_PATTERN.search(hay):
            continue
        try:
            resolved = urljoin(base_url, href)
        except ValueError:
            continue
        host = _hostname(resolved)
        if not host or host == base_host:
            continue  # internal link (the OTA's own pages), not an outbound business site
        if any(host == d or host.endswith(f".{d}") for d in AGGREGATOR_DOMAINS):
            continue  # links to another aggregator, not the business itself
        return resolved
    return None


def scrape_website_contact(url: str) -> Dict[str, Any]:
    try:
        soup = _fetch_and_parse(url)
        page = extract_email_and_instagram_from_page(soup)
        email, instagram, body_text = page["email"], page["instagram"], page["bodyText"]

        contact_match = CONTACT_TITLE_PATTERN.search(body_text)
        contact_name = contact_match.group(2) if contact_match else None

        # No email on the page itself - most real suppliers keep it one click away.
        # Follow the site's own Contact and Terms/Impressum links (in that priority
        # order). Only ONE level deep - this never crawls the whole site.
        if not email:
            subpage_links = [l for l in find_contact_and_terms_links(soup, url) if l != url]
            for link in subpage_links:
                try:
                    sub_soup = _fetch_and_parse(link)
                    sub = extract_email_and_instagram_from_page(sub_soup)
                except Exception:
                    continue
                if sub["email"]:
                    email = sub["email"]
                    if not instagram:
                        instagram = sub["instagram"]
                    print(f"[outreach_discovery] found email on a linked subpage (not the homepage): {link}")
                    break  # Contact was checked before Terms/Impressum - first hit wins
        return {"email": email, "instagram": instagram, "contactName": contact_name}
    except Exception:
        return {"email": None, "instagram": None, "contactName": None}


def scrape_aggregator_for_website_and_contact(url: str) -> Dict[str, Any]:
    """For a third-party listing page: first try to find and follow an outbound link to
    the supplier's own site (the most valuable outcome), then fall back to whatever
    contact info the listing page itself exposes."""
    try:
        soup = _fetch_and_parse(url)
        outbound = find_outbound_website_link(soup, url)
        if outbound:
            site_result = scrape_website_contact(outbound)
            if site_result["email"] or site_result["contactName"]:
                return {"website": outbound, **site_result}
            # Found their real site but no email on it - still worth surfacing the
            # website itself, plus whatever the listing page's own text has.
            listing_fallback = extract_email_and_instagram_from_page(soup)
            return {
                "website": outbound,
                "email": listing_fallback["email"],
                "instagram": site_result["instagram"] or listing_fallback["instagram"],
                "contactName": site_result["contactName"],
            }
        page = extract_email_and_instagram_from_page(soup)
        return {"website": None, "email": page["email"], "instagram": page["instagram"], "contactName": None}
    except Exception:
        return {"website": None, "email": None, "instagram": None, "contactName": None}


def scrape_instagram_bio_email(instagram_url: str) -> Optional[str]:
    """Best-effort only: Instagram serves almost all profile content behind a login
    wall to non-browser requests, so this frequently comes back empty even for a real
    business with a real email in their bio - expected, not an error. When it DOES
    work (some public profiles still expose bio text via the meta description), it's
    checked ahead of the website, since a bio is often more current than a contact page."""
    try:
        res = requests.get(instagram_url, timeout=REQUEST_TIMEOUT_S, headers=_SCRAPE_HEADERS)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        meta = (soup.find("meta", attrs={"name": "description"})
                or soup.find("meta", attrs={"property": "og:description"}))
        content = (meta.get("content") if meta else "") or ""
        return pick_best_email(EMAIL_PATTERN.findall(content))
    except Exception:
        return None


def extract_email_from_snippet(snippet: Optional[str]) -> Optional[str]:
    """Cheap, no-network fallback: Tavily's advanced search often returns a long
    extracted chunk of page text as the snippet, which sometimes already contains a
    contact email even when the site itself can't be scraped."""
    return pick_best_email(EMAIL_PATTERN.findall(snippet or ""))


def enrich_from_website(candidate: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(candidate)

    # Mock data already embeds a fabricated-but-well-formed email/website pair.
    if candidate.get("isMock"):
        emails = EMAIL_PATTERN.findall(candidate.get("snippet") or "")
        result["website"] = (candidate.get("websiteCandidate")
                             or f"https://www.{normalize_name(candidate.get('name') or '').replace(' ', '')}.com")
        result["email"] = emails[0] if emails else None
        result["contactName"] = "Reservations Team"
        result["instagramUrl"] = candidate.get("instagramUrl")
        return result

    # Priority 0: Instagram bio - see scrape_instagram_bio_email for why it's often
    # empty and that being expected.
    instagram_bio_email = (scrape_instagram_bio_email(candidate["instagramUrl"])
                           if candidate.get("instagramUrl") else None)

    # Priority 1: the business's own website - the most reliable place to find a
    # direct email. Only ever set from a non-aggregator, non-social URL (see
    # dedupe_candidates). Checks the homepage, then Contact/Impressum and Terms
    # subpages one click deep.
    if candidate.get("websiteCandidate"):
        site = scrape_website_contact(candidate["websiteCandidate"])
        result["website"] = candidate["websiteCandidate"]
        result["email"] = (instagram_bio_email or site["email"]
                           or extract_email_from_snippet(candidate.get("snippet")))
        result["contactName"] = site["contactName"]
        result["instagramUrl"] = candidate.get("instagramUrl") or site["instagram"]
        return result

    # Priority 2: no independent website, but we do have a listing page. First tries
    # to follow an outbound link to the supplier's OWN site; falls back to whatever
    # the listing page's own text exposes.
    if candidate.get("aggregatorUrl"):
        agg = scrape_aggregator_for_website_and_contact(candidate["aggregatorUrl"])
        result["website"] = agg["website"]
        result["email"] = (instagram_bio_email or agg["email"]
                           or extract_email_from_snippet(candidate.get("snippet")))
        result["contactName"] = agg["contactName"]
        result["instagramUrl"] = candidate.get("instagramUrl") or agg["instagram"]
        return result

    # Priority 3: nothing to scrape - last resort is the Instagram bio plus whatever
    # the snippet already contained. A Facebook Page stays in its own field rather
    # than being folded into "website"; it can't reliably be scraped behind a login wall.
    result["website"] = None
    result["email"] = instagram_bio_email or extract_email_from_snippet(candidate.get("snippet"))
    result["contactName"] = None
    return result


# ============================================================================
# 7. NORMALIZATION - shape returned to the review table
# ============================================================================
SOURCE_LABELS = {
    "tripadvisor": "Tripadvisor", "google": "Google", "website": "Official website",
    "instagram": "Instagram", "facebook": "Facebook", "trustpilot": "Trustpilot",
    "viator": "Viator", "getyourguide": "GetYourGuide",
}


def summarize_sources(sources: Optional[List[Dict[str, Any]]]) -> str:
    labels = [SOURCE_LABELS.get(s.get("source"), s.get("source")) for s in (sources or [])]
    return " + ".join(dict.fromkeys(labels))  # dict.fromkeys keeps insertion order


def guess_aggregator_label(url: Optional[str]) -> Optional[str]:
    """Human-readable label for a listing URL, so the review table can show
    "Trustpilot ↗" / "Viator ↗" rather than a hardcoded "Tripadvisor"."""
    if not url:
        return None
    host = _hostname(url)
    if not host:
        return "Listing"
    for needle, label in (("tripadvisor", "Tripadvisor"), ("trustpilot", "Trustpilot"),
                          ("viator", "Viator"), ("getyourguide", "GetYourGuide"),
                          ("yelp", "Yelp"), ("booking", "Booking.com"),
                          ("expedia", "Expedia"), ("tourradar", "TourRadar"),
                          ("lonelyplanet", "Lonely Planet")):
        if needle in host:
            return label
    return "Listing"


def build_selection_reason(candidate: Dict[str, Any]) -> str:
    """A human-readable one-liner explaining WHY this supplier made the list - what the
    operator actually needs to trust (or double-check) a match at a glance."""
    source_label = (candidate.get("aiReviewSource")
                    or summarize_sources(candidate.get("sources")) or "web search")
    rating = candidate.get("rating")
    if isinstance(rating, (int, float)) and not isinstance(rating, bool):
        count_part = f" ({candidate['reviewCount']} reviews)" if candidate.get("reviewCount") else ""
        rating_part = f"Rated {rating:.1f}/5{count_part} via {source_label}."
    elif candidate.get("hasPositiveSignal"):
        rating_part = f"Strong positive mentions found via {source_label} (no exact star rating listed)."
    else:
        rating_part = f"Found via {source_label} - no star rating listed, but no negative signals found either."
    teaser = candidate.get("aiReviewTeaser")
    return f"{rating_part} {teaser}" if teaser else rating_part


def to_supplier_record(candidate: Dict[str, Any], country: str, keyword: str) -> Dict[str, Any]:
    # One social link is enough - Instagram is more useful than Facebook when a
    # supplier has both (Facebook isn't searched directly, it only surfaces incidentally).
    social = candidate.get("instagramUrl") or candidate.get("facebookUrl") or None
    social_platform = ("Instagram" if candidate.get("instagramUrl")
                       else "Facebook" if candidate.get("facebookUrl") else None)
    snippet = candidate.get("snippet")
    return {
        "id": candidate.get("id"),
        "name": candidate.get("name"),
        "email": candidate.get("email") or None,
        "social": social,
        "socialPlatform": social_platform,
        "website": candidate.get("website") or None,
        # A listing page isn't the business's own site, but it's still a legitimate,
        # actionable reference when nothing else was found - a real supplier name plus
        # a real review page beats being dropped entirely.
        "listingUrl": candidate.get("aggregatorUrl") or None,
        "listingSource": guess_aggregator_label(candidate.get("aggregatorUrl")),
        "selectionReason": build_selection_reason(candidate),
        "reviewSummary": snippet[:240] if snippet else f"Well-reviewed {keyword} supplier in {country}.",
        "rating": candidate.get("rating"),
        "reviewCount": candidate.get("reviewCount"),
        "sources": candidate.get("sources") or [],
        # Ready-to-mail suppliers (a real email was found) are pre-selected so the
        # operator sees them as good to go; suppliers with no email start UNSELECTED,
        # since they aren't actually ready to send to yet.
        "selected": bool(candidate.get("email")),
        "isMock": bool(candidate.get("isMock")),
    }


# ============================================================================
# FINAL DEDUPE - collapse rows that share a contact channel
# ============================================================================
# dedupe_candidates() only merges by matching NAME - two results for the same real
# business can still end up as separate rows if their names came out differently.
# This second pass catches what name-matching can't: identical email and/or identical
# social profile link means the same business.
def normalize_email_key(email: Optional[str]) -> Optional[str]:
    return email.strip().lower() if email else None


def normalize_url_key(url: Optional[str]) -> Optional[str]:
    """Strips protocol/www/trailing-slash/query differences so
    "https://www.instagram.com/foo/" and "instagram.com/foo?utm=x" match."""
    if not url:
        return None
    host = _hostname(url)
    if not host:
        return url.strip().lower()
    path = re.sub(r"/+$", "", _pathname(url)).lower()
    return f"{re.sub(r'^www.', '', host)}{path}"


def merge_supplier_records(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Combines two rows known to be the same business - keeps whichever field is
    populated when only one side has it, and keeps the higher-rated side's
    rating/reason/review count when both have one."""
    a_rating = a.get("rating") if a.get("rating") is not None else -1
    b_rating = b.get("rating") if b.get("rating") is not None else -1
    b_is_better = b_rating > a_rating
    primary, secondary = (b, a) if b_is_better else (a, b)
    merged_email = primary.get("email") or secondary.get("email")
    merged = dict(primary)
    merged.update({
        "email": merged_email,
        "social": primary.get("social") or secondary.get("social"),
        "socialPlatform": primary.get("socialPlatform") or secondary.get("socialPlatform"),
        "website": primary.get("website") or secondary.get("website"),
        "listingUrl": primary.get("listingUrl") or secondary.get("listingUrl"),
        "listingSource": primary.get("listingSource") if primary.get("listingUrl") else secondary.get("listingSource"),
        "reviewSummary": primary.get("reviewSummary") or secondary.get("reviewSummary"),
        "sources": (primary.get("sources") or []) + (secondary.get("sources") or []),
        # Recomputed rather than inherited - a merge can combine a no-email row with an
        # email-having one, and the checkbox default must reflect the FINAL merged email.
        "selected": bool(merged_email),
    })
    return merged


def dedupe_suppliers_by_contact(suppliers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen_by_email: Dict[str, int] = {}
    seen_by_social: Dict[str, int] = {}
    result: List[Dict[str, Any]] = []

    for s in suppliers:
        email_key = normalize_email_key(s.get("email"))
        social_key = normalize_url_key(s.get("social"))

        existing_idx = None
        if email_key and email_key in seen_by_email:
            existing_idx = seen_by_email[email_key]
        elif social_key and social_key in seen_by_social:
            existing_idx = seen_by_social[social_key]

        if existing_idx is not None:
            result[existing_idx] = merge_supplier_records(result[existing_idx], s)
            # Point both keys at the merged row, in case this duplicate had a contact
            # channel the earlier row was missing.
            if email_key:
                seen_by_email[email_key] = existing_idx
            if social_key:
                seen_by_social[social_key] = existing_idx
            continue

        idx = len(result)
        result.append(s)
        if email_key:
            seen_by_email[email_key] = idx
        if social_key:
            seen_by_social[social_key] = idx

    return result


# ============================================================================
# AI VERIFICATION - optional Claude pass (port of aiVerificationService.js)
# ============================================================================
VERIFY_TOOL = {
    "name": "report_verification",
    "description": "Report a verification verdict for every candidate supplier provided.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "The candidate id exactly as given"},
                        "isDirectSupplier": {
                            "type": "boolean",
                            "description": ("true if this is a real local travel agent, travel agency, tour guide, "
                                            "local DMC, or local expert offering travel/sightseeing/adventures/tours/"
                                            "transfers/excursions/round trips/seat-in-coach/accommodation - false if it "
                                            "is an article, forum post, listicle, unrelated business, or generic "
                                            "booking marketplace"),
                        },
                        "isInCountry": {
                            "type": "boolean",
                            "description": "true if the business is physically located and operating within the searched country/region",
                        },
                        "cleanCompanyName": {
                            "type": "string",
                            "description": ("The correct, clean official business name - fix truncated/mangled titles "
                                            "if needed, otherwise repeat the given name unchanged"),
                        },
                        "reviewTeaser": {
                            "type": "string",
                            "description": ("1-2 sentence summary of customer feedback highlighting service quality, "
                                            "based only on the provided snippet - empty string if the snippet has "
                                            "nothing usable"),
                        },
                        "reviewSource": {
                            "type": "string",
                            "description": ('Which platform the review/snippet content is from (e.g. "Google", '
                                            '"Tripadvisor", "Instagram") - use the given source if unsure'),
                        },
                    },
                    "required": ["id", "isDirectSupplier", "isInCountry", "cleanCompanyName"],
                },
            },
        },
        "required": ["verdicts"],
    },
}


def is_ai_verification_enabled() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _build_verification_prompt(candidates: List[Dict[str, Any]], country: str, keyword: str) -> str:
    listing = "\n\n".join(
        f"{i + 1}. id=\"{c.get('id')}\"\n   name: {c.get('name')}\n   source: {c.get('source')}\n"
        f"   url: {c.get('sourceUrl')}\n   snippet: {(c.get('snippet') or '')[:300]}"
        for i, c in enumerate(candidates)
    )
    return (
        f'You are an expert travel industry procurement assistant. A search for local travel suppliers in '
        f'"{country}" offering "{keyword}" (or related tours, sightseeing, adventures, transfers, excursions, '
        f'round trips, seat-in-coach (SIC), or hotel accommodation) returned the raw candidates below, pulled '
        f'from search snippets. Some may be forum posts, articles, unrelated businesses, or based outside '
        f'{country} - your job is to judge each one.\n\n'
        f'For EVERY candidate, report:\n'
        f'- isDirectSupplier: is this really a local travel agent, travel agency, tour guide, local DMC, or local '
        f'expert offering the services above - not an article/forum post/listicle/unrelated business/generic '
        f'booking marketplace?\n'
        f'- isInCountry: is it physically located and operating within {country}?\n'
        f'- cleanCompanyName: the correct business name (fix it if the given name looks mangled or truncated, '
        f'e.g. a stray word or a generic page-title fragment)\n'
        f'- reviewTeaser + reviewSource: a short 1-2 sentence summary of any customer feedback in the snippet, '
        f'and which platform it\'s from\n\n'
        f'Candidates:\n\n{listing}\n\n'
        f'Call report_verification with a verdict for every single candidate listed (same count, same ids).'
    )


def verify_candidates(candidates: List[Dict[str, Any]], country: str, keyword: str) -> Optional[Dict[str, Any]]:
    """Returns {candidate_id: verdict} on success, or None if AI verification is
    disabled/unavailable/failed - callers treat None as "skip this step, rely on the
    rule-based filters that already ran"."""
    if not is_ai_verification_enabled() or not candidates:
        return None
    try:
        from anthropic import Anthropic
    except ImportError:
        print("[outreach_discovery] anthropic package not installed - skipping AI verification.")
        return None

    try:
        client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        response = client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL") or "claude-haiku-4-5",
            # Was 4096 in an earlier version of the original: real evidence showed the
            # call intermittently returning an unexpected shape on larger batches - at
            # ~30 candidates the verdicts array can run past 4096 output tokens, so the
            # tool_use call gets cut off mid-JSON. Doubled to give real headroom.
            max_tokens=8192,
            tools=[VERIFY_TOOL],
            tool_choice={"type": "tool", "name": "report_verification"},
            messages=[{"role": "user", "content": _build_verification_prompt(candidates, country, keyword)}],
        )
        tool_use = next((b for b in response.content if getattr(b, "type", None) == "tool_use"), None)
        if not tool_use or not isinstance(getattr(tool_use, "input", None), dict) \
                or not isinstance(tool_use.input.get("verdicts"), list):
            block_types = ", ".join(getattr(b, "type", "?") for b in response.content) or "(no content blocks)"
            print(f"[outreach_discovery] Unexpected AI verification response - skipping. "
                  f"stop_reason=\"{response.stop_reason}\", content block types=[{block_types}], "
                  f"candidates sent={len(candidates)}")
            return None
        if response.stop_reason == "max_tokens":
            print(f"[outreach_discovery] AI verification hit max_tokens ({len(candidates)} candidates sent) - "
                  f"verdicts may be truncated. Consider lowering MAX_SUPPLIER_CANDIDATES if this recurs.")
        return {v.get("id"): v for v in tool_use.input["verdicts"] if isinstance(v, dict)}
    except Exception as e:
        print(f"[outreach_discovery] AI verification call failed - falling back to rule-based filtering only: {e}")
        return None


# ============================================================================
# PUBLIC ENTRY POINT
# ============================================================================
def discover_suppliers(country: str, keyword: str, progress=None) -> Dict[str, Any]:
    """
    Runs the whole discovery pipeline and returns:
      {"suppliers": [...], "stats": {...}, "drop_log": [...]}

    The original returned only the supplier array and wrote its diagnostics to the
    server console. Inside the platform there IS no server console for the operator
    to read, so the same information is returned as structured data - the wizard shows
    it in an expandable panel. That distinction (a known brand never showing up at all
    = a search-recall problem, vs showing up and being dropped = an over-aggressive
    filter) is exactly what the original's logging existed to preserve.

    `progress` is an optional callable(str) for live status updates in the UI.
    """
    def report(msg):
        if progress:
            progress(msg)

    queries = build_queries(country, keyword)
    candidates: List[Dict[str, Any]] = []

    for q in queries:
        report(f"Searching {q['source']}…")
        results = run_provider_search(q["source"], q["query"], country, keyword,
                                      q["domains"], q["max_results"])
        for raw in results:
            candidates.append(parse_signals(raw, q["source"]))

    raw_count = len(candidates)
    report(f"{raw_count} raw result(s) found. Filtering…")

    relevance_tokens = build_relevance_tokens(country, keyword)
    drop_log: List[Dict[str, Any]] = []
    kept: List[Dict[str, Any]] = []

    for c in candidates:
        def reject(reason):
            drop_log.append({"name": c.get("name"), "url": c.get("sourceUrl"), "reason": reason, "stage": "pre-filter"})
            return False

        url = c.get("sourceUrl") or ""
        if is_generic_name(c.get("name")):
            # Before giving up entirely, see if it's sitting on its own real business
            # website - if so, rescue it with a domain-derived name instead of dropping.
            is_own_website = not is_aggregator_url(url) and not is_instagram_url(url) and not is_facebook_url(url)
            rescued = derive_name_from_url(url) if is_own_website else None
            if rescued and not is_generic_name(rescued):
                c["name"] = rescued
                c["nameRescuedFromDomain"] = True
            else:
                reject("generic/boilerplate name, no usable domain to rescue it with")
                continue
        if is_ota_or_marketplace(c.get("name")):
            reject("OTA/marketplace platform, not a direct supplier")
            continue
        if is_question_or_listicle_title(c.get("name")):
            reject("forum question / listicle round-up title, not one business")
            continue
        if is_editorial_content(c.get("name"), c.get("snippet")):
            reject("editorial/magazine content about the niche, not a supplier")
            continue
        if not is_tripadvisor_listing_url(url):
            reject("Tripadvisor forum/category page, not one business's own listing")
            continue
        if (is_instagram_url(url) or is_facebook_url(url)) and not looks_like_social_profile(c.get("source"), url):
            reject("social link to a post/group/discussion, not the business's own profile/page")
            continue
        if not is_relevant_candidate(c, relevance_tokens):
            reject("does not mention the searched country/keyword anywhere")
            continue
        kept.append(c)

    prefiltered_count = len(kept)

    vetted = vet_candidates(kept)
    vetted_ids = {c["id"] for c in vetted}
    for c in kept:
        if c["id"] not in vetted_ids:
            drop_log.append({
                "name": c.get("name"), "url": c.get("sourceUrl"), "stage": "vetting",
                "reason": (f"rating={c.get('rating')} below MIN_SUPPLIER_RATING={_min_rating()} with no positive "
                           f"signal, or an explicit negative signal in the text"),
            })
    vetted_count = len(vetted)
    report(f"{vetted_count} candidate(s) passed vetting. Checking for duplicates…")

    deduped = dedupe_candidates(vetted)[:_max_candidates()]

    ai_dropped = 0
    if is_ai_verification_enabled():
        report("Verifying candidates with AI…")
        verdicts = verify_candidates(deduped, country, keyword)
        if verdicts:
            before = len(deduped)
            surviving = []
            for c in deduped:
                v = verdicts.get(c["id"])
                if not v:
                    surviving.append(c)  # no verdict came back - don't punish it
                    continue
                if v.get("isDirectSupplier") is False or v.get("isInCountry") is False:
                    drop_log.append({"name": c.get("name"), "url": c.get("sourceUrl"), "stage": "ai-verification",
                                     "reason": "AI judged this not a direct supplier operating in the searched country"})
                    continue
                clean_name = (v.get("cleanCompanyName") or "").strip()
                if clean_name:
                    c["name"] = clean_name
                c["aiReviewTeaser"] = v.get("reviewTeaser") or None
                c["aiReviewSource"] = v.get("reviewSource") or None
                surviving.append(c)
            deduped = surviving
            ai_dropped = before - len(deduped)

    report(f"Looking up contact details for {len(deduped)} candidate(s)…")
    enriched = []
    for i, c in enumerate(deduped, 1):
        report(f"Looking up contact details ({i}/{len(deduped)}): {c.get('name')}")
        enriched.append(enrich_from_website(c))

    suppliers = [to_supplier_record(c, country, keyword) for c in enriched]
    # Suppliers with absolutely no way to reach them are dropped, but a listing
    # reference counts - a real supplier name plus a real review page is still useful
    # even with no scraped email, and dropping these quietly starved results down to
    # almost nothing but Instagram.
    before_contact_filter = len(suppliers)
    suppliers = [s for s in suppliers if s["email"] or s["website"] or s["social"] or s["listingUrl"]]
    no_contact_dropped = before_contact_filter - len(suppliers)

    suppliers = dedupe_suppliers_by_contact(suppliers)

    # Direct email is what the outreach campaign runs on, so suppliers with one are
    # surfaced first. Within that, own website ranks above social-only - own site is
    # the priority channel. Only then does rating decide; a missing rating isn't a red
    # flag on its own, it just means there's no number to sort by.
    suppliers.sort(key=lambda s: (
        0 if s.get("email") else 1,
        0 if s.get("website") else 1,
        -(s["rating"] if s.get("rating") is not None else -1),
    ))

    suppliers = suppliers[:_max_results()]
    report(f"Done — {len(suppliers)} supplier(s) ready for review.")

    return {
        "suppliers": suppliers,
        "drop_log": drop_log,
        "stats": {
            "raw": raw_count,
            "after_prefilter": prefiltered_count,
            "after_vetting": vetted_count,
            "after_dedupe": len(deduped) + ai_dropped,
            "ai_dropped": ai_dropped,
            "no_contact_dropped": no_contact_dropped,
            "final": len(suppliers),
            "used_mock_provider": not (os.getenv("TAVILY_API_KEY") or os.getenv("SERPAPI_API_KEY")),
        },
    }
