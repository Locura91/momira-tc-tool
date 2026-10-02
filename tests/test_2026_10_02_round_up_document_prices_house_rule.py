"""Regression tests for the 2026-10-02 house rule (product owner, verbatim): "if we ever have
numbers or prices from a document, we round up. So if there's a price of 35 and 10 cents, we
would say 36."

Before this, numeric_helpers.round_up_currency was only applied to COMPUTED prices (a flat total
divided across occupancy, confirmed 2026-09-27 - see numeric_helpers.py). A price taken directly
from a document (a base rate, a supplement, a tax, a hotel rate, ...) passed through _safe_float
unchanged, cents and all. This now applies round_up_currency as a code-enforced safety net at
every genuine document-price call site across every product type, while leaving percentages, pax
counts, deltas, and already-live/carried-forward data untouched - see each call site's own
docstring in builder.py for why.

These tests exercise the specific helper functions that were changed, directly - the same level
existing tests like test_builder_supplements.py already use - rather than the full
build_*_payloads pipelines (covered indirectly by the full suite, and exercising those end to end
needs a lot of unrelated scaffolding that would obscure what's actually being tested here).
"""
from builder import (
    _money_or_none, _safe_supplement_price, build_ticket_supplement_vos,
    build_transfer_supplement_vos, build_transfer_additional_service_vos,
    transport_base_child_price, _build_meal_plan_payload, round_up_currency,
    build_hotel_offer_payloads, build_hotel_supplement_payloads, build_hotel_rate_payloads,
)


# ============================================================
# ClosedTour: _safe_supplement_price (Supplement single/double/triple/quadruplePrice) and
# _money_or_none (price_list occupancy amounts).
# ============================================================

def test_safe_supplement_price_rounds_up_document_cents():
    assert _safe_supplement_price(35.10) == 36.0
    assert _safe_supplement_price(30.89) == 31.0
    assert _safe_supplement_price(0.75) == 1.0


def test_safe_supplement_price_leaves_whole_numbers_and_zero_unchanged():
    assert _safe_supplement_price(120) == 120.0
    assert _safe_supplement_price(0) == 0.0


def test_safe_supplement_price_rounds_up_the_unwrapped_dict_shape_too():
    """A supplement price sometimes arrives as a {"amount": ...} dict (the price_list shape
    bleeding in - see this function's own docstring) - must still round up after unwrapping."""
    assert _safe_supplement_price({"amount": 17.01}) == 18.0


def test_money_or_none_rounds_up_plain_number_and_dict_amount():
    assert _money_or_none(35.10, "EUR") == {"amount": 36.0, "currency": "EUR"}
    assert _money_or_none({"amount": 30.89}, "EUR") == {"amount": 31.0, "currency": "EUR"}


def test_money_or_none_zero_still_means_sold_at_no_extra_charge():
    """round_up_currency(0) == 0 - the 'sold, free' case must stay distinguishable from None
    ('not sold at all')."""
    assert _money_or_none(0, "EUR") == {"amount": 0.0, "currency": "EUR"}
    assert _money_or_none(None, "EUR") is None
    assert _money_or_none({}, "EUR") is None


# ============================================================
# Ticket: build_ticket_supplement_vos.
# ============================================================

def test_ticket_supplement_prices_round_up():
    vos = build_ticket_supplement_vos([{
        "name": "Fast-track supplement", "adult_price_supplement": 12.20,
        "children_price_supplement": 6.05, "infant_price_supplement": 0,
        "start_date": "2027-01-01", "end_date": "2027-12-31",
    }])
    assert len(vos) == 1
    vo = vos[0]
    assert vo.adultPriceSupplement == 13.0
    assert vo.childrenPriceSupplement == 7.0
    assert vo.infantPriceSupplement == 0.0


# ============================================================
# Transfer: build_transfer_supplement_vos (ABSOLUTE rounds, PERCENT never does) and
# build_transfer_additional_service_vos.
# ============================================================

def test_transfer_supplement_absolute_amount_rounds_up():
    out = build_transfer_supplement_vos([
        {"name": "Night surcharge", "amount": 11.30, "type": "ABSOLUTE"},
    ])
    assert len(out) == 1
    assert out[0].amount == 12.0
    assert out[0].type == "ABSOLUTE"


def test_transfer_supplement_percent_amount_is_never_rounded():
    """A percentage is a rate, not a document price - 12.5% must stay exactly 12.5, never
    ceiling'd to 13."""
    out = build_transfer_supplement_vos([
        {"name": "Peak season surcharge", "amount": 12.5, "type": "PERCENT"},
    ])
    assert len(out) == 1
    assert out[0].amount == 12.5
    assert out[0].type == "PERCENT"


def test_transfer_additional_service_price_rounds_up():
    out = build_transfer_additional_service_vos([
        {"name": "Child seat", "price": 8.40, "max_quantity": 2},
    ])
    assert len(out) == 1
    assert out[0].price == 9.0


