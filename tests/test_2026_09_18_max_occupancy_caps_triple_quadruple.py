"""Regression tests for a real product-owner house rule (2026-09-18):

    "if Occupancy is max 2, there can never be triple or quadruple prices"

Applies wherever this codebase already has a 4-slot single/double/triple/quadruple pricing
concept (ClosedTour price_list) or an adults+children distribution concept (Hotel rooms):

ROOT CAUSE: neither ClosedTour's normalize_price_list nor Hotel's room-distribution building had
any concept of "how many people can this room/cabin physically hold" - so a stray/misread AI
extraction (or a stale/bad human edit) could put a triplePrice/quadruplePrice, or a 3+-person
distribution, on a unit the source document itself says only sleeps 2, and nothing would catch it
before publish.

FIX: a new, extraction-only `max_occupancy` field (nullable int, only populated when the source
document explicitly states a capacity), enforced as a hard safety-net cap:
- builder.normalize_price_list(..., max_occupancy=...) drops triplePrice/quadruplePrice (and
  their *ChildPercentageDiscount siblings) whenever max_occupancy rules that occupancy out - see
  _MONEY_KEY_REQUIRED_OCCUPANCY.
- builder._room_pax_cap / _build_room_payload and build_hotel_rate_payloads's new
  room_name_to_max_occupancy parameter apply the same cap to Hotel room distributions and their
  priced counterparts.

Both are defense-in-depth: the primary instruction lives in the extraction prompts (ai_extractor.
OPTION_ONLY_SYSTEM_PROMPT / MODALITY_EXTRACTION_SYSTEM_PROMPT / EXTRACTION_SYSTEM_PROMPT for
ClosedTour, the Hotel rooms prompt for Hotel) - never invent triplePrice/quadruplePrice or a
distribution beyond a stated max_occupancy - but the builder-side guard holds even if extraction
gets it wrong or a human enters a bad value.
"""
import os

import ai_extractor
import builder
from builder import normalize_price_list, _room_pax_cap, _build_room_payload, build_hotel_rate_payloads

_HERE = os.path.dirname(os.path.abspath(__file__))
_HOTEL_FLOW_PY = os.path.join(os.path.dirname(_HERE), "flows", "hotel.py")


