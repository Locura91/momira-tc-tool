"""Regression tests for a real product-owner request (2026-09-21, verbatim):

    "Translating Closed Tours must work in bulk. Human selects an Supplier, the app shows a
    list with all closedtours, human can select all, or select none and then starts the
    translation. Keep in mind, the translation might take a while, because the text is long.
    We also split translation: either all languages, or only German, Spanish, French, Dutch,
    Polish, Italian, or ALL languages."

FOLLOW-UP (2026-09-22, verbatim): "could we not load all available closedtours from the
supplier and then the human selects all closedtorus that need an translation"

The first version of this picker required pasting known codes by hand, because this tool's own
API client (TranslationTCAPI = travelcompositor_api.TravelCompositorAPI) only had a single
closed-tour GET-by-code, no list endpoint - the Upload & Update tool's client (api_client.py)
already had one (GET /closedtour/{supplierId}, confirmed working there via
app_helpers.get_existing_tour_names' live duplicate-name check). That same endpoint
(get_closed_tours) was ported into travelcompositor_api.py, and the picker now calls it directly
to auto-load "all closedtours from the supplier" instead of asking for pasted codes - exactly
per this follow-up. Auto-loaded tours default UNCHECKED (loading everything a supplier has is
not the same as everything that needs translating); a paste-codes-by-hand fallback (the original
picker) is kept for when the listing call itself fails.

The "either all languages, or only <6 languages>, or ALL languages" request is served by the
EXISTING target_languages multiselect (already shared by every entity type, already defaults
to everything ticked, already lets a human untick down to any subset including exactly those
six) - a second, separate language-mode selector was deliberately NOT built: this tool already
removed a "test set vs all languages" toggle at the product owner's own request (see the
docstring right above the multiselect in translation_tool.py) specifically because a second
complete "mode" makes it too easy to think a translation run is complete when it only covered
a deliberately partial set. Reusing the one multiselect keeps that same safety property for
Closed Tours instead of re-introducing the pattern that was removed everywhere else.
"""
import inspect

import translation_tool as tt
import travelcompositor_api as tcapi


def test_single_code_text_input_is_gone():
    src = inspect.getsource(tt.render_translation_tool)
    assert 'st.text_input("Closed Tour Code"' not in src
    assert "tr_ct_code" not in src


def test_bulk_picker_is_wired_into_the_scope_step():
    src = inspect.getsource(tt.render_translation_tool)
    assert "_closed_tour_bulk_picker(supplier_id)" in src
    assert "closed_tour_codes = _closed_tour_bulk_picker(supplier_id)" in src


def test_travelcompositor_api_has_a_real_closed_tour_list_endpoint():
    """Ported from api_client.py's already-confirmed-working get_closed_tours - this tool's own
    client (travelcompositor_api.TravelCompositorAPI) previously lacked it entirely."""
    assert hasattr(tcapi.TravelCompositorAPI, "get_closed_tours")
    src = inspect.getsource(tcapi.TravelCompositorAPI.get_closed_tours)
    assert '/closedtour/{supplier_id}"' in src
    assert "first" in src and "limit" in src


def test_bulk_picker_auto_loads_from_the_real_list_endpoint_not_pasted_codes():
    src = inspect.getsource(tt._closed_tour_bulk_picker)
    assert "api.get_closed_tours(supplier_id, first=0, limit=200)" in src
    # The old "paste codes into a text area" UI must not be the primary path any more.
    assert 'st.text_area("Closed Tour codes"' not in src


def test_auto_loaded_tours_default_unselected():
    """"the human selects all closedtorus that need an translation" - loading everything a
    supplier has is not the same as everything that needs translating, so nothing should be
    pre-ticked just because it showed up in the auto-loaded list (unlike the manual paste
    fallback, where a pasted code is by definition something you wanted - see that test below)."""
    src = inspect.getsource(tt._closed_tour_bulk_picker)
    load_block = src[src.index("if st.button(label"):src.index("# A supplier switch invalidates")]
    assert 'st.session_state[f"tr_ct_pick_{t[\'code\']}"] = False' in load_block


def test_select_all_and_select_none_write_session_state_directly():
    # Same fix as app_helpers.render_candidate_filter's own select-all/none bug (2026-09-16):
    # writing st.session_state[key] = value directly for every candidate, not toggling/popping.
    src = inspect.getsource(tt._closed_tour_bulk_picker)
    assert 'st.button("Select all"' in src
    assert 'st.button("Select none"' in src
    assert 'st.session_state[f"tr_ct_pick_{t[\'code\']}"] = True' in src
    assert 'st.session_state[f"tr_ct_pick_{t[\'code\']}"] = False' in src


def test_supplier_switch_invalidates_the_previously_loaded_list():
    src = inspect.getsource(tt._closed_tour_bulk_picker)
    assert 'st.session_state.get("tr_ct_list_supplier") != supplier_id' in src


def test_listing_failure_falls_back_to_the_manual_paste_picker():
    """A listing-endpoint outage must not block bulk translation entirely - it degrades to the
    original paste-codes-by-hand flow instead."""
    src = inspect.getsource(tt._closed_tour_bulk_picker)
    assert "_closed_tour_manual_picker(supplier_id)" in src
    assert "tr_ct_list_error" in src


def test_manual_fallback_picker_still_works_standalone():
    src = inspect.getsource(tt._closed_tour_manual_picker)
    assert 'st.text_area("Closed Tour codes"' in src
    assert 'st.button("🔍 Fetch list"' in src
    assert "api.get_closed_tour(supplier_id, code)" in src
    # A pasted code is, by definition, something you wanted - defaults to fully ticked, the
    # opposite default from the auto-loaded list above.
    assert "st.session_state[f\"tr_ct_pick_{c['code']}\"] = True" in src


def test_run_button_requires_at_least_one_selected_code():
    src = inspect.getsource(tt.render_translation_tool)
    assert "if not closed_tour_codes:" in src
    assert "Fetch the closed tour list and select at least one" in src


def test_execution_iterates_the_bulk_selection_with_per_tour_progress():
    src = inspect.getsource(tt.render_translation_tool)
    closed_tour_exec = src[src.index("# ---- Closed Tours ----"):]
    assert "for idx, code in enumerate(closed_tour_codes):" in closed_tour_exec
    assert "progress_placeholder" in closed_tour_exec
    assert "sync_closed_tour(api, translator, store, supplier_id, code," in closed_tour_exec


def test_no_second_language_mode_was_introduced_for_closed_tours():
    """The bulk picker must not define its own language selector - Closed Tours use the
    one shared target_languages multiselect, same as every other entity type."""
    src = inspect.getsource(tt._closed_tour_bulk_picker)
    assert "target_languages" not in src
    assert "multiselect" not in src


def test_shared_target_languages_multiselect_still_covers_every_entity_type():
    src = inspect.getsource(tt.render_translation_tool)
    assert "options=DEFAULT_TARGET_LANGUAGES, default=DEFAULT_TARGET_LANGUAGES" in src
    # the curated 6-language subset from the request is a normal subset of the shared list -
    # nothing special needs to exist for it, it's just unticking the other 13.
    for lang in ("DE", "ES", "FR", "NL", "PL", "IT"):
        assert lang in tt.DEFAULT_TARGET_LANGUAGES
