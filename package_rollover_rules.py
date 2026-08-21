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

CONFIRMED (product owner, 2026-08-21) — DYNAMIC PACKAGES (no fixed calendar of departures):
"the upload shall be available on all days. Only limitations are holiday package IDs with a
closed tour...when a closed tour (like nile cruises) start on a specific day." Package
56355178's empty calendar (see above) turns out to be the NORMAL case for a dynamic package,
not a one-off Idea quirk — most packages can depart any day, so there's no calendar list to pick
a "candidate" from at all. propose_dynamic_rollover() below handles this: it proposes
today + TARGET_LEAD_DAYS directly rather than searching for a matching calendar entry, EXCEPT
for three confirmed blackout windows the human doesn't want a departure landing inside
("no departure should be set during christmas time, no departure New Years Eve...and no
departure during Easter time"):
  - Christmas/New Year: Dec 24 - Jan 1 inclusive (CONFIRMED exact range, 2026-08-21) - New
    Year's Eve (Dec 31) falls inside this window, so it isn't a separate rule.
  - Easter: the 10 days centered on Easter Sunday (CONFIRMED width, 2026-08-21), computed as
    Easter Sunday - 5 days through Easter Sunday + 4 days. Easter Sunday itself is calculated
    with the standard Anonymous Gregorian / Meeus-Jones-Butcher algorithm (CONFIRMED: "calculate
    it automatically" rather than a manually maintained per-year list), so this needs no yearly
    maintenance.
If the raw target date falls in a blackout window, it's shifted forward day by day until it
lands outside every window - never shifted earlier than the confirmed ~4-month lead time.
Packages built around a FIXED-departure component (a closed tour/cruise with specific weekly
departure days) are NOT auto-detected — CONFIRMED (product owner, 2026-08-21): "Human checks
manually, tool just warns." package_rollover_tool.py always shows a caution note on this path
telling the human to verify the proposed date against any embedded fixed-schedule component
themselves in Travel Compositor before applying anything.

CONFIRMED (product owner, 2026-08-21) — PICKING AMONG CALENDAR CANDIDATES: "the tool is testing
minimum 3 different days and...the cheapest offer has to be reviewed by humans." For packages
that DO have a calendar of fixed departure dates, propose_rollover() now recommends the CHEAPEST
qualifying candidate overall (not the one closest to the ~4-month target — that's now only a
tie-breaker when two qualifying candidates share the same lowest price, kept for predictability).
MIN_CANDIDATES_TO_TEST (3) is a visibility flag, not a hard requirement — if the calendar has
fewer than 3 future departures to consider, `below_minimum_test_coverage` is set on the result so
the human reviewing sees a caution rather than silently trusting a thin sample. Still open: a
genuinely DYNAMIC package (see propose_dynamic_rollover above) has no calendar of priced dates to
test 3 of in the first place — testing 3 candidate days there needs a re-quote/availability
endpoint this codebase hasn't identified yet (see the "package-auto-rollover-rules" project note).

CONFIRMED (product owner, 2026-08-21) — SCOPE, WHICH PACKAGES ACTUALLY NEED THIS TOOL: "Holiday
Packages that are coming from a ClosedTour and the ID includes only a closed tour...do not need
an update. But if a ClosedTour has dynamic flights and pre and post program included, then the
holiday package ID needs an update." So a package ID that's PURELY a ClosedTour (e.g. a
fixed-schedule cruise, no other components) is out of scope for this tool — its departure is
managed the normal way, through the ClosedTour itself. A package built around a ClosedTour PLUS
dynamic flights/pre-post program DOES need this tool. There's no confirmed field yet to tell
these apart automatically from the API response, so package_rollover_tool.py surfaces this as
guidance for the human, not an automatic filter. CONFIRMED (product owner, 2026-08-21): "for test
reasons, we can focus on the beginning for holiday packages without closedtours, so it is
available on all days" — i.e. start testing against fully DYNAMIC packages (propose_dynamic_
rollover's path), not the ClosedTour-calendar path, for now.
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
MIN_CANDIDATES_TO_TEST = 3       # CONFIRMED (product owner, 2026-08-21) — a visibility flag, not
                                  # a hard block; see module docstring's "PICKING AMONG CALENDAR
                                  # CANDIDATES" note.

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

    Picking rule (CONFIRMED, product owner, 2026-08-21): among candidates that pass BOTH the
    rating gate (if a rating was found at all — see 'rating_unverifiable' below) and the price
    cap (if current_price is known — see 'price_unverifiable'), pick the CHEAPEST one overall.
    If two or more tie on price, the closest to today + TARGET_LEAD_DAYS breaks the tie. If no
    qualifying candidate has a known price to compare, falls back to closest-to-target (the old
    rule), since "cheapest" can't be judged without a price.
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

    below_min = len(dated) < MIN_CANDIDATES_TO_TEST

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
            "candidates_tested": len(dated),
            "below_minimum_test_coverage": below_min,
            "rejected": rejected,
        }

    priced_qualifying = [c for c in qualifying if c["price"] is not None]
    if priced_qualifying:
        min_price = min(c["price"] for c in priced_qualifying)
        tied = [c for c in priced_qualifying if c["price"] == min_price]
        best = min(tied, key=lambda c: abs((c["date"] - target_date).days))
        picked_by = "cheapest_price"
    else:
        best = min(qualifying, key=lambda c: abs((c["date"] - target_date).days))
        picked_by = "closest_to_target_fallback"

    return {
        "status": "proposed",
        "proposed": best,
        "target_date": target_date,
        "max_price": max_price,
        "rating_unverifiable": best["rating"] is None,
        "price_unverifiable": max_price is not None and best["price"] is None,
        "alternatives_considered": len(qualifying) - 1,
        "qualifying": qualifying,
        "rejected": rejected,
        "candidates_tested": len(dated),
        "below_minimum_test_coverage": below_min,
        "picked_by": picked_by,
    }


