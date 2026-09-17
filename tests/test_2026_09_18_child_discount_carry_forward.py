"""Regression tests for a real product-owner report (2026-09-18):

    "extra child allowed in closedtour creation is not working in travel c. also the child
    discount must be added to all modality fields."

This file covers the SECOND half only (child_discount_percentage not propagating to every
Modality in a multi-modality ClosedTour). The first half (extra_child_allowed) is a confirmed,
already-documented Travel Compositor API limitation with no code fix available - see
claude/closedtour-extra-child-allowed-2026-08-26.md - and has its own existing regression test
(tests/test_2026_08_26_extra_child_allowed.py) asserting the payload never contains it.

ROOT CAUSE: each Modality in both multi-Modality ClosedTour flows is extracted independently by
its own AI call against the SAME shared source document, and doesn't always re-detect a
document-wide child_discount_percentage on every single Modality even when the source states it
once for the whole tour - the exact same class of gap already fixed for supplements (ClosedTour
supplements are tour-wide by house rule - see claude/supplement-rules.md).

FIX: mirrors the existing supplements carry-forward pattern in both flows, but with "fill only if
missing" semantics (not an unconditional overwrite like supplements) - a Modality that DID detect
its own (possibly genuinely different) discount is never silently clobbered:

- flows/multi_tour.py ("Create a new ClosedTour", multiple Modalities at once): falls back to
  Modality 1's ("modalities[0]") own child_discount_percentage.
- flows/multi_modality.py ("add Modality to an existing tour", a flat queue with no "Modality 0"
  concept): falls back to the first queue item's ("queue[0]") own child_discount_percentage, since
  that's the first one ever reviewed/extracted by construction.

app.py/flows/*.py can't be imported in a test process (heavy top-level Streamlit/API-client
setup), so this wiring is verified by reading the source text directly, per this suite's
established pattern (see test_2026_09_03_min_pax_guaranteed_departure.py).
"""
import os

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
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


def _function_source(src, def_line):
    start = src.index(def_line)
    end = src.index("\ndef ", start + len(def_line))
    return src[start:end]


# ======================================================================
# flows/multi_tour.py - "Create a new ClosedTour"
# ======================================================================
def test_multi_tour_flow_fills_missing_child_discount_from_modality_one():
    src = _read_app_py()
    window = _function_source(
        src, "def render_multi_tour_flow(client, supplier_id, currency, on_request, release_days, url, uploaded_files,")

    supplements_idx = window.index('mod["data"]["supplements"] = copy.deepcopy(modalities[0]["data"].get("supplements", []))')
    discount_idx = window.index(
        'mod["data"]["child_discount_percentage"] = \\\n'
        '                        modalities[0]["data"].get("child_discount_percentage")')
    # comes right after the existing supplements carry-forward, same file/phase
    assert supplements_idx < discount_idx

    window_around_discount = window[discount_idx - 400:discount_idx]
    assert 'mod["data"].get("child_discount_percentage") is None' in window_around_discount
    assert "midx > 0" in window_around_discount


# ======================================================================
# flows/multi_modality.py - "add Modality to an existing tour"
# ======================================================================
def test_multi_modality_flow_fills_missing_child_discount_from_first_queue_item():
    src = _read_app_py()
    window = _function_source(src, "def render_multi_modality_flow(client, url=None, uploaded_files=None):")

    extract_idx = window.index(
        'current["data"] = extract_option_only_data(st.session_state.mm_raw_text, human_hint=current["hint"])')
    discount_idx = window.index(
        'current["data"]["child_discount_percentage"] = \\\n'
        '                    queue[0]["data"].get("child_discount_percentage")')
    assert extract_idx < discount_idx

    window_around_discount = window[discount_idx - 400:discount_idx]
    assert 'current["data"].get("child_discount_percentage") is None' in window_around_discount
    assert "idx > 0" in window_around_discount
    assert 'queue[0]["data"]' in window_around_discount
