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

# Stamped on every delivery. app.py compares this against its own build string and says
# so on screen when they differ - a partial push (one file committed, another not) used to
# surface only as a traceback whose line numbers pointed at unrelated code.
MODULE_BUILD = "2026-09-03-voucher-remarks-no-raw-supplier-cancellation-text"

import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# The persistent blocklist lives in outreach_memory - see that module's docstring for why
# it is the single owner. These names are re-exported so existing callers keep working.
from outreach_memory import (extract_domain as _extract_domain, get_blocklist,
                             add_domain_to_blocklist, remove_domain_from_blocklist,
                             is_blocked)
# Suppliers a human added by hand in an earlier session, remembered per (country, theme) - see
# that module's own docstring for the two things this is used for below: resurfacing them
# straight into this search's results, and calibrating AI verification's judgment on NEW
# candidates for the same country/theme.
from outreach_learned_suppliers import resurface_remembered_suppliers

# CONFIRMED PRODUCT-OWNER REQUEST (2026-08-25): "the tool needs more time to find the correct
# email address" - raised again (was 8, then 15) after a real report of bad/empty results that
# traced partly to requests timing out before a genuine email was found. A small local tour
# operator's own site is often slow, and scrape_website_contact() can chain up to three fetches
# per candidate (homepage, then a Contact or Impressum/Terms subpage) - each one hitting this
# same ceiling.
REQUEST_TIMEOUT_S = 25

# Tavily/SerpAPI search calls get their OWN, separate (and longer) timeout - "advanced" search
# depth genuinely trades speed for quality even without contention, and per product-owner
# hypothesis (2026-08-25, after a Morocco run came back "0 raw results" across 40 combinations):
# "I think it is too short time for the AI to search for one combination." Kept apart from
# REQUEST_TIMEOUT_S above (tuned for scraping) rather than sharing one constant, so tuning one
# doesn't quietly change the other. See _select_and_run_provider's own docstring for the retry
# that goes with this.
SEARCH_REQUEST_TIMEOUT_S = 30


# ============================================================================
# CONFIG - read at call time (not import time) so Streamlit's secrets loading,
# which populates os.environ before the tool runs, is always picked up.
# ============================================================================
def _min_rating() -> float:
    try:
        return float(os.getenv("MIN_SUPPLIER_RATING") or "4.0")
    except ValueError:
        return 4.0


# CONFIRMED PRODUCT-OWNER DECISION (2026-08-28, full-app audit): the hard MIN_SUPPLIER_RATING
# cutoff paired with no review-count floor at all let a 5.0-star rating from a single review
# through identically to 4.8 from 500 - while auto-rejecting a well-established supplier sitting
# at, say, 3.8 stars across 500 reviews outright, with no override path. A large review count is
# itself a strong signal a business is real and established even when its rating dips slightly
# below the bar - so a candidate whose rating clears this LOWER floor AND whose review count
# clears the volume floor is let through to review despite missing MIN_SUPPLIER_RATING. Genuinely
# poor ratings (below this floor) are never rescued by volume alone - a business with thousands
# of 2-star reviews is still a bad supplier.
def _review_count_exception_rating_floor() -> float:
    try:
        return float(os.getenv("MIN_SUPPLIER_RATING_WITH_VOLUME") or "3.5")
    except ValueError:
        return 3.5


def _review_count_exception_volume_floor() -> int:
    try:
        return int(os.getenv("MIN_SUPPLIER_REVIEW_COUNT_FOR_EXCEPTION") or "100")
    except ValueError:
        return 100


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


def _enrichment_concurrency() -> int:
    """CONFIRMED PRODUCT-OWNER REQUEST (2026-08-25): "make the mail outreach faster... for the
    searching." Originally paired with an equivalent _search_concurrency() knob for the
    provider-query fan-out in discover_suppliers() - that one was REVERTED the same day after
    bursting concurrent connections at Tavily's single search endpoint triggered non-standard
    "432" errors (see discover_suppliers' own comment for the incident). This knob, for the
    per-candidate website/Instagram scraping pass in enrich_from_website, was NOT reverted: it
    hits N distinct supplier-owned domains once each rather than repeatedly bursting one shared
    API endpoint, so it doesn't share that failure mode. Read at call time (not import time),
    same pattern as _min_rating/_max_results, so an env var set after import (Streamlit secrets)
    is picked up; kept deliberately modest by default so a search doesn't hammer a supplier's
    own small website all at once."""
    try:
        return max(1, int(os.getenv("ENRICHMENT_CONCURRENCY") or "6"))
    except ValueError:
        return 6


# NOTE (2026-08-25): a _search_call_delay_s() pacing knob briefly lived here (20s between each
# sequential search call, to go easy on the provider's usage quota) and was removed the same
# day - see discover_suppliers' own comment at its query fan-out for why: it made a real
# Country Scope run look completely frozen for minutes at a time with zero visible progress,
# and pacing was never going to fix a genuinely exhausted PLAN-LEVEL quota anyway (only a
# rate-style limit that resets over a short window). If a real rate limit needs this again,
# reintroduce it WITH incremental UI feedback per query (not just per combination) so it
# reads as "working, slowly" rather than "hung."


