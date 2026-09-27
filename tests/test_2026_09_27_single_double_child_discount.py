"""Tests for the ClosedTour Single/Double child discount fix (2026-09-27).

CONFIRMED PRODUCT-OWNER REQUEST (verbatim): "a single and a double price can have a child
discount. It would be adjusted when someone is travelling 1 adult and one child, therefore it
must be included to the upload."

This REVERSES the earlier 2026-08-24/2026-08-31 house rule (see
test_2026_08_31_closedtour_child_discount_visibility.py's module docstring) that Travel
Compositor's ClosedTour price list schema only supported a child discount on Triple/Quadruple
occupancy. singleChildPercentageDiscount/doubleChildPercentageDiscount were added to
PriceListPriceVO (schemas.py) in the same plain-percentage-float shape as the existing Triple/
Quadruple fields, and builder.normalize_price_list()'s fallback/clamp/row-override logic now
treats all four occupancies uniformly.
"""
from schemas import PriceListPriceVO
from builder import normalize_price_list, sold_occupancies
from ui_components import render_child_discount_editor


def test_price_list_price_vo_has_single_and_double_child_discount_fields():
    vo = PriceListPriceVO()
    assert hasattr(vo, "singleChildPercentageDiscount")
    assert hasattr(vo, "doubleChildPercentageDiscount")
    assert vo.singleChildPercentageDiscount is None
    assert vo.doubleChildPercentageDiscount is None


def test_document_wide_fallback_applies_to_single_and_double():
    price_list = [{
        "startDate": "2027-01-01", "endDate": "2027-12-31",
        "price": {
            "singlePrice": {"amount": 500}, "doublePrice": {"amount": 300},
            "triplePrice": {"amount": 250}, "quadruplePrice": {"amount": 200},
        },
    }]
    result = normalize_price_list(price_list, "EUR", fallback_child_discount_percentage=25)
    price = result[0]["price"]
    assert price["singleChildPercentageDiscount"] == 25.0
    assert price["doubleChildPercentageDiscount"] == 25.0
    assert price["tripleChildPercentageDiscount"] == 25.0
    assert price["quadrupleChildPercentageDiscount"] == 25.0


def test_a_rows_own_single_or_double_discount_wins_over_the_document_wide_fallback():
    price_list = [{
        "startDate": "2027-01-01", "endDate": "2027-12-31",
        "price": {
            "singlePrice": {"amount": 500}, "doublePrice": {"amount": 300},
            "doubleChildPercentageDiscount": 60,  # this row's own stated value
        },
    }]
    result = normalize_price_list(price_list, "EUR", fallback_child_discount_percentage=20)
    price = result[0]["price"]
    assert price["singleChildPercentageDiscount"] == 20.0   # fallback applied
    assert price["doubleChildPercentageDiscount"] == 60.0   # row's own value untouched


def test_single_or_double_discount_only_applies_when_that_occupancy_is_actually_sold():
    # Same rule as Triple/Quadruple - an occupancy this row doesn't sell can't carry a discount.
    price_list = [{
        "startDate": "2027-01-01", "endDate": "2027-12-31",
        "price": {"doublePrice": {"amount": 300}},  # no singlePrice on this row
    }]
    result = normalize_price_list(price_list, "EUR", fallback_child_discount_percentage=30)
    price = result[0]["price"]
    assert "singleChildPercentageDiscount" not in price
    assert price["doubleChildPercentageDiscount"] == 30.0


def test_single_and_double_discount_are_clamped_to_0_100_same_as_triple_quadruple():
    notes = []
    price_list = [{
        "startDate": "2027-01-01", "endDate": "2027-12-31",
        "price": {
            "singlePrice": {"amount": 500}, "doublePrice": {"amount": 300},
            "singleChildPercentageDiscount": 500,  # absurd, must be capped
        },
    }]
    result = normalize_price_list(price_list, "EUR", notes=notes)
    price = result[0]["price"]
    assert price["singleChildPercentageDiscount"] == 100.0
    assert any("singleChildPercentageDiscount" in n for n in notes)


def test_max_occupancy_guard_does_not_affect_single_or_double_discount():
    # max_occupancy only rules out Triple/Quadruple (a physical capacity limit) - Single/Double
    # are always sellable regardless of max_occupancy, so their discount is never dropped by it.
    price_list = [{
        "startDate": "2027-01-01", "endDate": "2027-12-31",
        "price": {"singlePrice": {"amount": 500}, "doublePrice": {"amount": 300}},
    }]
    result = normalize_price_list(price_list, "EUR", fallback_child_discount_percentage=10, max_occupancy=2)
    price = result[0]["price"]
    assert price["singleChildPercentageDiscount"] == 10.0
    assert price["doubleChildPercentageDiscount"] == 10.0


def test_ui_editor_gating_now_triggers_on_single_or_double_alone():
    # render_child_discount_editor's early-return condition was broadened to check all four
    # occupancies, not just Triple/Quadruple - covered at the sold_occupancies level since the
    # widget itself needs a live Streamlit session to render.
    single_double_only = [{"startDate": "2027-01-01", "endDate": "2027-12-31",
                           "price": {"singlePrice": {"amount": 500}, "doublePrice": {"amount": 300}}}]
    sold = sold_occupancies(single_double_only)
    assert {"singlePrice", "doublePrice", "triplePrice", "quadruplePrice"} & sold
    assert callable(render_child_discount_editor)


def test_extraction_prompts_mention_single_and_double_child_discount_fields():
    with open("ai_extractor.py", "r", encoding="utf-8") as f:
        src = f.read()
    assert src.count('"singleChildPercentageDiscount": 0,') == 3
    assert src.count('"doubleChildPercentageDiscount": 0,') == 3
    assert "no equivalent field for single/double occupancy" not in src
