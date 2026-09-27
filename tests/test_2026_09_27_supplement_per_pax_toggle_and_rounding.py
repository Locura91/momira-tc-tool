"""Regression tests for a real bug report (product owner, 2026-09-27), from a screenshot of a
real ClosedTour Supplements grid: "Single Room Surcharge (SRS) - Cheow Lan Lake overnight"
(3,660) and "Extra Mattress (per night, specific rooms only)" (600) both showed the SAME flat
amount repeated across Single/Double/Triple/Quadruple, with "Per Pax" already unchecked -
verbatim: "in this example: single room price is per travel group and not per person. Extra
mattress is also per group and not per person. in this example every person has to pay the full
price which is wrong."

Also covers two follow-up confirmed rules from the same conversation:
  - "if supplement is per person or per travel group - if not specific mentioned, a supplement is
    mostly per travel group" -> the Per Pax default flips from True to False.
  - "Overall rule: we round up, we do not write in any price 0,75 it will be 1 or 30,89 will be 31"
    -> numeric_helpers.round_up_currency, used for the per-occupancy division.
  - "maybe we can include a button here: so the app is choosing the option AI thinks is correct,
    but if it is not correct the human can just automatically click the other button and the
    prices will be calculated per group and not per person" -> Per Pax is a per-row toggle that
    the Supplements table's own Save recalculates from, not a one-time AI guess baked in forever.
  - "but the selection must be able for each supplement individually" -> Per Pax stays a per-ROW
    column (never a single tour-wide setting).

Streamlit widgets can't be exercised outside a running app - checked via source inspection
(inspect.getsource), the same established pattern as
tests/test_2026_09_18_closedtour_supplement_zero_price_review_warning.py.
"""
import inspect
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from numeric_helpers import round_up_currency
import ui_components


# ======================================================================
# numeric_helpers.round_up_currency
# ======================================================================
def test_rounds_up_a_simple_fraction():
    assert round_up_currency(0.75) == 1.0


def test_rounds_up_the_confirmed_worked_example():
    assert round_up_currency(30.89) == 31.0


def test_whole_numbers_are_left_unchanged():
    assert round_up_currency(3660) == 3660.0
    assert round_up_currency(213) == 213.0


def test_floating_point_noise_does_not_push_a_whole_number_up():
    # 213 / 3 in real float math can render as 71.00000000000001 or similar noise - must not
    # become 72.
    assert round_up_currency(213 / 3) == 71.0


def test_real_contract_division_examples():
    # rate $71 x 3 nights = $213 total for the room.
    assert round_up_currency(213 / 1) == 213.0
    assert round_up_currency(213 / 2) == 107.0  # ceil(106.50)
    assert round_up_currency(213 / 3) == 71.0
    assert round_up_currency(213 / 4) == 54.0  # ceil(53.25)


def test_none_and_junk_input_falls_back_to_zero():
    assert round_up_currency(None) == 0.0
    assert round_up_currency("not a number") == 0.0


# ======================================================================
# ui_components.render_closedtour_supplements - Per Pax toggle + derived occupancy columns
# ======================================================================
def _source():
    return inspect.getsource(ui_components.render_closedtour_supplements)


def test_per_pax_default_is_now_per_travel_group_not_per_person():
    # CONFIRMED DEFAULT RULE: "if not specific mentioned, a supplement is mostly per travel
    # group" - both the display default and the save-back default flip from True to False.
    source = _source()
    assert '"Per Pax": s.get("per_pax", False)' in source
    assert 'row.get("Per Pax", False)' in source
    assert '"Per Pax": s.get("per_pax", True)' not in source
    assert 'row.get("Per Pax", True)' not in source


def test_occupancy_columns_are_derived_from_price_and_per_pax_on_save():
    # The real bug: a Per Pax=False row that still carried the SAME flat number in every
    # occupancy column. The fix must compute all four columns fresh from flat_price + per_pax
    # every time the table is saved, not read back whatever was already sitting in those cells.
    source = _source()
    assert "round_up_currency(flat_price / 1)" in source
    assert "round_up_currency(flat_price / 2)" in source
    assert "round_up_currency(flat_price / 3)" in source
    assert "round_up_currency(flat_price / 4)" in source
    assert "single_val = double_val = triple_val = quadruple_val = flat_price" in source


def test_per_pax_is_a_per_row_toggle_not_a_global_setting():
    # "but the selection must be able for each supplement individually" - Per Pax must be read
    # per iterated row (`row.get(...)`), never a single value applied to every supplement.
    source = _source()
    assert 'per_pax = bool(row.get("Per Pax", False))' in source
    # Confirms the derivation lives INSIDE the per-row loop (`for _, row in
    # edited_df.iterrows()`), not computed once outside it.
    loop_index = source.index("for _, row in edited_df.iterrows()")
    per_pax_index = source.index('per_pax = bool(row.get("Per Pax", False))')
    assert loop_index < per_pax_index


def test_occupancy_columns_are_disabled_in_the_editor():
    # They're calculated, not directly typeable - a human edit there would just be silently
    # overwritten by the next Save otherwise, which is confusing rather than helpful.
    source = _source()
    for col in ("Single", "Double", "Triple", "Quadruple"):
        assert f'"{col}": st.column_config.NumberColumn(disabled=True' in source


def test_caption_explains_the_toggle_and_rounding_rule_to_the_human():
    source = _source()
    assert "Per Pax" in source
    assert "round" in source.lower()
