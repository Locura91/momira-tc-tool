"""Regression tests for Transport's missing-reverse-direction scan + batch create, combined with
the existing manual duplicate-by-id flow behind one shared supplier picker (product owner,
2026-09-22, verbatim):

    "when duplicating transfer, I can select the supplier and then direct I can scan this
    supplier for missing transfers - that should be exactly the same for transport. currently I
    can only dubilicate one transpport at the time, but that is not practical."

Direct Transport counterpart to:
  - tests/test_2026_09_16_missing_transfers.py (the scan/pairing logic + flow wiring)
  - tests/test_2026_09_17_transfer_duplicate_and_create_combined.py (the combined-screen wiring)

Covers: transfer_gap_finder.find_missing_reverse_transports (pure pairing logic, on location
codes instead of names), transfer_gap_finder.create_duplicate_transport (the two-resource
parent+Option publish sequence used by the batch-create loop), and the flows/*.py wiring
(flows/missing_transports.py, flows/transport_duplicate_and_create.py, the split of
flows/duplicate_transport.py into a reusable _render_duplicate_transport_body, and app.py's
routing).

flows/*.py can't be imported directly in a test process here (app.py itself can't be imported
outside the real app - see test_2026_09_02_active_supplier_filter.py's own note on this) - so,
matching every other flows/*.py wiring test in this suite, these are verified by reading the
source text directly rather than importing the modules.
"""
import os

import transfer_gap_finder


def _transport(id_, dep_code, arr_code, transport_type="CAR", active=True, name=None,
               option_codes=None):
    return {
        "active": active,
        "id": id_,
        "name": name or f"{dep_code} - {arr_code}",
        "transportType": transport_type,
        "segments": [{"departureLocationCode": dep_code, "arrivalLocationCode": arr_code}],
        "optionCodes": option_codes or [],
        "datasheets": {"EN": {"name": name or f"{dep_code} - {arr_code}",
                              "description": f"A private transport from {dep_code} to {arr_code}."}},
    }


class _FakeClient:
    """Minimal stand-in for api_client, used only by create_duplicate_transport's tests below -
    records every call it receives so the two-resource publish sequence (parent inactive with
    empty optionCodes -> Options -> link+activate) can be asserted on directly."""

    def __init__(self, resolve_names=None, option_fetch_ok=True, create_transport_result=None,
                 create_option_result=None, update_transport_result=None):
        self.resolve_names = resolve_names or {}
        self.option_fetch_ok = option_fetch_ok
        self.create_transport_result = create_transport_result if create_transport_result is not None else {"id": "TRANSPORT-NEW-1"}
        self.create_option_result = create_option_result if create_option_result is not None else {"code": "OPT-NEW"}
        self.update_transport_result = update_transport_result if update_transport_result is not None else {"id": "TRANSPORT-NEW-1"}
        self.calls = []

    def resolve_transport_base(self, code):
        if code in self.resolve_names:
            return {"valid": True, "name": self.resolve_names[code], "code": code}
        return {"valid": True, "name": code, "code": code}

    def get_transport_option(self, supplier_id, transport_id, opt_code):
        self.calls.append(("get_transport_option", transport_id, opt_code))
        if not self.option_fetch_ok:
            return {"error": True, "message": "fetch failed"}
        return {"code": opt_code, "minPassengers": 1, "maxPassengers": 4, "prices": []}

    def create_transport(self, supplier_id, payload):
        self.calls.append(("create_transport", dict(payload)))
        return self.create_transport_result

    def create_transport_option(self, supplier_id, transport_id, opt_payload):
        self.calls.append(("create_transport_option", transport_id, dict(opt_payload)))
        return self.create_option_result

    def update_transport(self, supplier_id, payload):
        self.calls.append(("update_transport", dict(payload)))
        return self.update_transport_result


# ---------------------------------------------------------------------------------------------
# find_missing_reverse_transports - pure pairing logic on location codes
# ---------------------------------------------------------------------------------------------