# ============================================================
# Transport: transport_base_child_price (explicit child_price branch rounds; the base_price
# fallback is rounded by the caller before being passed in here).
# ============================================================

def test_transport_base_child_price_rounds_up_explicit_document_value():
    assert transport_base_child_price({"child_price": 22.15}, base_price=100.0) == 23.0


def test_transport_base_child_price_falls_back_to_already_rounded_base_price():
    # base_price is passed in already-rounded by the caller (round_up_currency applied before
    # this function is called) - this function must not need to round it again to be correct.
    assert transport_base_child_price({}, base_price=100.0) == 100.0
    assert transport_base_child_price(None, base_price=100.0) == 100.0


# ============================================================
# Hotel: _build_meal_plan_payload.
# ============================================================

def test_hotel_meal_plan_prices_round_up():
    payload = _build_meal_plan_payload({
        "meal_plan_hint": "BED_AND_BREAKFAST", "base_price": 14.25,
        "adult_prices": [9.01, 20], "child_prices": [4.99],
    })
    assert payload.basePrice == 15.0
    assert payload.adultPrices == [10.0, 20.0]
    assert payload.childPrices == [5.0]


def test_hotel_room_only_meal_plan_stays_zero():
    payload = _build_meal_plan_payload({"meal_plan_hint": "ROOM_ONLY", "base_price": 999})
    assert payload.basePrice == 0.0


# ============================================================
# round_up_currency itself (already existed, re-pinned here for completeness of this house
# rule's test coverage in one place).
# ============================================================

def test_round_up_currency_matches_the_product_owners_own_examples():
    assert round_up_currency(35.10) == 36.0
    assert round_up_currency(30.89) == 31.0


# ============================================================
# Hotel: build_hotel_offer_payloads/build_hotel_supplement_payloads - value/childValue round up
# only for an ABSOLUTE/STAY_TO_PAY amount, never for PERCENT.
# ============================================================

def test_hotel_offer_absolute_value_rounds_up():
    extracted = [{"name": "Gala Dinner Package", "type": "STAY_TO_PAY", "value": 45.20, "child_value": 22.10,
                  "room_names": []}]
    results = build_hotel_offer_payloads(extracted, {"Deluxe Room": "CODE-1"}, existing_hotel_snapshot=None,
                                          hotel_meal_plan_types=["ROOM_ONLY"])
    assert results[0]["offer_error"] is None
    assert results[0]["offer_payload"]["value"] == 46.0
    assert results[0]["offer_payload"]["childValue"] == 23.0


def test_hotel_offer_percent_value_is_never_rounded():
    extracted = [{"name": "Early Bird", "type": "PERCENT", "value": 12.5, "room_names": []}]
    results = build_hotel_offer_payloads(extracted, {"Deluxe Room": "CODE-1"}, existing_hotel_snapshot=None,
                                          hotel_meal_plan_types=["ROOM_ONLY"])
    assert results[0]["offer_payload"]["value"] == 12.5


def test_hotel_supplement_absolute_value_rounds_up():
    extracted = [{"name": "Christmas Gala Dinner", "type": "ABSOLUTE", "value": 140.30,
                  "apply": "PER_STAY_PERSON", "room_names": []}]
    results = build_hotel_supplement_payloads(extracted, {"Deluxe Room": "CODE-1"}, existing_hotel_snapshot=None,
                                               hotel_meal_plan_types=["ROOM_ONLY"])
    assert results[0]["supplement_error"] is None
    assert results[0]["supplement_payload"]["value"] == 141.0


def test_hotel_supplement_percent_value_is_never_rounded():
    extracted = [{"name": "Service Charge", "type": "PERCENT", "value": 7.5,
                  "apply": "PER_STAY_PERSON", "room_names": []}]
    results = build_hotel_supplement_payloads(extracted, {"Deluxe Room": "CODE-1"}, existing_hotel_snapshot=None,
                                               hotel_meal_plan_types=["ROOM_ONLY"])
    assert results[0]["supplement_payload"]["value"] == 7.5


# ============================================================
# Hotel: build_hotel_rate_payloads - distributionPrices amounts round up (the real DISTRIBUTION-
# mode pricing engine).
# ============================================================

def test_hotel_rate_distribution_prices_round_up():
    room_map = {"Deluxe Room": "CODE-1"}
    extracted_rates = [{
        "name": "Winter Rates",
        "seasons": [{
            "name": "Medium", "date_ranges": [{"start": "2026-11-01", "end": "2026-11-30"}],
            "room_prices": [{
                "room_name": "Deluxe Room",
                "distribution_prices": [{"adults": 1, "children": 0, "amount": 99.05}],
            }],
        }],
    }]
    results = build_hotel_rate_payloads(extracted_rates, room_map, {}, {})
    assert results[0]["rate_error"] is None
    room_price = results[0]["rate_payload"]["seasons"][0]["seasonRoomPrices"][0]
    assert room_price["distributionPrices"][0]["amount"] == 100.0
    # basePrice falls back to this same (already-rounded) distribution amount.
    assert room_price["basePrice"] == 100.0
