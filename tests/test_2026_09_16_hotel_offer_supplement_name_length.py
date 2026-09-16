"""Regression test for a real hotel publish failure (product owner, 2026-09-16):

    Publish "succeeded" for the hotel contract/rooms/meal plans, but 3 of 3 offers failed:
        Early Bird Discount 20% (book by 31 Jul 2026, not combinable with Gala Dinners/Meal
        Supplements/Club Packages): {"error":["Bean Validation constraint(s) violated on
        callback event:'prePersist'. Errors: HotelContractOffers18N.name:Size must be between
        0 and 100 ..."],"status":"BAD_REQUEST"}

Root cause: ai_extractor.py deliberately prompts for offer/supplement names that spell out the
combinability rule in parentheses (see its own comment near "not combinable with" guidance), so
a real, descriptive name easily runs past Travel Compositor's 100-character hard limit on
HotelContractOffers18N.name (offers) / the equivalent supplement translation row. Nothing in
builder.py enforced that limit before sending the payload, so a document with several long-named
offers/supplements could publish the hotel/rooms/meal plans fine while every offer/supplement
with a too-long name silently failed - "silently" in the sense that the failure only surfaced
after the fact, in the publish result, not as something the human could have avoided beforehand.

Fix: _translation_list gained an optional max_length - when a name is over the cap, it's
trimmed on a word boundary and gets a trailing "…" so it's visibly shortened rather than just
cut off mid-word. Only offer/supplement names pass max_length=100 - descriptions and
voucherRemarks (the same helper's other callers) have no such Travel Compositor limit.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from builder import _translation_list, build_hotel_offer_payloads, build_hotel_supplement_payloads

_REAL_OFFER_NAME = ("Early Bird Discount 20% (book by 31 Jul 2026, not combinable with Gala "
                     "Dinners/Meal Supplements/Club Packages)")


# ---------------------------------------------------------------------------------------------
# _translation_list itself
# ---------------------------------------------------------------------------------------------

def test_translation_list_leaves_short_text_unchanged_even_with_a_max_length():
    result = _translation_list("Half Board", max_length=100)
    assert result[0].description == "Half Board"


def test_translation_list_truncates_text_over_the_max_length():
    assert len(_REAL_OFFER_NAME) > 100
    result = _translation_list(_REAL_OFFER_NAME, max_length=100)
    assert len(result[0].description) <= 100


def test_translation_list_truncation_ends_with_an_ellipsis_not_a_mid_word_cut():
    result = _translation_list(_REAL_OFFER_NAME, max_length=100)
    assert result[0].description.endswith("…")
    # word-boundary trim: what remains before the ellipsis must be a prefix of the original text
    trimmed = result[0].description[:-1].rstrip(" ,.;:-")
    assert _REAL_OFFER_NAME.startswith(trimmed)


def test_translation_list_with_no_max_length_is_unaffected_descriptions_and_voucher_remarks():
    long_description = "A" * 500
    result = _translation_list(long_description)
    assert result[0].description == long_description


# ---------------------------------------------------------------------------------------------
# wired into offer/supplement building end to end
# ---------------------------------------------------------------------------------------------

def test_build_hotel_offer_payload_truncates_a_too_long_name():
    offer_data = {
        "name": _REAL_OFFER_NAME, "type": "PERCENTAGE", "value": 20,
        "room_names": [], "meal_plans": [],
        "travel_windows": [{"start": "2026-01-01", "end": "2026-07-31"}],
    }
    results = build_hotel_offer_payloads(
        [offer_data], room_name_to_provider_code={"Deluxe Room": "AUTO123"},
        hotel_meal_plan_types=["ROOM_ONLY"], hotel_provider_code="HRG-H1",
    )
    assert len(results) == 1
    assert results[0]["offer_error"] is None
    payload = results[0]["offer_payload"]
    assert payload is not None
    stored_name = payload["names"][0]["description"]
    assert len(stored_name) <= 100


def test_build_hotel_supplement_payload_truncates_a_too_long_name():
    supp_data = {
        "name": _REAL_OFFER_NAME.replace("Discount", "Supplement"), "value": 15, "apply": "PER_STAY",
        "room_names": [], "meal_plans": [],
        "travel_windows": [{"start": "2026-01-01", "end": "2026-12-31"}],
    }
    results = build_hotel_supplement_payloads(
        [supp_data], room_name_to_provider_code={"Deluxe Room": "AUTO123"},
        hotel_meal_plan_types=["ROOM_ONLY"], hotel_provider_code="HRG-H1",
    )
    assert len(results) == 1
    assert results[0]["supplement_error"] is None
    payload = results[0]["supplement_payload"]
    assert payload is not None
    stored_name = payload["names"][0]["description"]
    assert len(stored_name) <= 100


def test_build_hotel_offer_payload_leaves_a_short_name_untouched():
    offer_data = {
        "name": "Early Booking 10%", "type": "PERCENTAGE", "value": 10,
        "room_names": [], "meal_plans": [],
        "travel_windows": [{"start": "2026-01-01", "end": "2026-07-31"}],
    }
    results = build_hotel_offer_payloads(
        [offer_data], room_name_to_provider_code={"Deluxe Room": "AUTO123"},
        hotel_meal_plan_types=["ROOM_ONLY"], hotel_provider_code="HRG-H1",
    )
    payload = results[0]["offer_payload"]
    assert payload["names"][0]["description"] == "Early Booking 10%"
