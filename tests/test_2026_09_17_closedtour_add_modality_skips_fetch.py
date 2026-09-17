"""Regression tests for a real product-owner request (2026-09-17):

    "I am now adding a new modality to an existing closedtour. If we do that we do not need to
    fetch any information. If we select the supplier and if we select the ClosedTour Code, we
    just want to add a new Modality, regardless what is already online."

Previously, "add_option" (Step 1's "Add a new option to an existing tour") required a human to
click "Check what's already online for this code" in Step 3 before Continue would unlock - the
same "an UPDATE never asks for things the live record already has" gate used for update_tour/
update_option, which genuinely need to inherit live data (currency, min/max pax). add_option is
introducing something NEW, not editing what's already there, so the product owner wants it to
stop depending on a successful fetch of the tour's current state at all.

Fix:
  - app_helpers.ACTION_FIELDS["add_option"] now includes "currency" - asked directly in Step 3
    (same as "create"), instead of being pulled from a fetch.
  - The "Check what's already online for this code" button/results in Step 3 no longer render
    for add_option at all.
  - The Step 3 "Continue to Step 4" gate, and the Steps 4+ currency/min/max/provider_code
    re-derivation block, both dropped "add_option" from their action tuples.
  - The one place that genuinely still needs the tour's live data even for add_option - merging
    a brand-new Modality's supplements into the tour's EXISTING supplements list, so other
    Modalities' supplements aren't wiped out - now fetches it itself, automatically, at publish
    time, only when the new Modality actually has supplements to attach (most add_option runs
    don't, and never needed this fetch in the first place).

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


def test_action_fields_asks_for_currency_directly_for_add_option():
    src = _read_app_helpers()
    assert '"add_option": ["existing_tour_code", "modality_code", "currency", "on_request"],' in src


def test_check_online_button_no_longer_renders_for_add_option():
    src = _read_app_py()
    assert 'if "existing_tour_code" in needed and action != "add_option":' in src
    idx = src.index('if "existing_tour_code" in needed and action != "add_option":')
    window = src[idx:idx + 400]
    assert "Check what's already online for this code" in window


def test_step3_continue_gate_no_longer_requires_add_option_to_have_fetched():
    src = _read_app_py()
    marker = 'if action in ("update_tour", "update_option") and not fetched_tour_matches_code(existing_tour_code_in):'
    assert marker in src
    # the old, three-action tuple that used to include add_option must be gone
    assert 'if action in ("update_tour", "add_option", "update_option") and not fetched_tour_matches_code(existing_tour_code_in):' not in src


def test_continue_button_handler_no_longer_overrides_add_option_currency_from_a_fetch():
    src = _read_app_py()
    assert 'elif action == "add_option":\n            currency_in = st.session_state.get("fetched_tour_currency") or ""' not in src


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
