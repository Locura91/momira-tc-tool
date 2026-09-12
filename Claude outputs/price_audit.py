"""
price_audit.py

Hotel Price Audit tool (product owner, 2026-09-12, HRG-H1 - Steigenberger Golf Resort El Gouna).

THE PROBLEM THIS SOLVES: the product owner re-reviewed a hotel contract already run through the
app and found real mistakes in the extracted PRICES (room rates, meal plan supplements, early
birds/offers). The main hotel extraction (ai_extractor.extract_hotel_data) is one large
single-pass prompt covering everything a hotel contract can contain - property details, images,
rooms, meal plans, offers, supplements, and rates - and while the app is still in its "learning
phase" for how real supplier contracts are structured (every supplier formats these differently),
prices are the one category of mistake that actually costs Momira Travel money. A wrong hotel
description is a nuisance; a wrong room rate or early-bird percentage is a real bill, potentially
repeated across every booking made against it before anyone notices.

CONFIRMED PRODUCT-OWNER RULE: this tool is explicitly meant to be TEMPORARY SCAFFOLDING, not a
permanent feature - "this tool shall help the app learn in the beginning phase how hotel contracts
are built and should help to learn until we can remove the extra check." It exists to surface
extraction mistakes on the numbers specifically (not property details) while real contracts are
still teaching the main extractor's prompt how the different supplier formats work, and is
expected to be retired once that main prompt reliably gets prices right without a second check.

HOW IT WORKS (two independent pieces, deliberately kept separate from the main extraction so the
SAME mistake in that first pass can't just get repeated here):

  1. run_hotel_price_audit(raw_text, model=...) - a SECOND, independent Claude call using
     HOTEL_PRICE_AUDIT_SYSTEM_PROMPT, which is told to ignore every non-numeric field (hotel name,
     address, description, images, cancellation policy) and extract ONLY price-bearing facts:
     every room price per season/occupancy, every meal plan supplement amount, every offer's
     type+value+dates, every supplement's type+value+dates - each carrying a verbatim quote from
     the source so a human can jump straight to the right sentence in the contract. Being a
     narrower, single-purpose prompt, it is a genuinely independent second opinion, not just the
     same extraction run twice.

  2. compare_price_audit_to_extraction(audit_facts, hotel_data) - a PURE function, no API call,
     fully unit-testable - fuzzy-matches each audited price fact against the corresponding item
     already sitting in the main extraction's `hotel_data` (rooms/rates/meal_plans/offers/
     supplements) and returns one finding per audited fact: "match" (numbers agree), "mismatch"
     (found the item, but the number disagrees - the dangerous case, since this is exactly what
     costs real money), or "not_found" (the audited price doesn't appear in the extraction at
     all - a missed room/season/offer/supplement, or a name mismatch too large to match).

Name matching here is deliberately LOOSE (difflib similarity), unlike hotel_matcher.py's Travel
Compositor-facing matching, which is deliberately STRICT (see that module's own docstring on why
it never does fuzzy matching - risk of silently merging two different LIVE records). This tool
never writes anything or merges any live record; it only surfaces information for a human to look
at, so a loose match that leads a person to double-check the right item is a feature here, while
the same looseness on the write side would be a real hazard there.
"""

# Stamped on every delivery - see platform_store.py's own header for why this convention exists
# (a partial deploy that updated every other file but this one would go undetected otherwise).
MODULE_BUILD = "2026-09-12-hotel-price-audit"

import re
import unicodedata
import difflib
from typing import Any, Dict, List, Optional


def _safe_float(value, fallback=0.0):
    try:
        if value is None or value == "":
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _safe_int(value, fallback=0):
    try:
        if value is None or value == "":
            return fallback
        return int(round(float(value)))
    except (TypeError, ValueError):
        return fallback


def _norm_name(s: Optional[str]) -> str:
    """Same normalization as hotel_matcher._norm (case/whitespace/Unicode-insensitive) - kept as
    its own copy here rather than importing hotel_matcher, since this module's matching is
    deliberately a different (looser) policy than that one - see this file's own docstring."""
    if not s:
        return ""
    normalized = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", normalized).strip().lower()


def _loose_name_match(a: Optional[str], b: Optional[str], threshold: float = 0.6) -> bool:
    """Loose similarity match for HUMAN REVIEW only - never used to write/merge any live record
    (see module docstring). An exact normalized match always counts; otherwise a difflib
    similarity ratio above `threshold` counts as "probably the same item"."""
    an, bn = _norm_name(a), _norm_name(b)
    if not an or not bn:
        return False
    if an == bn:
        return True
    return difflib.SequenceMatcher(None, an, bn).ratio() >= threshold


