"""Regression tests for a real product-owner request (2026-09-21, verbatim):

    "Translating Closed Tours must work in bulk. Human selects an Supplier, the app shows a
    list with all closedtours, human can select all, or select none and then starts the
    translation. Keep in mind, the translation might take a while, because the text is long.
    We also split translation: either all languages, or only German, Spanish, French, Dutch,
    Polish, Italian, or ALL languages."

Travel Compositor exposes no endpoint that lists a supplier's Closed Tours - confirmed
elsewhere in this codebase for the identical reason (bulk_notes.needs_manual_codes,
price_refresh.py's own ClosedTour scope-out, flows/manual_information.py's "paste the tour
codes, one per line" box). So "the app shows a list with all closedtours" is implemented as:
paste the known codes once, fetch them into a checkable list (same Select all/Select none
shape as every other bulk picker in this app), then translate only what's checked.

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


def test_single_code_text_input_is_gone():
    src = inspect.getsource(tt.render_translation_tool)
    assert 'st.text_input("Closed Tour Code"' not in src
    assert "tr_ct_code" not in src


def test_bulk_picker_is_wired_into_the_scope_step():
    src = inspect.getsource(tt.render_translation_tool)
    assert "_closed_tour_bulk_picker(supplier_id)" in src
    assert "closed_tour_codes = _closed_tour_bulk_picker(supplier_id)" in src


def test_bulk_picker_pastes_codes_and_fetches_a_checkable_list():
    src = inspect.getsource(tt._closed_tour_bulk_picker)
    assert 'st.text_area("Closed Tour codes"' in src
    assert 'st.button("🔍 Fetch list"' in src
    assert "api.get_closed_tour(supplier_id, code)" in src


def test_select_all_and_select_none_write_session_state_directly():
    # Same fix as app_helpers.render_candidate_filter's own select-all/none bug (2026-09-16):
    # writing st.session_state[key] = value directly for every candidate, not toggling/popping.
    src = inspect.getsource(tt._closed_tour_bulk_picker)
    assert 'st.button("Select all"' in src
    assert 'st.button("Select none"' in src
    assert "st.session_state[f\"tr_ct_pick_{c['code']}\"] = True" in src
    assert "st.session_state[f\"tr_ct_pick_{c['code']}\"] = False" in src


def test_candidates_default_to_all_selected_on_fetch():
    """"on default all languages are marked" - the equivalent default for the closed tour
    checklist is that a freshly fetched list starts fully ticked, same as every other
    defaults-to-everything checklist in this tool."""
    src = inspect.getsource(tt._closed_tour_bulk_picker)
    fetch_block = src[src.index("if st.button(\"🔍 Fetch list\""):src.index("candidates = st.session_state.get")]
    assert "st.session_state[f\"tr_ct_pick_{c['code']}\"] = True" in fetch_block


def test_supplier_switch_invalidates_the_previously_fetched_list():
    src = inspect.getsource(tt._closed_tour_bulk_picker)
    assert 'st.session_state.get("tr_ct_candidates_supplier") != supplier_id' in src


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
