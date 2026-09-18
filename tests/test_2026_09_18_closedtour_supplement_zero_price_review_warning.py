"""Regression test for a follow-up to the "supplement can never be 0 Euro" house rule (product
owner, 2026-09-18).

Chris's exact words, with a screenshot showing a "Suite Supplement (Double Suite)" row with 0 in
every price column sitting untouched in the ClosedTour Supplements review grid: "example as
supplement listed: [screenshot] This should not be allowed, because a supplement cannot be 0."

The builder-level drop+note behavior (see tests/test_2026_09_18_supplement_zero_price_dropped.py
and builder.build_supplement_vos) only surfaces at PUBLISH time - a human looking at the review
grid before ever clicking Publish had no way to see a 0-Euro row was a problem. This adds a
matching warning directly to ui_components.render_closedtour_supplements' review screen, computed
straight from `data["supplements"]` (not from a save callback) so it's visible on the very first
render - before the human has opened the editor at all, which is exactly the state a freshly
AI-extracted 0-Euro row is in.

Streamlit widgets can't be exercised outside a running app, so this is checked via source
inspection (inspect.getsource), the same established pattern as
tests/test_2026_09_03_ticket_duration_unit_display.py and test_2026_09_02_medium_batch5_support.py.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ui_components


def _source():
    return inspect.getsource(ui_components.render_closedtour_supplements)


def test_warns_when_every_price_column_is_zero():
    source = _source()
    assert "zero_price_names" in source
    assert "A supplement can never be 0 Euro" in source


def test_zero_price_check_covers_all_five_price_columns():
    source = _source()
    assert '_safe_float(s.get("price", 0)) == 0' in source
    assert '_safe_float(s.get("single_price"' in source
    assert '_safe_float(s.get("double_price"' in source
    assert '_safe_float(s.get("triple_price", 0)) == 0' in source
    assert '_safe_float(s.get("quadruple_price", 0)) == 0' in source


def test_computed_from_data_directly_not_only_from_a_save_callback():
    # Must not be gated behind st.session_state.get(f"_{key_prefix}_supplements_zero_price")
    # populated only by a save callback - that would mean the warning never shows until the
    # human opens the editor and clicks Save at least once, missing exactly the
    # never-yet-touched, freshly-extracted state Chris's screenshot showed.
    source = _source()
    assert 'for s in (data.get("supplements") or [])' in source


def test_warning_names_the_affected_supplement():
    source = _source()
    # The warning message must actually list which row(s) are the problem, not just a generic
    # notice - same "tell the human which one and why" convention as the missing_name warning.
    assert '", ".join(f"\'{n}\'" for n in zero_price_names)' in source


def test_unnamed_rows_are_excluded_from_the_zero_price_check():
    # A blank row with no Name is already handled by the separate missing_name warning above -
    # it must not also be double-counted here.
    source = _source()
    assert "and s.get(\"name\")" in source