# ============================================================================
# 1. QUERY BUILDING - one query per target source
# ============================================================================
def build_queries(country: str, city: str, keyword: str) -> List[Dict[str, Any]]:
    """
    Builds targeted search queries for:
      - City-specific: local DMC + travel agency + private tour guide, ONE combined call
      - Country-wide: local DMC + travel agency + private tour guide, ONE combined call
      - Review sites (Tripadvisor/Viator/GetYourGuide), ONE combined call via include_domains
      - A fallback Instagram query (kept separate - see below)

    CONFIRMED PRODUCT-OWNER REQUEST (2026-08-26, forwarding advice from another AI tool that
    matches this tool's own real bottleneck): "Extract maximum number of URLs from ONE request...
    don't do search -> search -> search -> search." Before this, EVERY combination fired 10
    separate provider calls (dmc_city, agency_city, guide_city, dmc_country, agency_country,
    guide_country, tripadvisor, viator, getyourguide, instagram) - each its own Tavily/SerpAPI/
    Gemini request. On a Country Scope run of N combinations that's 10N calls, which is exactly
    why quota exhaustion (see incident-2026-08-25-outreach-concurrency-432-errors.md) and a
    single slow/timing-out call (see the 2026-08-26 "120 search calls failed" report) both bite
    N times harder than they need to. Consolidated to 4 calls per combination (3 with no city
    given) - each provider already supports asking for MORE results in one call
    (max_results/num), and multiple review-site domains in one call
    (Tavily's own include_domains, SerpAPI's "site:a OR site:b", Gemini's domain-restriction
    hint - see _search_with_tavily/_search_with_serpapi/_search_with_gemini_grounding).

    TRADE-OFF, ACCEPTED (confirmed product owner, 2026-08-26): the city/country supplier calls
    used to be tagged by exact type (dmc_city/agency_city/guide_city), shown on the review screen
    via SOURCE_LABELS as e.g. "DMC (City)" - one search can't tell you which of the three phrases
    actually matched, so the combined call is tagged just "supplier_city"/"supplier_country"
    ("Local Supplier (City)"/"Local Supplier (Country)"). The review-site merge has NO such loss:
    guess_aggregator_label() already derives the Tripadvisor/Viator/GetYourGuide badge shown in
    the table from the result's own URL, not from this source tag - only the mock-data provider's
    URL shape (tripadvisor-only, real API keys never hit this) loses its per-site flavor.

    Instagram stays its OWN call, NOT folded into the review-site call: is_generic_name() special-
    cases `source == "instagram"` results specifically (a generic-sounding name found via
    Instagram is treated differently than the same name found elsewhere) - merging it would
    silently break that check for every result in the merged call, not just Instagram's own.
    """
    country_base = f"{keyword} {country}".strip()
    queries = []

    # ---- 1. CITY-SPECIFIC (if city is provided) - ONE combined call ----
    if city and city.strip():
        city_base = f"{keyword} {city} {country}".strip()
        queries.append({
            "source": "supplier_city",
            "query": f"{city_base} local DMC, travel agency, tour operator, or private tour guide",
            "domains": [], "max_results": 15,
        })

    # ---- 2. COUNTRY-WIDE - ONE combined call ----
    queries.append({
        "source": "supplier_country",
        "query": f"{country_base} local DMC, travel agency, tour operator, or private tour guide",
        "domains": [], "max_results": 12,
    })

    # ---- 3. REVIEW SITES - ONE combined call across all three domains ----
    # CONFIRMED REAL GAP (audit, 2026-08-28): this is specifically the query most likely to
    # surface a parseable star rating (see RATING_PATTERNS/vet_candidates below) - the one hard
    # signal a candidate needs to clear MIN_SUPPLIER_RATING. Capped at 4 while being asked to
    # cover THREE domains at once starved the rating check of its best evidence right after the
    # 2026-08-26 consolidation folded three separate review-site calls into this one. Raised to
    # 12 - roughly what tripadvisor/viator/getyourguide used to return SEPARATELY before that
    # merge, now split across one call instead of three.
    queries.append({
        "source": "reviews",
        "query": f"{country_base} reviews",
        "domains": ["tripadvisor.com", "viator.com", "getyourguide.com"],
        "max_results": 12,
    })

    # ---- 4. FALLBACK SOCIAL - kept separate, see docstring ----
    queries.append({"source": "instagram", "query": f"{country_base}", "domains": ["instagram.com"], "max_results": 2})

    return queries


# ============================================================================
# 2. PLUGGABLE SEARCH PROVIDERS
# ============================================================================
def _raise_for_status_with_body(res: "requests.Response") -> None:
    """Like Response.raise_for_status(), but the raised error's message carries the response
    BODY, not just the bare status line.

    CONFIRMED REAL GAP (product owner, 2026-08-25, recurring after the concurrency revert):
    "60 search call(s) failed... Sample error: `dmc_city: 432 Client Error:  for url:
    https://api.tavily.com/search`". A bare requests.raise_for_status() message is only ever
    the status line - it never includes what the provider actually SAID, and Tavily/SerpAPI
    both return a JSON body explaining exactly why a call failed (an invalid/expired key, a
    plan's usage limit reached, a malformed request) whenever they return a non-2xx status.
    Without the body, "432" alone forces a guess between rate limiting, a WAF/anti-bot layer,
    or a broken API key - three problems with three different fixes (wait it out, slow down
    requests, or replace the key) - each time this happens. Surfacing the body turns that
    guess into a fact the operator can act on immediately, without needing a developer to add
    print statements and reproduce it."""
    try:
        res.raise_for_status()
    except requests.exceptions.HTTPError as e:
        body = (res.text or "").strip()
        if body:
            raise requests.exceptions.HTTPError(f"{e} — response body: {body[:500]}",
                                                response=res) from e
        raise


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
    res = requests.post("https://api.tavily.com/search", json=payload, timeout=SEARCH_REQUEST_TIMEOUT_S)
    _raise_for_status_with_body(res)
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
        timeout=SEARCH_REQUEST_TIMEOUT_S,
    )
    _raise_for_status_with_body(res)
    data = res.json()
    return [{"title": r.get("title"), "url": r.get("link"), "snippet": r.get("snippet") or ""}
            for r in (data.get("organic_results") or [])]


GEMINI_SEARCH_MODEL = "gemini-2.5-flash"


def _get_gemini_client():
    """Factored out to a single call so tests can monkeypatch it instead of the real
    google-genai SDK (already a project dependency - see translator.py's GeminiTranslator,
    which uses the identical genai.Client(api_key=...) pattern)."""
    from google import genai
    return genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def _search_with_gemini_grounding(query: str, domains: List[str], max_results: int) -> List[Dict[str, Any]]:
    """Google Search results via Gemini's "Grounding with Google Search" tool.

    CONFIRMED PRODUCT-OWNER REQUEST (2026-08-25), after Tavily's own error body (see
    _raise_for_status_with_body) confirmed a genuine plan usage-limit exhaustion: "what is a
    free tool I could use for only this search? Could I use Gemini Free Tier?" - checked
    against Google's own official pricing page: Gemini 2.5 Flash's free tier includes Grounding
    with Google Search, free up to 500 requests/day, no credit card required for a Google AI
    Studio key - a far bigger free allowance than Tavily's or SerpAPI's free tiers. GEMINI_API_KEY
    is already a config key this codebase uses (see translator.py's GeminiTranslator, for
    translation) - the same key works here.

    UNLIKE Tavily/SerpAPI, this is not a raw search-results endpoint - it is a generated answer
    WITH grounding metadata (the source chunks/URLs Gemini actually drew on). The candidate list
    here is reconstructed from that metadata rather than a results array: one entry per grounding
    chunk (title + URL), with its snippet built from whichever grounding-support text segments
    cite that chunk (falling back to the title alone when a chunk has no supporting segment) -
    the closest equivalent this provider can give to the same {"title", "url", "snippet"} shape
    every other provider in this module returns.

    Used as an automatic FALLBACK when the primary provider (Tavily, or SerpAPI when Tavily
    isn't configured) fails - see _select_and_run_provider - rather than a manual switch; it can
    also serve as the primary itself when GEMINI_API_KEY is the only search-provider key set.

    TIMEOUT (2026-08-25): unlike _search_with_tavily/_search_with_serpapi, this call originally
    had no client-side timeout at all, so a single stalled/slow Gemini call could run
    indefinitely - directly reported by the product owner as "each search combination is taking
    around 1 to 3 minutes". A grounded generate_content call is inherently slower than a plain
    search-results API (it's a full LLM turn plus a live Google Search, not a single lookup), so
    some slowdown vs. Tavily/SerpAPI is expected and not itself a bug - but with no ceiling, one
    slow call could stall an entire discovery run. Capped it at the SAME budget the other two
    providers already use (SEARCH_REQUEST_TIMEOUT_S) via http_options - NOT a bare "timeout" key,
    see translator.py's module docstring for why that bare key fails validation entirely."""
    client = _get_gemini_client()
    model = os.getenv("GEMINI_SEARCH_MODEL") or os.getenv("GEMINI_MODEL") or GEMINI_SEARCH_MODEL
    domain_hint = f" Restrict results to these domains only: {', '.join(domains)}." if domains else ""
    prompt = (f"Search the web for: {query}.{domain_hint} List the real businesses or pages you "
             f"find, one per source, with their name and a short description of each.")
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config={
            "tools": [{"google_search": {}}],
            "http_options": {"timeout": SEARCH_REQUEST_TIMEOUT_S * 1000},
        },
    )

    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return []
    grounding = getattr(candidates[0], "grounding_metadata", None)
    if grounding is None:
        # CONFIRMED REAL GAP (audit, 2026-08-28): Gemini answered but never actually grounded
        # the response in a live search (happens when the model decides plain knowledge
        # answers the prompt, or the search tool silently declines to fire) - this is a real
        # anomaly, not "the provider searched and genuinely found nothing", but returning []
        # here made the two indistinguishable from every caller's point of view. Logged (same
        # print-based diagnostic convention as this module's other provider-failure paths) so a
        # run reporting "0 raw results" can be traced back to this specific cause instead of
        # looking identical to a clean empty search.
        print(f"[outreach_discovery] Gemini grounding search returned no grounding_metadata "
             f"(ungrounded answer) for query: {query!r}")
        return []
    chunks = getattr(grounding, "grounding_chunks", None) or []
    supports = getattr(grounding, "grounding_supports", None) or []

    snippets_by_chunk: Dict[int, List[str]] = {}
    for support in supports:
        segment = getattr(support, "segment", None)
        segment_text = ((getattr(segment, "text", "") or "").strip()) if segment else ""
        if not segment_text:
            continue
        for idx in (getattr(support, "grounding_chunk_indices", None) or []):
            snippets_by_chunk.setdefault(idx, []).append(segment_text)

    results = []
    for i, chunk in enumerate(chunks[:max_results]):
        web = getattr(chunk, "web", None)
        url = ((getattr(web, "uri", "") or "").strip()) if web else ""
        if not url:
            continue
        title = ((getattr(web, "title", "") or "").strip()) if web else ""
        snippet = " ".join(snippets_by_chunk.get(i, [])).strip() or title
        results.append({"title": title, "url": url, "snippet": snippet})
    return results


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


