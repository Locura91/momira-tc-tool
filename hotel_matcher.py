"""
hotel_matcher.py

Unlike Transfer/Transport, a Hotel's own identity is trivial to recognize:
providerCode is HUMAN-ASSIGNED (confirmed real example: "CAI-H1"), not
Travel Compositor-generated, so there's no fuzzy/app-tracked matching needed
at the hotel level at all - just GET /hotel/{supplierId}/{providerCode}
directly and check whether it exists.

What DOES need matching, because Travel Compositor auto-generates opaque
identifiers for everything nested under a hotel, is recognizing:
  1. WHICH EXISTING ROOM a document's room corresponds to - a room's own
     providerCode is a system-generated "AUTO_..." string (confirmed real:
     "I don't set any other AUTO code to it") - matched by room NAME.
  2. WHICH EXISTING RATE a document's rate-group corresponds to - matched by
     rate NAME.
  3. WHICH EXISTING SEASON within a rate a document's season corresponds to -
     matched by season NAME first, falling back to date-range OVERLAP
     (mirrors transport_matcher.match_bracket_to_existing_option()'s
     overlap fallback) since season names in real data look like arbitrary
     human labels ("Season AUTEO EXAMPLE") that could plausibly change
     between rate refreshes even when the underlying date range is the same.

No persistent local JSON store is needed here (unlike transfer_matcher.py/
transport_matcher.py) - a single GET /hotel/{supplierId}/{providerCode}
already returns the FULL nested picture (rooms, rates, seasons,
seasonRoomPrices, offers, supplements, stopSales all at once), so matching
can always be done live against a fresh GET rather than needing an app-side
memory of past uploads.
"""
from typing import List, Optional


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def match_room_by_name(room_name: str, existing_rooms: List[dict]) -> Optional[dict]:
    """Finds the existing room (a real ContractRoomVO dict, as returned nested inside
    GET /hotel/{supplierId}/{providerCode}) whose name matches, case/whitespace-insensitively.
    Returns None if this is a genuinely new room name not seen before."""
    target = _norm(room_name)
    if not target:
        return None
    for room in existing_rooms or []:
        if isinstance(room, dict) and _norm(room.get("name")) == target:
            return room
    return None


def match_rate_by_name(rate_name: str, existing_rates: List[dict]) -> Optional[dict]:
    """Finds the existing rate (a real ContractHotelRateVO dict) whose name matches. Returns
    None if this is a genuinely new rate group not seen before."""
    target = _norm(rate_name)
    if not target:
        return None
    for rate in existing_rates or []:
        if isinstance(rate, dict) and _norm(rate.get("name")) == target:
            return rate
    return None


def _date_ranges_overlap(ranges_a: List[dict], ranges_b: List[dict]) -> bool:
    """True if ANY range in A overlaps ANY range in B (ISO 'YYYY-MM-DD' strings compare
    correctly as plain strings, no date parsing needed)."""
    for a in ranges_a or []:
        a_start, a_end = a.get("start"), a.get("end")
        if not a_start or not a_end:
            continue
        for b in ranges_b or []:
            b_start, b_end = b.get("start"), b.get("end")
            if not b_start or not b_end:
                continue
            if a_start <= b_end and b_start <= a_end:
                return True
    return False


def match_season_to_existing(season_name: str, date_ranges: List[dict], existing_seasons: List[dict]) -> Optional[dict]:
    """Finds the existing season (a real ContractHotelSeasonVO dict) a fresh document's season
    corresponds to. Prefers an exact NAME match; falls back to date-range OVERLAP (mirrors
    transport_matcher.match_bracket_to_existing_option()'s bracket-overlap fallback) since season
    names in real data ("Season AUTEO EXAMPLE") look like arbitrary human labels that could
    change between rate refreshes even when the underlying date range is unchanged. Returns None
    if this is a genuinely new season."""
    target = _norm(season_name)
    if target:
        for season in existing_seasons or []:
            if isinstance(season, dict) and _norm(season.get("name")) == target:
                return season

    for season in existing_seasons or []:
        if isinstance(season, dict) and _date_ranges_overlap(date_ranges, season.get("dateRanges") or []):
            return season
    return None


def match_offer_or_supplement_by_name(item_name: str, existing_items: List[dict]) -> Optional[dict]:
    """Light dedup helper for Offers/Supplements (both confirmed CREATE-ONLY, no PUT endpoint) -
    finds an existing offer/supplement whose first English name matches, purely to avoid
    re-creating an identical-looking one within the same run/upload. NOT a true update path -
    see ContractHotelOffersVO's docstring in schemas.py for why offers/supplements are always
    freshly created rather than update-matched (they're inherently date-bounded and self-expire,
    same reasoning confirmed for Rates)."""
    target = _norm(item_name)
    if not target:
        return None
    for item in existing_items or []:
        if not isinstance(item, dict):
            continue
        for n in item.get("names") or []:
            if isinstance(n, dict) and _norm(n.get("description")) == target:
                return item
    return None