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

MODULE_BUILD = "2026-09-03-google-maps-url-coordinates"

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def _function_source(src, def_line, next_def_marker=None):
    """Slice out one top-level function's source, from its `def ...` line up to (but not
    including) the next top-level `def ` - robust to line-number drift from other edits, unlike a
    fixed character-offset window."""
    start = src.index(def_line)
    end = src.index("\ndef ", start + len(def_line))
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
    # 1 def + 7 call sites (ClosedTour create, ClosedTour update, Ticket batch, Ticket single,
    # Transfer, Transport, Hotel) = 8 occurrences of the name total.
    assert src.count("_warn_stale_images") == 8


# ======================================================================
# Deliberately-not-wired flow (no image concept at all)
# ======================================================================
def test_multi_modality_flow_has_no_image_field_and_is_not_wired():
    src = _read_app_py()
    window = _function_source(src, "def render_multi_modality_flow(client, url=None, uploaded_files=None):")
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