_PROVIDER_ENV_KEYS = (("tavily", "TAVILY_API_KEY"), ("serpapi", "SERPAPI_API_KEY"),
                    ("gemini", "GEMINI_API_KEY"))


def _configured_provider_chain() -> List[str]:
    """Which providers actually have a key set, in fallback order - the single source of truth
    for _select_and_run_provider's own chain AND for _run_provider_search_with_diagnostics'
    error message below (2026-08-26: a real "read operation timed out" report couldn't be traced
    to a specific provider from the error text alone, since a plain requests.Timeout carries no
    provider name - unlike an HTTPError, whose body/message usually names one, e.g. "gemini 429
    RESOURCE_EXHAUSTED"). Naming which provider(s) were actually in play turns "check
    TAVILY_API_KEY/SERPAPI_API_KEY" from a guess into an actual answer."""
    return [name for name, env_key in _PROVIDER_ENV_KEYS if os.getenv(env_key)]


# CONFIRMED (full-app audit MED-HIGH, 2026-09-02): the fallback chain above re-tries every
# configured provider from scratch on EVERY query, with no memory of a provider that already
# confirmed itself dead (429/quota-exhausted) earlier in the SAME run. A dead primary provider
# still got hit on every one of ~4 queries x N combinations - on a 40-combination Country Scope
# run, that's up to 160 wasted calls to a provider already known to be exhausted, which can burn
# a middle-of-the-chain provider's (e.g. SerpAPI's) entire monthly free-tier quota on nothing but
# retries of a call that was never going to succeed. This is NOT the pacing-sleep change that was
# tried and reverted the same day this fallback chain was built (see _select_and_run_provider's
# own docstring) - it adds no delay and makes a run FASTER, not slower: once a provider is
# confirmed rate-limited/quota-exhausted, later queries in this same run skip straight past it to
# the next provider in the chain instead of paying for (and waiting on) a call already known to
# fail. A provider failing for any OTHER reason (bad key, network blip, a genuine 4xx unrelated
# to quota) is NOT tripped here - it keeps being tried on every call, same as before, so those
# failure modes keep surfacing accurately rather than being silently forgiven after one hit.
_PROVIDER_CIRCUIT_BREAKER: Dict[str, float] = {}  # provider name -> time.time() it tripped
_CIRCUIT_BREAKER_COOLDOWN_SECONDS = 3600  # long enough to stop re-hitting a dead provider for
                                           # the rest of a same-session run; short enough that a
                                           # genuinely transient rate limit (not a plan-level
                                           # exhaustion) can be tried again later the same day


def _is_rate_limit_or_quota_error(exc: Exception) -> bool:
    """True only for the specific "this provider is out of quota / rate-limited right now"
    failure shape (429, or a body naming RESOURCE_EXHAUSTED/rate limit/usage limit - see
    _raise_for_status_with_body and this module's own confirmed-incident comments above for the
    real error text these providers actually send) - never for a generic exception, so a bad key
    or an unrelated 4xx keeps failing loudly on every call instead of being mistaken for a
    quota trip and silently skipped."""
    text = str(exc)
    lowered = text.lower()
    return ("429" in text or "resource_exhausted" in lowered or "rate limit" in lowered
            or "rate-limit" in lowered or "usage limit" in lowered or "quota" in lowered)


def _circuit_is_open(name: str) -> bool:
    tripped_at = _PROVIDER_CIRCUIT_BREAKER.get(name)
    if tripped_at is None:
        return False
    if (time.time() - tripped_at) >= _CIRCUIT_BREAKER_COOLDOWN_SECONDS:
        _PROVIDER_CIRCUIT_BREAKER.pop(name, None)  # cooldown elapsed - eligible again
        return False
    return True


def _trip_circuit_breaker(name: str) -> None:
    _PROVIDER_CIRCUIT_BREAKER[name] = time.time()


def reset_circuit_breakers() -> None:
    """Test/diagnostic hook - clears every tripped breaker so a fresh run/test isn't affected by
    a previous one's state."""
    _PROVIDER_CIRCUIT_BREAKER.clear()