def _read_hotel_flow():
    with open(_HOTEL_FLOW_PY, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------------------------
# builder.normalize_price_list - ClosedTour price_list
# ---------------------------------------------------------------------------------------------

def _row(**price_overrides):
    price = {
        "singlePrice": {"amount": 100, "currency": "EUR"},
        "doublePrice": {"amount": 150, "currency": "EUR"},
        "triplePrice": {"amount": 200, "currency": "EUR"},
        "quadruplePrice": {"amount": 250, "currency": "EUR"},
    }
    price.update(price_overrides)
    return {"startDate": "2027-01-01", "endDate": "2027-12-31", "price": price}


def test_max_occupancy_2_drops_triple_and_quadruple():
    out = normalize_price_list([_row()], "EUR", max_occupancy=2)
    price = out[0]["price"]
    assert price["singlePrice"]["amount"] == 100
    assert price["doublePrice"]["amount"] == 150
    assert "triplePrice" not in price
    assert "quadruplePrice" not in price


def test_max_occupancy_3_drops_only_quadruple():
    out = normalize_price_list([_row()], "EUR", max_occupancy=3)
    price = out[0]["price"]
    assert price["triplePrice"]["amount"] == 200
    assert "quadruplePrice" not in price


def test_max_occupancy_4_or_higher_changes_nothing():
    out = normalize_price_list([_row()], "EUR", max_occupancy=4)
    price = out[0]["price"]
    assert price["triplePrice"]["amount"] == 200
    assert price["quadruplePrice"]["amount"] == 250


def test_no_max_occupancy_stated_changes_nothing_same_as_before_this_rule():
    out = normalize_price_list([_row()], "EUR", max_occupancy=None)
    price = out[0]["price"]
    assert price["triplePrice"]["amount"] == 200
    assert price["quadruplePrice"]["amount"] == 250


def test_max_occupancy_2_also_drops_the_child_discount_fields():
    out = normalize_price_list(
        [_row(tripleChildPercentageDiscount=50, quadrupleChildPercentageDiscount=100)],
        "EUR", max_occupancy=2)
    price = out[0]["price"]
    assert "tripleChildPercentageDiscount" not in price
    assert "quadrupleChildPercentageDiscount" not in price


def test_max_occupancy_2_records_a_note_when_something_was_actually_dropped():
    notes = []
    normalize_price_list([_row()], "EUR", max_occupancy=2, notes=notes)
    assert any("triplePrice" in n for n in notes)
    assert any("quadruplePrice" in n for n in notes)


def test_max_occupancy_2_with_no_triple_or_quadruple_price_present_adds_no_note():
    notes = []
    normalize_price_list([_row(triplePrice={}, quadruplePrice={})], "EUR", max_occupancy=2, notes=notes)
    assert notes == []


def test_max_occupancy_survives_a_row_that_is_still_sellable_at_single_and_double():
    # A max-2 room is still perfectly valid - only triple/quadruple are physically impossible.
    out = normalize_price_list([_row()], "EUR", max_occupancy=2)
    assert len(out) == 1
    assert out[0]["price"]["singlePrice"]["amount"] == 100


def test_max_occupancy_string_value_is_coerced_like_every_other_numeric_field():
    out = normalize_price_list([_row()], "EUR", max_occupancy="2")
    assert "triplePrice" not in out[0]["price"]


def test_max_occupancy_combines_correctly_with_the_child_discount_fallback():
    # A fallback child discount for triple/quadruple must not resurrect an occupancy that
    # max_occupancy has just ruled out.
    out = normalize_price_list([_row()], "EUR", max_occupancy=2, fallback_child_discount_percentage=50)
    price = out[0]["price"]
    assert "tripleChildPercentageDiscount" not in price
    assert "quadrupleChildPercentageDiscount" not in price


# ---------------------------------------------------------------------------------------------
# builder._room_pax_cap / _build_room_payload - Hotel rooms
# ---------------------------------------------------------------------------------------------

def test_room_pax_cap_uses_the_stated_max_occupancy_when_smaller_than_the_system_cap():
    assert _room_pax_cap({"max_occupancy": 2}) == 2


def test_room_pax_cap_falls_back_to_the_system_cap_when_unstated():
    assert _room_pax_cap({"max_occupancy": None}) == builder._MAX_OCCUPANCY_PAX
    assert _room_pax_cap({}) == builder._MAX_OCCUPANCY_PAX


def test_room_pax_cap_never_exceeds_the_system_cap_even_if_stated_higher():
    assert _room_pax_cap({"max_occupancy": 20}) == builder._MAX_OCCUPANCY_PAX


def test_build_room_payload_drops_a_distribution_beyond_the_stated_max_occupancy():
    room_data = {
        "name": "Twin Room", "max_occupancy": 2,
        "distributions": [{"adults": 1, "children": 0}, {"adults": 2, "children": 0},
                           {"adults": 2, "children": 1}],  # 3 total - exceeds max_occupancy 2
    }
    payload = _build_room_payload(room_data)
    kept = [(d.adults, d.children) for d in payload.distributions]
    assert (1, 0) in kept
    assert (2, 0) in kept
    assert (2, 1) not in kept


def test_build_room_payload_unaffected_when_max_occupancy_not_stated():
    room_data = {
        "name": "Family Suite",
        "distributions": [{"adults": 2, "children": 2}],  # 4 total, no cap stated
    }
    payload = _build_room_payload(room_data)
    assert [(d.adults, d.children) for d in payload.distributions] == [(2, 2)]


# ---------------------------------------------------------------------------------------------
# build_hotel_rate_payloads - room_name_to_max_occupancy threading
# ---------------------------------------------------------------------------------------------

def _rate_data_for(room_name, distribution_prices):
    return [{
        "name": "Standard Rate",
        "seasons": [{
            "name": "2027 season",
            "date_ranges": [{"start": "2027-01-01", "end": "2027-12-31"}],
            "room_prices": [{"room_name": room_name, "units_quota": 20, "units_on_request": 0,
                              "base_price": 0.0, "distribution_prices": distribution_prices,
                              "adult_prices": [], "child_prices": []}],
            "meal_plans": [],
        }],
        "offer_names": [], "supplement_names": [], "stop_sales": [],
    }]


def test_priced_distributions_beyond_room_cap_are_dropped():
    distribution_prices = [
        {"adults": 1, "children": 0, "amount": 100},
        {"adults": 2, "children": 0, "amount": 150},
        {"adults": 2, "children": 1, "amount": 200},  # 3 total - exceeds max_occupancy 2
    ]
    results = build_hotel_rate_payloads(
        _rate_data_for("Twin Room", distribution_prices), {"Twin Room": "ROOM-1"}, {}, {},
        room_name_to_distributions={"Twin Room": [{"adults": 1, "children": 0}, {"adults": 2, "children": 0}]},
        room_name_to_max_occupancy={"Twin Room": 2})
    payload = results[0]["rate_payload"]
    combos = {(p["adults"], p["children"])
              for p in payload["seasons"][0]["seasonRoomPrices"][0]["distributionPrices"]}
    assert (1, 0) in combos
    assert (2, 0) in combos
    assert (2, 1) not in combos


def test_priced_distributions_unaffected_when_room_has_no_stated_max_occupancy():
    distribution_prices = [
        {"adults": 2, "children": 1, "amount": 200},
    ]
    results = build_hotel_rate_payloads(
        _rate_data_for("Family Suite", distribution_prices), {"Family Suite": "ROOM-2"}, {}, {},
        room_name_to_distributions={"Family Suite": [{"adults": 2, "children": 1}]},
        room_name_to_max_occupancy={})
    payload = results[0]["rate_payload"]
    combos = {(p["adults"], p["children"])
              for p in payload["seasons"][0]["seasonRoomPrices"][0]["distributionPrices"]}
    assert (2, 1) in combos


# ---------------------------------------------------------------------------------------------
# ai_extractor.py - the field is present in every relevant prompt
# ---------------------------------------------------------------------------------------------

def test_max_occupancy_field_present_in_closed_tour_prompts():
    assert '"max_occupancy": null' in ai_extractor.EXTRACTION_SYSTEM_PROMPT
    assert '"max_occupancy": null' in ai_extractor.MODALITY_EXTRACTION_SYSTEM_PROMPT
    assert '"max_occupancy": null' in ai_extractor.OPTION_ONLY_SYSTEM_PROMPT


def test_max_occupancy_field_present_in_hotel_rooms_prompt():
    assert '"max_occupancy": null' in ai_extractor.HOTEL_EXTRACTION_SYSTEM_PROMPT


def test_max_occupancy_defaulted_in_extract_structured_data(monkeypatch):
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: {"price_list": []})
    data = ai_extractor.extract_structured_data("irrelevant")
    assert data["max_occupancy"] is None


