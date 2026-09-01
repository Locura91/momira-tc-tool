"""Regression tests for full-app-audit CRITICAL #2 (2026-09-01): "Update ticket"/"Update tour"
silently deactivated the live record on every use.

CONFIRMED REAL BUG: build_ticket_payloads()/build_closed_tour_payloads() always set
active=False on the main payload ("LOCKED default" - correct for a brand-new record, which
must land as a draft for human review before going live). The SAME payload dict is reused
verbatim by app.py's "Update an existing ticket's details" / "Update an existing tour's
details" branches, which build their update payload as `dict(payloads["main_ticket_payload"])`
/ `dict(payloads["main_tour_payload"])` with no override - so active=False rode straight
through to a full-body PUT on every update, silently taking a live, active ticket/tour off
sale while the UI reported "✅ updated." The very next call (a pricing update) then failed the
app's own ACTIVE-required guard.

Fix: app.py now overrides update_payload["active"] with the live fetched record's own current
active state (st.session_state.tk_fetched_ticket / st.session_state.fetched_tour - the same
"Check what's already online" snapshot already used to inherit currency/min/maxPassengers on
Ticket update) immediately before calling client.update_ticket()/client.update_closed_tour().

app.py runs top-level Streamlit calls on import and can't be safely imported in a test process
(same constraint noted in test_2026_08_31_closedtour_child_discount_visibility.py) - these tests
read its source text instead, confirming the override is present and sits between building the
update payload and the actual publish call at each of the two call sites.
"""
import os
import re

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_ticket_update_carries_forward_the_live_active_state():
    src = _read_app_py()
    # Locate the "Update an existing ticket's details" branch's update-payload construction.
    marker = 'update_payload = dict(payloads["main_ticket_payload"])'
    idx = src.index(marker)
    # The publish call itself, so we can bound the window we search for the override in.
    publish_idx = src.index('result = client.update_ticket(supplier_id, update_payload)', idx)
    window = src[idx:publish_idx]
    assert 'tk_fetched_ticket' in window, (
        "update_ticket's payload must be corrected using the live fetched ticket snapshot "
        "before publish, or active=False (the LOCKED create-time default) silently deactivates "
        "the live ticket on every update"
    )
    assert 'update_payload["active"]' in window, (
        "the update payload's active field must be explicitly overridden, not left as "
        "build_ticket_payloads' hardcoded active=False"
    )


def test_closed_tour_update_carries_forward_the_live_active_state():
    src = _read_app_py()
    marker = 'update_payload = dict(payloads["main_tour_payload"])'
    idx = src.index(marker)
    # This branch calls update_closed_tour via try_code_variants - locate that call to bound
    # the search window the same way as the ticket test above.
    publish_idx = src.index('client.update_closed_tour(payloads["supplier_id"]', idx)
    window = src[idx:publish_idx]
    assert 'fetched_tour' in window, (
        "update_closed_tour's payload must be corrected using the live fetched tour snapshot "
        "before publish, or active=False (the LOCKED create-time default) silently deactivates "
        "the live tour on every update"
    )
    assert 'update_payload["active"]' in window, (
        "the update payload's active field must be explicitly overridden, not left as "
        "build_closed_tour_payloads' hardcoded active=False"
    )


def test_main_ticket_payload_still_defaults_active_false_for_brand_new_tickets():
    # Guardrail: the fix must NOT touch the CREATE-time default - a brand-new ticket must still
    # land inactive/draft for human review, only UPDATE should inherit the live state.
    from builder import build_ticket_payloads
    import inspect
    src = inspect.getsource(build_ticket_payloads)
    assert re.search(r'active\s*=\s*False,?\s*#\s*LOCKED default', src), (
        "build_ticket_payloads must still hardcode active=False as the create-time default - "
        "this test guards against the CRITICAL #2 fix being applied in the wrong place"
    )


def test_main_tour_payload_still_defaults_active_false_for_brand_new_tours():
    from builder import build_closed_tour_payloads
    import inspect
    src = inspect.getsource(build_closed_tour_payloads)
    assert re.search(r'active\s*=\s*False\s*#\s*LOCKED', src), (
        "build_closed_tour_payloads must still hardcode active=False as the create-time "
        "default - this test guards against the CRITICAL #2 fix being applied in the wrong "
        "place"
    )