def _select_and_run_provider(source: str, query: str, country: str, keyword: str,
                             domains: List[str], max_results: int) -> List[Dict[str, Any]]:
    """The actual provider dispatch, shared by run_provider_search and its diagnostics-
    returning sibling below - factored out once so the retry behaviour here only has to be
    written and tested in one place, not duplicated across both callers.

    CONFIRMED PRODUCT-OWNER HYPOTHESIS (2026-08-25, after the Morocco "0 raw results across 40
    combinations" report): "I think it is too short time for the AI to search for one
    combination." A single slow or contended request timing out used to mean that ENTIRE
    query's results vanished silently - unlike a failed website scrape (which only costs one
    candidate's contact info, already best-effort by design - see _fetch_and_parse), a failed
    SEARCH call loses every candidate that query would have found: often 1/7th to 1/10th of one
    combination's total recall, gone with no record beyond a server-console print. Retried once
    on a timeout/connection error before giving up (not on other errors - a bad key or a 4xx
    fails identically twice, so retrying there only wastes time), and given its own longer
    budget (SEARCH_REQUEST_TIMEOUT_S) than scraping gets, since Tavily's "advanced" search_depth
    already trades speed for quality even without contention. NOTE: the query fan-out that calls
    this is no longer concurrent (see discover_suppliers' own comment) - a brief concurrency
    change here triggered non-standard "432" errors from Tavily, reverted the same day. This
    retry/timeout logic is unrelated to that and stays in place - HTTPError (which a 432 is) is
    deliberately NOT retried above, so it never amplified the burst that caused the 432s.

    AUTOMATIC FALLBACK CHAIN, Tavily -> SerpAPI -> Gemini (built 2026-08-25, same day, in two
    steps): once the 432s were traced to a genuine Tavily plan usage-limit exhaustion (confirmed
    by the response body - see _raise_for_status_with_body), the product owner asked for a
    second free-tier provider rather than just pacing requests, and specifically named Gemini's
    free tier (checked against Google's own pricing page - see _search_with_gemini_grounding's
    own docstring). A Gemini-only fallback then hit a SECOND real limit the very next run: its
    free tier isn't just 500 requests/day, it's ALSO capped at just 5 requests per MINUTE for
    gemini-2.5-flash (confirmed by the actual 429 RESOURCE_EXHAUSTED error, which named that
    exact quota) - and since Tavily's quota was still exhausted, nearly EVERY search call was
    falling through to Gemini, blowing straight past 5/minute on a Country Scope run. Product
    owner's confirmed choice: add SerpAPI (already wired in, free 250 searches/month, no
    per-minute limit reported) as a middle step, so Gemini is only reached once BOTH a real
    search API has failed - a true last resort, not the de facto primary.

    Every provider with a key configured is tried, in this fixed order: Tavily, then SerpAPI,
    then Gemini - each with its own existing transient-error retry (see above) before moving on
    to the next. A provider with no key set is skipped entirely (never counted as "tried and
    failed"). If NONE of the three keys are set, the mock provider runs instead (unchanged, no
    fallback chain - mock is for "no keys configured", never used after a REAL provider fails,
    since silently substituting demo data for a genuine search failure would be worse than
    surfacing the failure). If every configured provider in the chain fails, the LAST one's
    error is what propagates - visible to the user exactly as before (see
    _run_provider_search_with_diagnostics)."""
    def _run(name: str) -> List[Dict[str, Any]]:
        if name == "tavily":
            return _search_with_tavily(query, domains, max_results)
        if name == "serpapi":
            return _search_with_serpapi(query, domains, max_results)
        if name == "gemini":
            return _search_with_gemini_grounding(query, domains, max_results)
        return _search_with_mock_provider(source, country, keyword)

    def _call_with_transient_retry(name: str) -> List[Dict[str, Any]]:
        try:
            return _run(name)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            return _run(name)

    chain = _configured_provider_chain()
    if not chain:
        return _run("mock")

    last_error: Optional[Exception] = None
    for name in chain:
        if _circuit_is_open(name):
            # Already confirmed rate-limited/quota-exhausted earlier this run - don't pay for
            # (or wait on) another call known to fail; go straight to the next provider.
            last_error = last_error or RuntimeError(
                f"{name} is skipped this run - a rate-limit/quota error tripped its breaker "
                f"earlier and the cooldown hasn't elapsed yet")
            continue
        try:
            return _call_with_transient_retry(name)
        except Exception as e:
            if _is_rate_limit_or_quota_error(e):
                _trip_circuit_breaker(name)
            last_error = e
    raise last_error


def run_provider_search(source: str, query: str, country: str, keyword: str,
                        domains: Optional[List[str]] = None, max_results: int = 10) -> List[Dict[str, Any]]:
    try:
        return _select_and_run_provider(source, query, country, keyword, domains or [], max_results)
    except Exception as e:
        print(f"[outreach_discovery] provider search failed for \"{query}\": {e}")
        return []


def _run_provider_search_with_diagnostics(source: str, query: str, country: str, keyword: str,
                                          domains: Optional[List[str]], max_results: int
                                          ) -> "tuple[List[Dict[str, Any]], Optional[str]]":
    """CONFIRMED REAL INCIDENT (2026-08-25): a Morocco Country Scope run (40 combinations) came
    back with "0 raw results" for every single one - real to the person who saw it as "not a
    single supplier found, that can't be" for a country with plenty of real DMCs/guides. The
    breakdown panel this feeds correctly distinguishes a search problem from a filter problem
    (see this module's docstring), but "0 raw" was itself ambiguous in a second way it never
    accounted for: run_provider_search() swallows EVERY exception (a bad/expired API key, a rate
    limit, a network failure, a malformed provider response) and returns an empty list either
    way - printed to a server console the person using this tool never sees. "The provider
    genuinely found nothing for this query" and "every single call to the provider failed with
    an error" were indistinguishable from inside the app, and only the second one means the tool
    itself is broken right now (an expired/rate-limited key would fail identically for EVERY
    query, not just Morocco's - which is exactly what the reported all-zero, 40-combination
    result looks like).

    Same provider selection (via _select_and_run_provider, retry included) as run_provider_search
    - kept as a SEPARATE function rather than changing that one's return shape, since
    run_provider_search's plain "-> list" contract has existing callers (see its own module
    docstring's pipeline step 2) that expect a bare list back no matter what. This sibling is
    used only by discover_suppliers' own query fan-out, where the caller can actually do
    something useful with the distinction."""
    try:
        return _select_and_run_provider(source, query, country, keyword, domains or [], max_results), None
    except Exception as e:
        # CONFIRMED REAL FOLLOW-UP (2026-08-26): a "read operation timed out" report couldn't be
        # traced to a specific provider - a plain requests.Timeout, unlike an HTTPError, carries
        # no provider name in its own text. Name which provider(s) actually have a key configured
        # (the chain that was tried, in order - the LAST one is whoever's error this actually is)
        # right in the message, so "check TAVILY_API_KEY/SERPAPI_API_KEY" isn't a guess anymore.
        chain = _configured_provider_chain()
        chain_note = f" [chain tried: {' → '.join(chain)}]" if chain else " [no provider key configured - mock data]"
        print(f"[outreach_discovery] provider search failed for \"{query}\": {e}{chain_note}")
        return [], f"{source}: {e}{chain_note}"


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
    # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): this pattern used to match ANY single
    # digit in parens, with no lookahead - a phone number formatted with a parenthesized trunk
    # prefix ("+20 (0)100...", a completely standard way to write an Egyptian number) matched
    # "(0)" as a "0-star rating," and vet_candidates below treats a confidently-parsed numeric
    # rating as its STRONGEST signal, enforcing the bar with no review-count rescue for a
    # genuinely low one - so real local businesses with a phone number in their snippet (exactly
    # the kind of strong "this is a real, contactable business" signal this search is trying to
    # find) got hard-rejected on a rating that was never actually there. A real "(N.N)"/"(N)"
    # rating is essentially never immediately glued to another digit with no space (it's always
    # followed by a space, punctuation, or the end of the snippet) - a trunk-prefix "(0)" in a
    # phone number almost always IS glued straight to the rest of the digits ("(0)100..."), so
    # the negative lookahead below tells the two apart without needing a phone-number parser.
    re.compile(r"\((\d(?:[.,]\d)?)\)(?!\d)"),                                  # "(4.6)" next to a name
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
    "viator", "getyourguide", "expedia", "booking.com", "trip.com",  # added aggregators
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
    "viator", "getyourguide", "tripadvisor", "expedia", "booking",  # added aggregators
]