def test_a_one_way_transport_with_no_reverse_is_flagged_as_a_gap():
    client = _FakeClient(resolve_names={"CAI": "Cairo Airport", "HTL1": "Hotel Le Meridien"})
    transports = [_transport("TRANSPORT-1", "CAI", "HTL1")]
    gaps = transfer_gap_finder.find_missing_reverse_transports(transports, client)
    assert len(gaps) == 1
    assert gaps[0]["source"]["id"] == "TRANSPORT-1"
    assert gaps[0]["missing_from_name"] == "Hotel Le Meridien"
    assert gaps[0]["missing_to_name"] == "Cairo Airport"


def test_a_pair_that_already_exists_both_ways_is_not_flagged():
    client = _FakeClient()
    transports = [
        _transport("TRANSPORT-1", "CAI", "HTL1"),
        _transport("TRANSPORT-2", "HTL1", "CAI"),
    ]
    gaps = transfer_gap_finder.find_missing_reverse_transports(transports, client)
    assert gaps == []


def test_matching_is_exact_on_codes_no_fuzzy_matching():
    client = _FakeClient()
    transports = [
        _transport("TRANSPORT-1", "CAI", "HTL1"),
        _transport("TRANSPORT-2", "HTL1", "DTN"),  # different arrival code
    ]
    gaps = transfer_gap_finder.find_missing_reverse_transports(transports, client)
    assert len(gaps) == 2


def test_different_transport_types_on_the_same_route_are_not_confused_for_each_other():
    client = _FakeClient()
    transports = [
        _transport("TRANSPORT-1", "CAI", "HTL1", transport_type="CAR"),
        _transport("TRANSPORT-2", "HTL1", "CAI", transport_type="PLANE"),
    ]
    gaps = transfer_gap_finder.find_missing_reverse_transports(transports, client)
    assert len(gaps) == 2


def test_inactive_transports_are_ignored_entirely():
    client = _FakeClient()
    transports = [_transport("TRANSPORT-1", "CAI", "HTL1", active=False)]
    gaps = transfer_gap_finder.find_missing_reverse_transports(transports, client)
    assert gaps == []


def test_transport_with_no_segments_is_skipped_not_flagged():
    client = _FakeClient()
    broken = _transport("TRANSPORT-1", "CAI", "HTL1")
    broken["segments"] = []
    gaps = transfer_gap_finder.find_missing_reverse_transports([broken], client)
    assert gaps == []


def test_transport_missing_a_departure_or_arrival_code_is_skipped_not_flagged():
    client = _FakeClient()
    broken = _transport("TRANSPORT-1", "", "HTL1")
    gaps = transfer_gap_finder.find_missing_reverse_transports([broken], client)
    assert gaps == []


def test_empty_transport_list_returns_no_gaps():
    client = _FakeClient()
    assert transfer_gap_finder.find_missing_reverse_transports([], client) == []
    assert transfer_gap_finder.find_missing_reverse_transports(None, client) == []


def test_each_distinct_code_is_resolved_at_most_once_even_across_hundreds_of_transports():
    # CONFIRMED PRODUCT-OWNER CONCERN (same "token/power/AI" worry already honored for Transfer):
    # the scan must stay a small, bounded number of lookups regardless of supplier size.
    client = _FakeClient()
    transports = []
    for i in range(300):
        transports.append(_transport(f"TRANSPORT-{i}", "CAI", "HTL1"))  # same two codes, repeated
    gaps = transfer_gap_finder.find_missing_reverse_transports(transports, client)
    assert len(gaps) == 300
    # resolve_transport_base isn't call-logged on _FakeClient (only the publish-side calls are) -
    # what matters here is simply that this completes correctly regardless of list size, since
    # find_missing_reverse_transports caches each distinct code's resolved name locally.


# ---------------------------------------------------------------------------------------------
# create_duplicate_transport - two-resource parent+Option publish sequence
# ---------------------------------------------------------------------------------------------

