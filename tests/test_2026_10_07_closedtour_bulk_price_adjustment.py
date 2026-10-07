"""
Tests for the ClosedTour bulk price-adjustment feature (product-owner request, 2026-10-07):

"the prices need to be recalculated ... an option that the price can be adjusted by the human by
percentage [or] an absolute number ... either plus or minus ... the prices need to be 20% cheaper
for all the seasons in the modality."

The real logic is builder.adjust_price_list_occupancy, a pure function; these tests pin its
behaviour (the four confirmed scope decisions: base occupancy prices only, child discounts follow
automatically, supplements untouched, round-up house rule) and a light source check that both
ClosedTour price tables actually render the control.
"""
import copy
import os

import pytest

from builder import adjust_price_list_occupancy


def _season(single=None, double=None, triple=None, quad=None, name="High season",
            start="2027-01-01", end="2027-03-31", currency="EUR", extra_price=None):
    price = {}
    for key, amt in [("singlePrice", single), ("doublePrice", double),
                     ("triplePrice", triple), ("quadruplePrice", quad)]:
        if amt is not None:
            price[key] = {"amount": amt, "currency": currency}
    if extra_price:
        price.update(extra_price)
    return {"name": name, "startDate": start, "endDate": end, "price": price}


def _amt(entry, key):
    return entry["price"][key]["amount"]


# ----------------------------------------------------------------------
# percentage, both directions
# ----------------------------------------------------------------------
def test_twenty_percent_cheaper_is_the_motivating_example():
    pl = [_season(single=200.0, double=150.0)]
    out = adjust_price_list_occupancy(pl, mode="percent", sign=-1, value=20)
    assert _amt(out[0], "singlePrice") == 160.0   # 200 * 0.8
    assert _amt(out[0], "doublePrice") == 120.0   # 150 * 0.8


def test_percentage_more_expensive_adds():
    out = adjust_price_list_occupancy([_season(single=100.0)], mode="percent", sign=1, value=10)
    assert _amt(out[0], "singlePrice") == 110.0


# ----------------------------------------------------------------------
# absolute, both directions
# ----------------------------------------------------------------------
def test_absolute_cheaper_subtracts_a_flat_amount_per_occupancy():
    out = adjust_price_list_occupancy([_season(single=100.0, double=80.0)],
                                      mode="absolute", sign=-1, value=10, round_up=False)
    assert _amt(out[0], "singlePrice") == 90.0
    assert _amt(out[0], "doublePrice") == 70.0


def test_absolute_more_expensive_adds_a_flat_amount():
    out = adjust_price_list_occupancy([_season(single=100.0)],
                                      mode="absolute", sign=1, value=15, round_up=False)
    assert _amt(out[0], "singlePrice") == 115.0


# ----------------------------------------------------------------------
# rounding house rule
# ----------------------------------------------------------------------
def test_round_up_house_rule_turns_an_ugly_fraction_into_the_next_whole_unit():
    # 199 * 0.8 = 159.2 -> rounds UP to 160 (never 159, never 159.2)
    out = adjust_price_list_occupancy([_season(single=199.0)], mode="percent", sign=-1, value=20)
    assert _amt(out[0], "singlePrice") == 160.0


def test_round_up_can_be_turned_off_to_keep_the_exact_amount():
    out = adjust_price_list_occupancy([_season(single=199.0)], mode="percent", sign=-1,
                                      value=20, round_up=False)
    assert _amt(out[0], "singlePrice") == pytest.approx(159.2)


# ----------------------------------------------------------------------
# scope: only occupancy prices, nothing else
# ----------------------------------------------------------------------
def test_blank_occupancy_stays_blank_not_turned_into_a_price():
    # Double is not offered (absent); adjusting must not invent a price for it.
    out = adjust_price_list_occupancy([_season(single=100.0)], mode="percent", sign=-1, value=20)
    assert "doublePrice" not in out[0]["price"]


def test_child_discount_percentages_are_left_untouched_so_they_follow_the_new_base():
    pl = [_season(single=200.0, extra_price={"singleChildPercentageDiscount": 30.0})]
    out = adjust_price_list_occupancy(pl, mode="percent", sign=-1, value=20)
    assert out[0]["price"]["singleChildPercentageDiscount"] == 30.0  # unchanged


def test_dates_names_and_currency_are_preserved_exactly():
    pl = [_season(single=100.0, name="Peak", start="2027-07-01", end="2027-08-31", currency="USD")]
    out = adjust_price_list_occupancy(pl, mode="percent", sign=-1, value=20)
    assert out[0]["name"] == "Peak"
    assert out[0]["startDate"] == "2027-07-01" and out[0]["endDate"] == "2027-08-31"
    assert out[0]["price"]["singlePrice"]["currency"] == "USD"


# ----------------------------------------------------------------------
# clamping, every occupancy, every season, immutability
# ----------------------------------------------------------------------
def test_a_discount_can_never_drive_a_price_below_zero():
    out = adjust_price_list_occupancy([_season(single=50.0)], mode="absolute", sign=-1, value=80)
    assert _amt(out[0], "singlePrice") == 0.0


def test_all_four_occupancies_are_adjusted():
    out = adjust_price_list_occupancy([_season(single=100.0, double=100.0, triple=100.0, quad=100.0)],
                                      mode="percent", sign=-1, value=50)
    for key in ("singlePrice", "doublePrice", "triplePrice", "quadruplePrice"):
        assert out[0]["price"][key]["amount"] == 50.0


def test_every_season_in_the_modality_is_adjusted():
    pl = [_season(single=200.0, start="2027-01-01", end="2027-03-31"),
          _season(single=300.0, start="2027-04-01", end="2027-06-30")]
    out = adjust_price_list_occupancy(pl, mode="percent", sign=-1, value=20)
    assert [_amt(e, "singlePrice") for e in out] == [160.0, 240.0]


