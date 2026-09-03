"""
Geocodes place names into latitude/longitude for Ticket geolocation. Travel
Compositor's own destination data was confirmed (via live testing) to NOT
include coordinates, so this relies on free third-party geocoders instead.

CONFIRMED REAL ISSUE (reported by the team - "geolocation not found for
important places, most of the time"): OpenStreetMap's public Nominatim
instance frequently returns ZERO results when called from cloud-hosted apps
(e.g. Streamlit Cloud) - this isn't a bug in how we call it, it's Nominatim's
own published usage policy explicitly discouraging "heavy use"/production
traffic from shared/cloud IP ranges, and it silently returns an empty result
rather than a clear error. The code comments here already flagged this as a
risk before it became a real problem.

FIX: try Nominatim first (still useful, still free, still attributed), and
if it comes back empty, automatically fall back to Photon (komoot.io) - a
SEPARATE free, no-API-key-required OpenStreetMap-based geocoder with its own
infrastructure/rate limits, so a Nominatim block doesn't also block Photon.
Between the two, a real, well-known place should resolve far more reliably
than relying on either alone.

IMPORTANT - Nominatim Usage Policy (https://operations.osmfoundation.org/policies/nominatim/):
  - Absolute max 1 request/second - enforced below via a built-in delay
  - Results are cached in-memory so the same place is never looked up twice
    in one session
  - Requires a real identifying User-Agent (not a generic library default) -
    set below
  - Requires visible attribution wherever results are shown: "Geocoding
    data (c) OpenStreetMap contributors" - the app displays this
  - This is donated infrastructure, not meant for heavy/bulk/production use.
    If usage grows significantly, consider a dedicated Nominatim instance
    or a commercial geocoding provider instead of the public endpoint.

Photon (https://photon.komoot.io) is also built on OpenStreetMap data, so the
same "(c) OpenStreetMap contributors" attribution covers results from either
provider - no extra attribution needed.
"""
import re
import time
from urllib.parse import urlparse

import requests

MODULE_BUILD = "2026-09-03-voucher-remarks-no-raw-supplier-cancellation-text"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
PHOTON_URL = "https://photon.komoot.io/api/"
USER_AGENT = "MomiraTravelCompositorTool/1.0 (internal DMC-to-TravelCompositor upload tool)"

# CONFIRMED FEATURE (product owner, 2026-09-03): "If I have to manually change the coordinates,
# I would like to just add the URL with the correct place from google. Could the app then get the
# correct data like longitude and latitude?" - a human who already found the right pin in Google
# Maps shouldn't have to read the lat/lng off the screen and retype it by hand (error-prone, easy
# to transpose). This lets them paste the Maps URL/share-link instead and extracts the coordinates
# automatically.
_SHORT_LINK_HOSTS = ("goo.gl", "maps.app.goo.gl", "g.co")

# Tried in this order - a place URL's own precise pin (the "!3d..!4d.." pair Google Maps embeds
# for the actual marked place) is preferred over the map's current viewport center (the "@lat,lng"
# in the URL, which just reflects wherever the map happened to be panned/zoomed to and can be
# noticeably off from the actual pin for a small/precise location). "q=" and "ll=" cover the
# simpler share-link/plain-coordinate-search URL shapes.
_COORD_PATTERNS = (
    re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)"),
    re.compile(r"[?&]q=(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)"),
    re.compile(r"[?&]ll=(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)"),
    re.compile(r"@(-?\d+\.\d+),(-?\d+\.\d+)"),
)

_cache = {}
_failure_cache = {}  # cache_key -> monotonic time.time() of the last failed attempt
_last_nominatim_request_time = [0.0]

# CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): a failed attempt (both providers'
# calls themselves failed - see _nominatim_search's docstring) is retried after this long,
# rather than never (the original bug) or on every single call (which would re-pay the full
# 1 req/sec Nominatim throttle for every repeated lookup of the same place within a short
# burst - a real cost, and exactly the "heavy use" pattern the usage policy asks callers to
# avoid). Long enough that a burst of calls for the same place (e.g. many documents from one
# session mentioning the same city) only actually hits the network once; short enough that a
# transient blip is never mistaken for "this place does not exist" for the life of the
# container, unlike the original bug.
_FAILURE_RETRY_SECONDS = 300


