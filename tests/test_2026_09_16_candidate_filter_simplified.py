"""Regression test for a real product-owner request (2026-09-16), the same day the 2026-09-15
"select all / select none" fix (test_2026_09_15_multi_service_select_all_none.py) was deployed:
"i see the new filter, but we must keep it simple. No text to be added, just select all or
unselect all."

app_helpers.render_candidate_filter originally (2026-09-15 and earlier) rendered a filter text
box plus THREE buttons - "Keep only these" (tick every row matching the typed term), "Select
all", "Clear all" - above every detected-service candidate list. Per this request, the filter
box and "Keep only these" button are retired: the function now renders exactly two buttons,
"Select all" and "Select none".

This is a single shared helper used by every detected-list screen in the app (multi_transfer,
multi_transport, multi_ticket create/update, multi_tour, multi_modality, and the single-Ticket
multi-excursion screen), so fixing it here fixes it everywhere at once - no per-flow-file changes
were needed for this particular request, unlike 2026-09-15's fix.

app.py can't be imported in a test process (heavy top-level Streamlit/API-client setup), so its
wiring is verified by reading its own source text, per this suite's established pattern.
"""
import inspect
import os

import app_helpers

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    """Returns app.py's source concatenated with every module under flows/ and app_helpers.py."""
    with open(_APP_PY, "r", encoding="utf-8") as f:
        src = f.read()
    app_helpers_path = os.path.join(os.path.dirname(_APP_PY), "app_helpers.py")
    if os.path.isfile(app_helpers_path):
        with open(app_helpers_path, "r", encoding="utf-8") as f:
            src += chr(10) + f.read()
    flows_dir = os.path.join(os.path.dirname(_APP_PY), "flows")
    if os.path.isdir(flows_dir):
        for _name in sorted(os.listdir(flows_dir)):
            if _name.endswith(".py") and _name != "__init__.py":
                with open(os.path.join(flows_dir, _name), "r", encoding="utf-8") as f:
                    src += chr(10) + f.read()
    return src


def test_the_filter_text_box_and_keep_only_these_button_are_gone():
    source = inspect.getsource(app_helpers.render_candidate_filter)
    assert "st.text_input" not in source
    assert "Keep only these" not in source
    assert "Filter {noun}" not in source


def test_only_select_all_and_select_none_remain():
    source = inspect.getsource(app_helpers.render_candidate_filter)
    assert '"Select all"' in source
    assert '"Select none"' in source
    # "Clear all" was the old label for the second button - renamed for clarity to match the
    # product owner's own wording ("select all or unselect all").
    assert '"Clear all"' not in source


def test_the_return_direction_button_is_unaffected():
    # A separate feature (mirror a ticked transfer/transport route) that happens to live in the
    # same function - this request only asked to simplify the filter/select controls above it.
    source = inspect.getsource(app_helpers.render_candidate_filter)
    assert "Add the return direction" in source


def test_app_py_no_longer_mentions_the_retired_filter_text():
    src = _read_app_py()
    assert "Keep only these" not in src
