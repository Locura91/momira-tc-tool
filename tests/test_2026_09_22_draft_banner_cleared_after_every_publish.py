"""Regression test for a real product-owner report (2026-09-22, verbatim):

    "Found unfinished work saved from 2026-09-22 06:53 UTC - a page reload (or the app
    restarting) doesn't have to mean starting over. --> This message is now too often. It even
    comes, after the human pressed successfully publish. when its published, we do not need to
    save something that has been done already"

draft_autosave.py's own docstring already anticipated the fix mechanism
(`clear_on_publish_success()` - "Call this right after a flow's publish has actually
succeeded"), but before this fix it was only actually CALLED from two places: flows/ticket.py
and app.py's legacy ClosedTour success screen (which flows/multi_tour.py's bulk ClosedTour flow
also benefits from for free, since it sets the same `just_published_tour_code`/
`just_published_supplier_id` keys that app.py's own unconditional bottom-of-file check reads).
Every OTHER publish path - Transfer duplicate, Transport duplicate, Hotel contract publish,
Transfer batch, Transport batch - never told draft_autosave a publish had succeeded, so the
"found unfinished work" banner kept offering to restore data a human had already successfully
published and had no reason to want back.

Fixed by calling draft_autosave.clear_on_publish_success() at each of those flows' own genuine
"this is fully done" moment - the same place each flow already resets its own session-state
keys or shows its "batch published"/"published in full" success message.

flows/*.py can't be imported directly in a test process here (hotel.py/multi_transfer.py/
multi_transport.py all do `from app import (...)` at module level, and app.py itself can't be
imported outside the real app - see test_2026_09_02_active_supplier_filter.py's own note on
this) - so, matching that suite's established pattern, these are verified by reading the
source text directly rather than importing the modules.
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_FLOWS_DIR = os.path.join(os.path.dirname(_HERE), "flows")


def _read_flow(name):
    with open(os.path.join(_FLOWS_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


def test_duplicate_transfer_imports_and_calls_clear_on_publish_success():
    src = _read_flow("duplicate_transfer.py")
    assert "import draft_autosave" in src
    assert "draft_autosave.clear_on_publish_success()" in src
    idx = src.index("draft_autosave.clear_on_publish_success()")
    assert 'st.success(f"✅ Published successfully' in src[:idx]


def test_duplicate_transport_imports_and_calls_clear_on_publish_success():
    src = _read_flow("duplicate_transport.py")
    assert "import draft_autosave" in src
    assert "draft_autosave.clear_on_publish_success()" in src


def test_hotel_clears_draft_only_on_the_full_success_branch():
    src = _read_flow("hotel.py")
    assert "import draft_autosave" in src
    idx = src.index("draft_autosave.clear_on_publish_success()")
    # must sit in the "else" (no all_failures) branch, shortly after st.balloons() (only
    # separated by this fix's own explanatory comment), not the partial-failure
    # st.error(...) branch right above it
    window_before = src[max(0, idx - 400):idx]
    assert "st.balloons()" in window_before
    assert "st.error(" not in window_before


def test_multi_transfer_clears_draft_only_when_the_whole_batch_succeeded():
    src = _read_flow("multi_transfer.py")
    assert "import draft_autosave" in src
    idx = src.index("draft_autosave.clear_on_publish_success()")
    before = src[:idx]
    assert 'if all(q.get("publish_status") == "success" for q in queue):' in before
    window_before = src[max(0, idx - 400):idx]
    assert "st.balloons()" in window_before


def test_multi_transport_clears_draft_only_when_the_whole_batch_succeeded():
    src = _read_flow("multi_transport.py")
    assert "import draft_autosave" in src
    idx = src.index("draft_autosave.clear_on_publish_success()")
    before = src[:idx]
    assert 'if all(q.get("publish_status") == "success" for q in queue):' in before
    window_before = src[max(0, idx - 400):idx]
    assert "st.balloons()" in window_before


def test_ticket_and_app_already_had_it_and_still_do():
    """Regression guard - these two call sites already existed before this fix and must not
    have been removed by it."""
    src = _read_flow("ticket.py")
    assert "draft_autosave.clear_on_publish_success()" in src
    app_py = os.path.join(os.path.dirname(_HERE), "app.py")
    with open(app_py, "r", encoding="utf-8") as f:
        app_src = f.read()
    assert "draft_autosave.clear_on_publish_success()" in app_src
