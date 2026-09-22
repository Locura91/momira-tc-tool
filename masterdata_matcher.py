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

MODULE_BUILD = "2026-09-22-outreach-contacted-before-second-column"

import math
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional
from text_normalize import normalize_name as _norm

# CONFIRMED PRODUCT-OWNER RULE (2026-09-16, after seeing "Siwa Shali Resort" search results
# include Sharm-area resorts 900+ km away at 68-70% "match confidence"): "The name must be
# closer searched to the one i type in, the destination cannot be further than 150 km." Two
# separate tightenings below implement this - see _score_records and find_candidates.

# Below this, a candidate is noise rather than a real suggestion - keeps the picker short and
# avoids showing a human a "match" that shares nothing but a common word like "Hotel" or
# "Resort". Raised from 0.45 (2026-09-16): plain difflib similarity alone gave a hotel like
# "Sharm Resort" a 69% score against a search for "Siwa Shali Resort" - both are "<place>
# Resort", which is enough shared text for SequenceMatcher to call it close, without the two
# hotels having anything real in common. See _SUBSTRING_BOOST below for why this rise doesn't
# also break the legitimate case of typing only part of a hotel's real name.
_MIN_NAME_SCORE = 0.72

# A human typing a SHORTENED version of the real name (e.g. "Steigenberger" for "Steigenberger
# Golf Resort El Gouna") is a normal, wanted search, not noise - but its plain SequenceMatcher
# ratio can sit as low as ~0.55, well under _MIN_NAME_SCORE above. Boosted only when the
# (normalized) query is fully contained in the candidate name or vice versa - a real substring
# relationship, unlike two names that merely share common words/letters in different places.
_SUBSTRING_BOOST = 0.20

# Within this distance a geolocation match meaningfully boosts confidence.
_GEO_BOOST_RADIUS_KM = 50.0
_GEO_BOOST_WEIGHT = 0.15

# CONFIRMED HARD RULE (product owner, 2026-09-16, verbatim: "the destination cannot be further
# than 150 km"): when the caller supplies a destination lat/lon AND the candidate record has its
# own geolocation to compare against, anything farther than this is EXCLUDED outright - not just
# scored lower. A hotel 900km from the place a human typed is never the right answer regardless
# of how its name happens to score, so no name similarity can outweigh this. Only applies when a
# real distance was actually computed - a record with no geolocation of its own can't be judged
# this way and is left to name matching alone, same as when the caller gives no destination.
_GEO_HARD_LIMIT_KM = 150.0

# A country-filtered top match at or above this is trusted outright - no need to also search
# outside the filter. Below it, the country filter itself might be the problem (see the
# fallback pass in find_candidates) rather than the hotel genuinely being a weak match.
_STRONG_MATCH_THRESHOLD = 0.80


def _name_score(query: str, candidate: str) -> float:
    """Plain similarity, boosted when one name is fully contained in the other (normalized) -
    see _SUBSTRING_BOOST's own docstring for why that specific case earns a boost and a
    coincidental "both contain the word Resort" case does not."""
    norm_query, norm_candidate = _norm(query), _norm(candidate)
    score = SequenceMatcher(None, norm_query, norm_candidate).ratio()
    if norm_query and norm_candidate and (norm_query in norm_candidate or norm_candidate in norm_query):
        score = min(1.0, score + _SUBSTRING_BOOST)
    return score


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _score_records(query: str, records: List[Dict[str, Any]], lat: Optional[float],
                    lon: Optional[float], have_geo: bool, flag_country_mismatch: bool = False
                    ) -> List[Dict[str, Any]]:
    """Scores one pool of records against `query` (+ optional geolocation). Shared by
    find_candidates' primary (country-filtered) and fallback (whole-index) passes below, so the
    same name/geo scoring logic is never duplicated between them."""
    scored = []
    for record in records:
        if not isinstance(record, dict):
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
                if geo_km > _GEO_HARD_LIMIT_KM:
                    # CONFIRMED HARD RULE - see _GEO_HARD_LIMIT_KM's own comment. Not a score
                    # penalty: this candidate is dropped entirely, no name score can outweigh it.
                    continue
                if geo_km <= _GEO_BOOST_RADIUS_KM:
                    score = min(1.0, score + _GEO_BOOST_WEIGHT * (1 - geo_km / _GEO_BOOST_RADIUS_KM))

        entry = {**record, "score": round(score, 4), "name_score": round(name_score, 4),
                 "geo_km": round(geo_km, 1) if geo_km is not None else None}
        if flag_country_mismatch:
            entry["country_mismatch"] = True
        scored.append(entry)
    return scored