# ---- Dynamic packages (no fixed calendar of departures) ---------------------------------
# CONFIRMED (product owner, 2026-08-21) — see the module docstring's "DYNAMIC PACKAGES" note.

CHRISTMAS_BLACKOUT_START = (12, 24)   # Dec 24 - CONFIRMED (product owner, 2026-08-21)
CHRISTMAS_BLACKOUT_END = (1, 1)       # Jan 1 (following year) - CONFIRMED (product owner, 2026-08-21)
EASTER_WINDOW_DAYS_BEFORE = 5         # CONFIRMED width (product owner, 2026-08-21): "10 days
EASTER_WINDOW_DAYS_AFTER = 4          # centered on Easter" = Easter Sunday - 5 .. Easter Sunday + 4


def easter_sunday(year: int) -> date:
    """Easter Sunday (Gregorian calendar) for a given year, via the standard Anonymous
    Gregorian / Meeus-Jones-Butcher algorithm. CONFIRMED (product owner, 2026-08-21):
    "calculate it automatically" rather than maintaining a per-year list by hand."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def is_blackout_date(d: date):
    """Returns (True, reason) if `d` falls inside a CONFIRMED no-departure blackout window -
    Christmas/New Year (Dec 24 - Jan 1) or the 10 days centered on Easter Sunday. Returns
    (False, None) otherwise. See the module docstring's "DYNAMIC PACKAGES" note for the exact
    confirmed ranges."""
    if (d.month == CHRISTMAS_BLACKOUT_START[0] and d.day >= CHRISTMAS_BLACKOUT_START[1]) or \
       (d.month == CHRISTMAS_BLACKOUT_END[0] and d.day <= CHRISTMAS_BLACKOUT_END[1]):
        return True, "Christmas / New Year blackout (Dec 24 - Jan 1)"
    for year in (d.year - 1, d.year, d.year + 1):
        sunday = easter_sunday(year)
        window_start = sunday - timedelta(days=EASTER_WINDOW_DAYS_BEFORE)
        window_end = sunday + timedelta(days=EASTER_WINDOW_DAYS_AFTER)
        if window_start <= d <= window_end:
            return True, f"Easter blackout ({window_start.isoformat()} to {window_end.isoformat()})"
    return False, None


def nearest_non_blackout_date(target: date, max_search_days: int = 60) -> date:
    """Starting at `target`, walks FORWARD day by day until a date outside every blackout
    window is found. Walks forward only (never earlier) so the proposed departure never moves
    ahead of the confirmed ~4-month lead time - it only ever gets pushed later, past the
    blackout window it landed in."""
    d = target
    for _ in range(max_search_days):
        blackout, _ = is_blackout_date(d)
        if not blackout:
            return d
        d = d + timedelta(days=1)
    return d  # pragma: no cover - confirmed blackout windows are well under 60 days wide


def propose_dynamic_rollover(today: date) -> Dict[str, Any]:
    """For a package with NO usable calendar of fixed departure dates - CONFIRMED (product
    owner, 2026-08-21) to be the normal case for a dynamic package, not a data gap. Proposes
    today + TARGET_LEAD_DAYS directly (skipping the candidate-matching in propose_rollover()
    entirely, since there's no calendar to match against), shifted later if that date falls in
    a confirmed blackout window.

    Does NOT check price or hotel rating - a dynamic package has no calendar entry to read
    those from; a real quote for the proposed date has to be checked in Travel Compositor.
    Does NOT know whether this package contains a fixed-departure component (e.g. a cruise) -
    CONFIRMED (product owner, 2026-08-21): "Human checks manually, tool just warns" - see
    package_rollover_tool.py's caution note on this path.
    """
    raw_target = today + timedelta(days=TARGET_LEAD_DAYS)
    blackout, reason = is_blackout_date(raw_target)
    proposed_date = nearest_non_blackout_date(raw_target) if blackout else raw_target
    return {
        "status": "proposed_dynamic",
        "target_date": raw_target,
        "proposed_date": proposed_date,
        "shifted_for_blackout": blackout,
        "blackout_reason": reason,
    }