def _nominatim_search(clean_query: str, limit: int):
    """Raw Nominatim call - returns (results, ok). `ok` is False only when the CALL ITSELF
    failed (network error, timeout, non-200 status) - never for a legitimate zero-result
    answer, so callers can tell "we don't know" apart from "we asked and there really is
    nothing." Never raises.

    CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): this used to return a bare list, with
    no way to tell a genuine "no such place" apart from "the request itself failed" - both
    collapsed to the same `[]`. geocode_search cached that `[]` FOREVER (an in-memory,
    process-lifetime cache with no expiry - "the same place is never looked up twice in one
    session", by design, for real answers), so a single transient network blip on a real,
    valid place permanently turned it into "this place does not exist" for the rest of the
    container's life, with every later lookup for that exact query served the same cached
    failure without ever trying again."""
    elapsed = time.time() - _last_nominatim_request_time[0]
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)

    # CONFIRMED BUG FIX (full-app audit MED, 2026-09-02): the throttle clock used to only advance
    # inside the try block, right after a successful `requests.get` call - if the request itself
    # raised (timeout, connection error, DNS failure), the clock was never updated at all. The
    # NEXT call then measured elapsed time against a stale (possibly very old, or 0.0 on the
    # first-ever call) timestamp, saw it comfortably over 1.1s, and skipped the sleep entirely -
    # so a burst of failures produced a burst of requests against donated infrastructure with no
    # throttling, exactly the risk this delay exists to prevent. Recorded in a `finally` now, so
    # every actual network attempt advances the clock whether it succeeds or fails.
    try:
        try:
            res = requests.get(
                NOMINATIM_URL,
                params={"q": clean_query, "format": "json", "limit": limit, "addressdetails": 0},
                headers={"User-Agent": USER_AGENT},
                timeout=10
            )
        finally:
            _last_nominatim_request_time[0] = time.time()
        if res.status_code != 200:
            return [], False
        return [
            {
                "latitude": float(item["lat"]),
                "longitude": float(item["lon"]),
                "display_name": item.get("display_name", ""),
                "type": item.get("type", ""),
                "provider": "nominatim",
            }
            for item in res.json()
        ], True
    except Exception:
        return [], False


def _photon_search(clean_query: str, limit: int):
    """
    Raw Photon call - returns (results, ok), same convention as _nominatim_search (see its
    docstring for why `ok` exists - CONFIRMED BUG FIX, full-app audit HIGH, 2026-09-01). Same
    result shape as _nominatim_search so callers can't tell the difference. Photon's response is
    GeoJSON: each feature has geometry.coordinates = [lon, lat] (note: reversed vs. lat/lon) and
    a properties dict with name/city/state/country pieces to reconstruct a human-readable
    display_name from (Photon doesn't provide one ready-made the way Nominatim does). Never
    raises.
    """
    try:
        res = requests.get(
            PHOTON_URL,
            params={"q": clean_query, "limit": limit},
            headers={"User-Agent": USER_AGENT},
            timeout=10
        )
        if res.status_code != 200:
            return [], False
        results = []
        for feature in res.json().get("features", []):
            coords = (feature.get("geometry") or {}).get("coordinates")
            if not coords or len(coords) < 2:
                continue
            props = feature.get("properties") or {}
            name_parts = [props.get(k) for k in ("name", "city", "state", "country") if props.get(k)]
            # Avoid "Paris, Paris, France"-style duplication when name == city etc.
            deduped_parts = []
            for part in name_parts:
                if part not in deduped_parts:
                    deduped_parts.append(part)
            results.append({
                "latitude": float(coords[1]),
                "longitude": float(coords[0]),
                "display_name": ", ".join(deduped_parts) or clean_query,
                "type": props.get("osm_value") or props.get("type") or "",
                "provider": "photon",
            })
        return results, True
    except Exception:
        return [], False


def geocode_search(query: str, limit: int = 5) -> list:
    """
    Returns up to `limit` candidate results for a place name, each:
    {"latitude": float, "longitude": float, "display_name": str, "type": str}
    Unlike geocode() (which silently trusts the single top result), this lets
    a human see several options and pick the genuinely correct one - useful
    when a broad place name (e.g. "Bali") could resolve to several very
    different points depending on which entity gets picked.

    Tries Nominatim first, then falls back to Photon if Nominatim returns
    nothing (see module docstring - this is the fix for "important places
    return no results most of the time", which is Nominatim silently
    declining cloud-hosted traffic rather than a real "place doesn't exist").

    Cached per unique (query, limit) to avoid repeated lookups and respect
    Nominatim's 1 request/second policy - but a CONFIRMED-FAILED attempt (both providers'
    calls themselves failed, not just "found nothing" - see _nominatim_search's docstring,
    CONFIRMED BUG FIX full-app audit HIGH, 2026-09-01) is only cached for
    _FAILURE_RETRY_SECONDS, not forever, and is retried after that window instead of being
    remembered as "this place does not exist" for the life of the container. A real "no
    results from either provider" answer is still cached indefinitely, same as before.
    """
    clean_query = (query or "").strip()
    if not clean_query:
        return []

    cache_key = (clean_query.lower(), limit)
    if cache_key in _cache:
        return _cache[cache_key]
    last_failure = _failure_cache.get(cache_key)
    if last_failure is not None and (time.time() - last_failure) < _FAILURE_RETRY_SECONDS:
        return []

    results, nominatim_ok = _nominatim_search(clean_query, limit)
    photon_ok = True
    if not results:
        results, photon_ok = _photon_search(clean_query, limit)

    if nominatim_ok or photon_ok:
        _cache[cache_key] = results
        _failure_cache.pop(cache_key, None)
    else:
        _failure_cache[cache_key] = time.time()
    return results


