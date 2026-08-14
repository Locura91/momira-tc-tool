"""Tests for price_refresh.bracket_price_for - the "what does this existing bracket cost now"
lookup, including the minimum-party-size solo bracket rule.

CONFIRMED REAL BUG (product owner, real document: "HRG Airport to El Quseir", "Private Transfer
p.p. valid for (Min.2 pax) in Vehicle" priced at 32 - the live 1-pax bracket should become 64
(32*2), the minimum-party total, but was proposed unchanged at 32). Root cause: the solo-bracket
multiplication only ran when no exact bracket match existed, and the AI sometimes still emitted
a min_pax=1 entry carrying the raw per-person number, which satisfied the exact-match check
first and skipped the multiplication. See bracket_price_for's docstring for the full story.
"""
from price_refresh import bracket_price_for


def make_finding(brackets, minimum_pax=1, found=True):
    return {"found": found, "brackets": brackets, "minimum_pax": minimum_pax,
            "matched_row": "", "currency": "", "confidence": "high", "note": ""}


def test_solo_bracket_multiplied_when_ai_correctly_omits_a_1pax_entry():
    finding = make_finding([{"min_pax": 2, "max_pax": 9, "price": 32.0,
                             "child_price": None, "infant_price": None}], minimum_pax=2)
    price = bracket_price_for(finding, min_pax=1, max_pax=1, minimum_pax=2)
    assert price == 64.0  # 32 * 2, the real reported case


def test_solo_bracket_still_multiplied_when_ai_wrongly_includes_a_raw_1pax_entry():
    # This is the exact bug: the AI included BOTH the real bracket AND a spurious min_pax=1
    # entry carrying the unmultiplied per-person price. The fix must not trust the spurious
    # entry directly.
    finding = make_finding([
        {"min_pax": 1, "max_pax": 1, "price": 32.0, "child_price": None, "infant_price": None},
        {"min_pax": 2, "max_pax": 9, "price": 32.0, "child_price": None, "infant_price": None},
    ], minimum_pax=2)
    price = bracket_price_for(finding, min_pax=1, max_pax=1, minimum_pax=2)
    assert price == 64.0


def test_no_minimum_party_size_means_exact_1pax_match_is_trusted_directly():
    finding = make_finding([{"min_pax": 1, "max_pax": 1, "price": 18.0,
                             "child_price": None, "infant_price": None}], minimum_pax=1)
    price = bracket_price_for(finding, min_pax=1, max_pax=1, minimum_pax=1)
    assert price == 18.0  # no multiplication - 1 pax is genuinely priced on its own


def test_non_solo_bracket_unaffected_by_minimum_party_rule():
    # A wider existing bracket (e.g. 1-4 pax, the DEFAULT rate) is not the solo target and must
    # not be multiplied just because a minimum party size applies elsewhere on the route.
    finding = make_finding([{"min_pax": 2, "max_pax": 9, "price": 9.0,
                             "child_price": None, "infant_price": None}], minimum_pax=2)
    price = bracket_price_for(finding, min_pax=1, max_pax=4, minimum_pax=2)
    assert price == 9.0


def test_solo_bracket_falls_back_to_any_multi_pax_bracket_when_minimum_not_separately_priced():
    finding = make_finding([{"min_pax": 3, "max_pax": 9, "price": 20.0,
                             "child_price": None, "infant_price": None}], minimum_pax=2)
    price = bracket_price_for(finding, min_pax=1, max_pax=1, minimum_pax=2)
    assert price == 40.0  # falls back to the only real bracket (min_pax=3) * minimum_pax=2


def test_not_found_returns_none():
    finding = make_finding([], found=False)
    assert bracket_price_for(finding, 1, 1, 2) is None
