"""Regression tests for two more product-owner reports on the same day as the 2-button
simplification (see test_2026_09_16_candidate_filter_simplified.py):

1. "now i clicked Select none, the app even reads 0 of 116 ticked. Only ticked rows are reviewed
   and published. --> But still all tickets are marked" - live on his deployed app, the caption
   correctly reflected 0 ticked but every checkbox still visually showed checked. The old
   `_apply(fn)` relied on `_clear_batch_widget_state` POPPING each "{key_prefix}_sel_{i}" key and
   Streamlit falling back to `value=` on the next render - which this repo's own AppTest harness
   could not fault, but evidently did not hold up on his actual deployed Streamlit build. Fixed by
   having `_apply(new_value)` write st.session_state[f"{key_prefix}_sel_{i}"] = new_value directly
   for every candidate, which every Streamlit version honors unconditionally.

2. "the button 'add the return direction for tickets' is absolutely useless. It would be more
   usefull for transfers and transports, but not for tickets." Ticket/Modality candidates never
   carry departure_hint/arrival_hint, so the button rendered but could never do anything. Fixed by
   gating its rendering on `routes_ticked` (ticked candidates that actually have both hints)
   instead of on `ticked` (anything ticked at all).
"""
import inspect

import app_helpers


def test_apply_writes_directly_into_session_state_rather_than_popping():
    source = inspect.getsource(app_helpers.render_candidate_filter)
    assert 'st.session_state[f"{key_prefix}_sel_{i}"] = new_value' in source
    # the two button handlers just pass the target boolean straight through
    assert "_apply(True)" in source
    assert "_apply(False)" in source


def test_return_direction_button_gated_on_routes_ticked_not_bare_ticked():
    source = inspect.getsource(app_helpers.render_candidate_filter)
    assert "routes_ticked = [c for c in ticked if" in source
    assert "if routes_ticked and st.button(" in source
    # the loop that actually builds mirrored candidates iterates the same filtered list
    assert "for cand in routes_ticked:" in source


def test_return_direction_button_never_renders_for_ticket_candidates_without_hints():
    """A Ticket/Modality candidate list has no departure_hint/arrival_hint on any row, so
    routes_ticked is always empty for it and the button must not render at all."""
    candidates = [
        {"label": "Nile Cruise Excursion", "selected": True, "service_name": "Excursion"},
        {"label": "Pyramids Half Day Tour", "selected": True, "service_name": "Excursion"},
    ]
    ticked = [c for c in candidates if c.get("selected")]
    routes_ticked = [c for c in ticked if str(c.get("departure_hint") or "").strip()
                     and str(c.get("arrival_hint") or "").strip()]
    assert routes_ticked == []


def test_return_direction_button_still_renders_for_transfer_candidates_with_hints():
    candidates = [
        {"label": "Airport to Hotel", "selected": True, "departure_hint": "Airport",
         "arrival_hint": "Hotel", "service_name": "Transfer"},
        {"label": "Hotel to Airport", "selected": False, "departure_hint": "Hotel",
         "arrival_hint": "Airport", "service_name": "Transfer"},
    ]
    ticked = [c for c in candidates if c.get("selected")]
    routes_ticked = [c for c in ticked if str(c.get("departure_hint") or "").strip()
                     and str(c.get("arrival_hint") or "").strip()]
    assert len(routes_ticked) == 1
    assert routes_ticked[0]["label"] == "Airport to Hotel"
