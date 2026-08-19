"""
package_rollover_rules.py — CONFIRMED PRODUCT-OWNER RULES (2026-08-19) for the Package
Rollover prototype: automatically finding a replacement departure date for a Holiday Package
whose current departure is about to close, within fixed, deterministic rules (same
"business rules live in code, not in a prompt" pattern as trip_search_rules.py).

CONFIRMED RULES (product owner, 2026-08-19):
  - A package's current departure counts as "closed" once it's under 14 days away.
  - A replacement departure should land roughly 4 months ahead of today (~122 days).
  - The replacement's hotel review score must be 8+ (assumed /10 scale — see the
    "package-auto-rollover-rules" project note; not yet confirmed against a real response).
  - The replacement's price must not exceed the CURRENTLY PUBLISHED LIVE price by more than
    3.5%.
  - Nothing here ever calls Travel Compositor's PUT endpoint itself — this module only picks
    a candidate and explains why; applying the change is a separate, human-approved step (see
    package_rollover_tool.py).

HEURISTIC FIELD-FINDING (the part that ISN'T confirmed yet): this codebase has never seen a
real GET /package/{micrositeId}/{holidayPackageId} or GET /package/calendar/... response, so
the exact field names for a departure's date/price/hotel-rating are unknown. find_candidates()
below scans each calendar entry's own keys, case-insensitively, for common travel-API naming
patterns instead of one fixed name — and always reports back the EXACT key name it matched
next to the value, so a human looking at the tool's output can immediately see (and correct) a
wrong guess. This is deliberately not trusted to be right on the first real package; it exists
to let the FIRST real Package ID Chris tries give us a real example to confirm or fix the
field names against, the same "flagged, not guessed" posture as the rest of this codebase.

CONFIRMED AGAINST A REAL RESPONSE (package 56355178, 2026-08-19): Travel Compositor represents
money as an OBJECT, not a flat number — {"amount": 1459.56, "currency": "EUR"} — never a plain
1459.56. The original heuristic didn't know this and, matching "totalPrice" before
"pricePerPerson" AND handing the whole {"amount":..., "currency":...} object to a generic
string-based number parser, produced 291912.0 for a real package whose actual per-person price
was 1459.56 (rounds to the 1460 Chris reported) — the parser was reading the Python
str()-repr of the dict itself, not the amount inside it. Fixed by _extract_amount() below,
which unwraps the {"amount": ...} shape first, and by re-ordering _PRICE_KEY_GROUPS to prefer
"pricePerPerson" over "totalPrice" (the rule is stated per person: "1460 Euro per Person").
Also confirmed: a hotel's rating is a LIST of per-source objects —
[{"score": "8.6", "source": "Booking.com", "numReviews": 3279}, {"score": "4.5",
"source": "Tripadvisor", ...}, {"score": "7.2", "source": "Expedia", ...}] — not one flat
number, and different sources use different scales (Tripadvisor's 4.5 is out of 5, not /10).
_extract_rating() picks PREFERRED_RATING_SOURCE — Booking.com by default, since it had by far
the most reviews in the one real example seen and its scale matches the assumed /10 — but this
default is NOT yet confirmed as the source the product owner wants used; see the
"package-auto-rollover-rules" project note.
"""
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import date_format

# No MODULE_BUILD constant here, deliberately — same convention as trip_search_rules.py:
# this module isn't imported directly by app.py (only via package_rollover_tool.py, which
# IS tracked), so it's an "extra" file delivered whenever it's touched rather than tracked
# in app.py's partial-deploy check.

CLOSED_WITHIN_DAYS = 14          # CONFIRMED (product owner, 2026-08-19)
TARGET_LEAD_DAYS = 122           # ~4 months — CONFIRMED (product owner, 2026-08-19)
MIN_HOTEL_RATING = 8             # assumed /10 scale — see module docstring
MAX_PRICE_INCREASE_PCT = 3.5     # CONFIRMED (product owner, 2026-08-19), vs. current live price
# NOT yet confirmed with the product owner — see module docstring's 2026-08-19 note.
PREFERRED_RATING_SOURCE = "Booking.com"

