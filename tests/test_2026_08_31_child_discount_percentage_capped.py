"""Tests for the child-discount 0-100% safety cap (2026-08-31).

CONFIRMED SAFETY RULE (product owner, 2026-08-31): "can i now enter the number of children and
the % discount to app? If yes, we must make sure, that the app never allows more than 100%
discount - because in travel compositor people could enter 100000% discount and the price will
be absolutely wrong."

Travel Compositor's own admin screen enforces no upper (or lower) bound on
tripleChildPercentageDiscount/quadrupleChildPercentageDiscount - see
builder.normalize_price_list()'s own docstring. The cap has to hold for BOTH ways a value can
reach the published payload:
  1. The document-wide child_discount_percentage a human can type into
     ui_components.render_child_discount_editor - the widget's own number_input already refuses
     anything outside 0-100 (a hard Streamlit limit).
  2. A row's own tripleChildPercentageDiscount/quadrupleChildPercentageDiscount straight from AI
     extraction - which, unlike the document-wide field, is never shown to a human at all, so it
     needs the SAME protection without anyone having reviewed it first.

builder._clamp_child_discount_percentage() is the single choke point both paths pass through
(inside normalize_price_list, called from build_closed_tour_payloads right before publish) -
these tests exercise it directly, then confirm the cap survives the full real builder pipeline.
"""
from builder import (
    _clamp_child_discount_percentage,
    build_closed_tour_payloads,
    normalize_price_list,
)
from test_builder_closed_tour import make_pre_config, minimal_extracted_data


_TRIPLE_QUAD_PRICE_LIST = [{
    "startDate": "2027-01-01", "endDate": "2027-12-31",
    "price": {"triplePrice": {"amount": 250}, "quadruplePrice": {"amount": 200}},
}]


def test_clamp_helper_leaves_in_range_values_untouched():
    assert _clamp_child_discount_percentage(50) == (50.0, False)
    assert _clamp_child_discount_percentage(0) == (0.0, False)
    assert _clamp_child_discount_percentage(100) == (100.0, False)


def test_clamp_helper_caps_an_absurd_value_exactly_like_the_product_owner_described():
    """The exact scenario raised: someone (or a bad extraction) enters 100000%."""
    pct, was_clamped = _clamp_child_discount_percentage(100000)
    assert pct == 100.0
    assert was_clamped is True


def test_clamp_helper_floors_a_negative_value_at_zero():
    pct, was_clamped = _clamp_child_discount_percentage(-25)
    assert pct == 0.0
    assert was_clamped is True


def test_clamp_helper_treats_unusable_input_as_absent_not_a_crash():
    assert _clamp_child_discount_percentage("not a number") == (None, False)
    assert _clamp_child_discount_percentage(None) == (None, False)
    assert _clamp_child_discount_percentage(float("nan")) == (None, False)


def test_normalize_price_list_caps_the_document_wide_fallback():
    out = normalize_price_list(_TRIPLE_QUAD_PRICE_LIST, "EUR", fallback_child_discount_percentage=100000)
    price = out[0]["price"]
    assert price["tripleChildPercentageDiscount"] == 100.0
    assert price["quadrupleChildPercentageDiscount"] == 100.0


def test_normalize_price_list_caps_a_rows_own_extracted_value_too():
    """The more important case: a row's own value, which no UI currently lets a human review
    before it reaches the API, gets exactly the same protection as the document-wide field."""
    rows = [{"startDate": "2027-01-01", "endDate": "2027-12-31",
             "price": {"triplePrice": {"amount": 250}, "tripleChildPercentageDiscount": 100000}}]
    out = normalize_price_list(rows, "EUR")
    assert out[0]["price"]["tripleChildPercentageDiscount"] == 100.0


def test_normalize_price_list_reports_the_clamp_when_a_notes_list_is_given():
    notes = []
    normalize_price_list(_TRIPLE_QUAD_PRICE_LIST, "EUR",
                          fallback_child_discount_percentage=100000, notes=notes)
    assert notes  # something was recorded
    assert any("100000" in n for n in notes)


def test_normalize_price_list_stays_silent_when_no_notes_list_is_given():
    """Every pre-existing direct caller of normalize_price_list (test_builder_price_list.py, and
    both call sites inside build_closed_tour_payloads for the supplement-stripping pass) doesn't
    pass notes= at all - must keep working exactly as before, no crash, no required argument."""
    out = normalize_price_list(_TRIPLE_QUAD_PRICE_LIST, "EUR", fallback_child_discount_percentage=100000)
    assert out[0]["price"]["tripleChildPercentageDiscount"] == 100.0


def test_normalize_price_list_does_not_report_a_note_for_an_in_range_value():
    notes = []
    normalize_price_list(_TRIPLE_QUAD_PRICE_LIST, "EUR",
                          fallback_child_discount_percentage=50, notes=notes)
    assert notes == []


def test_end_to_end_absurd_document_wide_percentage_never_reaches_the_real_payload(fake_api_client):
    """The full scenario, through the actual publish pipeline: a document/human enters 100000%,
    and the real tour_option_payload sent toward Travel Compositor must still cap at 100%."""
    result = build_closed_tour_payloads(
        make_pre_config(),
        minimal_extracted_data(price_list=_TRIPLE_QUAD_PRICE_LIST, child_discount_percentage=100000),
        fake_api_client,
    )
    price = result["tour_option_payload"]["priceList"][0]["price"]
    assert price["tripleChildPercentageDiscount"] == 100.0
    assert price["quadrupleChildPercentageDiscount"] == 100.0
    assert result["child_discount_clamp_notes"]  # surfaced, not silent


def test_end_to_end_normal_percentage_produces_no_clamp_note(fake_api_client):
    result = build_closed_tour_payloads(
        make_pre_config(),
        minimal_extracted_data(price_list=_TRIPLE_QUAD_PRICE_LIST, child_discount_percentage=25),
        fake_api_client,
    )
    price = result["tour_option_payload"]["priceList"][0]["price"]
    assert price["tripleChildPercentageDiscount"] == 25.0
    assert result["child_discount_clamp_notes"] == []


def test_end_to_end_a_rows_own_absurd_extracted_value_is_also_capped(fake_api_client):
    """Covers the path with no UI review at all today - a row's own extracted discount, not the
    document-wide field."""
    price_list = [{
        "startDate": "2027-01-01", "endDate": "2027-12-31",
        "price": {"triplePrice": {"amount": 250}, "tripleChildPercentageDiscount": 500},
    }]
    result = build_closed_tour_payloads(
        make_pre_config(), minimal_extracted_data(price_list=price_list), fake_api_client)
    assert result["tour_option_payload"]["priceList"][0]["price"]["tripleChildPercentageDiscount"] == 100.0
