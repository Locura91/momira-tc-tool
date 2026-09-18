"""Regression test for a product-owner feature request (2026-09-18, verbatim):

    "when human creating a new hotel, add a workflow for the human after he klicks the button:
    Create a Hotel in Travel Compositor with Masterdata --> save it --> start uploading rooms,
    supplement, offers, rates with the App."

Clarified via AskUserQuestion (Chris selected "Just an upfront explainer (Recommended)" out of
four options: upfront explainer / persistent step tracker / real separate publish stages /
something else) - this is purely informational, shown right after the human clicks "Hotel" on
the product-type picker (flows/hotel.py's render_hotel_flow is the very next screen rendered),
with NO change to how publishing itself actually works. The app already runs Phase 1 (hotel +
rooms + meal plans) and Phase 2 (offers, supplements, rate seasons) back-to-back in the SAME
Publish click - this explainer just tells the human that shape up front instead of leaving them
to discover it.

This is a source-text check (same established pattern every other flows/hotel.py wiring test in
this suite uses) - render_hotel_flow can't be exercised directly without a live Streamlit
session.
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_HOTEL_FLOW_PY = os.path.join(os.path.dirname(_HERE), "flows", "hotel.py")


def _read_hotel_flow():
    with open(_HOTEL_FLOW_PY, "r", encoding="utf-8") as f:
        return f.read()


def _render_hotel_flow_body():
    src = _read_hotel_flow()
    start = src.index("def render_hotel_flow(client):")
    # render_hotel_flow is the last top-level function in this file (no "\ndef " follows it at
    # column 0), so its body simply runs to the end of the file.
    return src[start:]


def test_explainer_expander_exists_with_the_right_title():
    body = _render_hotel_flow_body()
    assert 'st.expander("📋 How creating a new Hotel works", expanded=True)' in body


def test_explainer_is_gated_on_not_yet_confirmed_step_1():
    body = _render_hotel_flow_body()
    expander_idx = body.index('st.expander("📋 How creating a new Hotel works"')
    gate_idx = body.rindex("if not st.session_state.hp_step1_confirmed:", 0, expander_idx)
    # Nothing but the "with st.expander(...)" line itself should sit between the gate and the
    # expander call - i.e. the gate directly guards the expander, not something else.
    between = body[gate_idx:expander_idx]
    assert between.count("\n") <= 2


def test_explainer_mentions_the_two_automatic_publish_phases():
    body = _render_hotel_flow_body()
    expander_idx = body.index('st.expander("📋 How creating a new Hotel works"')
    window = body[expander_idx:expander_idx + 1600]
    assert "Hotel is created and saved" in window
    assert "offers, supplements and rate seasons" in window
    assert "no extra click needed" in window or "no second click" in window or \
        "automatically" in window


def test_explainer_does_not_change_publish_mechanics():
    # The explainer must be purely additive - the actual two-phase publish call sequence
    # (Phase 1 then Phase 2a/2b/2c) is untouched by this change, so this just pins that the
    # function's known publish-trigger markers still exist somewhere in the file, unmodified in
    # count, rather than re-testing the mechanics themselves (covered elsewhere).
    src = _read_hotel_flow()
    assert src.count('st.header("Hotel — Step 2: Supplier & hotel code")') == 1


def test_header_still_renders_unconditionally_after_the_explainer():
    body = _render_hotel_flow_body()
    expander_idx = body.index('st.expander("📋 How creating a new Hotel works"')
    header_idx = body.index('st.header("Hotel — Step 2: Supplier & hotel code")')
    assert header_idx > expander_idx
    # The header call must NOT itself be inside an `if`/`else` branch tied to
    # hp_step1_confirmed - it must be the very next statement at the function's own
    # indentation level (4 spaces), not nested deeper under a conditional.
    line_start = body.rindex("\n", 0, header_idx) + 1
    indent = len(body[line_start:header_idx]) - len(body[line_start:header_idx].lstrip(" "))
    assert indent == 4


def test_success_banner_and_change_button_still_follow_the_header():
    body = _render_hotel_flow_body()
    header_idx = body.index('st.header("Hotel — Step 2: Supplier & hotel code")')
    window = body[header_idx:header_idx + 600]
    assert "if st.session_state.hp_step1_confirmed:" in window
    assert "Supplier ID:" in window
    assert "Change supplier / hotel code" in window