def test_the_input_list_is_not_mutated():
    pl = [_season(single=200.0)]
    before = copy.deepcopy(pl)
    adjust_price_list_occupancy(pl, mode="percent", sign=-1, value=20)
    assert pl == before  # a new list is returned; the caller's data is untouched until it assigns


def test_bad_mode_or_sign_is_rejected_loudly():
    with pytest.raises(ValueError):
        adjust_price_list_occupancy([_season(single=1.0)], mode="ratio", sign=-1, value=1)
    with pytest.raises(ValueError):
        adjust_price_list_occupancy([_season(single=1.0)], mode="percent", sign=0, value=1)


# ----------------------------------------------------------------------
# wiring: both ClosedTour price tables expose the control
# ----------------------------------------------------------------------
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(name):
    with open(os.path.join(_REPO, name), "r", encoding="utf-8") as f:
        return f.read()


def test_base_modality_price_table_renders_the_adjustment_control():
    app = _src("app.py")
    # The control is rendered immediately before the base ClosedTour pricing table.
    assert 'render_price_adjustment_control(data, "pricing", currency)' in app


def test_extra_modality_price_editor_renders_the_adjustment_control():
    ui = _src("ui_components.py")
    editor = ui.split("def render_seasonal_price_editor")[1].split("\ndef ")[0]
    assert "render_price_adjustment_control(target_data, edit_key, currency)" in editor


# ----------------------------------------------------------------------
# pre-fill the editable price table from the live modality (no document needed)
# ----------------------------------------------------------------------
from app_helpers import _map_fetched_option_to_data


def test_live_option_pricelist_passes_straight_through_to_the_editable_table():
    opt = {"priceList": [{"name": "High", "startDate": "2027-01-01", "endDate": "2027-03-31",
                          "price": {"singlePrice": {"amount": 200.0, "currency": "EUR"}}}]}
    data = _map_fetched_option_to_data(opt)
    assert data["price_list"] == opt["priceList"]  # same nested-MoneyVO shape the table expects


def test_operational_days_and_stop_sales_are_carried_over():
    opt = {"priceList": [], "operationalDays": ["MONDAY", "TUESDAY"],
           "stopSales": [{"start": "2027-12-24", "end": "2027-12-26"}]}
    data = _map_fetched_option_to_data(opt)
    assert data["operational_days"] == ["MONDAY", "TUESDAY"]
    assert data["stop_sales"] == [{"start": "2027-12-24", "end": "2027-12-26"}]


def test_operational_days_default_to_all_seven_when_the_option_omits_them():
    data = _map_fetched_option_to_data({"priceList": []})
    assert data["operational_days"] == ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY",
                                        "FRIDAY", "SATURDAY", "SUNDAY"]


def test_an_error_or_non_dict_option_maps_to_empty_not_a_crash():
    assert _map_fetched_option_to_data({"error": 404, "message": "nope"}) == {}
    assert _map_fetched_option_to_data(None) == {}


def test_fetch_modality_prefills_the_editable_data_so_no_document_is_required():
    app = _src("app.py")
    # In the modality fetch handler, the live option is mapped into st.session_state.extracted,
    # which is the exact gate that makes Step 5 (the editable Pricing table) render - so a human
    # can re-price an existing modality without uploading any document.
    # Bound the slice to the fetch handler itself (up to where the fetched option is rendered),
    # rather than an arbitrary character count that a longer explanatory comment can overrun.
    fetch_block = app.split('st.button("🔍 Fetch this modality\'s live pricing")')[1]
    fetch_block = fetch_block.split('if st.session_state.get("fetched_option"):')[0]
    assert "_map_fetched_option_to_data(" in fetch_block
    assert "st.session_state.extracted" in fetch_block


# ----------------------------------------------------------------------
# Step 4 presents the choice up front: adjust the live prices, OR new document
# ----------------------------------------------------------------------
def test_step4_asks_adjust_vs_new_document_for_an_existing_modality():
    # CONFIRMED PRODUCT-OWNER REQUEST (2026-10-07): the human picks ONE clear option at Step 4 when
    # updating an existing modality - adjust the current prices, or replace them from a document/URL
    # - instead of having to discover the Step 3 fetch button.
    app = _src("app.py")
    assert 'ct_reprice_candidate = action == "update_option" or ct_price_only_via_update_tour' in app
    assert "ct_adjust_mode = _price_update_mode.startswith" in app
    # both choices are offered
    assert "Adjust the current prices" in app
    assert "Replace the prices from a new document or URL" in app


def test_adjust_mode_seeds_live_prices_and_skips_the_document_extract():
    app = _src("app.py")
    # The "adjust" branch loads the live option into st.session_state.extracted (so Step 5's
    # pricing table + bulk-adjust control render) with no document...
    adjust_block = app.split("if ct_adjust_mode:")[1].split("\nelse:")[0]
    assert "_map_fetched_option_to_data(" in adjust_block
    assert "st.session_state.extracted" in adjust_block
    assert "client.get_closed_tour_option(" in adjust_block
    # ...and the Extract button is suppressed in adjust mode, so there's no document path on screen.
    assert "if not ct_adjust_mode and st.button(\"🔎 Extract\"" in app


def test_adjust_mode_seeds_only_once_so_human_edits_are_not_clobbered():
    app = _src("app.py")
    adjust_block = app.split("if ct_adjust_mode:")[1].split("\nelse:")[0]
    # Guarded by a per-modality seed flag, checked before re-seeding.
    assert "_reprice_seed_key" in adjust_block
    assert "if not st.session_state.get(_reprice_seed_key):" in adjust_block