# ----------------------------------------------------------------------
# Piece 1: the independent, price-only extraction pass
# ----------------------------------------------------------------------
HOTEL_PRICE_AUDIT_SYSTEM_PROMPT = """You are auditing a hotel contract document for a tour operator (Momira Travel). Your ONLY
job is to extract every PRICE-BEARING NUMBER in the document as a flat, literal list - you are not building the
final catalog record, just double-checking numbers a separate process already extracted. IGNORE completely:
hotel name/address/description/images/category/chain/cancellation policy/children's ages/any other
non-numeric field. If it doesn't have a currency amount or percentage attached to it, do not extract it.

Extract EVERY instance of each of these four kinds of price fact, even if the same number appears to repeat
across seasons/rooms/occupancies - completeness matters more than brevity here, since this is a cross-check,
not the final record. For EVERY fact, include a `quote` field: the exact, verbatim sentence or table row (or as
close as reasonably possible) from the source document that states this number, so a human reviewer can find
it again immediately.

1. ROOM PRICES - one entry per room type x season x occupancy combination the document prices:
   {"kind": "room_price", "room_name": "", "season_label": "" (whatever the document calls this season/period,
    e.g. "Winter 26-27", "01 Nov - 20 Dec", or "" if there's only one undivided price period),
    "adults": <int>, "children": <int>, "amount": <number>, "currency": "", "quote": ""}

2. MEAL PLAN SUPPLEMENT PRICES - one entry per distinct amount a meal plan add-on charges:
   {"kind": "meal_plan_price", "meal_plan_name": "" (e.g. "Half Board", "All Inclusive"),
    "which": "base" (1st adult) | "extra_adult" (each additional adult) | "child",
    "amount": <number>, "currency": "", "quote": ""}

3. OFFERS (discounts, including every Early Bird tier) - one entry per distinct discount:
   {"kind": "offer", "name": "" (short label, e.g. "Early Bird 20%"), "type": "PERCENT" | "ABSOLUTE",
    "value": <number>, "travel_start": "YYYY-MM-DD" or "", "travel_end": "YYYY-MM-DD" or "",
    "booking_start": "YYYY-MM-DD" or "", "booking_end": "YYYY-MM-DD" or "", "currency": "", "quote": ""}
   travel_start/end = the stay/travel dates the discount applies to. booking_start/end = the window in which
   the booking itself must be MADE (e.g. "book by 31-Jul-26"). Leave whichever pair the document doesn't state
   as empty strings - do not invent dates.

4. SUPPLEMENTS (mandatory extra charges, e.g. resort fees, compulsory gala dinners, club packages) - one entry
   per distinct charge:
   {"kind": "supplement", "name": "" (short label), "type": "PERCENT" | "ABSOLUTE", "value": <number>,
    "currency": "", "quote": ""}

Numbers must be copied EXACTLY as stated - never round, never convert currency, never compute a total that
isn't already stated as one number in the source. If a price table uses one shared column for several
occupancy combinations (e.g. one "Triple" price covering "2 AD+1 CH", "1 AD+2 CH", and "3 AD"), extract ONE
room_price entry per combination, all carrying that same amount - do not collapse them into one entry.

Return your findings as the price_facts array. If the document genuinely states no price-bearing numbers for
one of the four kinds, that kind simply contributes no entries - never invent a placeholder."""


def run_hotel_price_audit(raw_text: str, model: str = "claude-sonnet-5") -> Dict[str, Any]:
    """Runs the independent price-only extraction pass over the SAME raw document text the main
    hotel extraction (ai_extractor.extract_hotel_data) was given, and returns
    {"price_facts": [...]}. Reuses ai_extractor's shared Claude-calling plumbing (_call_claude,
    _required_keys_schema) rather than duplicating the API-calling code - imported lazily so this
    module can be imported (and its pure compare_price_audit_to_extraction function unit-tested)
    without needing an ANTHROPIC_API_KEY configured at import time."""
    from ai_extractor import _call_claude, _required_keys_schema

    defaults = {"price_facts": []}
    data = _call_claude(HOTEL_PRICE_AUDIT_SYSTEM_PROMPT, raw_text, model, max_tokens=8192,
                         input_schema=_required_keys_schema(defaults))
    if not isinstance(data.get("price_facts"), list):
        data["price_facts"] = []
    return data


# ----------------------------------------------------------------------
# Piece 2: pure comparison logic (no API call - fully unit-testable)
# ----------------------------------------------------------------------
_AMOUNT_TOLERANCE = 0.01


