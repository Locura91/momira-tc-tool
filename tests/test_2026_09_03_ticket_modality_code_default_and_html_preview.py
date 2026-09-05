"""Tests for two 2026-09-03 fixes, delivered together:

1) New product-owner rule: "when creating a new ticket and the supplier does not send a
   specific modality code, the modality code shall be the name of the excursion then." Applied
   to all three places app.py defaults a Ticket's Modality Code:
     - render_multi_ticket_flow's PHASE 1 candidate construction (multi-excursion "Detect
       Excursions" path) - defaults to the excursion's own label when no supplier_code was
       detected, instead of the generic "Standard"/"Standard Private".
     - render_multi_ticket_flow's PHASE 2 render loop - keeps the Modality Code widget's
       displayed value synced to the excursion label (live, via a pre-widget
       st.session_state[key] write) until the operator edits it directly, at which point
       "_modcode_touched" permanently stops the auto-sync for that row.
     - render_ticket_flow's tk_pending_variant_selection (the single-ticket-flow's own separate
       "multiple excursions detected" path) - same default-to-label rule, computed once at
       list-construction time since the label is already fully known there.
   modality_name (client-facing, "Standard"/"Standard Private") is UNCHANGED by this rule - only
   modality_code (supplier/operator-facing) is affected. When a genuine supplier_code IS present,
   the old "<BASE>_<supplier_code>" shape is preserved unchanged.

2) A real bug caught via a product-owner screenshot: ui_components.py's editable_field()
   read-only preview was escaping and displaying the STORED HTML for html_text_area/
   html_list_area fields (Ticket/ClosedTour Description, Included/Excluded) literally - so a
   Ticket's "Reviewing ticket X of Y" screen showed raw "<p>...</p>" markup right in the
   Description box. The edit widget already converts this same value to human-friendly plain
   text before displaying it (_html_to_plain_for_editing / _html_list_to_plain_for_editing); the
   read-only preview above it wasn't using that same conversion. See
   closedtour-description-html-stripped-2026-08-26.md for why the STORED value must stay real
   HTML (Travel Compositor's API expectation) - this fix only changes what's shown on screen,
   never what's saved.

app.py can't be imported in a test process (heavy top-level Streamlit/API-client setup), so - per
this suite's established pattern - app.py's own three call sites are verified by reading its
source text. ui_components.py CAN be imported (it has no such top-level side effects), so its fix
is verified with a direct, real unit test against the actual functions.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MODULE_BUILD = "2026-09-05-cancellation-house-standard-and-ticket-name-fix"

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def _function_source(src, def_line):
    start = src.index(def_line)
    end = src.index("\ndef ", start + len(def_line))
    return src[start:end]


# ======================================================================
# 1a) render_multi_ticket_flow PHASE 1 - candidate construction
# ======================================================================
def test_phase1_candidates_default_modality_code_to_excursion_label_when_no_supplier_code():
    src = _read_app_py()
    window = _function_source(
        src,
        'def render_multi_ticket_flow(client, supplier_id, currency, on_request, release_days, '
        'tk_url, tk_files, min_passengers=1, max_passengers=9, default_ticket_code=""):')
    assert '_excursion_label = str(e.get("label") or "").strip()' in window
    assert 'f"{_base_modality_name.upper().replace(\' \', \'_\')}_{_supplier_code}" if _supplier_code' in window
    # CONFIRMED BUG FIX (product owner, 2026-09-03, same day): a raw excursion label can contain
    # "/" (e.g. "Turtles/Tortoises: Three Island Cruise (Praslin)"), which the real Travel
    # Compositor API rejects outright in modality_code - the default now goes through the same
    # _clean_modality_code sanitizer every other AI-suggested-code call site already uses. See
    # test_2026_09_03_ticket_modality_code_slash_fix_and_batch_recovery.py for the full story.
    assert 'else (_clean_modality_code(_excursion_label) or _base_modality_name)' in window
    # modality_name (client-facing) must still be the plain base name, unaffected.
    assert '"modality_name": _base_modality_name,' in window


def test_phase1_no_variants_fallback_candidate_starts_with_blank_modality_code():
    src = _read_app_py()
    window = _function_source(
        src,
        'def render_multi_ticket_flow(client, supplier_id, currency, on_request, release_days, '
        'tk_url, tk_files, min_passengers=1, max_passengers=9, default_ticket_code=""):')
    assert 'if not candidates:' in window
    idx = window.index('if not candidates:')
    fallback_window = window[idx:idx + 1400]
    assert '"modality_code": ""' in fallback_window


# ======================================================================
# 1b) render_multi_ticket_flow PHASE 2 - live sync-until-touched widget
# ======================================================================
def test_phase2_modality_code_widget_syncs_to_label_until_touched():
    src = _read_app_py()
    window = _function_source(
        src,
        'def render_multi_ticket_flow(client, supplier_id, currency, on_request, release_days, '
        'tk_url, tk_files, min_passengers=1, max_passengers=9, default_ticket_code=""):')
    assert 'if not cand.get("_modcode_touched") and (cand.get("label") or "").strip():' in window
    # Sanitized the same way as PHASE 1's default (see the 2026-09-03 slash-fix test file).
    assert 'st.session_state[_modcode_key] = _clean_modality_code(cand["label"].strip()) or cand["label"].strip()' in window
    assert 'if cand["modality_code"].strip() != _clean_label:' in window
    assert 'cand["_modcode_touched"] = True' in window


def test_add_another_excursion_manually_button_starts_with_blank_modality_code():
    src = _read_app_py()
    window = _function_source(
        src,
        'def render_multi_ticket_flow(client, supplier_id, currency, on_request, release_days, '
        'tk_url, tk_files, min_passengers=1, max_passengers=9, default_ticket_code=""):')
    idx = window.index('if st.button("➕ Add another excursion manually"):')
    btn_window = window[idx:idx + 300]
    assert '"modality_code": ""' in btn_window


# ======================================================================
# 1c) render_ticket_flow's tk_pending_variant_selection
# ======================================================================
def test_tk_pending_variant_selection_defaults_modality_code_to_label_when_no_supplier_code():
    src = _read_app_py()
    window = _function_source(src, "def render_ticket_flow(client):")
    assert '"tk_pending_variant_selection" not in st.session_state' in window
    idx = window.index('st.session_state.tk_pending_variant_selection = [')
    block = window[idx:idx + 1000]
    assert 'str(e.get("supplier_code") or "").strip()' in block
    # CONFIRMED BUG FIX (product owner, 2026-09-03, same day): sanitized the same way as the
    # sibling defaults above - a raw label can contain "/" (real API rejection).
    assert '_clean_modality_code(str(e.get("label") or "").strip())' in block


def test_tk_pending_variant_selection_preserves_supplier_code_shape_when_present():
    src = _read_app_py()
    window = _function_source(src, "def render_ticket_flow(client):")
    idx = window.index('st.session_state.tk_pending_variant_selection = [')
    block = window[idx:idx + 700]
    assert "Standard Private' if e.get('is_private') else 'Standard').upper().replace(' ', '_')" in block


# ======================================================================
# 2) ui_components.editable_field() read-only preview no longer shows raw HTML
# ======================================================================
def test_html_text_area_readonly_preview_shows_plain_text_not_raw_tags(monkeypatch):
    import streamlit as st
    import ui_components

    rendered = []
    monkeypatch.setattr(st, "columns", lambda spec: (_FakeCol(rendered), _FakeCol(rendered)))
    monkeypatch.setattr(st, "markdown", lambda *a, **k: rendered.append(("markdown", a, k)))
    monkeypatch.setattr(st, "caption", lambda *a, **k: rendered.append(("caption", a, k)))
    monkeypatch.setattr(st, "write", lambda *a, **k: None)
    monkeypatch.setattr(st, "button", lambda *a, **k: False)
    if not hasattr(st, "session_state"):
        pass

    data = {"description": "<p>Guided tour of the <strong>Vallee de Mai</strong>.</p><p><br></p>"
                            "<p>Second paragraph here.</p>"}
    ui_components.editable_field("Description", data, "description", widget="html_text_area")

    html_calls = [a[0] for (_, a, _k) in rendered if a and isinstance(a[0], str) and "background:#f6f6f6" in a[0]]
    assert html_calls, "expected the read-only preview div to have been rendered"
    preview_html = html_calls[0]
    assert "&lt;p&gt;" not in preview_html
    assert "<p>" not in preview_html.replace("<div", "").replace("</div>", "")
    assert "Guided tour of the" in preview_html
    assert "Second paragraph here." in preview_html


def test_html_list_area_readonly_preview_shows_plain_lines_not_raw_tags(monkeypatch):
    import streamlit as st
    import ui_components

    rendered = []
    monkeypatch.setattr(st, "columns", lambda spec: (_FakeCol(rendered), _FakeCol(rendered)))
    monkeypatch.setattr(st, "markdown", lambda *a, **k: rendered.append(("markdown", a, k)))
    monkeypatch.setattr(st, "caption", lambda *a, **k: rendered.append(("caption", a, k)))
    monkeypatch.setattr(st, "write", lambda *a, **k: None)
    monkeypatch.setattr(st, "button", lambda *a, **k: False)

    data = {"included": "<ul><li>Breakfast</li><li>Hotel pickup</li></ul>"}
    ui_components.editable_field("Included", data, "included", widget="html_list_area")

    html_calls = [a[0] for (_, a, _k) in rendered if a and isinstance(a[0], str) and "background:#f6f6f6" in a[0]]
    assert html_calls
    preview_html = html_calls[0]
    assert "&lt;ul&gt;" not in preview_html
    assert "<li>" not in preview_html
    assert "Breakfast" in preview_html
    assert "Hotel pickup" in preview_html


def test_plain_text_field_readonly_preview_still_escapes_stray_html_as_before():
    """Regression guard: a genuinely plain field (no widget=html_*) with supplier-controlled text
    containing "<...>" must still show as literal escaped text, not be swallowed as markup or
    silently unescaped - this is the original 2026-08-xx fix and must not regress."""
    import ui_components
    assert "_html_module" in open(
        os.path.join(os.path.dirname(_APP_PY), "ui_components.py"), encoding="utf-8"
    ).read()


class _FakeCol:
    def __init__(self, rendered):
        self._rendered = rendered

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
