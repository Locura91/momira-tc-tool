"""Regression tests for a real production failure (product owner, 2026-09-11, HRG-H1 -
Steigenberger Golf Resort El Gouna): after the room-publish fix landed, Phase 1 (hotel + rooms +
meal plans) published successfully, but every offer, every supplement, and the rate itself failed:

    Early Bird 20%/15%/10%: "createHotelOffer.offersVO.providerRoomCodes: Size must be between 1
    and 2147483647 ([]), createHotelOffer.offersVO.mealPlans: Size must be between 1 and
    2147483647 ([])"
    Compulsory Christmas/New Year Gala Dinner (BB/HB): "createHotelSupplement.supplementVO.
    providerRoomCodes: Size must be between 1 and 2147483647 ([])"
    Half Board/Club Package Supplement: "...providerRoomCodes: Size must be...([]), ...mealPlans:
    Size must be...([])"
    Winter 26-27 Contract Rates: "java.lang.IllegalArgumentException: Room price missing for
    distributions: 1 Ad. + 2 Ch., 2 Ad. + 1 Ch."

Root cause 1 (offers/supplements): ai_extractor.py's own documented convention is that an offer/
supplement naming NO specific room ("room_names": []) applies to every room, and analogously an
unnamed meal plan applies regardless of meal plan - exactly the common real-world case (the
contract's Early Bird applies to "All room category"; Gala Dinners/supplements name no room at
all). But build_hotel_offer_payloads/build_hotel_supplement_payloads sent that convention's empty
list straight to Travel Compositor as a literal empty JSON array, and Travel Compositor requires
both fields non-empty. Fixed: both builders now resolve an empty room_names/meal_plans list into
the hotel's ACTUAL full room-code list / meal-plan-type list (read from the just-published Phase 1
payload), via new helpers _resolve_offer_or_supplement_room_codes/_resolve_offer_or_supplement_
meal_plans. A NAMED room that fails to resolve (a real mismatch) is kept as an explicit error
rather than silently widening to "all rooms".

Root cause 2 (rates): a room's own "distributions" (its allowed occupancy combos, e.g. this
contract's Junior Suite: "2 AD +2 CH, or 2 AD+1 CH, or 1 AD+ 02 CH, or 03 AD", all priced at the
same "Triple" figure) can list MORE combos than extraction ends up pricing individually for a
season - Travel Compositor rejects the whole rate if any allowed combo has no price at all.
Fixed: build_hotel_rate_payloads now fills any such gap by reusing the price already given for
another combo with the SAME TOTAL occupancy (adults+children) - safe, since these tables are
genuinely structured by total headcount, not by the adult/child split - via the new
_fill_missing_distribution_prices helper. A gap that can't be filled this way (no same-total-pax
price to reuse) blocks just that rate with a clear, editable message instead of Travel
Compositor's raw Java exception text.
"""
from builder import (
    build_hotel_offer_payloads,
    build_hotel_supplement_payloads,
    build_hotel_rate_payloads,
    _fill_missing_distribution_prices,
)


# ======================================================================
# 1. Offers - room_names/meal_plans empty ("applies to everything") resolves to real full lists
# ======================================================================
def test_offer_with_no_named_rooms_gets_every_current_room_code():
    extracted = [{"name": "Early Bird 20%", "type": "PERCENT", "value": 20, "room_names": []}]
    room_map = {"Deluxe Room": "HRG-H1-DELUXEROOM-1", "Premium Room": "HRG-H1-PREMIUMROOM-2",
                "Junior Suite": "HRG-H1-JUNIORSUITE-3"}
    results = build_hotel_offer_payloads(extracted, room_map, existing_hotel_snapshot=None,
                                          hotel_meal_plan_types=["ROOM_ONLY", "BED_AND_BREAKFAST"])
    assert results[0]["offer_error"] is None
    codes = results[0]["offer_payload"]["providerRoomCodes"]
    assert set(codes) == set(room_map.values())
    assert len(codes) == 3


def test_offer_with_no_meal_plans_gets_every_current_meal_plan_type():
    extracted = [{"name": "Early Bird 20%", "type": "PERCENT", "value": 20, "room_names": []}]
    room_map = {"Deluxe Room": "HRG-H1-DELUXEROOM-1"}
    results = build_hotel_offer_payloads(extracted, room_map, existing_hotel_snapshot=None,
                                          hotel_meal_plan_types=["ROOM_ONLY", "BED_AND_BREAKFAST", "HALF_BOARD"])
    assert results[0]["offer_error"] is None
    assert set(results[0]["offer_payload"]["mealPlans"]) == {"ROOM_ONLY", "BED_AND_BREAKFAST", "HALF_BOARD"}


