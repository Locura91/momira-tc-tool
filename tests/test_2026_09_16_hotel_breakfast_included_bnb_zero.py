"""Regression tests for a real product request (product owner, 2026-09-16):

    "If meal type with Breakfast is already included, the app still must add the B&B as 0 Euro
    to the Meal plans."

Clarified (follow-up question, same day): the trigger is specifically a document stating the
ROOM RATE ITSELF already includes breakfast (e.g. "rate includes breakfast", a B&B-only rate
with no separate Room Only option) - not merely that a Breakfast/B&B meal plan happens to be
priced at 0 already (that case already works fine on its own, nothing needed).

Same shape as the existing, already-confirmed "Room Only is always taken as 0 money" rule
(_ensure_room_only_meal_plan): a BED_AND_BREAKFAST entry at 0 cost must always be present when
this is true, deterministically, rather than relying only on the AI extractor's own best-effort
0-cost meal_plans entry (ai_extractor.py's MEAL PLANS section already asks for one, but a prompt
is a hope, not a guarantee).

Wiring: ai_extractor.py's hotel prompt now asks for a new top-level "breakfast_included_in_rate"
boolean; builder.py's build_hotel_contract_payload reads extracted["breakfast_included_in_rate"]
and runs it through the new _ensure_breakfast_included_meal_plan, mirroring
_ensure_room_only_meal_plan's existing call the line above it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas import HumanPreConfig
from builder import build_hotel_contract_payload, _ensure_breakfast_included_meal_plan


def make_pre_config(**overrides):
    defaults = dict(
        supplier_id="51216", provider_code="HRG-H1", min_pax=1, max_pax=4,
        currency="EUR", modality_code="STANDARD",
    )
    defaults.update(overrides)
    return HumanPreConfig(**defaults)


def _room(name="Deluxe Room"):
    return {"name": name, "distributions": [{"adults": 2, "children": 0}]}


# ---------------------------------------------------------------------------------------------
# _ensure_breakfast_included_meal_plan directly
# ---------------------------------------------------------------------------------------------

def test_does_nothing_when_the_flag_is_false():
    result = _ensure_breakfast_included_meal_plan([], False)
    assert result == []


def test_does_nothing_when_the_flag_is_absent_none():
    result = _ensure_breakfast_included_meal_plan([{"meal_plan_hint": "Half Board", "base_price": 20}], None)
    assert result == [{"meal_plan_hint": "Half Board", "base_price": 20}]


def test_adds_a_zero_cost_bed_and_breakfast_entry_when_flag_is_true_and_none_exists():
    result = _ensure_breakfast_included_meal_plan([], True)
    assert len(result) == 1
    assert result[0]["meal_plan_hint"] == "BED_AND_BREAKFAST"
    assert result[0]["base_price"] == 0.0


def test_does_not_duplicate_an_existing_bed_and_breakfast_entry():
    existing = [{"meal_plan_hint": "Bed and Breakfast", "base_price": 0.0, "adult_prices": [], "child_prices": []}]
    result = _ensure_breakfast_included_meal_plan(existing, True)
    assert len(result) == 1


def test_never_overwrites_a_genuinely_priced_bed_and_breakfast_entry():
    # If the document ALSO separately prices a paid B&B upgrade rate alongside the included-
    # breakfast default, that price is real and must not be zeroed out just because the flag
    # is set - the flag only ensures a 0-cost entry exists when NONE is present at all.
    existing = [{"meal_plan_hint": "Bed and Breakfast", "base_price": 25.0, "adult_prices": [], "child_prices": []}]
    result = _ensure_breakfast_included_meal_plan(existing, True)
    assert len(result) == 1
    assert result[0]["base_price"] == 25.0


# ---------------------------------------------------------------------------------------------
# wired end to end through build_hotel_contract_payload
# ---------------------------------------------------------------------------------------------

def test_hotel_payload_gets_a_zero_cost_bnb_meal_plan_when_breakfast_included_in_rate():
    extracted = {
        "hotelname": "Test Hotel", "rooms": [_room()],
        "breakfast_included_in_rate": True, "meal_plans": [],
    }
    result = build_hotel_contract_payload(make_pre_config(), extracted)
    meal_plans = result["hotel_payload"]["mealPlans"]
    bnb_entries = [mp for mp in meal_plans if mp["mealPlan"] == "BED_AND_BREAKFAST"]
    assert len(bnb_entries) == 1
    assert bnb_entries[0]["basePrice"] == 0.0


def test_hotel_payload_still_gets_room_only_at_zero_alongside_the_bnb_entry():
    extracted = {
        "hotelname": "Test Hotel", "rooms": [_room()],
        "breakfast_included_in_rate": True, "meal_plans": [],
    }
    result = build_hotel_contract_payload(make_pre_config(), extracted)
    meal_plans = result["hotel_payload"]["mealPlans"]
    room_only_entries = [mp for mp in meal_plans if mp["mealPlan"] == "ROOM_ONLY"]
    assert len(room_only_entries) == 1
    assert room_only_entries[0]["basePrice"] == 0.0


def test_hotel_payload_unaffected_when_breakfast_included_in_rate_is_false():
    extracted = {
        "hotelname": "Test Hotel", "rooms": [_room()],
        "breakfast_included_in_rate": False, "meal_plans": [
            {"meal_plan_hint": "Half Board", "base_price": 20.0, "adult_prices": [], "child_prices": []},
        ],
    }
    result = build_hotel_contract_payload(make_pre_config(), extracted)
    meal_plans = result["hotel_payload"]["mealPlans"]
    assert not any(mp["mealPlan"] == "BED_AND_BREAKFAST" for mp in meal_plans)
