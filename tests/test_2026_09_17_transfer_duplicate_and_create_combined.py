"""Regression tests for the combined Transfer create screen (product owner, 2026-09-17):

    "the transfer duplicate section shall be combined with transfer find & create. We must put
    them together. Long term Goal for this section is, that human selects the correct supplier,
    the app checks all transfers and identifies missing duplicates in a list for example. Then
    the human reviews all possible duplicates and can say 'select all' or 'select none' for auto
    creation."

That "select all"/"select none" scan-and-list workflow already existed in
flows/missing_transfers.py (2026-09-16) - it's the primary section of the new combined screen
(flows/transfer_duplicate_and_create.py). The manual "paste a known Transfer id" flow from
flows/duplicate_transfer.py is kept as a secondary option underneath, for a record the automated
scan doesn't catch.

Source-text checks only (same established pattern as every other flows/*.py wiring test in this
suite - these modules aren't importable in a test process due to app.py's circular imports).
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_COMBINED_FLOW_PY = os.path.join(_REPO_ROOT, "flows", "transfer_duplicate_and_create.py")
_MISSING_TRANSFERS_PY = os.path.join(_REPO_ROOT, "flows", "missing_transfers.py")
_DUPLICATE_TRANSFER_PY = os.path.join(_REPO_ROOT, "flows", "duplicate_transfer.py")
_APP_PY = os.path.join(_REPO_ROOT, "app.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _read_combined_flow():
    return _read(_COMBINED_FLOW_PY)


# ---------------------------------------------------------------------------------------------
# 1. one shared supplier picker, not two
# ---------------------------------------------------------------------------------------------

def test_combined_flow_picks_the_supplier_exactly_once():
    src = _read_combined_flow()
    assert src.count("_ur_pick_momira_supplier(") == 1


def test_supplier_is_picked_before_either_section_renders():
    src = _read_combined_flow()
    pick_idx = src.index("_ur_pick_momira_supplier(")
    scan_idx = src.index("_render_missing_transfers_body(")
    manual_idx = src.index("_render_duplicate_transfer_body(")
    assert pick_idx < scan_idx < manual_idx


# ---------------------------------------------------------------------------------------------
# 2. both bodies are reused, not reimplemented
# ---------------------------------------------------------------------------------------------

def test_combined_flow_imports_both_body_functions_rather_than_duplicating_logic():
    src = _read_combined_flow()
    assert "from flows.missing_transfers import _render_missing_transfers_body" in src
    assert "from flows.duplicate_transfer import _render_duplicate_transfer_body" in src


def test_the_scan_section_is_the_primary_path_the_manual_path_is_secondary_in_an_expander():
    src = _read_combined_flow()
    scan_idx = src.index("_render_missing_transfers_body(")
    expander_idx = src.index("with st.expander(")
    manual_idx = src.index("_render_duplicate_transfer_body(")
    assert scan_idx < expander_idx < manual_idx


# ---------------------------------------------------------------------------------------------
# 3. the underlying body functions actually exist in their own modules, with the shape the
#    combined flow expects (supplier_id already known, no picker of their own)
# ---------------------------------------------------------------------------------------------

def test_missing_transfers_body_function_exists_and_takes_a_resolved_supplier_id():
    src = _read(_MISSING_TRANSFERS_PY)
    assert "def _render_missing_transfers_body(client, supplier_id):" in src


def test_duplicate_transfer_body_function_exists_and_takes_a_resolved_supplier_id():
    src = _read(_DUPLICATE_TRANSFER_PY)
    assert "def _render_duplicate_transfer_body(client, supplier_id):" in src


def test_standalone_entry_points_still_exist_for_backward_compatibility():
    # render_missing_transfers_flow / render_duplicate_transfer_flow (self-picking their own
    # supplier) must still exist and still delegate to the shared body functions, not diverge
    # into their own separate copy of the logic.
    missing_src = _read(_MISSING_TRANSFERS_PY)
    assert "def render_missing_transfers_flow(client):" in missing_src
    assert "_render_missing_transfers_body(client, supplier_id)" in missing_src

    dup_src = _read(_DUPLICATE_TRANSFER_PY)
    assert "def render_duplicate_transfer_flow(client):" in dup_src
    assert "_render_duplicate_transfer_body(client, supplier_id)" in dup_src


# ---------------------------------------------------------------------------------------------
# 4. app.py wiring - one combined menu entry, the two old separate ones are gone
# ---------------------------------------------------------------------------------------------

def test_app_py_wires_exactly_one_combined_transfer_menu_entry():
    src = _read(_APP_PY)
    assert "TRANSFER_DUPLICATE_AND_CREATE_CHOICE" in src
    assert 'pt_choice_transfer_duplicate_and_create' in src
    assert "render_transfer_duplicate_and_create_flow(client)" in src
    assert "DUPLICATE_TRANSFER_CHOICE" not in src
    assert "MISSING_TRANSFERS_CHOICE" not in src


def test_app_py_imports_the_combined_flow_not_the_two_standalone_ones():
    src = _read(_APP_PY)
    assert "from flows.transfer_duplicate_and_create import render_transfer_duplicate_and_create_flow" in src
    assert "from flows.duplicate_transfer import render_duplicate_transfer_flow" not in src
    assert "from flows.missing_transfers import render_missing_transfers_flow" not in src


def test_transport_duplicate_menu_entry_is_untouched():
    # Only the two Transfer entries were merged - Transport's own duplicate-by-id destination
    # (no scan yet) must be unaffected.
    src = _read(_APP_PY)
    assert "DUPLICATE_TRANSPORT_CHOICE" in src
    assert "render_duplicate_transport_flow(client)" in src


# ---------------------------------------------------------------------------------------------
# 5. the combined screen has its own back button and explains both paths
# ---------------------------------------------------------------------------------------------

def test_combined_flow_has_its_own_back_to_step_1_button():
    src = _read_combined_flow()
    assert 'st.button("🔙 Back to Step 1", key="tdc_back")' in src
    assert "st.session_state.product_type = None" in src