# Ordered most-specific-first: the first hint group with ANY match in an entry's keys wins.
# "priceperperson" before "totalprice" — CONFIRMED against a real response (package 56355178,
# 2026-08-19): the rule is stated per person ("1460 Euro per Person"), and totalPrice is the
# whole party's total, not a per-person figure.
_DATE_KEY_GROUPS = [["departuredate"], ["startdate"], ["date"]]
_PRICE_KEY_GROUPS = [["priceperperson"], ["totalprice"], ["price"], ["amount"], ["cost"]]
_RATING_KEY_GROUPS = [["reviewscore"], ["hotelrating"], ["rating"], ["review"], ["score"]]


def _find_field(entry: Dict[str, Any], hint_groups: List[List[str]]):
    """Returns (matched_key, raw_value) for the first hint group that matches any key in
    `entry`, case-insensitively, or (None, None) if nothing matches. Only looks at this one
    entry's own top-level keys — nested shapes would need a real example to design against."""
    if not isinstance(entry, dict):
        return None, None
    lowered = {k.lower(): (k, v) for k, v in entry.items()}
    for hints in hint_groups:
        for key_l, (real_key, value) in lowered.items():
            if any(h in key_l for h in hints):
                return real_key, value
    return None, None


def _extract_amount(value) -> Optional[float]:
    """Unwraps Travel Compositor's real money shape — {"amount": 1459.56, "currency": "EUR"}
    — before falling back to the generic string parser. CONFIRMED real bug (package 56355178,
    2026-08-19) without this: parse_number_loose(the whole dict) read Python's str()-repr of
    the dict itself and produced 291912.0 for an actual amount of 1459.56 / 2919.12."""
    if isinstance(value, dict) and "amount" in value:
        return parse_number_loose(value.get("amount"))
    return parse_number_loose(value)


def _extract_rating(value, preferred_source: str = PREFERRED_RATING_SOURCE) -> Optional[float]:
    """Unwraps Travel Compositor's real hotel-rating shape — a LIST of per-source objects,
    e.g. [{"score": "8.6", "source": "Booking.com", "numReviews": 3279}, ...] — CONFIRMED
    against package 56355178, 2026-08-19. Different sources use different scales (Tripadvisor
    is out of 5, not /10), so this picks ONE source (preferred_source) rather than averaging
    or taking the first — averaging across mismatched scales would produce a meaningless
    number. Falls back to the first entry if the preferred source isn't present, and still
    handles a plain number/money-object for safety in case some other response shape is flat."""
    if isinstance(value, list):
        preferred = next((v for v in value if isinstance(v, dict)
                          and preferred_source.lower() in str(v.get("source", "")).lower()), None)
        chosen = preferred or (value[0] if value and isinstance(value[0], dict) else None)
        if chosen is None:
            return None
        return parse_number_loose(chosen.get("score"))
    return _extract_amount(value)


def parse_date_loose(value) -> Optional[date]:
    """Best-effort: a date string in ISO (optionally with a time part, e.g.
    '2026-12-01T00:00:00') or DD/MM/YYYY becomes a date object; anything else is None rather
    than raising, since this is scanning API data of an unconfirmed shape."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    text = text.split("T")[0].split(" ")[0]
    iso = date_format.to_iso_date(text)
    try:
        y, m, d = iso.split("-")
        return date(int(y), int(m), int(d))
    except (ValueError, AttributeError):
        return None


def parse_number_loose(value) -> Optional[float]:
    """Best-effort numeric parse — handles a plain number, a numeric string, or a string with
    thousands separators/currency symbols (e.g. '1.234,56 EUR') by stripping anything that
    isn't a digit, dot, comma, or minus, then trying dot-decimal and comma-decimal in turn."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    cleaned = "".join(ch for ch in text if ch.isdigit() or ch in ".,-")
    if not cleaned:
        return None
    for candidate in (cleaned, cleaned.replace(".", "").replace(",", ".")):
        try:
            return float(candidate)
        except ValueError:
            continue
    return None


def find_price(entry: Dict[str, Any]):
    """Public wrapper around the price heuristic for a single dict (e.g. a package info or
    day-to-day response) — returns (matched_field_name, parsed_price) so a caller outside
    this module (package_rollover_tool.py) doesn't need to reach into the private
    _find_field/_PRICE_KEY_GROUPS internals directly. Unwraps TC's real {"amount": ...} money
    shape via _extract_amount — see its docstring for the real bug this fixes."""
    key, raw = _find_field(entry, _PRICE_KEY_GROUPS)
    return key, _extract_amount(raw)