def test_offer_naming_a_specific_room_still_only_applies_to_that_room():
    extracted = [{"name": "Suite-only deal", "type": "PERCENT", "value": 20, "room_names": ["Junior Suite"]}]
    room_map = {"Deluxe Room": "HRG-H1-DELUXEROOM-1", "Junior Suite": "HRG-H1-JUNIORSUITE-3"}
    results = build_hotel_offer_payloads(extracted, room_map, existing_hotel_snapshot=None,
                                          hotel_meal_plan_types=["ROOM_ONLY"])
    assert results[0]["offer_error"] is None
    assert results[0]["offer_payload"]["providerRoomCodes"] == ["HRG-H1-JUNIORSUITE-3"]


def test_offer_naming_a_room_that_never_resolves_is_an_explicit_error_not_a_silent_all_rooms():
    extracted = [{"name": "Typo'd room deal", "type": "PERCENT", "value": 20, "room_names": ["Jr Suite"]}]
    room_map = {"Deluxe Room": "HRG-H1-DELUXEROOM-1", "Junior Suite": "HRG-H1-JUNIORSUITE-3"}
    results = build_hotel_offer_payloads(extracted, room_map, existing_hotel_snapshot=None,
                                          hotel_meal_plan_types=["ROOM_ONLY"])
    assert results[0]["offer_payload"] is None
    assert "Jr Suite" in results[0]["offer_error"]
    assert "none matched" in results[0]["offer_error"]


def test_hotel_meal_plan_types_defaults_to_empty_when_not_passed_backward_compat():
    # Older call sites (tests, or code not yet updated) that don't pass hotel_meal_plan_types
    # must not crash - they just get an empty mealPlans list, same shape as before this fix for
    # an offer that specifies no meal plan of its own.
    extracted = [{"name": "Legacy call", "type": "PERCENT", "value": 10, "room_names": []}]
    results = build_hotel_offer_payloads(extracted, {"Deluxe Room": "CODE-1"})
    assert results[0]["offer_error"] is None
    assert results[0]["offer_payload"]["mealPlans"] == []


# ======================================================================
# 2. Supplements - same empty-array fix, plus the apply-basis-required rule still works
# ======================================================================
def test_supplement_with_no_named_room_gets_every_current_room_code():
    extracted = [{"name": "Compulsory Christmas Gala Dinner (BB)", "type": "ABSOLUTE", "value": 140,
                  "apply": "PER_STAY_PERSON", "room_names": [], "meal_plans": ["Bed and Breakfast"]}]
    room_map = {"Deluxe Room": "HRG-H1-DELUXEROOM-1", "Premium Room": "HRG-H1-PREMIUMROOM-2"}
    results = build_hotel_supplement_payloads(extracted, room_map, existing_hotel_snapshot=None,
                                               hotel_meal_plan_types=["ROOM_ONLY", "BED_AND_BREAKFAST", "HALF_BOARD"])
    assert results[0]["supplement_error"] is None
    assert set(results[0]["supplement_payload"]["providerRoomCodes"]) == set(room_map.values())
    # A named meal plan hint narrows to just that plan, not every plan on the hotel.
    assert results[0]["supplement_payload"]["mealPlans"] == ["BED_AND_BREAKFAST"]


def test_supplement_meal_plan_hint_is_mapped_through_the_shared_meal_plan_mapper():
    extracted = [{"name": "Half Board Supplement", "type": "ABSOLUTE", "value": 30,
                  "apply": "PER_NIGHT_PERSON", "room_names": [], "meal_plans": ["Half board"]}]
    results = build_hotel_supplement_payloads(extracted, {"Deluxe Room": "CODE-1"}, existing_hotel_snapshot=None,
                                               hotel_meal_plan_types=["ROOM_ONLY", "HALF_BOARD"])
    assert results[0]["supplement_payload"]["mealPlans"] == ["HALF_BOARD"]


def test_supplement_still_requires_an_explicit_apply_basis():
    # CONFIRMED PRODUCT-OWNER RULE, unaffected by this fix - still blocks rather than guesses.
    extracted = [{"name": "Ambiguous fee", "type": "ABSOLUTE", "value": 10, "apply": "", "room_names": []}]
    results = build_hotel_supplement_payloads(extracted, {"Deluxe Room": "CODE-1"}, existing_hotel_snapshot=None,
                                               hotel_meal_plan_types=["ROOM_ONLY"])
    assert results[0]["supplement_payload"] is None
    assert "charging basis" in results[0]["supplement_error"]


