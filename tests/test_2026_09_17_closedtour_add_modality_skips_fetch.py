"""Regression tests for a real product-owner request (2026-09-17), LATER REVERSED (2026-09-27):

    2026-09-17 (original): "I am now adding a new modality to an existing closedtour. If we do
    that we do not need to fetch any information. If we select the supplier and if we select the
    ClosedTour Code, we just want to add a new Modality, regardless what is already online."

    2026-09-27 (reversal, verbatim): "when adding a new modality to an existing closedtour, human
    selects the supplier, then the app shall fetch all closedtours available from theis supplier,
    and then we add the new modality. we do no specify another time the currency as this
    information is betted within main information and the currency can not change for different
    mdalities."

So as of 2026-09-27, add_option is BACK to fetching live tour data - the difference from
update_tour/update_option is only that add_option now presents that fetch as a ClosedTour PICKER
(fetch this supplier's tours via `get_existing_tour_names`, human picks one) rather than a
free-text code the human types and then confirms with a button, and Currency is inherited from
the picked tour rather than asked again. The 2026-09-17 "asks for currency directly, skips the
fetch entirely" behavior below is intentionally history, kept in this file's tests (updated in
place) so a future edit doesn't silently reintroduce it.

Current (2026-09-27) shape:
  - app_helpers.ACTION_FIELDS["add_option"] does NOT include "currency" again - it's inherited
    from the fetched/picked tour, same as update_tour/update_option.
  - Step 3 renders a ClosedTour picker (`get_existing_tour_names`) for add_option, and the
    "Check what's already online for this code" fetch/gate is shared with update_tour/
    update_option again (three-action tuple, not excluding add_option).
  - The Step 3 "Continue to Step 4" gate requires add_option's fetch to match too, and the
    Continue button handler pulls add_option's currency from `fetched_tour_currency`.
  - Embedded-image extraction/R2-upload is skipped entirely for add_option (2026-09-27, verbatim:
    "no need for auto image upload for creating a new modality. when new modality is being
    created, we focus only on the new prices.") - unrelated to the fetch reversal itself, but
    landed in the same batch of changes.

app.py can't be safely imported in a test process (it runs top-level Streamlit calls on import) -
these read its source text directly, same as every other app.py-side regression test in this
suite (see test_2026_08_31_closedtour_child_discount_visibility.py).
"""
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP_PY = os.path.join(_REPO_ROOT, "app.py")
_APP_HELPERS_PY = os.path.join(_REPO_ROOT, "app_helpers.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def _read_app_helpers():
    with open(_APP_HELPERS_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_action_fields_no_longer_asks_for_currency_directly_for_add_option():
    # REVERSED 2026-09-27: currency is inherited from the picked/fetched tour again, not asked.
    src = _read_app_helpers()
    assert '"add_option": ["existing_tour_code", "modality_code", "on_request"],' in src
    assert '"add_option": ["existing_tour_code", "modality_code", "currency", "on_request"],' not in src


def test_check_online_button_renders_again_for_add_option():
    # REVERSED 2026-09-27: add_option shares the "Check what's already online" fetch again,
    # now via a ClosedTour picker built from get_existing_tour_names.
    src = _read_app_py()
    assert 'if "existing_tour_code" in needed and action != "add_option":' not in src
    assert 'if "existing_tour_code" in needed:' in src
    idx = src.index('if "existing_tour_code" in needed:')
    window = src[idx:idx + 5000]
    assert "Check what's already online for this code" in window
    assert "get_existing_tour_names" in window


def test_step3_continue_gate_requires_add_option_to_have_fetched_again():
    # REVERSED 2026-09-27: add_option is back in the fetch-match gate, alongside update_tour/
    # update_option.
    src = _read_app_py()
    marker = 'if action in ("update_tour", "update_option", "add_option") and not fetched_tour_matches_code(existing_tour_code_in):'
    assert marker in src
    # the narrower 2026-09-17 two-action tuple that excluded add_option must be gone
    assert 'if action in ("update_tour", "update_option") and not fetched_tour_matches_code(existing_tour_code_in):' not in src


def test_continue_button_handler_inherits_add_option_currency_from_the_fetch_again():
    # REVERSED 2026-09-27: add_option's currency now comes from the fetched tour, same
    # mechanism as update_tour, instead of being typed directly in Step 3.
    src = _read_app_py()
    assert 'elif action == "add_option":\n            currency_in = st.session_state.get("fetched_tour_currency") or ""' in src


def test_steps_4_plus_rederivation_block_no_longer_covers_add_option():
    src = _read_app_py()
    marker_warn = 'if action in ("update_tour", "update_option") and not fetched_tour_matches_code(existing_tour_code):'
    marker_blend = 'if action in ("update_tour", "update_option") and fetched_tour_matches_code(existing_tour_code):'
    assert marker_warn in src
    assert marker_blend in src
    assert 'if action in ("update_tour", "update_option", "add_option") and not fetched_tour_matches_code(existing_tour_code):' not in src
    assert 'if action in ("update_tour", "update_option", "add_option") and fetched_tour_matches_code(existing_tour_code):' not in src


def test_supplement_attach_step_fetches_live_tour_itself_when_needed():
    # The one genuine dependency on live data add_option still has (merging a new Modality's
    # supplements into the tour's existing supplements list) must fetch it automatically at
    # publish time now, rather than relying on session_state.fetched_tour having been populated
    # by a Step 3 fetch that no longer happens.
    src = _read_app_py()
    idx = src.index("Adding '{modality_code}''s supplements to the tour...")
    window = src[idx:idx + 1500]
    assert 'old_tour = st.session_state.get("fetched_tour")' in window
    assert 'client.get_closed_tour(payloads["supplier_id"], c)' in window
    assert "try_code_variants(" in window


def test_existing_tour_code_input_still_required_for_add_option():
    # Chris still selects supplier + types the ClosedTour Code - only the mandatory fetch step
    # was removed, not the code field itself.
    src = _read_app_py()
    assert 'if "existing_tour_code" in needed and not existing_tour_code_in:' in src