def find_departure_date(entry: Dict[str, Any]):
    """Same as find_price, for the CURRENT departure date on a package info/day-to-day
    response — (matched_field_name, parsed_date). Used to show the human the actual old-vs-new
    comparison (not just the new proposal on its own)."""
    key, raw = _find_field(entry, _DATE_KEY_GROUPS)
    return key, parse_date_loose(raw)


def is_departure_closed(departure_date: date, today: date) -> bool:
    """CONFIRMED RULE: under 14 days away counts as closed and needs rolling."""
    if departure_date is None:
        return False
    return (departure_date - today).days < CLOSED_WITHIN_DAYS


def find_candidates(calendar_response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Walks a GET /package/calendar/... response looking for a list of departure-like
    entries, and normalizes each one to {"date", "price", "rating", "date_field", "price_field",
    "rating_field", "raw"} using the heuristic field-finder above. `*_field` is the exact
    source key matched (or None), so a human reviewing this can see exactly what was guessed.

    Response shape is unconfirmed, so this looks for the first list-of-dicts found anywhere at
    the top level (a direct list response, or a list under a key like "departures"/"calendar"/
    "dates"/"result") rather than assuming one fixed key name.
    """
    entries: List[Dict[str, Any]] = []
    if isinstance(calendar_response, list):
        entries = [e for e in calendar_response if isinstance(e, dict)]
    elif isinstance(calendar_response, dict):
        for value in calendar_response.values():
            if isinstance(value, list) and value and all(isinstance(e, dict) for e in value):
                entries = value
                break

    candidates = []
    for entry in entries:
        date_key, date_raw = _find_field(entry, _DATE_KEY_GROUPS)
        price_key, price_raw = _find_field(entry, _PRICE_KEY_GROUPS)
        rating_key, rating_raw = _find_field(entry, _RATING_KEY_GROUPS)
        candidates.append({
            "date": parse_date_loose(date_raw),
            "date_field": date_key,
            "price": _extract_amount(price_raw),
            "price_field": price_key,
            "rating": _extract_rating(rating_raw),
            "rating_field": rating_key,
            "raw": entry,
        })
    return candidates


def propose_rollover(candidates: List[Dict[str, Any]], current_price: Optional[float],
                     today: date) -> Dict[str, Any]:
    """Applies the confirmed rules to a list of find_candidates() output and returns a dict
    describing the best replacement departure found (or why none qualified), for a human to
    review. Never calls any API — pure decision logic over data already fetched.

    Picking rule: among candidates that pass BOTH the rating gate (if a rating was found at
    all — see 'rating_unverifiable' below) and the price cap (if current_price is known — see
    'price_unverifiable'), pick the one whose date is closest to today + TARGET_LEAD_DAYS.
    """
    target_date = today + timedelta(days=TARGET_LEAD_DAYS)
    # Rounded to cents before comparison — a plain float multiply (100 * 1.035) lands on
    # 103.49999999999999, which would reject a price of exactly 103.50 on a floating-point
    # technicality even though it's exactly at the confirmed 3.5% cap.
    max_price = (round(current_price * (1 + MAX_PRICE_INCREASE_PCT / 100), 2)
                if current_price is not None else None)

    dated = [c for c in candidates if c["date"] is not None and c["date"] > today]
    if not dated:
        return {"status": "no_dated_candidates", "candidates_seen": len(candidates)}

    qualifying = []
    rejected = []
    for c in dated:
        reasons = []
        if c["rating"] is not None and c["rating"] < MIN_HOTEL_RATING:
            reasons.append(f"rating {c['rating']} < {MIN_HOTEL_RATING}")
        if max_price is not None and c["price"] is not None and c["price"] > max_price:
            reasons.append(f"price {c['price']} > cap {round(max_price, 2)}")
        if reasons:
            rejected.append({**c, "rejected_because": reasons})
        else:
            qualifying.append(c)

    if not qualifying:
        return {
            "status": "no_qualifying_candidates",
            "candidates_seen": len(candidates),
            "rejected": rejected,
        }

    best = min(qualifying, key=lambda c: abs((c["date"] - target_date).days))

    return {
        "status": "proposed",
        "proposed": best,
        "target_date": target_date,
        "max_price": max_price,
        "rating_unverifiable": best["rating"] is None,
        "price_unverifiable": max_price is not None and best["price"] is None,
        "alternatives_considered": len(qualifying) - 1,
        "rejected": rejected,
    }
