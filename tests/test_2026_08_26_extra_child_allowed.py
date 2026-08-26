"""Tests for the ClosedTour "Extra child allowed" feature (2026-08-26).

CONFIRMED REAL FEATURE (product owner, screenshots of Travel Compositor's own Modality screen):
"Extra child allowed" checkbox + a "Max [bracket] extra child" number per occupancy price.
"First of all, we must detect if a child is allowed from the URL or the description (or changed
by human manually) and if so, we must add the maximum number of children per distribution. Single
is max one child, double is max 2 child and triple is max 2 child - only if not different stated."

CONFIRMED REAL LIMITATION (same day, a real GET on a live option that has both set in Travel
Compositor's UI - RAK-2/StandardPrivate - came back with NEITHER field in the JSON response, only
the already-modeled singlePrice/doublePrice/triplePrice/quadruplePrice +
tripleChildPercentageDiscount/quadrupleChildPercentageDiscount): this is admin-screen-only today,
so compute_extra_child_plan() is a DISPLAY-ONLY computation (see its own docstring) - nothing here
is ever sent to Travel Compositor's API. schemas.py's payload models are deliberately untouched.
"""
from builder import compute_extra_child_plan, build_closed_tour_payloads
from schemas import HumanPreConfig
from test_builder_closed_tour import make_pre_config, minimal_extracted_data


_FULL_PRICE_LIST = [{
    "startDate": "2027-01-01", "endDate": "2027-12-31",
    "price": {
        "singlePrice": {"amount": 981.0, "currency": "EUR"},
        "doublePrice": {"amount": 522.0, "currency": "EUR"},
        "triplePrice": {"amount": 446.0, "currency": "EUR"},
        "quadruplePrice": {"amount": 437.0, "currency": "EUR"},
    },
}]


def test_house_defaults_single_1_double_2_triple_2_quadruple_0():
    """The exact numbers from the product-owner's report, applied when the document states none
    of its own."""
    plan = compute_extra_child_plan(True, _FULL_PRICE_LIST)
    by_label = {b["label"]: b["max_extra_child"] for b in plan["brackets"]}
    assert plan["allowed"] is True
    assert by_label == {"Single": 1, "Double": 2, "Triple": 2, "Quadruple": 0}


def test_not_allowed_returns_no_brackets():
    plan = compute_extra_child_plan(False, _FULL_PRICE_LIST)
    assert plan["allowed"] is False
    assert plan["brackets"] == []


def test_only_sold_occupancies_get_a_bracket():
    """An occupancy this Modality doesn't price can't carry an extra child either - same
    'occupancies must agree' rule already used for supplements (strip_unsold_supplement_occupancies)."""
    price_list = [{"startDate": "2027-01-01", "endDate": "2027-12-31",
                  "price": {"singlePrice": {"amount": 500}, "doublePrice": {"amount": 300}}}]
    plan = compute_extra_child_plan(True, price_list)
    labels = {b["label"] for b in plan["brackets"]}
    assert labels == {"Single", "Double"}


def test_a_stated_override_wins_over_the_house_default():
    """'only if not different stated' - a document that says 'up to 2 children in a double room'
    (double=2 here is already the default, so use triple=0 to prove override actually applies)."""
    plan = compute_extra_child_plan(True, _FULL_PRICE_LIST, {"triple": 0})
    by_label = {b["label"]: b["max_extra_child"] for b in plan["brackets"]}
    assert by_label["Triple"] == 0
    assert by_label["Single"] == 1  # untouched brackets keep the house default
    assert by_label["Double"] == 2


def test_a_stated_zero_override_is_honored_not_treated_as_missing():
    """0 is a real, meaningful override ('no extra child in a triple') - must not be confused with
    'nothing stated' and silently replaced by the house default of 2."""
    plan = compute_extra_child_plan(True, _FULL_PRICE_LIST, {"triple": 0})
    triple = next(b for b in plan["brackets"] if b["label"] == "Triple")
    assert triple["max_extra_child"] == 0


def test_junk_override_value_falls_back_to_house_default():
    plan = compute_extra_child_plan(True, _FULL_PRICE_LIST, {"single": "not a number"})
    single = next(b for b in plan["brackets"] if b["label"] == "Single")
    assert single["max_extra_child"] == 1


def test_wired_into_build_closed_tour_payloads(fake_api_client):
    """The full builder pipeline exposes the plan for the review screen (see
    ui_components.render_extra_child_notice), computed off the SAME sorted price list about to be
    published so it can never recommend an occupancy this tour doesn't sell."""
    result = build_closed_tour_payloads(
        make_pre_config(),
        minimal_extracted_data(extra_child_allowed=True),
        fake_api_client,
    )
    plan = result["extra_child_plan"]
    assert plan["allowed"] is True
    labels = {b["label"] for b in plan["brackets"]}
    # minimal_extracted_data's own default price_list only prices Single/Double.
    assert labels == {"Single", "Double"}


def test_defaults_to_allowed_when_extraction_omits_the_field(fake_api_client):
    """Defensive: an older cached extraction dict with no extra_child_allowed key at all must not
    crash build_closed_tour_payloads - defaults to allowed=True, matching the extraction prompt's
    own documented default."""
    data = minimal_extracted_data()
    data.pop("extra_child_allowed", None)
    result = build_closed_tour_payloads(make_pre_config(), data, fake_api_client)
    assert result["extra_child_plan"]["allowed"] is True


def test_not_sent_to_the_real_api_payload(fake_api_client):
    """CONFIRMED REAL LIMITATION: extraChildAllowed/maxSingleExtraChild etc. must never appear in
    the actual tour_option_payload/main_tour_payload sent to Travel Compositor - a real GET on a
    live option with both set in the UI came back with neither field."""
    result = build_closed_tour_payloads(
        make_pre_config(), minimal_extracted_data(extra_child_allowed=True), fake_api_client)
    option_keys = set(result["tour_option_payload"].keys())
    tour_keys = set(result["main_tour_payload"].keys())
    assert not any("xtraChild" in k or "ExtraChild" in k for k in option_keys)
    assert not any("xtraChild" in k or "ExtraChild" in k for k in tour_keys)
