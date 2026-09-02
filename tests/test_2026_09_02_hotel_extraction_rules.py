"""Tests for the 9 confirmed product-owner decisions from the first real Hotel contract review
(claude/hotel-section-first-real-contract-2026-09-02.md, contracts: Bird Island Private Island
Villas, Six Senses Zil Pasyon, Four Seasons Resort Seychelles at Desroches Island), implemented
2026-09-02 as new "CONFIRMED PRODUCT-OWNER RULE" paragraphs inside HOTEL_EXTRACTION_SYSTEM_PROMPT
in ai_extractor.py.

These rules live entirely in prompt text handed to the extraction model - there is no separate
Python code path to unit-test for most of them, so these tests assert on the prompt's source text
directly (the same pattern used elsewhere in this codebase for prompt-only rules). This at least
guarantees: the rule text made it into the prompt, didn't get silently deleted/mangled by a later
edit, and states the correct decision (not its opposite) - which is the class of regression most
worth guarding against here, since a follow-up edit to a neighboring paragraph could easily nick
or duplicate one of these blocks.

Decisions covered (see the project doc for full context on each):
  1. Villa/room-type modeling - shared physical unit, sub-configuration inherits its count.
  2. Missing distribution_prices combos computed from base rate + per-bedroom/occupancy supplement.
  3. Long-stay/percentage discounts modeled as Offer - no code change (pre-existing mechanism);
     asserted here only as a non-regression check that the OFFERS section still supports it.
  4. Minimum-stay range -> always the HIGHER number.
  5. Per-room-category cancellation policy -> pick the one covering the most room categories.
  6. Request-only room categories -> units_quota=0 / units_on_request=<real count or 1, flagged>.
  7. Markup out of scope - no code change; asserted here only as a non-regression check that no
     markup logic was accidentally introduced into the hotel prompt.
  8. Promotion combination whitelist -> folded into the offer's own "name" field as text.
  9. Multiple child sub-bands -> union the age range; use the MORE EXPENSIVE stated price
     wherever a tiered child price must collapse into TC's single combined band.
"""
import ai_extractor as ax


def _prompt():
    return ax.HOTEL_EXTRACTION_SYSTEM_PROMPT


# ======================================================================
# Decision 9 - multi sub-band child ages: union range + more expensive price wins
# ======================================================================
def test_decision_9_child_age_union_rule_present():
    p = _prompt()
    assert "MULTIPLE STATED SUB-BANDS" in p
    assert "union every" in p.lower()
    assert "LOWEST of all the stated minimums" in p
    assert "HIGHEST of all the stated maximums" in p


def test_decision_9_more_expensive_price_wins():
    p = _prompt()
    assert "MULTIPLE SUB-BANDS PRICED DIFFERENTLY" in p
    assert "MORE EXPENSIVE" in p
    # must NOT tell the model to use the cheaper/average price for this case
    idx = p.index("MULTIPLE SUB-BANDS PRICED DIFFERENTLY")
    block = p[idx: idx + 1600]
    assert "never the cheaper" in block
    assert "never an average" in block


# ======================================================================
# Decision 4 - minimum stay range -> higher number
# ======================================================================
def test_decision_4_minimum_stay_range_uses_higher_number():
    p = _prompt()
    assert "A RANGE INSTEAD OF ONE NUMBER" in p
    idx = p.index("A RANGE INSTEAD OF ONE NUMBER")
    block = p[idx: idx + 700]
    assert "HIGHER" in block
    assert "not the lower" in block


# ======================================================================
# Decision 5 - per-room-category cancellation -> policy covering most room categories
# ======================================================================
def test_decision_5_cancellation_picks_policy_for_most_room_categories():
    p = _prompt()
    assert "DIFFERENT POLICIES PER ROOM CATEGORY" in p
    idx = p.index("DIFFERENT POLICIES PER ROOM CATEGORY")
    block = p[idx: idx + 700]
    assert "LARGEST number of" in block
    assert "do not attempt to merge or average" in block


# ======================================================================
# Decision 1 - shared physical unit room modeling
# ======================================================================
def test_decision_1_shared_physical_unit_rule_present():
    p = _prompt()
    assert "SHARED PHYSICAL UNITS" in p
    idx = p.index("SHARED PHYSICAL UNITS")
    block = p[idx: idx + 1200]
    assert "its own room_prices/units_quota" in block or "OWN independent" in block
    assert "use the physical unit's own stated count" in block


# ======================================================================
# Decision 2 - compute missing distribution_prices from base + per-bedroom supplement
# ======================================================================
def test_decision_2_base_plus_supplement_rule_present():
    p = _prompt()
    assert "BASE RATE PLUS PER-BEDROOM/PER-EXTRA-CAP SUPPLEMENT" in p
    idx = p.index("BASE RATE PLUS PER-BEDROOM/PER-EXTRA-CAP SUPPLEMENT")
    block = p[idx: idx + 1400]
    assert "still price_type \"DISTRIBUTION\"" in block
    assert "Compute the missing distribution_prices combos yourself" in block


# ======================================================================
# Decision 6 - request-only room categories
# ======================================================================
def test_decision_6_request_only_rooms_rule_present():
    p = _prompt()
    assert "SOLD ON REQUEST ONLY" in p
    idx = p.index("SOLD ON REQUEST ONLY")
    block = p[idx: idx + 900]
    assert "units_quota=0" in block
    assert "units_on_request=" in block
    assert "use 1 and note in the top-level description" in block
    assert "still be added to the system" in block.lower() or "must still be added" in block.lower()


# ======================================================================
# Decision 8 - promotion combination whitelist folded into offer name
# ======================================================================
def test_decision_8_combination_whitelist_folded_into_name():
    p = _prompt()
    assert "PROMOTION COMBINATION WHITELISTS" in p
    idx = p.index("PROMOTION COMBINATION WHITELISTS")
    block = p[idx: idx + 1000]
    assert "own separate offer entry" in block.lower() or "OWN separate offer entry" in block
    assert "\"name\"" in block
    assert "enforce or structurally encode the combination rule" in block


# ======================================================================
# Decisions 3 and 7 - non-regression: no new Promotion entity, no markup logic added
# ======================================================================
def test_decision_3_no_new_promotion_entity_introduced():
    p = _prompt()
    # offers/supplements are still the only discount/charge vehicle in the hotel prompt
    assert "OFFERS (discounts) and SUPPLEMENTS" in p
    assert "\"promotions\":" not in p


def test_decision_7_no_markup_field_introduced():
    p = _prompt()
    assert "markup" not in p.lower()


# ======================================================================
# Sanity: all 9 decision markers exist exactly once each (no accidental duplication
# from a later edit landing in the wrong place)
# ======================================================================
def test_all_nine_decision_markers_appear_exactly_once():
    p = _prompt()
    markers = [
        "MULTIPLE STATED SUB-BANDS",
        "MULTIPLE SUB-BANDS PRICED DIFFERENTLY",
        "A RANGE INSTEAD OF ONE NUMBER",
        "DIFFERENT POLICIES PER ROOM CATEGORY",
        "SHARED PHYSICAL UNITS",
        "BASE RATE PLUS PER-BEDROOM/PER-EXTRA-CAP SUPPLEMENT",
        "SOLD ON REQUEST ONLY",
        "PROMOTION COMBINATION WHITELISTS",
    ]
    for marker in markers:
        assert p.count(marker) == 1, f"expected exactly one occurrence of {marker!r}, found {p.count(marker)}"


def test_prompt_still_valid_and_compiles():
    # the prompt must still be a plain string built successfully at import time
    assert isinstance(_prompt(), str)
    assert len(_prompt()) > 1000
