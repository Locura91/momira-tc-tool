"""
masterdata_matcher.py — scores candidates from the local Travel Compositor master-hotel index
(masterdata_store.py) against a hotel name/country/geolocation typed in by a human, for the
"Use Travel Compositor master data for this hotel?" step of the Hotel contract wizard
(product owner request, 2026-09-06).

Deliberately NOT the same style as hotel_matcher.py's room/rate/season matching, and on purpose:
that module avoids fuzzy matching entirely because it applies a match SILENTLY (auto-resolving
which existing room a document's room updates) - a false-positive fuzzy match there would
silently overwrite the wrong room's data. Here, the opposite risk profile applies: every match
this module proposes is shown to a human to CONFIRM or reject before anything is applied (product
owner decision, 2026-09-06) - so returning generous, ranked candidates (including imperfect ones)
is more useful than being conservative, since a human is the one who decides whether a candidate
is actually the same hotel.

Matching signal, in order of trust:
  1. countryCode - a hard filter when the caller has one, since it's normally known (from the
     hotel contract's own destination) and cuts the ~361k-record index down enormously before any
     string comparison runs.
  2. Name similarity - difflib.SequenceMatcher (same library api_client.py already uses for
     destination-name matching), on a normalized (casefolded, whitespace-collapsed) name.
  3. Geolocation proximity - only applied as a SCORE BOOST when the caller supplies lat/lng (e.g.
     already geocoded the contract's address), never as a hard filter, since it's optional input
     and a hotel's name can be a strong enough signal on its own.
No GIATA-based matching path: confirmed (product owner, 2026-09-06) that contracts received from
local partners never carry a GIATA id, so it's not a usable signal on our input side even though
Travel Compositor's own data carries one.
"""

MODULE_BUILD = "2026-09-11-price-refresh-option-base-and-price-dates"

import math
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

# Below this, a candidate is noise rather than a real suggestion - keeps the picker short and
# avoids showing a human a "match" that shares nothing but a common word like "Hotel" or "Resort".
_MIN_NAME_SCORE = 0.45

# Within this distance a geolocation match meaningfully boosts confidence; beyond it, no boost.
_GEO_BOOST_RADIUS_KM = 50.0
_GEO_BOOST_WEIGHT = 0.15


def _norm(s: Optional[str]) -> str:
    """Same normalization approach as hotel_matcher._norm (NFKC + whitespace collapse +
    casefold) - deliberately consistent across the app's matching modules."""
    if not s:
        return ""
    normalized = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", normalized).strip().lower()


def _name_score(query: str, candidate: str) -> float:
    return SequenceMatcher(None, _norm(query), _norm(candidate)).ratio()


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def find_candidates(
    hotel_name: str,
    index: List[Dict[str, Any]],
    country_code: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    limit: int = 8,
) -> List[Dict[str, Any]]:
    """
    Returns up to `limit` records from `index` (as produced by masterdata_store.load_index()),
    ranked by match confidence, each with a 'score' (0-1) and 'name_score'/'geo_km' attached for
    the UI to show its reasoning. Empty list if hotel_name is blank or index is empty - never
    raises, since this backs an interactive search box a human can retype at any time.
    """
    query = (hotel_name or "").strip()
    if not query or not index:
        return []

    country_filter = (country_code or "").strip().upper() or None
    have_geo = lat is not None and lon is not None

    scored = []
    for record in index:
        if not isinstance(record, dict):
            continue
        if country_filter and (record.get("countryCode") or "").strip().upper() != country_filter:
            continue

        name_score = _name_score(query, record.get("name") or "")
        if name_score < _MIN_NAME_SCORE:
            continue

        geo_km = None
        score = name_score
        if have_geo:
            geoloc = record.get("geolocation") or {}
            r_lat, r_lon = geoloc.get("latitude"), geoloc.get("longitude")
            if isinstance(r_lat, (int, float)) and isinstance(r_lon, (int, float)):
                geo_km = _haversine_km(lat, lon, r_lat, r_lon)
                if geo_km <= _GEO_BOOST_RADIUS_KM:
                    score = min(1.0, score + _GEO_BOOST_WEIGHT * (1 - geo_km / _GEO_BOOST_RADIUS_KM))

        scored.append({**record, "score": round(score, 4), "name_score": round(name_score, 4),
                        "geo_km": round(geo_km, 1) if geo_km is not None else None})

    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:limit]


def datasheet_to_masterdata_seed(datasheet: Dict[str, Any]) -> Dict[str, Any]:
    """
    Converts a raw GET /accommodations/{id}/datasheet response (IdeaHotelDataVO / this app's
    ApiStaticContentAccommodationDataSheetVO shape - both confirmed identical field-for-field via
    Swagger, 2026-09-06) into the small, flat seed dict the Hotel wizard actually consumes:
    already-hosted image URLs (Travel Compositor's own CDN - no R2 re-upload needed, unlike
    document-extracted images), a plain-text block for the extraction pipeline to fold in
    alongside the URL/document text, and geolocation for the geocoding-search default.
    """
    if not isinstance(datasheet, dict):
        return {"image_urls": [], "text_block": "", "geolocation": None, "name": None}

    image_urls = [img.get("url") for img in (datasheet.get("images") or [])
                  if isinstance(img, dict) and img.get("url")]

    lines = []
    name = datasheet.get("name")
    if name:
        lines.append(f"Hotel name: {name}")
    if datasheet.get("address"):
        lines.append(f"Address: {datasheet['address']}")
    if datasheet.get("phoneNumber"):
        lines.append(f"Phone: {datasheet['phoneNumber']}")
    if datasheet.get("chain"):
        lines.append(f"Chain: {datasheet['chain']}")
    if datasheet.get("description"):
        lines.append(f"Description: {datasheet['description']}")
    facilities = [
        (f.get("translations") or {}).get("EN") or next(iter((f.get("translations") or {}).values()), None)
        for f in (datasheet.get("accommodationFacilities") or [])
        if isinstance(f, dict)
    ]
    facilities = [f for f in facilities if f]
    if facilities:
        lines.append("Facilities: " + ", ".join(facilities))

    text_block = ("--- SOURCE: TRAVEL COMPOSITOR MASTER DATA ---\n" + "\n".join(lines)) if lines else ""

    return {
        "image_urls": list(dict.fromkeys(image_urls)),
        "text_block": text_block,
        "geolocation": datasheet.get("geolocation") or None,
        "name": name,
    }
