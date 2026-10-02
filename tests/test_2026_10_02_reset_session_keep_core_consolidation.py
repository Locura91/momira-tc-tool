"""Regression tests for the 2026-10-02 weekly duplicate-code audit: the "clear the whole session
but keep client/suppliers_cache/product_type/active_tool" 9-line block was hand-copied byte-for-
byte 8 times across app.py (3x), flows/multi_tour.py (2x), and flows/ticket.py (3x). Moved to
app_helpers.reset_session_keep_core(); every call site now just calls it.

flows/multi_tour.py and flows/ticket.py can't be imported standalone in a test process - designed
to be loaded only via app.py's own controlled circular-import sequence - so, same as existing
tests for those modules, the call-site checks below inspect source text instead of importing and
calling the functions directly. reset_session_keep_core() itself lives in app_helpers.py, which
IS safely importable, so its own behavior is tested directly.
"""
from pathlib import Path

import streamlit as st

from app_helpers import reset_session_keep_core

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_reset_session_keep_core_keeps_exactly_the_four_core_keys_and_clears_everything_else():
    st.session_state.clear()
    st.session_state.client = "the-client"
    st.session_state.suppliers_cache = {"48940": "Some Supplier"}
    st.session_state.product_type = "ClosedTour"
    st.session_state.active_tool = "closed_tour"
    st.session_state.some_scratch_key = "should be wiped"
    st.session_state.cfg_supplier_id = "48940"

    reset_session_keep_core()

    assert st.session_state.client == "the-client"
    assert st.session_state.suppliers_cache == {"48940": "Some Supplier"}
    assert st.session_state.product_type == "ClosedTour"
    assert st.session_state.active_tool == "closed_tour"
    assert "some_scratch_key" not in st.session_state
    assert "cfg_supplier_id" not in st.session_state


def test_reset_session_keep_core_tolerates_missing_active_tool_key():
    st.session_state.clear()
    st.session_state.client = "the-client"
    st.session_state.suppliers_cache = {}
    st.session_state.product_type = "Ticket"
    # active_tool deliberately not set.
    reset_session_keep_core()
    assert st.session_state.active_tool is None


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text()


def test_no_hand_copied_reset_block_remains_anywhere():
    for path in ("app.py", "flows/multi_tour.py", "flows/ticket.py"):
        src = _read(path)
        assert "keep_client = st.session_state.client" not in src, f"found in {path}"
        assert "st.session_state.active_tool = keep_tool" not in src, f"found in {path}"


def test_every_former_call_site_now_calls_the_shared_helper():
    app_src = _read("app.py")
    assert app_src.count("reset_session_keep_core()") == 3

    multi_tour_src = _read("flows/multi_tour.py")
    assert multi_tour_src.count("reset_session_keep_core()") == 2

    ticket_src = _read("flows/ticket.py")
    assert ticket_src.count("reset_session_keep_core()") == 3


def test_multi_tour_do_something_else_variant_still_skips_action_prefill():
    """The one behavioral outlier found during the audit: this variant deliberately does NOT set
    cfg_action/step1_confirmed after the reset, so a human re-picks the action. Pin that the
    consolidation didn't accidentally make it match the other two call sites."""
    src = _read("flows/multi_tour.py")
    idx = src.index("Do something else with this Code")
    snippet = src[idx:idx + 1200]
    assert "reset_session_keep_core()" in snippet
    assert "st.session_state.cfg_action = " not in snippet
    assert "st.session_state.step1_confirmed = " not in snippet
    assert "cfg_prefill_supplier_id" in snippet