# Fragment names like "In" or "On" happen when a page's real title never loaded and
# the search API fell back to a stray body word - a single common short word is never
# an actual business name.
NAME_STOPWORDS = {
    "in", "on", "at", "by", "to", "of", "for", "and", "or", "is", "it", "a",
    "an", "the", "this", "that", "with", "from",
}


# CONFIRMED REAL BUG (audit, 2026-08-28): `n.startswith(g)` is a plain prefix check with no
# word boundary, so a real business whose name happens to START WITH a blocklisted word as a
# substring - not a whole word - was wrongly rejected: "homeland".startswith("home") is True,
# so "Homeland Tours" (a plausible supplier name, and Momira sources hotel/accommodation
# contracts too) was silently dropped as boilerplate. Same class of bug
# EDITORIAL_PUBLISHER_PATTERNS already solved below for "afar" matching inside "safari" - anchor
# each blocklist phrase to the START of the name AND require a word boundary right after it, so
# "home" still correctly matches "Home", "Home Page", "Home | Company Name" but not "Homeland".
GENERIC_NAME_BLOCKLIST_PATTERNS = [
    re.compile(rf"^{re.escape(g)}\b") for g in GENERIC_NAME_BLOCKLIST
]


def is_generic_name(name: Optional[str]) -> bool:
    if not name:
        return True
    n = name.lower().strip()
    if len(n) < 3:
        return True
    if n in NAME_STOPWORDS:
        return True
    return any(p.match(n) for p in GENERIC_NAME_BLOCKLIST_PATTERNS)


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
#
# CONFIRMED REAL GAP (audit, 2026-08-28): still too narrow - "Top Travel Agencies in Kenya" and
# "Top 15 Tour Operators in Nairobi" have neither a leading digit nor a year, so both slipped
# through unflagged. Added two more alternatives: (a) "Best"/"Top" followed by a number anywhere
# near the start ("Top 15 ..."), (b) "Best"/"Top" followed later by a plural collective noun for
# a category of supplier ("agencies", "operators", "companies", ...) - a real single business
# essentially never names itself that way in its own title, so this stays conservative.
LISTICLE_TITLE_PATTERN = re.compile(
    r"^\d{1,3}\s*(best|top)\b"
    r"|^(the\s+)?(best|top)\b.*\b(19|20)\d{2}(\s*[/\-]\s*\d{2,4})?\b"
    r"|^(the\s+)?(best|top)\s+\d+\b"
    r"|^(the\s+)?(best|top)\b.*\b(agencies|operators|companies|suppliers|dmcs?|guides)\b",
    re.I)


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
# Add global DMC aggregators/consolidators here as well.
OTA_MARKETPLACE_BLOCKLIST = [
    "expedia", "booking.com", "viator", "getyourguide", "trip.com",
    "kayak", "skyscanner", "hotels.com", "agoda",
    # International DMC platforms / consolidators
    "fyndtravel", "dmcworld", "globetrotter", "arrivia", "tourico",
    "hotelbeds", "gta", "travelbound", "travelex", "allianz",
]


# CONFIRMED REAL BUG (audit, 2026-08-28): plain substring matching (`b in n`) rejects any real
# business whose name merely CONTAINS a blocklisted word - "kayak" matches inside "Kayaking
# Excursions" (an entirely plausible real supplier given Momira sources excursions), and "gta"
# (meant as the DMC-platform abbreviation) matches inside ordinary words like "Regatta". Same
# class of bug EDITORIAL_PUBLISHER_PATTERNS already solved for "afar" inside "safari" - word-
# boundary match instead, so "kayak" still correctly matches "Kayak.com" or "Kayak Travel" but
# not "Kayaking Excursions".
OTA_MARKETPLACE_PATTERNS = [
    re.compile(rf"\b{re.escape(b)}\b") for b in OTA_MARKETPLACE_BLOCKLIST
]


def is_ota_or_marketplace(name: Optional[str]) -> bool:
    if not name:
        return False
    n = name.lower()
    return any(p.search(n) for p in OTA_MARKETPLACE_PATTERNS)


# New filter: drop candidates that appear to be international/global DMCs
# by looking at name, snippet, or URL for words like "global", "international", "worldwide"
# combined with "dmc" or "destination management".
GLOBAL_DMC_KEYWORDS = re.compile(
    r"\b(global|international|worldwide)\s+(dmc|destination\s*management)",
    re.I
)


def is_likely_international_dmc(candidate: Dict[str, Any]) -> bool:
    """Returns True if the candidate appears to be a global/international DMC
    (not a local, in-country operator)."""
    text = (f"{candidate.get('name') or ''} {candidate.get('snippet') or ''} "
            f"{candidate.get('sourceUrl') or ''}")
    if GLOBAL_DMC_KEYWORDS.search(text):
        return True
    # Also check for known domain patterns of international DMCs
    url = candidate.get("sourceUrl") or ""
    host = _hostname(url)
    if host:
        # If the domain contains "dmc" but also "global" or "world" – block
        if "dmc" in host and (host.startswith("global") or host.startswith("world")):
            return True
    return False


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
    review_count_rating_floor = _review_count_exception_rating_floor()
    review_count_volume_floor = _review_count_exception_volume_floor()
    kept = []
    for c in candidates:
        rating = c.get("rating")
        review_count = c.get("reviewCount")
        # A confidently-parsed numeric rating is the strongest signal - enforce the bar.
        # bool is a subclass of int in Python, so it's excluded explicitly; JS's
        # `typeof x === 'number'` would never be true for a boolean.
        if isinstance(rating, (int, float)) and not isinstance(rating, bool):
            if rating >= min_rating:
                kept.append(c)
                continue
            # CONFIRMED PRODUCT-OWNER DECISION (2026-08-28): a high REVIEW COUNT is itself a
            # strong real-business signal, worth letting through even when the rating alone
            # falls short of the strict bar - see _review_count_exception_rating_floor's
            # docstring. Never rescues a genuinely poor rating (below the lower floor).
            if (rating >= review_count_rating_floor
                    and isinstance(review_count, (int, float)) and not isinstance(review_count, bool)
                    and review_count >= review_count_volume_floor):
                c["keptOnReviewVolume"] = True
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