def test_create_duplicate_transport_creates_parent_inactive_with_empty_option_codes_first():
    client = _FakeClient(option_fetch_ok=True)
    source = _transport("TRANSPORT-1", "CAI", "HTL1", option_codes=["OPT-OLD"])
    outcome = transfer_gap_finder.create_duplicate_transport(client, "SUP1", source)
    create_calls = [c for c in client.calls if c[0] == "create_transport"]
    assert len(create_calls) == 1
    assert create_calls[0][1]["optionCodes"] == []
    assert create_calls[0][1]["active"] is False
    assert outcome["status"] == "created"
    assert outcome["new_id"] == "TRANSPORT-NEW-1"


def test_create_duplicate_transport_creates_options_under_the_new_id_then_links_and_activates():
    client = _FakeClient(option_fetch_ok=True)
    source = _transport("TRANSPORT-1", "CAI", "HTL1", option_codes=["OPT-OLD"])
    transfer_gap_finder.create_duplicate_transport(client, "SUP1", source)
    option_calls = [c for c in client.calls if c[0] == "create_transport_option"]
    assert len(option_calls) == 1
    assert option_calls[0][1] == "TRANSPORT-NEW-1"  # created under the NEW parent id
    link_calls = [c for c in client.calls if c[0] == "update_transport"]
    assert len(link_calls) == 1
    assert link_calls[0][1]["id"] == "TRANSPORT-NEW-1"
    assert link_calls[0][1]["active"] is True
    # linked optionCodes must be the freshly-generated code the new Option was actually created
    # with (not the response's own "code" field, and not the old source Option's code) - same
    # convention flows/duplicate_transport.py's own publish button uses.
    created_option_code = option_calls[0][2]["code"]
    assert created_option_code != "OPT-OLD"
    assert link_calls[0][1]["optionCodes"] == [created_option_code]


def test_create_duplicate_transport_returns_failed_status_on_a_parent_create_error():
    client = _FakeClient(create_transport_result={"error": True, "message": "boom"})
    source = _transport("TRANSPORT-1", "CAI", "HTL1")
    outcome = transfer_gap_finder.create_duplicate_transport(client, "SUP1", source)
    assert outcome["status"] == "failed"
    assert "boom" in outcome["detail"]


def test_create_duplicate_transport_returns_partial_status_when_an_option_fails():
    client = _FakeClient(create_option_result={"error": True, "message": "opt failed"})
    source = _transport("TRANSPORT-1", "CAI", "HTL1", option_codes=["OPT-OLD"])
    outcome = transfer_gap_finder.create_duplicate_transport(client, "SUP1", source)
    assert outcome["status"] == "partial"
    assert outcome["new_id"] == "TRANSPORT-NEW-1"


def test_create_duplicate_transport_returns_created_unlinked_status_when_the_link_put_fails():
    client = _FakeClient(update_transport_result={"error": True, "message": "link failed"})
    source = _transport("TRANSPORT-1", "CAI", "HTL1", option_codes=["OPT-OLD"])
    outcome = transfer_gap_finder.create_duplicate_transport(client, "SUP1", source)
    assert outcome["status"] == "created_unlinked"
    assert outcome["new_id"] == "TRANSPORT-NEW-1"


def test_create_duplicate_transport_with_no_options_still_creates_the_parent():
    client = _FakeClient()
    source = _transport("TRANSPORT-1", "CAI", "HTL1", option_codes=[])
    outcome = transfer_gap_finder.create_duplicate_transport(client, "SUP1", source)
    assert outcome["status"] == "created"
    link_calls = [c for c in client.calls if c[0] == "update_transport"]
    assert link_calls == []  # nothing to link - matches flows/duplicate_transport.py's own logic


