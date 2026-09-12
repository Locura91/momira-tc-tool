"""Regression tests for a real production failure (product owner, 2026-09-12, HRG-H1 -
Steigenberger Golf Resort El Gouna): after the 2026-09-11 offer/supplement empty-array fix landed,
the SAME republish surfaced two further, distinct Phase 2 failures:

    Early Bird 20%/15%/10%: "Bean Validation constraint(s) violated on callback event:
    'prePersist'. Errors: HotelContractOffers.providerCode:must not be null (...)"
    Compulsory Christmas/New Year Gala Dinner (both listed twice): "Bean Validation constraint(s)
    violated on callback event:'prePersist'. Errors: HotelContractSupplement.providerCode:must
    not be null (...)"
    Club Package Supplement: "java.lang.IllegalArgumentException: Travel window can not be
    empty!"

Root cause 1 (providerCode): exactly the same shape of bug the 2026-09-11 ROOM fix already solved
(HotelContractRoom.providerCode:must not be null) - ContractHotelOffersVO/ContractHotelSupplementVO
both left providerCode unset on create, based on an unverified "system-generated" assumption
(schemas.py docstrings, inferred only from GET examples). Fixed the same way: builder.py now
generates a deterministic client-side placeholder providerCode
(_hotel_offer_supplement_placeholder_code) for every new offer/supplement and always reads the
REAL code back from the create response, never from what was sent.

Root cause 2 (travel window): unlike providerRoomCodes/mealPlans, there is no "applies to
everything" convention documented for an offer/supplement's travel_windows - a compulsory,
year-round charge that states no specific dates legitimately extracts an empty travel_windows
list, which Travel Compositor rejects outright ("Travel window can not be empty!"). Fixed the
same way build_transfer_supplement_vos already handles an undated transfer supplement: when no
window is stated, default to today -> the far-future "runs indefinitely" date (2049-12-31)
instead of sending an empty array.
"""
from builder import (
    build_hotel_offer_payloads,
    build_hotel_supplement_payloads,
    _hotel_offer_supplement_placeholder_code,
    _TRANSFER_MAX_END_DATE,
)


# ======================================================================
# 1. providerCode placeholder generation
# ======================================================================
def test_placeholder_code_is_derived_from_hotel_code_kind_name_and_index():
    code = _hotel_offer_supplement_placeholder_code("HRG-H1", "OFFER", "Early Bird 20%", 0)
    assert code.startswith("HRG-H1-OFFER-")
    assert "EARLYBIRD20" in code
    assert code.endswith("-1")


def test_placeholder_code_falls_back_to_a_generic_slug_for_an_unnamed_item():
    code = _hotel_offer_supplement_placeholder_code("HRG-H1", "SUPP", "", 2)
    assert code == "HRG-H1-SUPP-SUPP-3"


def test_placeholder_code_falls_back_to_a_generic_hotel_code_when_missing():
    code = _hotel_offer_supplement_placeholder_code(None, "OFFER", "Deal", 0)
    assert code.startswith("HOTEL-OFFER-")


def test_two_offers_get_distinct_placeholder_codes():
    extracted = [
        {"name": "Early Bird 20%", "type": "PERCENT", "value": 20, "room_names": []},
        {"name": "Early Bird 15%", "type": "PERCENT", "value": 15, "room_names": []},
    ]
    room_map = {"Deluxe Room": "HRG-H1-DELUXEROOM-1"}
    results = build_hotel_offer_payloads(extracted, room_map, existing_hotel_snapshot=None,
                                          hotel_meal_plan_types=["ROOM_ONLY"],
                                          hotel_provider_code="HRG-H1")
    codes = [r["offer_payload"]["providerCode"] for r in results]
    assert all(codes)
    assert len(set(codes)) == 2