def _distinct_host(url_a: Optional[str], url_b: Optional[str]) -> bool:
    """True when both URLs are present and point at genuinely different hosts - used for a
    website/listing candidate, where two different pages on the SAME site (a homepage vs an
    "/about" page) are still the same business - see dedupe_candidates' own comment. For a
    social-profile URL, use _distinct_social_profile instead (see its docstring for why host
    alone is the wrong comparison there)."""
    if not url_a or not url_b:
        return False
    host_a, host_b = _hostname(url_a), _hostname(url_b)
    return bool(host_a) and bool(host_b) and host_a != host_b


def _distinct_social_profile(url_a: Optional[str], url_b: Optional[str]) -> bool:
    """True when both URLs are present and refer to genuinely different social profiles - used
    for Instagram/Facebook, where every profile shares the SAME host (instagram.com) and the
    profile identity lives entirely in the path (instagram.com/citytoursA vs
    instagram.com/citytoursB are different businesses, not a host difference the way two
    different websites would be). Compares the full normalized reference (host + path) rather
    than just the host."""
    if not url_a or not url_b:
        return False
    key_a, key_b = normalize_url_key(url_a), normalize_url_key(url_b)
    return bool(key_a) and bool(key_b) and key_a != key_b


def dedupe_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_name: Dict[str, Dict[str, Any]] = {}
    for c in candidates:
        key = normalize_name(c.get("name") or "")
        url = c.get("sourceUrl") or ""

        # CONFIRMED BUG FIX (full-app audit MED plausible, 2026-09-02): merging purely on
        # normalized name silently attached one business's Instagram/Facebook/website (and its
        # rating) onto a DIFFERENT business that just happens to share, or normalize to, the
        # same name - a real risk for generic names ("City Tours", "Desert Adventures"). The
        # attached channel could then outrank a correct website email found elsewhere for the
        # (wrong) merged record. When the incoming candidate's own contact channel genuinely
        # conflicts with what's already recorded under this name - a DIFFERENT host, not the
        # same profile resurfacing from a second query/source - that's the strongest signal
        # available that these are two separate real businesses, so the candidate is kept as
        # its own separate entry instead of overwriting or being silently absorbed into the
        # first one's contact info.
        existing = by_name.get(key)
        if existing is not None:
            conflict = (
                (is_instagram_url(url) and _distinct_social_profile(url, existing.get("instagramUrl")))
                or (is_facebook_url(url) and _distinct_social_profile(url, existing.get("facebookUrl")))
                or (not is_instagram_url(url) and not is_facebook_url(url)
                    and not is_aggregator_url(url)
                    and _distinct_host(url, existing.get("websiteCandidate")))
            )
            if conflict:
                key = f"{key}::distinct::{_hostname(url) or url}"

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