def find_candidates(
    hotel_name: str,
    index: List[Dict[str, Any]],
    country_code: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    limit: int = 8,
    strict_country: bool = False,
) -> List[Dict[str, Any]]:
    """
    Returns up to `limit` records from `index` (as produced by masterdata_store.load_index()),
    ranked by match confidence, each with a 'score' (0-1) and 'name_score'/'geo_km' attached for
    the UI to show its reasoning. Empty list if hotel_name is blank or index is empty - never
    raises, since this backs an interactive search box a human can retype at any time.

    CONFIRMED REAL BUG (product owner, 2026-09-16, real example): searching "Siwa Shali Resort"
    with country code EG returned 8 candidates - none of them the actual hotel, even though
    Travel Compositor's own "Automap with master" search found it immediately by name. The
    country filter used to be a HARD exclude on the record's own countryCode field, which can be
    missing/blank/wrong on a specific Travel Compositor master-data record even when the hotel
    itself is genuinely in that country - a data-quality gap on Travel Compositor's side, but one
    that was silently hiding an otherwise-perfect name match rather than surfacing it, which is
    exactly the "a human decides" philosophy this module's own docstring commits to. Fixed by
    treating the country filter as a first, fast PASS rather than an absolute one (default,
    strict_country=False): when its best result isn't convincingly strong (or it finds nothing at
    all), a second pass searches the WHOLE index by name (+ geo boost/penalty) too, and any good
    match found that way is merged in - flagged with country_mismatch=True so the UI can show a
    human why a hotel from an unexpected country showed up, rather than hiding it from them
    entirely.

    strict_country=True (CONFIRMED PRODUCT-OWNER CHOICE, 2026-09-16, same day, for the new
    destination-confirmed search step in app_helpers._render_hotel_masterdata_step): once a
    human has explicitly confirmed a REAL Travel Compositor destination (not just typed a free-
    text country guess), country_code carries far more authority than an unconfirmed 2-letter
    code ever did - the product owner explicitly chose a hard filter for that case over keeping
    this fallback. Skips the whole-index fallback pass entirely; only records inside
    country_filter are ever considered. Has no effect when country_code is blank.
    """
    query = (hotel_name or "").strip()
    if not query or not index:
        return []

    country_filter = (country_code or "").strip().upper() or None
    have_geo = lat is not None and lon is not None

    if country_filter:
        primary_pool = [r for r in index if isinstance(r, dict)
                        and (r.get("countryCode") or "").strip().upper() == country_filter]
    else:
        primary_pool = index

    scored = _score_records(query, primary_pool, lat, lon, have_geo)
    scored.sort(key=lambda r: r["score"], reverse=True)

    if (not strict_country and country_filter
            and (not scored or scored[0]["score"] < _STRONG_MATCH_THRESHOLD)):
        already_ids = {r.get("id") for r in scored}
        fallback_pool = [r for r in index if isinstance(r, dict) and r.get("id") not in already_ids
                        and (r.get("countryCode") or "").strip().upper() != country_filter]
        fallback_scored = _score_records(query, fallback_pool, lat, lon, have_geo, flag_country_mismatch=True)
        scored.extend(fallback_scored)
        scored.sort(key=lambda r: r["score"], reverse=True)

    return scored[:limit]


def find_by_raw_substring(text: str, index: List[Dict[str, Any]], limit: int = 50) -> List[Dict[str, Any]]:
    """Diagnostic tool, not a matching path a normal search uses: a plain, UNSCORED, UNFILTERED
    case-insensitive substring search over the whole local index's own 'name' field - no
    _MIN_NAME_SCORE threshold, no country filter, no geo. Answers one narrow question directly:
    "is a hotel with this text anywhere in our local copy at all, and if so, what does Travel
    Compositor's own record actually say for its country/name?" - rather than leaving that as a
    guess when find_candidates comes back empty or wrong.

    CONFIRMED NEED (product owner, 2026-09-16): after find_candidates' country-filter fallback
    fix still didn't surface "Siwa Shali Resort" (a real hotel confirmed to exist in Travel
    Compositor's own "Automap with master" search), the open question became whether the record
    is missing from our LOCAL COPY entirely (a sync completeness/coverage gap - a different bug
    from the one already fixed) versus present but scoring too low for some other reason. This
    function exists so that question has a direct answer instead of another guess."""
    query = (text or "").strip().lower()
    if not query or not index:
        return []
    out = []
    for record in index:
        if not isinstance(record, dict):
            continue
        if query in (record.get("name") or "").strip().lower():
            out.append(record)
            if len(out) >= limit:
                break
    return out