def geocode(query: str) -> dict:
    """
    Returns {"latitude": float|None, "longitude": float|None,
              "display_name": str|None, "valid": bool, "provider": str|None}.
    "provider" is "nominatim" or "photon" - whichever actually served this
    result - so callers can show accurate attribution/labeling instead of
    assuming it always came from Nominatim.
    Thin wrapper around geocode_search() (which already has the Nominatim ->
    Photon fallback) - takes the top result. Cached in-memory per unique
    query (case-insensitive) via geocode_search()'s own cache.
    """
    results = geocode_search(query, limit=1)
    if not results:
        return {"latitude": None, "longitude": None, "display_name": None, "valid": False, "provider": None}
    top = results[0]
    return {
        "latitude": top["latitude"],
        "longitude": top["longitude"],
        "display_name": top["display_name"],
        "valid": True,
        "provider": top.get("provider"),
    }


def _resolve_short_google_maps_url(url: str) -> str:
    """Google Maps share-links are often shortened (maps.app.goo.gl/..., goo.gl/maps/...) and
    carry no coordinates themselves - they redirect (a normal HTTP redirect, not JS) to the real,
    long google.com/maps URL that does. Follows the redirect chain and returns wherever it lands;
    on any failure (network error, non-redirecting host, timeout) returns the input unchanged so
    the caller's own regex pass still runs against it - some short links don't even need this.
    A HEAD request is tried first since it's cheap and Google honors it for these links; GET is
    the fallback for any host that doesn't."""
    try:
        res = requests.head(url, allow_redirects=True, timeout=10, headers={"User-Agent": USER_AGENT})
        if res.url and res.url != url:
            return res.url
    except Exception:
        pass
    try:
        res = requests.get(url, allow_redirects=True, timeout=10, headers={"User-Agent": USER_AGENT}, stream=True)
        return res.url or url
    except Exception:
        return url


def parse_google_maps_url(url: str) -> dict:
    """
    Extracts {"latitude": float, "longitude": float} straight out of a Google Maps URL the human
    pastes in, instead of them having to read the coordinates off the page and type them by hand.
    Returns {"latitude": float|None, "longitude": float|None, "valid": bool, "error": str|None}.

    Handles every URL shape actually seen from Google Maps' own "Share" / address-bar copy:
      - A place page with its own precise pin, e.g.
        .../maps/place/Some+Place/@27.39,33.67,17z/data=!4m6!3m5!...!8m2!3d27.3949!4d33.6784!...
        (uses the !3d/!4d pin, NOT the @ viewport center - see _COORD_PATTERNS' comment for why)
      - A bare coordinate search/share link, e.g. .../maps?q=27.3949,33.6784 or .../maps?ll=...
      - A plain "current view" link, e.g. .../maps/@27.3949,33.6784,15z
      - A shortened share-link (maps.app.goo.gl/..., goo.gl/maps/...) - resolved to its real,
        long URL first (one network round-trip), then parsed the same as any other Maps URL.

    Never raises - a URL that isn't recognized, isn't reachable (for a short link), or simply
    doesn't contain coordinates in any of the shapes above comes back as valid=False with a
    human-readable `error`, so the caller can show it rather than silently doing nothing.
    """
    raw = (url or "").strip()
    if not raw:
        return {"latitude": None, "longitude": None, "valid": False, "error": "Paste a Google Maps link first."}

    candidate = raw if re.match(r"^https?://", raw, re.I) else f"https://{raw}"
    try:
        host = urlparse(candidate).netloc.lower()
    except Exception:
        host = ""

    if not host or ("google" not in host and "goo.gl" not in host and "g.co" not in host):
        return {"latitude": None, "longitude": None, "valid": False,
                "error": "That doesn't look like a Google Maps link."}

    resolved = candidate
    if any(h in host for h in _SHORT_LINK_HOSTS):
        resolved = _resolve_short_google_maps_url(candidate)

    for pattern in _COORD_PATTERNS:
        m = pattern.search(resolved)
        if not m:
            continue
        lat, lng = float(m.group(1)), float(m.group(2))
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            return {"latitude": lat, "longitude": lng, "valid": True, "error": None}

    return {"latitude": None, "longitude": None, "valid": False,
            "error": "Couldn't find coordinates in that link - try copying the link again from "
                     "Google Maps' own Share button, or right-click the exact pin and copy the "
                     "coordinates shown at the top of the menu."}
