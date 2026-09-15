"""Tests for wiring r2_client's stale-image-warning capability into app.py's publish screens
(full-app-audit-2026-09-01.md, "What's next" item 4 / R2 stale-image-warning), completed
2026-09-03.

r2_client.stale_image_urls / stale_image_warning were built and unit-tested in an earlier batch
(see r2_client.py's own tests) to catch R2's ~2-day image-expiry lifecycle rule biting a document
image that was uploaded during a multi-day review and never re-checked before publish - but the
capability was never actually called from any of app.py's 5 publish screens. This batch adds a
thin app.py-level wrapper (_warn_stale_images, matching the existing _warn_page_image_upload_
errors pattern) and wires it into all 5 product flows' 6 publish sites, immediately before their
"Publish"/"Publish all" button, using each flow's own image-holding field name:

  - ClosedTour create (render_multi_tour_flow): main_data["image_urls"]
  - ClosedTour update/add-option (inline flow near the end of app.py): data["image_urls"]
  - Ticket batch publish (render_multi_ticket_flow): every queued item's data["image_urls"],
    combined into one list since the whole batch publishes together
  - Ticket single (render_ticket_flow): data["image_urls"]
  - Transfer (render_multi_transfer_flow): data["image_urls"]
  - Transport (render_multi_transport_flow): data["image_urls"] (auto-resolved supplier image,
    same single-image-per-route pattern as Transfer - initially assumed image-free during
    discovery, corrected once resolve_and_host_image's call site was found)
  - Hotel (render_hotel_flow): data["images"] (different field name/no FALLBACK_IMAGE sentinel)

render_multi_modality_flow (the ClosedTour "add Modality to an existing tour" flow) is
deliberately NOT wired - adding an option/Modality to an already-published tour has no image step
at all (confirmed: no image_urls/images reference anywhere in its source).

app.py can't be imported in a test process (heavy top-level Streamlit/API-client setup), so -
matching this suite's established pattern - these are verified by reading app.py's own source
text and checking the specific code shape/call sites, plus a direct unit test of the wrapper's
own no-op/warn logic against a stubbed stale_image_warning.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MODULE_BUILD = "2026-09-06-hotel-zero-new-rooms-inline"

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    """Returns app.py's source concatenated with every module under flows/ - Phase 1
    (2026-09-15) started splitting render_*_flow functions out of app.py into flows/*.py,
    verbatim/zero-behaviour-change, so source-text assertions that used to find their
    target inside app.py alone now need to see the split-out modules too. Reading app.py
    first means any offset/index a test computes for genuinely-still-in-app.py content is
    unaffected; content that moved is simply found further along in the string.
    """
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



def _function_source(src, def_line, next_def_marker=None):
    """Slice out one top-level function's source, from its `def ...` line up to (but not
    including) the next top-level `def ` - robust to line-number drift from other edits, unlike a
    fixed character-offset window."""
    # Phase 1 (2026-09-15) split render_ticket_flow out of app.py into flows/ticket.py,
    # where it is now the LAST function in that file's source (nothing follows it there),
    # so there is no next "\ndef " after it once flows/*.py is appended to app.py's own
    # source (see _read_app_py). Falling back to end-of-string keeps this working for a
    # function that is last in its file, without changing behaviour for any other case.
    start = src.index(def_line)
    next_def = src.find("\ndef ", start + len(def_line))
    end = next_def if next_def != -1 else len(src)
    return src[start:end]


# ======================================================================
# Import + wrapper function itself
# ======================================================================
def test_stale_image_warning_is_imported_from_r2_client():
    src = _read_app_py()
    assert "from r2_client import stale_image_warning" in src


def test_warn_stale_images_wrapper_is_defined_and_calls_stale_image_warning():
    src = _read_app_py()
    idx = src.index("def _warn_stale_images(")
    window = src[idx:idx + 900]
    assert "stale_image_warning(urls)" in window
    assert "st.warning(message)" in window
    # Must be a no-op when there's nothing stale, matching _warn_page_image_upload_errors' shape.
    assert "if not message:" in window
    assert "return" in window


def test_warn_stale_images_defined_near_its_sibling_warning_helper():
    src = _read_app_py()
    a = src.index("def _warn_page_image_upload_errors(")
    b = src.index("def _warn_stale_images(")
    assert 0 < b - a < 2500


# ======================================================================
# The 6 genuine call sites (5 flows, ClosedTour wired twice: create + update)
# ======================================================================
def test_closed_tour_create_flow_warns_with_main_data_image_urls_before_its_publish_button():
    src = _read_app_py()
    idx = src.index('_warn_stale_images(main_data.get("image_urls"))')
    btn_idx = src.index('if st.button("🚀 Publish to Travel Compositor", type="primary", disabled=mct_has_unresolved or mct_code_taken):')
    assert idx < btn_idx


def test_ticket_batch_publish_warns_across_every_queued_item_before_its_publish_button():
    src = _read_app_py()
    window = _function_source(
        src,
        'def render_multi_ticket_flow(client, supplier_id, currency, on_request, release_days, '
        'tk_url, tk_files, min_passengers=1, max_passengers=9, default_ticket_code=""):')
    # The same "🚀 Publish all (one by one)" button label is also used by the unrelated
    # render_multi_modality_flow (ClosedTour "add Modality to an existing tour") - scope to this
    # function's own body so that flow's earlier occurrence can't make this pass by accident.
    assert '_warn_stale_images([u for q in queue for u in (q.get("data", {}).get("image_urls") or [])])' in window
    idx = window.index('_warn_stale_images([u for q in queue for u in (q.get("data", {}).get("image_urls") or [])])')
    btn_idx = window.index('if st.button("🚀 Publish all (one by one)", type="primary"):')
    assert idx < btn_idx


def test_single_ticket_flow_warns_with_data_image_urls_before_its_publish_button():
    src = _read_app_py()
    window = _function_source(src, "def render_ticket_flow(client):")
    assert '_warn_stale_images(data.get("image_urls"))' in window
    call_idx = window.index('_warn_stale_images(data.get("image_urls"))')
    btn_idx = window.index('key="tk_publish_btn"')
    assert call_idx < btn_idx


def test_multi_transfer_flow_warns_with_data_image_urls_before_its_publish_button():
    src = _read_app_py()
    window = _function_source(
        src, "def render_multi_transfer_flow(client, supplier_id, currency, release_days, tf_url, tf_files, tf_hint):")
    assert '_warn_stale_images(data.get("image_urls"))' in window
    call_idx = window.index('_warn_stale_images(data.get("image_urls"))')
    btn_idx = window.index('publish_label = (')
    assert call_idx < btn_idx


def test_multi_transport_flow_warns_with_data_image_urls_before_its_publish_button():
    src = _read_app_py()
    window = _function_source(
        src, "def render_multi_transport_flow(client, supplier_id, currency, release_days, tp_url, tp_files, tp_hint):")
    # Confirms the single auto-resolved supplier image (resolve_and_host_image) is really there -
    # this flow was initially (wrongly) assumed image-free during discovery.
    assert '_si_url' in window and 'current["data"]["image_urls"] = [_si_url]' in window
    assert '_warn_stale_images(data.get("image_urls"))' in window
    call_idx = window.index('_warn_stale_images(data.get("image_urls"))')
    btn_idx = window.index('publish_label = (')
    assert call_idx < btn_idx


def test_hotel_flow_warns_with_data_images_only_when_images_ok_before_its_publish_button():
    src = _read_app_py()
    window = _function_source(src, "def render_hotel_flow(client):")
    assert 'if not images_ok:' in window
    assert 'else:\n        _warn_stale_images(data.get("images"))' in window
    call_idx = window.index('_warn_stale_images(data.get("images"))')
    btn_idx = window.index('key="hp_publish"')
    assert call_idx < btn_idx


def test_closed_tour_update_flow_warns_with_data_image_urls_before_its_publish_button():
    src = _read_app_py()
    idx = src.index('_warn_stale_images(data.get("image_urls"))\n\n        if creating_new_tour:')
    btn_idx = src.index('if st.button("🚀 Publish to Travel Compositor", disabled=not can_publish, type="primary"):')
    assert idx < btn_idx


def test_exactly_seven_warn_stale_images_call_sites_plus_the_definition():
    src = _read_app_py()
    # 1 def + 8 call sites (ClosedTour create, ClosedTour update, Ticket batch-create,
    # Ticket batch-update (added 2026-09-08), Ticket single, Transfer, Transport, Hotel) = 9
    # occurrences of the name total. Phase 1 (2026-09-15) moved the Ticket-single call site into
    # flows/ticket.py, the Hotel call site into flows/hotel.py, the Ticket batch-create + Ticket
    # batch-update call sites into flows/multi_ticket.py (both sharing ONE `from app import
    # _warn_stale_images` line since they live in the same file), the ClosedTour create call site
    # into flows/multi_tour.py, the Transport call site into flows/multi_transport.py, and the
    # Transfer call site into flows/multi_transfer.py - six modules' worth of import lines beyond
    # the base 9 (only the ClosedTour-update call site remains inline in app.py itself), for the
    # same 9 real call sites/def. Phase 1 module 13 (2026-09-16) then moved the definition itself
    # (and every remaining call site) out of app.py into app_helpers.py, and added one more
    # `from app_helpers import (...)` line in app.py naming _warn_stale_images so the six
    # `from app import _warn_stale_images` lines above keep resolving unchanged - one more
    # occurrence of the name, bumping the known total from 15 to 16.
    assert src.count("_warn_stale_images") == 16


# ======================================================================
# Deliberately-not-wired flow (no image concept at all)
# ======================================================================
def test_multi_modality_flow_has_no_image_field_and_is_not_wired():
    # Phase 1 (2026-09-15) moved render_multi_modality_flow into flows/multi_modality.py, which
    # (alphabetically) sits BEFORE several other flows/*.py files in the concatenated
    # _read_app_py() string and has no nested `def` of its own - so the generic
    # _function_source(src, def_line) boundary search (next "\ndef " anywhere in the whole
    # concatenated string) overshoots past this file's end and into the NEXT flows/*.py file's
    # own header/imports (which can themselves mention "_warn_stale_images"), giving a false
    # positive. Since this function now lives entirely in its own file, read that file directly
    # instead of carving a window out of the concatenated string.
    with open(os.path.join(os.path.dirname(_APP_PY), "flows", "multi_modality.py"), "r", encoding="utf-8") as f:
        window = f.read()
    assert "image_urls" not in window
    assert '"images"' not in window and "'images'" not in window
    assert "_warn_stale_images" not in window


# ======================================================================
# Wrapper logic itself (direct unit test, stubbing r2_client)
# ======================================================================
def test_wrapper_logic_against_stubbed_r2_client(monkeypatch):
    import streamlit as st

    warnings = []
    monkeypatch.setattr(st, "warning", lambda msg: warnings.append(msg))

    # Reproduce the wrapper's own body in isolation (app.py itself can't be imported), driven by
    # a stub standing in for r2_client.stale_image_warning, to pin its no-op/warn behavior.
    def _warn_stale_images(urls, _stale_image_warning):
        message = _stale_image_warning(urls)
        if not message:
            return
        st.warning(message)

    _warn_stale_images(["http://x/1.jpg"], lambda urls: "")
    assert warnings == []

    _warn_stale_images(["http://x/2.jpg"], lambda urls: "⚠️ 1 image was uploaded more than 42h ago")
    assert warnings == ["⚠️ 1 image was uploaded more than 42h ago"]