def find_by_giata_id(giata_id: str, index: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Exact-match lookup by GIATA id, for a human who already has one written on the contract
    or knows it by heart, rather than searching by name.

    CONFIRMED REAL REQUEST (product owner, 2026-09-11): "Could we search it with Giatacodes if
    we enter it or just by manually adding the name of the hotel in the first step". Name search
    (find_candidates above) exists specifically BECAUSE contracts from local partners never
    carry a GIATA id (see this module's own docstring) - but that's a statement about what
    CONTRACTS carry, not about what a human operator might separately know or look up. When a
    GIATA id is available, it's authoritative - unlike name matching, this never needs a human
    to confirm which of several candidates is right, because a GIATA id can only belong to one
    property.

    A GIATA id is compared as a normalized string (Travel Compositor's own data can carry it as
    either an int or a str depending on record - confirmed by index_meta's own record shape) so
    "1234" matches whether the stored value is the int 1234 or the string "1234". Returns every
    exact match (normally zero or one, but never assumes exactly one - a data quality issue in
    the master index should surface as multiple results for a human to pick between, not crash
    or silently pick one). Each result carries score=1.0 (an exact id match, not a fuzzy guess)
    so the UI can render it identically to a find_candidates result."""
    query = str(giata_id or "").strip()
    if not query or not index:
        return []
    out = []
    for record in index:
        if not isinstance(record, dict):
            continue
        rid = record.get("giataId")
        if rid is None:
            continue
        if str(rid).strip() == query:
            out.append({**record, "score": 1.0, "name_score": None, "geo_km": None})
    return out


def datasheet_to_masterdata_seed(datasheet: Dict[str, Any]) -> Dict[str, Any]:
    """
    Converts a raw GET /accommodations/{id}/datasheet response (IdeaHotelDataVO / this app's
    ApiStaticContentAccommodationDataSheetVO shape - both confirmed identical field-for-field via
    Swagger, 2026-09-06) into the small, flat seed dict the Hotel wizard actually consumes:
    already-hosted image URLs (Travel Compositor's own CDN - no R2 re-upload needed, unlike
    document-extracted images), a plain-text block for the extraction pipeline to fold in
    alongside the URL/document text, and geolocation for the geocoding-search default.

    ALSO CARRIES THE MASTER RECORD'S OWN IDENTITY (`accommodation_id`, `giata_id`) as of
    2026-09-13. Before this, everything identifying WHICH master record a human had picked was
    dropped the moment its content had been copied out - the datasheet is fetched BY the
    accommodation id, and then that id was thrown away.

    That matters because of a confirmed API limitation (product owner, 2026-09-13: "we must make
    sure that Automap with master is also set, so the hotel is not a duplicate in the travel
    compositor surface"). Travel Compositor's "Automap with master" - the option on its own "New
    hotel using master data" screen that stops a contract appearing as a duplicate property -
    CANNOT be set through the API at all. Confirmed against the real Swagger for both relevant
    sections: `ContractHotelDetailedVO` (the POST/PUT /hotel/{supplierId} request body) carries
    19 fields and not one of them references a master accommodation, and the entire "Web content
    - Accommodations" section is read-only - six GET endpoints, no POST, no PUT, nothing with
    "map" in it. So the mapping is unavoidably a human step in Travel Compositor's back office.
    The only thing this app can do is make that step short and hard to forget, which needs
    exactly these two ids kept rather than discarded (see hotel_automap.py for where they go)."""
    if not isinstance(datasheet, dict):
        return {"image_urls": [], "text_block": "", "geolocation": None, "name": None,
                "accommodation_id": None, "giata_id": None}

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

    # Normalized to str/int-or-None rather than passed through raw: the master index carries a
    # GIATA id as either an int or a str depending on record (same quirk find_by_giata_id already
    # handles), and these two values are read back later by a human retyping them into Travel
    # Compositor's own search box, where "1234" and "1234.0" are not the same thing.
    accommodation_id = datasheet.get("id")
    giata_id = datasheet.get("giataId")
    return {
        "image_urls": list(dict.fromkeys(image_urls)),
        "text_block": text_block,
        "geolocation": datasheet.get("geolocation") or None,
        "name": name,
        "accommodation_id": str(accommodation_id).strip() if accommodation_id is not None else None,
        "giata_id": str(giata_id).strip() if giata_id is not None else None,
    }