def test_max_occupancy_defaulted_in_extract_modality_data(monkeypatch):
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: {"price_list": []})
    data = ai_extractor.extract_modality_data("irrelevant")
    assert data["max_occupancy"] is None


def test_max_occupancy_defaulted_in_extract_option_only_data(monkeypatch):
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: {"price_list": []})
    data = ai_extractor.extract_option_only_data("irrelevant")
    assert data["max_occupancy"] is None


def test_max_occupancy_passed_through_when_extracted(monkeypatch):
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: {"price_list": [], "max_occupancy": 2})
    data = ai_extractor.extract_structured_data("irrelevant")
    assert data["max_occupancy"] == 2


# ---------------------------------------------------------------------------------------------
# flows/hotel.py - room_name_to_max_occupancy wiring (source-text check, same established
# pattern as test_2026_09_16_hotel_room_distributions_seeded_from_existing.py)
# ---------------------------------------------------------------------------------------------

def test_room_name_to_max_occupancy_is_built_from_this_documents_own_rooms_only():
    src = _read_hotel_flow()
    build_idx = src.index("room_name_to_max_occupancy = {")
    window = src[build_idx:build_idx + 300]
    assert 'data.get("rooms")' in window
    # unlike room_name_to_distributions, this must NOT be seeded from the existing snapshot -
    # max_occupancy is an extraction-only concept, never part of Travel Compositor's own room
    # payload, so an existing live room has no max_occupancy to seed it from.
    assert '(existing_snapshot or {}).get("rooms")' not in window


def test_room_name_to_max_occupancy_feeds_build_hotel_rate_payloads():
    src = _read_hotel_flow()
    build_idx = src.index("room_name_to_max_occupancy = {")
    call_idx = src.index("build_hotel_rate_payloads(", build_idx)
    window = src[build_idx:call_idx + 500]
    assert "room_name_to_max_occupancy=room_name_to_max_occupancy" in window