# ======================================================================
# 3. Rates - missing distribution-price gap filled by same-total-pax reuse, or blocked clearly
# ======================================================================
def test_fill_missing_distribution_prices_reuses_the_same_total_pax_price():
    # Junior Suite: 4 allowed combos sharing one "Triple" price (131), but only 2 got extracted.
    existing = [{"adults": 2, "children": 2, "amount": 131.0}, {"adults": 3, "children": 0, "amount": 131.0}]
    allowed = [{"adults": 2, "children": 2}, {"adults": 2, "children": 1},
               {"adults": 1, "children": 2}, {"adults": 3, "children": 0}]
    filled, still_missing, notes = _fill_missing_distribution_prices(existing, allowed)
    assert still_missing == []
    filled_keys = {(p["adults"], p["children"]): p["amount"] for p in filled}
    # Both gaps (2 Ad + 1 Ch, 1 Ad + 2 Ch) share total pax 3 with "3 Ad + 0 Ch" -> reused 131.0.
    assert filled_keys[(2, 1)] == 131.0
    assert filled_keys[(1, 2)] == 131.0
    assert len(notes) == 2


def test_fill_missing_distribution_prices_leaves_a_true_gap_when_no_same_pax_price_exists():
    existing = [{"adults": 1, "children": 0, "amount": 100.0}]
    allowed = [{"adults": 1, "children": 0}, {"adults": 4, "children": 0}]
    filled, still_missing, notes = _fill_missing_distribution_prices(existing, allowed)
    assert still_missing == ["4 Ad. + 0 Ch."]
    assert notes == []


def test_build_hotel_rate_payloads_fills_gap_and_still_publishes_the_rate():
    room_map = {"Junior Suite": "HRG-H1-JUNIORSUITE-3"}
    room_distributions = {"Junior Suite": [{"adults": 2, "children": 2}, {"adults": 2, "children": 1},
                                            {"adults": 1, "children": 2}, {"adults": 3, "children": 0}]}
    extracted_rates = [{
        "name": "Winter 26-27 Contract Rates",
        "seasons": [{
            "name": "Medium", "date_ranges": [{"start": "2026-11-01", "end": "2026-11-30"}],
            "room_prices": [{
                "room_name": "Junior Suite",
                "distribution_prices": [{"adults": 2, "children": 2, "amount": 131.0},
                                         {"adults": 3, "children": 0, "amount": 131.0}],
            }],
        }],
    }]
    results = build_hotel_rate_payloads(extracted_rates, room_map, {}, {},
                                         room_name_to_distributions=room_distributions)
    assert results[0]["rate_error"] is None
    assert results[0]["rate_payload"] is not None
    assert len(results[0]["rate_warnings"]) == 1
    room_price = results[0]["rate_payload"]["seasons"][0]["seasonRoomPrices"][0]
    combos = {(p["adults"], p["children"]) for p in room_price["distributionPrices"]}
    assert combos == {(2, 2), (2, 1), (1, 2), (3, 0)}


def test_build_hotel_rate_payloads_blocks_the_rate_when_a_gap_cannot_be_filled():
    room_map = {"Junior Suite": "HRG-H1-JUNIORSUITE-3"}
    room_distributions = {"Junior Suite": [{"adults": 1, "children": 0}, {"adults": 4, "children": 0}]}
    extracted_rates = [{
        "name": "Winter 26-27 Contract Rates",
        "seasons": [{
            "name": "Medium", "date_ranges": [{"start": "2026-11-01", "end": "2026-11-30"}],
            "room_prices": [{
                "room_name": "Junior Suite",
                "distribution_prices": [{"adults": 1, "children": 0, "amount": 100.0}],
            }],
        }],
    }]
    results = build_hotel_rate_payloads(extracted_rates, room_map, {}, {},
                                         room_name_to_distributions=room_distributions)
    assert results[0]["rate_payload"] is None
    assert "4 Ad. + 0 Ch." in results[0]["rate_error"]
    assert results[0]["rate_name"] == "Winter 26-27 Contract Rates"


def test_room_name_to_distributions_defaults_to_empty_when_not_passed_backward_compat():
    room_map = {"Deluxe Room": "CODE-1"}
    extracted_rates = [{
        "name": "Rate", "seasons": [{"name": "Season", "date_ranges": [{"start": "2026-01-01", "end": "2026-01-31"}],
                                      "room_prices": [{"room_name": "Deluxe Room",
                                                        "distribution_prices": [{"adults": 1, "children": 0, "amount": 50.0}]}]}],
    }]
    results = build_hotel_rate_payloads(extracted_rates, room_map, {}, {})
    assert results[0]["rate_error"] is None
    assert results[0]["rate_warnings"] == []