def _extracted_room_prices(hotel_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for rate in (hotel_data or {}).get("rates") or []:
        for season in (rate or {}).get("seasons") or []:
            season_name = (season or {}).get("name")
            for rp in (season or {}).get("room_prices") or []:
                room_name = (rp or {}).get("room_name")
                for dp in (rp or {}).get("distribution_prices") or []:
                    if not isinstance(dp, dict):
                        continue
                    out.append({
                        "room_name": room_name,
                        "season_name": season_name,
                        "adults": _safe_int(dp.get("adults", 1), fallback=1),
                        "children": _safe_int(dp.get("children", 0)),
                        "amount": _safe_float(dp.get("amount", 0)),
                    })
    return out


def _check_room_price_fact(fact: Dict[str, Any], extracted: List[Dict[str, Any]]) -> Dict[str, Any]:
    room_name = fact.get("room_name")
    adults = _safe_int(fact.get("adults", 1), fallback=1)
    children = _safe_int(fact.get("children", 0))
    amount = _safe_float(fact.get("amount", 0))
    label = f"Room price: '{room_name or '(unnamed)'}', {adults} Ad. + {children} Ch."

    name_matches = [rp for rp in extracted if _loose_name_match(rp["room_name"], room_name)]
    occupancy_matches = [rp for rp in name_matches if rp["adults"] == adults and rp["children"] == children]
    if occupancy_matches:
        best = min(occupancy_matches, key=lambda rp: abs(rp["amount"] - amount))
        if abs(best["amount"] - amount) <= _AMOUNT_TOLERANCE:
            return {"status": "match", "kind": "room_price", "label": label,
                    "message": f"{label}: contract states {amount}, extraction has {best['amount']} - matches.",
                    "quote": fact.get("quote")}
        return {"status": "mismatch", "kind": "room_price", "label": label,
                "message": f"{label}: contract states {amount}, but the extraction has {best['amount']} for "
                           f"this exact room/occupancy - check this price before publishing.",
                "quote": fact.get("quote")}
    if name_matches:
        return {"status": "not_found", "kind": "room_price", "label": label,
                "message": f"{label}: room '{room_name}' is in the extraction, but has no price for this "
                           f"exact occupancy (contract states {amount}) - check the room's distributions/"
                           f"room_prices.",
                "quote": fact.get("quote")}
    return {"status": "not_found", "kind": "room_price", "label": label,
            "message": f"{label}: contract states {amount}, but room '{room_name}' wasn't found anywhere "
                       f"in the extraction.",
            "quote": fact.get("quote")}


def _check_meal_plan_price_fact(fact: Dict[str, Any], meal_plans: List[Dict[str, Any]]) -> Dict[str, Any]:
    mp_name = fact.get("meal_plan_name")
    which = (fact.get("which") or "base").strip().lower()
    amount = _safe_float(fact.get("amount", 0))
    label = f"Meal plan price: '{mp_name or '(unnamed)'}' ({which})"

    candidates = [mp for mp in (meal_plans or []) if isinstance(mp, dict)
                  and _loose_name_match(mp.get("meal_plan_hint"), mp_name)]
    if not candidates:
        return {"status": "not_found", "kind": "meal_plan_price", "label": label,
                "message": f"{label}: contract states {amount}, but meal plan '{mp_name}' wasn't found "
                           f"anywhere in the extraction.",
                "quote": fact.get("quote")}

    best_diff = None
    best_value = None
    for mp in candidates:
        if which == "base":
            value = _safe_float(mp.get("base_price", 0))
        elif which == "extra_adult":
            adult_prices = mp.get("adult_prices") or []
            value = _safe_float(adult_prices[0]) if adult_prices else None
        else:  # "child"
            child_prices = mp.get("child_prices") or []
            value = _safe_float(child_prices[0]) if child_prices else None
        if value is None:
            continue
        diff = abs(value - amount)
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_value = value

    if best_value is None:
        return {"status": "not_found", "kind": "meal_plan_price", "label": label,
                "message": f"{label}: contract states {amount}, but the extraction's meal plan "
                           f"'{mp_name}' has no '{which}' price to compare against.",
                "quote": fact.get("quote")}
    if best_diff <= _AMOUNT_TOLERANCE:
        return {"status": "match", "kind": "meal_plan_price", "label": label,
                "message": f"{label}: contract states {amount}, extraction has {best_value} - matches.",
                "quote": fact.get("quote")}
    return {"status": "mismatch", "kind": "meal_plan_price", "label": label,
            "message": f"{label}: contract states {amount}, but the extraction has {best_value} - "
                       f"check this price before publishing.",
            "quote": fact.get("quote")}


def _check_offer_or_supplement_fact(fact: Dict[str, Any], items: List[Dict[str, Any]], kind: str) -> Dict[str, Any]:
    name = fact.get("name")
    fact_type = (fact.get("type") or "").strip().upper()
    value = _safe_float(fact.get("value", 0))
    kind_label = "Offer" if kind == "offer" else "Supplement"
    label = f"{kind_label}: '{name or '(unnamed)'}'"

    def _item_names(item):
        return [n.get("description") for n in (item or {}).get("names") or [] if isinstance(n, dict)]

    name_matches = [item for item in (items or []) if isinstance(item, dict)
                    and any(_loose_name_match(n, name) for n in _item_names(item))]
    same_type = [item for item in name_matches if (item.get("type") or "").strip().upper() == fact_type] \
        or name_matches
    if same_type:
        best = min(same_type, key=lambda item: abs(_safe_float(item.get("value", 0)) - value))
        best_value = _safe_float(best.get("value", 0))
        if abs(best_value - value) <= _AMOUNT_TOLERANCE:
            return {"status": "match", "kind": kind, "label": label,
                    "message": f"{label}: contract states {value}, extraction has {best_value} - matches.",
                    "quote": fact.get("quote")}
        return {"status": "mismatch", "kind": kind, "label": label,
                "message": f"{label}: contract states {value}, but the extraction has {best_value} - "
                           f"check this value before publishing.",
                "quote": fact.get("quote")}

    # No name match at all - fall back to "does ANY item of this kind share the same type+value",
    # purely to distinguish "probably just named differently" from "genuinely missing".
    value_matches = [item for item in (items or []) if isinstance(item, dict)
                      and (item.get("type") or "").strip().upper() == fact_type
                      and abs(_safe_float(item.get("value", 0)) - value) <= _AMOUNT_TOLERANCE]
    if value_matches:
        return {"status": "not_found", "kind": kind, "label": label,
                "message": f"{label}: contract states {fact_type} {value} - a {kind} with that exact type/"
                           f"value exists in the extraction under a different name; double-check it's the "
                           f"same one and not a genuinely separate {kind} that's missing.",
                "quote": fact.get("quote")}
    return {"status": "not_found", "kind": kind, "label": label,
            "message": f"{label}: contract states {fact_type} {value}, but nothing matching this "
                       f"{kind} was found in the extraction at all.",
            "quote": fact.get("quote")}


def compare_price_audit_to_extraction(audit_facts: List[Dict[str, Any]],
                                       hotel_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The core of this tool: for every audited price fact, decides whether the main extraction
    (`hotel_data`, the same dict driving the Hotel Step 4 review screen) already has a matching
    figure. Returns one finding per fact: {"status": "match"|"mismatch"|"not_found", "kind": str,
    "label": str, "message": str, "quote": str|None}.

    "mismatch" is the dangerous case this tool exists for - the item is there, but the NUMBER
    disagrees with the contract, which is exactly the kind of error that costs Momira Travel
    money if it reaches a live rate. "not_found" covers both a genuinely missed item and a name
    difference too large for the loose matcher to bridge - either way, worth a human glance.
    "match" is included too (not just filtered out) so a human reviewing this list sees full
    coverage, not just an unexplained absence of complaints."""
    hotel_data = hotel_data or {}
    extracted_room_prices = _extracted_room_prices(hotel_data)
    meal_plans = hotel_data.get("meal_plans") or []
    offers = hotel_data.get("offers") or []
    supplements = hotel_data.get("supplements") or []

    findings = []
    for fact in audit_facts or []:
        if not isinstance(fact, dict):
            continue
        kind = fact.get("kind")
        if kind == "room_price":
            findings.append(_check_room_price_fact(fact, extracted_room_prices))
        elif kind == "meal_plan_price":
            findings.append(_check_meal_plan_price_fact(fact, meal_plans))
        elif kind == "offer":
            findings.append(_check_offer_or_supplement_fact(fact, offers, "offer"))
        elif kind == "supplement":
            findings.append(_check_offer_or_supplement_fact(fact, supplements, "supplement"))
        # An unrecognized kind is silently skipped rather than raising - a forward-compatibility
        # choice, not a swallowed error: the audit prompt above only ever emits these four kinds,
        # so this only matters if the prompt itself is extended later without updating this list.
    return findings


def summarize_findings(findings: List[Dict[str, Any]]) -> Dict[str, int]:
    """Small helper for the UI: counts findings by status, so a summary line ("3 mismatches, 2
    missing, 14 verified") can be shown before the human expands the full list."""
    summary = {"match": 0, "mismatch": 0, "not_found": 0}
    for f in findings or []:
        status = (f or {}).get("status")
        if status in summary:
            summary[status] += 1
    return summary