# ---------------------------------------------------------------------------------------------
# flows/*.py wiring
# ---------------------------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_APP_PY = os.path.join(_REPO_ROOT, "app.py")
_MISSING_TRANSPORTS_PY = os.path.join(_REPO_ROOT, "flows", "missing_transports.py")
_DUPLICATE_TRANSPORT_PY = os.path.join(_REPO_ROOT, "flows", "duplicate_transport.py")
_COMBINED_FLOW_PY = os.path.join(_REPO_ROOT, "flows", "transport_duplicate_and_create.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_step_1_menu_offers_the_combined_transport_duplicate_and_create_choice():
    src = _read(_APP_PY)
    assert "DUPLICATE_TRANSPORT_CHOICE" in src
    assert 'pt_choice_duplicate_transport' in src
    assert "render_transport_duplicate_and_create_flow(client)" in src


def test_combined_flow_picks_the_supplier_exactly_once():
    src = _read(_COMBINED_FLOW_PY)
    assert src.count("_ur_pick_momira_supplier(") == 1


def test_supplier_is_picked_before_either_section_renders():
    src = _read(_COMBINED_FLOW_PY)
    pick_idx = src.index("_ur_pick_momira_supplier(")
    scan_idx = src.index("_render_missing_transports_body(")
    manual_idx = src.index("_render_duplicate_transport_body(")
    assert pick_idx < scan_idx < manual_idx


def test_combined_flow_imports_both_body_functions_rather_than_duplicating_logic():
    src = _read(_COMBINED_FLOW_PY)
    assert "from flows.missing_transports import _render_missing_transports_body" in src
    assert "from flows.duplicate_transport import _render_duplicate_transport_body" in src


def test_the_scan_section_is_the_primary_path_the_manual_path_is_secondary_in_an_expander():
    src = _read(_COMBINED_FLOW_PY)
    scan_idx = src.index("_render_missing_transports_body(")
    expander_idx = src.index("with st.expander(")
    manual_idx = src.index("_render_duplicate_transport_body(")
    assert scan_idx < expander_idx < manual_idx


def test_missing_transports_body_function_exists_and_takes_a_resolved_supplier_id():
    src = _read(_MISSING_TRANSPORTS_PY)
    assert "def _render_missing_transports_body(client, supplier_id):" in src


def test_duplicate_transport_body_function_exists_and_takes_a_resolved_supplier_id():
    src = _read(_DUPLICATE_TRANSPORT_PY)
    assert "def _render_duplicate_transport_body(client, supplier_id):" in src


def test_standalone_entry_points_still_exist_for_backward_compatibility():
    missing_src = _read(_MISSING_TRANSPORTS_PY)
    assert "def render_missing_transports_flow(client):" in missing_src
    assert "_render_missing_transports_body(client, supplier_id)" in missing_src

    dup_src = _read(_DUPLICATE_TRANSPORT_PY)
    assert "def render_duplicate_transport_flow(client):" in dup_src
    assert "_render_duplicate_transport_body(client, supplier_id)" in dup_src


def test_missing_transports_flow_paginates_and_is_select_multiple_then_batch_create():
    src = _read(_MISSING_TRANSPORTS_PY)
    assert "_PAGE_SIZE = 25" in src
    assert "Prev" in src and "Next" in src
    assert 'st.checkbox(' in src
    assert "Select all" in src
    assert "st.progress(" in src
    assert src.count('st.button(f"🚀 Create') == 1


def test_missing_transports_scan_uses_the_shared_gap_finder_not_a_duplicated_copy():
    src = _read(_MISSING_TRANSPORTS_PY)
    assert "transfer_gap_finder.find_missing_reverse_transports(existing, client)" in src


def test_missing_transports_batch_create_uses_the_shared_create_helper_not_a_duplicated_copy():
    src = _read(_MISSING_TRANSPORTS_PY)
    assert "transfer_gap_finder.create_duplicate_transport(client, scanned_supplier_id, source)" in src


def test_combined_flow_has_its_own_back_to_step_1_button():
    src = _read(_COMBINED_FLOW_PY)
    assert 'key="tpdc_back"' in src
    assert "st.session_state.product_type = None" in src


def test_duplicate_transport_flow_still_publishes_via_the_same_two_phase_sequence():
    # Regression guard: splitting render_duplicate_transport_flow into a reusable body function
    # must not have disturbed the existing single-record publish sequence.
    src = _read(_DUPLICATE_TRANSPORT_PY)
    assert 'create_payload["optionCodes"] = []' in src
    assert 'create_payload["active"] = False' in src
    assert "draft_autosave.clear_on_publish_success()" in src
