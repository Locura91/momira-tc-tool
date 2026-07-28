"""
Geocodes place names into latitude/longitude using OpenStreetMap's free
Nominatim service. Travel Compositor's own destination data was confirmed
(via live testing) to NOT include coordinates, so this replaces that
approach entirely for Ticket geolocation.

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
"""
import time
import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "MomiraTravelCompositorTool/1.0 (internal DMC-to-TravelCompositor upload tool)"

_cache = {}
_last_request_time = [0.0]


def geocode(query: str) -> dict:
    """
    Returns {"latitude": float|None, "longitude": float|None,
              "display_name": str|None, "valid": bool}.
    Cached in-memory per unique query (case-insensitive) to avoid repeated
    lookups and respect the 1 request/second policy.
    """
    clean_query = (query or "").strip()
    if not clean_query:
        return {"latitude": None, "longitude": None, "display_name": None, "valid": False}

    cache_key = clean_query.lower()
    if cache_key in _cache:
        return _cache[cache_key]

    elapsed = time.time() - _last_request_time[0]
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)

    try:
        res = requests.get(
            NOMINATIM_URL,
            params={"q": clean_query, "format": "json", "limit": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=10
        )
        _last_request_time[0] = time.time()
        if res.status_code == 200:
            data = res.json()
            if data:
                result = {
                    "latitude": float(data[0]["lat"]),
                    "longitude": float(data[0]["lon"]),
                    "display_name": data[0].get("display_name"),
                    "valid": True,
                }
            else:
                result = {"latitude": None, "longitude": None, "display_name": None, "valid": False}
        else:
            result = {"latitude": None, "longitude": None, "display_name": None, "valid": False}
    except Exception:
        result = {"latitude": None, "longitude": None, "display_name": None, "valid": False}

    _cache[cache_key] = result
    return result