def test_offer_payload_never_leaves_providercode_null():
    extracted = [{"name": "Early Bird 20%", "type": "PERCENT", "value": 20, "room_names": []}]
    room_map = {"Deluxe Room": "HRG-H1-DELUXEROOM-1"}
    results = build_hotel_offer_payloads(extracted, room_map, existing_hotel_snapshot=None,
                                          hotel_meal_plan_types=["ROOM_ONLY"],
                                          hotel_provider_code="HRG-H1")
    assert results[0]["offer_error"] is None
    assert results[0]["offer_payload"]["providerCode"]


def test_supplement_payload_never_leaves_providercode_null():
    extracted = [{"name": "Compulsory Christmas Gala Dinner", "type": "ABSOLUTE", "value": 50,
                  "apply": "PER_NIGHT_PERSON", "room_names": [],
                  "travel_windows": [{"start": "2026-12-24", "end": "2026-12-26"}]}]
    room_map = {"Deluxe Room": "HRG-H1-DELUXEROOM-1"}
    results = build_hotel_supplement_payloads(extracted, room_map, existing_hotel_snapshot=None,
                                               hotel_meal_plan_types=["BED_AND_BREAKFAST"],
                                               hotel_provider_code="HRG-H1")
    assert results[0]["supplement_error"] is None
    assert results[0]["supplement_payload"]["providerCode"]


def test_backward_compat_default_hotel_provider_code_still_produces_a_non_null_code():
    """A caller that hasn't been updated to pass hotel_provider_code yet must still get a
    non-null placeholder, not a regression back to the providerCode=None bug."""
    extracted = [{"name": "Early Bird 20%", "type": "PERCENT", "value": 20, "room_names": []}]
    room_map = {"Deluxe Room": "HRG-H1-DELUXEROOM-1"}
    results = build_hotel_offer_payloads(extracted, room_map, existing_hotel_snapshot=None,
                                          hotel_meal_plan_types=["ROOM_ONLY"])
    assert results[0]["offer_payload"]["providerCode"]


# ======================================================================
# 2. travelWindows defaults to "runs indefinitely" when the document states no dates
# ======================================================================
def test_supplement_with_no_stated_travel_window_defaults_to_today_through_far_future():
    extracted = [{"name": "Club Package Supplement", "type": "ABSOLUTE", "value": 30,
                  "apply": "PER_STAY", "room_names": []}]
    room_map = {"Deluxe Room": "HRG-H1-DELUXEROOM-1"}
    results = build_hotel_supplement_payloads(extracted, room_map, existing_hotel_snapshot=None,
                                               hotel_meal_plan_types=["ROOM_ONLY"],
                                               hotel_provider_code="HRG-H1")
    assert results[0]["supplement_error"] is None
    windows = results[0]["supplement_payload"]["travelWindows"]
    assert len(windows) == 1
    assert windows[0]["end"] == _TRANSFER_MAX_END_DATE


def test_offer_with_no_stated_travel_window_defaults_instead_of_sending_empty_array():
    extracted = [{"name": "No-date offer", "type": "PERCENT", "value": 10, "room_names": []}]
    room_map = {"Deluxe Room": "HRG-H1-DELUXEROOM-1"}
    results = build_hotel_offer_payloads(extracted, room_map, existing_hotel_snapshot=None,
                                          hotel_meal_plan_types=["ROOM_ONLY"],
                                          hotel_provider_code="HRG-H1")
    assert results[0]["offer_error"] is None
    assert len(results[0]["offer_payload"]["travelWindows"]) == 1


def test_stated_travel_window_is_used_as_is_not_overridden_by_the_fallback():
    extracted = [{"name": "Early Bird 20%", "type": "PERCENT", "value": 20, "room_names": [],
                  "travel_windows": [{"start": "2026-01-01", "end": "2026-07-31"}]}]
    room_map = {"Deluxe Room": "HRG-H1-DELUXEROOM-1"}
    results = build_hotel_offer_payloads(extracted, room_map, existing_hotel_snapshot=None,
                                          hotel_meal_plan_types=["ROOM_ONLY"],
                                          hotel_provider_code="HRG-H1")
    windows = results[0]["offer_payload"]["travelWindows"]
    assert windows == [{"start": "2026-01-01", "end": "2026-07-31"}]
