"""Regression test for a confirmed real production crash (2026-09-16, reported while "creating
new tickets"): StreamlitWidgetAlreadyInstantiatedError raised inside _dmy_date_field the moment a
human used the calendar popover on ANY date field, in ANY flow - not something specific to
tickets, that was just where it got reported first (14 call sites share this one helper: hotel,
ticket, transfer, transport, multi-ticket, multi-ticket-modality, closed tour).

Root cause: the popover used to write st.session_state[key] = picked_date directly - the SAME
key the text_input(key=key) widget above it had already been instantiated with, in that same
script run. Streamlit refuses that outright: a widget's own key can only be set BEFORE that
widget is created (i.e. on the rerun that follows), never after it already ran this pass.

Fix: the picked value is queued into a separate "<key>__pending" session_state slot instead of
overwriting `key` directly, and st.rerun() is called; on the fresh rerun that follows, the queued
value is consumed and written into `key` at the very TOP of the function - BEFORE the text_input
widget for that key is instantiated in this new run, which is exactly when Streamlit allows a
widget's initial value to be set programmatically.

app.py can't be imported in a test process (heavy top-level Streamlit/API-client setup), so -
matching this suite's established pattern (see test_2026_09_01_medium_batch1_app_py.py) - this is
verified by reading app.py's own source text and checking the specific code shape.
"""
import os
import re

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    """app.py's source concatenated with app_helpers.py - Phase 1 module 13 (2026-09-16) moved
    ~96 shared helper functions/constants out of app.py into app_helpers.py, verbatim/zero-
    behaviour-change, so source-text assertions that used to find their target inside app.py
    alone now need to see app_helpers.py too. Reading app.py first keeps this purely additive.
    """
    with open(_APP_PY, "r", encoding="utf-8") as f:
        src = f.read()
    app_helpers_path = os.path.join(os.path.dirname(_APP_PY), "app_helpers.py")
    if os.path.isfile(app_helpers_path):
        with open(app_helpers_path, "r", encoding="utf-8") as f:
            src += chr(10) + f.read()
    return src


def _dmy_date_field_body():
    src = _read_app_py()
    start = src.index("def _dmy_date_field(")
    # next top-level "def " after this one marks the end of the function
    end = src.index("\ndef ", start + 1)
    return src[start:end]


def test_dmy_date_field_no_longer_writes_directly_into_its_own_widget_key():
    body = _dmy_date_field_body()
    # the confirmed-broken line must be gone: no direct assignment of the picked value into the
    # same `key` the text_input widget above was instantiated with.
    assert 'st.session_state[key] = picked.strftime("%d/%m/%Y")' not in body


def test_dmy_date_field_queues_the_picked_value_into_a_separate_pending_slot():
    body = _dmy_date_field_body()
    assert 'pending_key = f"{key}__pending"' in body
    assert 'st.session_state[pending_key] = picked.strftime("%d/%m/%Y")' in body


def test_dmy_date_field_consumes_the_pending_value_before_the_widget_is_created():
    body = _dmy_date_field_body()
    # the pending-value consumption must happen BEFORE st.text_input(..., key=key, ...) is
    # called - otherwise the fix reintroduces the exact same ordering bug it's fixing.
    pending_idx = body.index("if pending_key in st.session_state:")
    widget_idx = body.index("st.text_input(label,")
    assert pending_idx < widget_idx, (
        "the pending-value consumption must happen before the text_input widget is instantiated"
    )
    assert "st.session_state[key] = st.session_state.pop(pending_key)" in body


def test_dmy_date_field_still_reruns_after_queueing_a_picked_date():
    body = _dmy_date_field_body()
    idx = body.index('st.session_state[pending_key] = picked.strftime("%d/%m/%Y")')
    tail = body[idx: idx + 200]
    assert "st.rerun()" in tail