def cap_candidates_by_rating(candidates: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    """The best `n` candidates by rating, missing ratings sorted last.

    Used by discover_suppliers' max_results override to cut the candidate list down BEFORE
    the expensive per-candidate steps (AI verification, website enrichment) rather than only
    trimming the final display - see that function's docstring. A missing rating isn't a red
    flag, it just sorts after anything that has one, matching the convention the final
    suppliers.sort() in discover_suppliers already uses. bool is excluded explicitly since it's
    a subclass of int in Python and would otherwise pass the numeric-rating check."""
    ranked = sorted(
        candidates,
        key=lambda c: -(c["rating"] if isinstance(c.get("rating"), (int, float))
                        and not isinstance(c.get("rating"), bool) else -1)
    )
    return ranked[:max(1, n)]


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
    r"contact(\s|-|_)?us\b|\bcontact\b|\bkontakt\b|\bimpressum\b|\bimprint\b"
    r"|\benquire\b|\bquote\b|book(\s|-|_)?now|reservation", re.I)   # expanded
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
    """CONFIRMED PRODUCT-OWNER REQUEST (2026-08-25): "the tool needs more time to find the
    correct email address." This is the one shared fetch every scrape call site goes through -
    a candidate's own homepage, its Contact/Impressum/Terms subpages, an aggregator listing
    page, and an outbound link followed FROM one - so retrying it once here (on a timeout or
    connection error only; a 404/403/etc. genuinely won't succeed on a second try) covers all of
    them without needing the same retry written at each call site."""
    try:
        res = requests.get(url, timeout=REQUEST_TIMEOUT_S, headers=_SCRAPE_HEADERS)
        res.raise_for_status()
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
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
            # Even if site_result has no email, we have the website – that's valuable.
            listing_fallback = extract_email_and_instagram_from_page(soup)
            return {
                "website": outbound,  # always return the found official site
                "email": site_result["email"] or listing_fallback["email"],
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
    # CONFIRMED PRODUCT-OWNER-ACCEPTED TRADE-OFF (2026-08-26, see build_queries' own docstring):
    # the city/country supplier searches were consolidated from 3 typed calls each into 1, so a
    # result from that call can no longer say which specific phrase (DMC/agency/guide) matched -
    # only that it came from the combined local-supplier search.
    "supplier_city": "Local Supplier (City)", "supplier_country": "Local Supplier (Country)",
    "reviews": "Review Sites",
    # Kept for OLD cached/remembered results built before the 2026-08-26 consolidation.
    "dmc_city": "DMC (City)", "agency_city": "Travel Agency (City)", "guide_city": "Tour Guide (City)",
    "dmc_country": "DMC (Country)", "agency_country": "Travel Agency (Country)", "guide_country": "Tour Guide (Country)",
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
    reason = f"{rating_part} {teaser}" if teaser else rating_part
    # CONFIRMED PRODUCT-OWNER DECISION (2026-08-28): a candidate let through despite sitting
    # below MIN_SUPPLIER_RATING, on review-count strength (see vet_candidates) - named here so
    # the human reviewing the list knows this one is here on volume, not on rating alone.
    if candidate.get("keptOnReviewVolume"):
        reason = f"{reason} (below the usual rating bar, but kept for its strong review count.)"
    return reason


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
        # CONFIRMED RULE (product owner, 2026-08-16): only pre-tick a supplier for sending
        # when a real email address was actually found. A website or social link alone isn't
        # enough to send anything - ticking those too just meant unticking them by hand on
        # every run, since they can't be emailed without an address being added first.
        #
        # CONFIRMED BUG FIX (full-app audit MED, 2026-09-02): that rule didn't account for a
        # MOCK candidate - _search_with_mock_provider fabricates a plausible-looking
        # "info@{slug}.com" for every demo row (see its own docstring), which satisfied
        # bool(candidate.get("email")) exactly like a real one and pre-ticked fabricated
        # suppliers for sending. One click on the review screen's "Send" button would have
        # emailed an address that was never real. Mock candidates are never pre-ticked now,
        # regardless of whether they carry a (fabricated) email.
        "selected": bool(candidate.get("email")) and not bool(candidate.get("isMock")),
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
    # CONFIRMED BUG FIX (full-app audit LOW, 2026-09-02): the "." in "www." was unescaped, so
    # this matched "www" plus ANY single character, not just a literal dot - a host genuinely
    # starting "wwwX..." (rare, but not impossible) would have four characters wrongly stripped
    # instead of none, causing a false dedupe collision with an unrelated host. Escaped now.
    stripped_host = re.sub(r"^www\.", "", host)
    return f"{stripped_host}{path}"


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
        # CONFIRMED RULE (product owner, 2026-08-16): pre-tick for sending ONLY when an actual
        # email survived the merge - a website or social link alone still isn't sendable.
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


def _build_verification_prompt(candidates: List[Dict[str, Any]], country: str, keyword: str,
                               known_examples: Optional[List[Dict[str, Any]]] = None) -> str:
    listing = "\n\n".join(
        f"{i + 1}. id=\"{c.get('id')}\"\n   name: {c.get('name')}\n   source: {c.get('source')}\n"
        f"   url: {c.get('sourceUrl')}\n   snippet: {(c.get('snippet') or '')[:300]}"
        for i, c in enumerate(candidates)
    )
    # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-30): "the App can learn which suppliers are
    # needed and to improve the search results" - part of that is calibration, not just recall.
    # A supplier a human already added by hand for this exact country/theme is real evidence of
    # what a genuine match looks like HERE specifically (a real business name, a real domain
    # shape), on top of the generic instructions above. Capped at 8 - this is calibration
    # context for judging OTHER candidates, not something that should crowd out the actual
    # candidate listing on a country/theme with a long remembered history.
    examples_block = ""
    if known_examples:
        examples_listing = "\n".join(
            f"- {e.get('name')}" + (f" ({e.get('website')})" if e.get("website") else "")
            for e in known_examples[:8]
        )
        examples_block = (
            f'\nFor reference, a human has already manually confirmed these ARE genuine, in-country direct '
            f'suppliers for this same "{keyword}" search in {country}:\n{examples_listing}\n'
            f'Use them ONLY to calibrate what a real match looks like for this country/theme - they are not '
            f'candidates to verify themselves and will not appear in the candidate list below.\n'
        )
    return (
        f'You are an expert travel industry procurement assistant. A search for local travel suppliers in '
        f'"{country}" offering "{keyword}" (or related tours, sightseeing, adventures, transfers, excursions, '
        f'round trips, seat-in-coach (SIC), or hotel accommodation) returned the raw candidates below, pulled '
        f'from search snippets. Some may be forum posts, articles, unrelated businesses, or based outside '
        f'{country} - your job is to judge each one.\n'
        f'{examples_block}\n'
        f'Important: DMCs (Destination Management Companies), local travel agencies, tour operators, and private '
        f'tour guides are ALL valid direct suppliers. Prefer to keep a candidate if it looks like a legitimate '
        f'travel business with a website, even if the snippet is sparse. Only reject if it is clearly an OTA '
        f'(like Viator/GetYourGuide), a forum post, a listicle, or an unrelated business.\n\n'
        f'For EVERY candidate, report:\n'
        f'- isDirectSupplier: true if it is a travel agency, tour operator, DMC, or tour guide - false if it is '
        f'an article, forum, listicle, OTA, or unrelated.\n'
        f'- isInCountry: true if the business is physically located and operating within {country}.\n'
        f'- cleanCompanyName: the correct business name (fix mangled/truncated titles if needed).\n'
        f'- reviewTeaser + reviewSource: a short 1-2 sentence summary of any customer feedback in the snippet, '
        f'and which platform it\'s from.\n\n'
        f'Candidates:\n\n{listing}\n\n'
        f'Call report_verification with a verdict for every single candidate listed (same count, same ids).'
    )


def verify_candidates(candidates: List[Dict[str, Any]], country: str, keyword: str,
                      known_examples: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """Returns {candidate_id: verdict} on success, or None if AI verification is
    disabled/unavailable/failed - callers treat None as "skip this step, rely on the
    rule-based filters that already ran".

    `known_examples` - suppliers a human already vetted by hand for this exact country/theme
    (see outreach_learned_suppliers.resurface_remembered_suppliers) - passed through to the
    prompt as calibration reference only; see _build_verification_prompt's own docstring."""
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
            max_tokens=8192,
            tools=[VERIFY_TOOL],
            tool_choice={"type": "tool", "name": "report_verification"},
            messages=[{"role": "user", "content": _build_verification_prompt(
                candidates, country, keyword, known_examples)}],
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
def discover_suppliers(country: str, city: str, keyword: str, progress=None,
                       max_results: Optional[int] = None) -> Dict[str, Any]:
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

    `max_results` overrides the usual _max_results() cap for THIS call. CONFIRMED RULE
    (product owner, 2026-08-16): a country-scope run can queue many place/theme
    combinations at once with no upper limit on how many - passing max_results=1 there
    (see outreach_tool._process_one_queued_job) is what actually makes that fast, not just
    smaller. The cap is applied right after dedupe, BEFORE AI verification and per-
    candidate website enrichment - both of which run once per surviving candidate - so
    capping early means one AI-verification call and one website fetch per combination
    instead of up to _max_candidates() of each. Candidates are ranked by rating first, so
    the one candidate that survives is the best one found, not just whichever query found
    it first."""
    def report(msg):
        if progress:
            progress(msg)

    # Load the user blocklist once per search
    user_blocklist = get_blocklist()

    # Suppliers a human already added by hand for this EXACT (country, keyword) combination in
    # an earlier session (see outreach_learned_suppliers' own docstring) - computed once, up
    # front, and used twice below: as calibration reference for AI verification, and merged
    # straight into the final result list so they don't have to be found (or re-added) again.
    remembered_suppliers = resurface_remembered_suppliers(country, keyword)

    queries = build_queries(country, city, keyword)
    candidates: List[Dict[str, Any]] = []

    # REVERTED (2026-08-25, CONFIRMED REAL INCIDENT): these queries were briefly run
    # concurrently via a ThreadPoolExecutor (a same-day speed change), but bursting up to
    # 6 simultaneous connections at one search endpoint,
    # repeated once per place/theme combination across a 20-40 combination Country Scope
    # run, triggered non-standard "432 Client Error" responses from Tavily - not a
    # documented Tavily status code (their own docs only mention 429 for rate limiting),
    # and consistent in shape with an intermediary (WAF/anti-bot layer) blocking bursts of
    # concurrent requests rather than Tavily itself. Product owner: "we have to change it
    # back, as we had always results and now nothing any more. The time for the search was
    # much longer, but that's okay." Back to one query at a time; _run_provider_search_with_
    # diagnostics (error visibility) and the longer SEARCH_REQUEST_TIMEOUT_S / retry-once
    # logic in _select_and_run_provider are kept - neither is implicated in the 432s.
    #
    # PACED, THEN REMOVED AGAIN (2026-08-25, same day): a 20s-per-call pacing sleep briefly
    # lived here after Tavily's error body (see _raise_for_status_with_body) confirmed "This
    # request exceeds your plan's set usage limit." Product owner asked for the pacing
    # ("we have to change the search time again to 20 seconds per field"), but a real Country
    # Scope run (9 combinations x ~10 sources each) then sat at "0 of 9 searched" for many
    # minutes at a stretch with zero visible progress - the pacing sleep blocks a single
    # Streamlit script run with no incremental UI update in between, so it read as a frozen
    # screen, not a working one. Worse, pacing was never going to fix a genuinely exhausted
    # PLAN-LEVEL quota (as opposed to a rate limit that resets over a short window) - Tavily's
    # own wording ("upgrade your plan or contact support@tavily.com") reads like the former.
    # Product owner, once he saw the effect: "12 hour ago the Outreach search was much better,
    # we destroyed it today with all the changes" - confirmed choice (2026-08-25) was to remove
    # the pacing entirely rather than keep it or shorten it. Back to one query at a time, at
    # full speed - the SAME shape this loop had right after the concurrency revert above.
    # _run_provider_search_with_diagnostics (error visibility, now with the response body) and
    # the SEARCH_REQUEST_TIMEOUT_S / retry-once logic in _select_and_run_provider are unrelated
    # to the pacing and stay in place. The actual fix for a genuine plan quota is on Tavily's
    # side (upgrade the plan, wait for the usage window to reset) or switching to the
    # SERPAPI_API_KEY fallback _select_and_run_provider already supports.
    report(f"Searching {len(queries)} source(s)…")
    results_per_query = [
        _run_provider_search_with_diagnostics(q["source"], q["query"], country, keyword,
                                               q["domains"], q["max_results"])
        for q in queries
    ]
    # CONFIRMED REAL INCIDENT (2026-08-25): see _run_provider_search_with_diagnostics' own
    # docstring - every provider call failing with an error (bad/expired key, rate limit,
    # network issue) used to look IDENTICAL to "the provider genuinely found nothing", both as
    # raw_count == 0. Collecting the errors here lets the caller tell the two apart instead of
    # reporting a plausible-but-wrong "no suppliers exist" conclusion.
    provider_errors = []
    for q, (results, error) in zip(queries, results_per_query):
        if error:
            provider_errors.append(error)
        for raw in results:
            candidates.append(parse_signals(raw, q["source"]))

    raw_count = len(candidates)
    if provider_errors:
        report(f"{raw_count} raw result(s) found ({len(provider_errors)} of {len(queries)} "
               f"source(s) failed with an error). Filtering…")
    else:
        report(f"{raw_count} raw result(s) found. Filtering…")

    relevance_tokens = build_relevance_tokens(country, keyword)
    drop_log: List[Dict[str, Any]] = []
    kept: List[Dict[str, Any]] = []

    for c in candidates:
        def reject(reason):
            drop_log.append({"name": c.get("name"), "url": c.get("sourceUrl"), "reason": reason, "stage": "pre-filter"})
            return False

        url = c.get("sourceUrl") or ""

        # ---- NEW: Drop domains on the user blocklist ----
        domain = _extract_domain(url)
        if domain in user_blocklist:
            reject(f"domain '{domain}' is on the user blocklist (previously rejected)")
            continue

        # ---- NEW: Drop global/international DMCs ----
        if is_likely_international_dmc(c):
            reject("international/global DMC (not local)")
            continue

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

    if max_results is not None:
        # Cutting HERE, before verification/enrichment, is what actually saves the time - see
        # the max_results docstring above.
        deduped = cap_candidates_by_rating(deduped, max_results)
        report(f"Keeping the top {len(deduped)} candidate(s) for speed…")

    ai_dropped = 0
    if is_ai_verification_enabled():
        report("Verifying candidates with AI…")
        verdicts = verify_candidates(deduped, country, keyword, known_examples=remembered_suppliers)
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

    # NOT reverted alongside the query fan-out above (see that comment for the incident) -
    # this hits N distinct supplier-owned domains once each, not one shared API endpoint
    # repeatedly, so it doesn't share the burst-triggered-432s failure mode. Each candidate's
    # website/Instagram scrape is independent of every other candidate's, so this doesn't wait
    # for one supplier's site to respond before starting the next. Order-preserving map():
    # `enriched` must line up with `deduped` index-for-index exactly as a sequential loop would.
    report(f"Looking up contact details for {len(deduped)} candidate(s)…")
    if deduped:
        with ThreadPoolExecutor(max_workers=min(len(deduped), _enrichment_concurrency())) as pool:
            enriched = list(pool.map(enrich_from_website, deduped))
    else:
        enriched = []

    suppliers = [to_supplier_record(c, country, keyword) for c in enriched]
    # Suppliers with absolutely no way to reach them are dropped, but a listing
    # reference counts - a real supplier name plus a real review page is still useful
    # even with no scraped email, and dropping these quietly starved results down to
    # almost nothing but Instagram.
    before_contact_filter = len(suppliers)
    suppliers = [s for s in suppliers if s["email"] or s["website"] or s["social"] or s["listingUrl"]]
    no_contact_dropped = before_contact_filter - len(suppliers)

    suppliers = dedupe_suppliers_by_contact(suppliers)

    # ---- Resurface suppliers a human already vetted by hand for this exact combination ----
    # Merged through the SAME contact-based dedupe pass, not just appended - a remembered
    # supplier the automated search also independently found again this run must collapse into
    # ONE row (keeping whichever side has richer data), never show up twice. The delta in count
    # before/after is an exact count of how many remembered suppliers actually added a NEW row
    # this run (as opposed to merging into one the automated search already found).
    remembered_added = 0
    if remembered_suppliers:
        before_remember_merge = len(suppliers)
        suppliers = dedupe_suppliers_by_contact(suppliers + remembered_suppliers)
        remembered_added = len(suppliers) - before_remember_merge

    # Direct email is what the outreach campaign runs on, so suppliers with one are
    # surfaced first. Within that, own website ranks above social-only - own site is
    # the priority channel. Only then does rating decide; a missing rating isn't a red
    # flag on its own, it just means there's no number to sort by.
    #
    # NOTE: a remembered supplier is sorted and capped exactly like anything else here, so on a
    # combination that already returns a full page of highly-rated automated results, a
    # remembered one COULD still be cut by max_results below. Not special-cased to bypass the
    # cap - remembered supplier counts per combination are small in practice (a handful, not a
    # page), and exempting them from the same cap/sort rules other suppliers use would be a
    # bigger, less predictable change than the "don't make me re-add this" problem calls for.
    suppliers.sort(key=lambda s: (
        0 if s.get("email") else 1,
        0 if s.get("website") else 1,
        -(s["rating"] if s.get("rating") is not None else -1),
    ))

    suppliers = suppliers[:(max_results if max_results is not None else _max_results())]
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
            # CONFIRMED BUG FIX (full-app audit MED, 2026-09-02): this check used to ignore
            # GEMINI_API_KEY entirely - a real Gemini-only run (a legitimate configuration, see
            # _select_and_run_provider's own docstring on Gemini being usable as the sole
            # provider) got mislabeled "these are mock results, don't email them" despite every
            # candidate being genuinely found, and the reverse gap existed too: with none of the
            # three keys set, this was the only sanity check standing between a real send and a
            # batch of fabricated addresses, so it must recognize every provider the chain
            # actually supports, not just the first two historically added. Uses
            # _configured_provider_chain() - the single source of truth for which providers are
            # actually configured - instead of re-listing keys here a second time.
            "used_mock_provider": not _configured_provider_chain(),
            "provider_error_count": len(provider_errors),
            "provider_error_sample": provider_errors[0] if provider_errors else None,
            "remembered_available": len(remembered_suppliers),
            "remembered_added": remembered_added,
        },
    }