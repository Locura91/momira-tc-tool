"""Regression tests for the "Missing Transfers" scan/batch-create feature (2026-09-16), the
natural next step once the single-transfer duplicate-and-swap flow was proven out. Chris:

    "Goal with the duplicate must be, that humans create one way transfers, then the app must
    be controlled by human and human adds the supplier as usually, the app checks is there are
    missing transfers and then provides a list with all possible missing transfers. The style
    can be similar to the bulk price transfer update."

Follow-up clarifications (AskUserQuestion round-trip, same day):
  - "transfer flipped, it can not be looser - as it works right now is perfect. The route can
    not be touched, just swapped." -> exact-match pairing only, no fuzzy matching.
  - "Yes, please flag possible missing transfers." -> every one-way transfer without an exact
    reverse counterpart is flagged.
  - Token/cost safety: the scan itself must never call the AI - see
    test_scanning_hundreds_of_transfers_makes_no_ai_or_network_call_beyond_the_initial_fetch.
  - Batch style: "List + select multiple, publish as a batch".
  - Paging: "Paginated table, e.g. 25-50 rows per page".

Covers: transfer_gap_finder.find_missing_reverse_transfers (pure pairing logic),
transfer_gap_finder.build_and_rewrite_transfer_swap_payload (the shared AI-rewrite helper now
used by both flows/duplicate_transfer.py and flows/missing_transfers.py), and
flows/missing_transfers.py's wiring (app.py menu entry, pagination, batch-create).
"""
import os

import pytest

import transfer_gap_finder
import ai_extractor as ax


def _transfer(id_, dep_name, arr_name, vehicle_type="CAR", active=True, name=None):
    return {
        "active": active,
        "id": id_,
        "name": name or f"{dep_name} - {arr_name}",
        "vehicleType": vehicle_type,
        "departure": {"name": dep_name, "geolocation": {"latitude": 1.0, "longitude": 1.0}},
        "arrival": {"name": arr_name, "geolocation": {"latitude": 2.0, "longitude": 2.0}},
        "datasheets": {"EN": {"name": name or f"{dep_name} - {arr_name}",
                              "description": f"A private transfer from {dep_name} to {arr_name}.",
                              "pickupDescription": f"Meet your driver at {dep_name} for the ride to {arr_name}.",
                              "voucherRemarks": ""}},
        "transferToHotel": True,
    }


# ---------------------------------------------------------------------------------------------
# find_missing_reverse_transfers - pure pairing logic
# ---------------------------------------------------------------------------------------------

def test_a_one_way_transfer_with_no_reverse_is_flagged_as_a_gap():
    transfers = [_transfer("TRANSFER-1", "Cairo Airport", "Hotel Le Meridien")]
    gaps = transfer_gap_finder.find_missing_reverse_transfers(transfers)
    assert len(gaps) == 1
    assert gaps[0]["source"]["id"] == "TRANSFER-1"
    assert gaps[0]["missing_from_name"] == "Hotel Le Meridien"
    assert gaps[0]["missing_to_name"] == "Cairo Airport"


def test_a_pair_that_already_exists_both_ways_is_not_flagged():
    transfers = [
        _transfer("TRANSFER-1", "Cairo Airport", "Hotel Le Meridien"),
        _transfer("TRANSFER-2", "Hotel Le Meridien", "Cairo Airport"),
    ]
    gaps = transfer_gap_finder.find_missing_reverse_transfers(transfers)
    assert gaps == []


def test_matching_is_exact_no_fuzzy_route_matching():
    # CONFIRMED PRODUCT-OWNER FEEDBACK: "transfer flipped, it can not be looser - as it works
    # right now is perfect. The route can not be touched, just swapped." A near-miss name must
    # NOT be treated as satisfying the pair.
    transfers = [
        _transfer("TRANSFER-1", "Cairo Airport", "Hotel Le Meridien"),
        _transfer("TRANSFER-2", "Hotel Le Meridien", "Cairo Downtown"),  # different arrival
    ]
    gaps = transfer_gap_finder.find_missing_reverse_transfers(transfers)
    assert len(gaps) == 2  # both are still missing their true reverse


def test_matching_is_case_and_whitespace_insensitive_like_transfer_matcher():
    transfers = [
        _transfer("TRANSFER-1", "Cairo Airport", "Hotel Le Meridien"),
        _transfer("TRANSFER-2", "hotel  le meridien", "CAIRO AIRPORT"),
    ]
    gaps = transfer_gap_finder.find_missing_reverse_transfers(transfers)
    assert gaps == []


def test_different_vehicle_types_on_the_same_route_are_not_confused_for_each_other():
    # A sedan one-way and a van reverse-direction are two separate products - the van's reverse
    # is still missing even though "a" transfer nominally covers the route.
    transfers = [
        _transfer("TRANSFER-1", "Cairo Airport", "Hotel Le Meridien", vehicle_type="SEDAN"),
        _transfer("TRANSFER-2", "Hotel Le Meridien", "Cairo Airport", vehicle_type="VAN"),
    ]
    gaps = transfer_gap_finder.find_missing_reverse_transfers(transfers)
    assert len(gaps) == 2


def test_inactive_transfers_are_ignored_entirely():
    transfers = [
        _transfer("TRANSFER-1", "Cairo Airport", "Hotel Le Meridien", active=False),
    ]
    gaps = transfer_gap_finder.find_missing_reverse_transfers(transfers)
    assert gaps == []


def test_transfer_missing_a_departure_or_arrival_name_is_skipped_not_flagged():
    broken = _transfer("TRANSFER-1", "", "Hotel Le Meridien")
    gaps = transfer_gap_finder.find_missing_reverse_transfers([broken])
    assert gaps == []


def test_empty_transfer_list_returns_no_gaps():
    assert transfer_gap_finder.find_missing_reverse_transfers([]) == []
    assert transfer_gap_finder.find_missing_reverse_transfers(None) == []


def test_scanning_hundreds_of_transfers_makes_no_ai_or_network_call_beyond_the_initial_fetch():
    # CONFIRMED PRODUCT-OWNER CONCERN: "the time/power/token from AI is gone" - the scan/pairing
    # step is pure Python. Monkeypatching ai_extractor's client-getter to explode proves the scan
    # never touches it, however many transfers are scanned.
    def _explode():
        raise AssertionError("find_missing_reverse_transfers must never call the AI")

    real = ax._get_anthropic_client
    ax._get_anthropic_client = _explode
    try:
        transfers = []
        for i in range(300):
            transfers.append(_transfer(f"TRANSFER-{i}", f"Place {i}", f"Place {i + 1000}"))
        gaps = transfer_gap_finder.find_missing_reverse_transfers(transfers)
        assert len(gaps) == 300
    finally:
        ax._get_anthropic_client = real


# ---------------------------------------------------------------------------------------------
# build_and_rewrite_transfer_swap_payload - shared AI-rewrite helper
# ---------------------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def restore_ai_seams():
    real_client_fn = ax._get_anthropic_client
    real_stream_fn = ax._stream_claude_message
    yield
    ax._get_anthropic_client = real_client_fn
    ax._stream_claude_message = real_stream_fn


def test_build_and_rewrite_swaps_the_route_and_leaves_a_clean_swap_untouched_by_ai():
    calls = []
    ax._get_anthropic_client = lambda: calls.append("called") or object()
    source = _transfer("TRANSFER-1", "Cairo Airport", "Hotel Le Meridien")
    payload, swap_report, route_info = transfer_gap_finder.build_and_rewrite_transfer_swap_payload(source)
    assert payload["departure"]["name"] == "Hotel Le Meridien"
    assert payload["arrival"]["name"] == "Cairo Airport"
    assert route_info["new_departure_name"] == "Hotel Le Meridien"
    # description/pickupDescription both contain the place names literally, so the free literal
    # swap handles them - the AI must not be called for a clean swap
    assert calls == []


def test_build_and_rewrite_calls_the_ai_only_for_a_field_the_literal_swap_could_not_handle():
    source = _transfer("TRANSFER-1", "Cairo Airport", "Hotel Le Meridien")
    # a description that names neither place literally - the literal swap can't handle this
    source["datasheets"]["EN"]["description"] = (
        "A private transfer is available to your booked accommodation by air-conditioned vehicle.")

    seen = {}

    def _fake_stream(client, model, max_tokens, system_prompt, user_content):
        seen["called"] = True
        return "A private transfer is available from your booked accommodation to the airport.", "end_turn"

    ax._get_anthropic_client = lambda: object()
    ax._stream_claude_message = _fake_stream

    payload, swap_report, route_info = transfer_gap_finder.build_and_rewrite_transfer_swap_payload(source)
    assert seen.get("called") is True
    assert swap_report["description"] == "ai"
    assert "airport" in payload["datasheets"]["EN"]["description"].lower()


def test_build_and_rewrite_falls_back_gracefully_when_the_ai_call_fails():
    source = _transfer("TRANSFER-1", "Cairo Airport", "Hotel Le Meridien")
    source["datasheets"]["EN"]["description"] = "A transfer to your booked accommodation."

    def _raises(*args, **kwargs):
        raise RuntimeError("API down")

    ax._get_anthropic_client = _raises
    payload, swap_report, route_info = transfer_gap_finder.build_and_rewrite_transfer_swap_payload(source)
    # falls back to the original (unswapped) text, and the report still says False - nothing
    # silently lost, no crash
    assert swap_report["description"] is False


# ---------------------------------------------------------------------------------------------
# flows/missing_transfers.py wiring
# ---------------------------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_PY = os.path.join(os.path.dirname(_HERE), "app.py")
_MISSING_TRANSFERS_FLOW_PY = os.path.join(os.path.dirname(_HERE), "flows", "missing_transfers.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def _read_missing_transfers_flow():
    with open(_MISSING_TRANSFERS_FLOW_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_step_1_create_menu_offers_the_missing_transfers_choice():
    src = _read_app_py()
    assert "MISSING_TRANSFERS_CHOICE" in src
    assert 'pt_choice_missing_transfers' in src
    assert "render_missing_transfers_flow(client)" in src


def test_missing_transfers_flow_is_imported_from_its_own_module():
    src = _read_app_py()
    assert "from flows.missing_transfers import render_missing_transfers_flow" in src


def test_flow_uses_the_shared_build_and_rewrite_helper_not_a_duplicated_copy():
    src = _read_missing_transfers_flow()
    assert "transfer_gap_finder.build_and_rewrite_transfer_swap_payload(source)" in src
    assert "rewrite_route_description_for_new_direction(" not in src


def test_flow_paginates_instead_of_rendering_everything_at_once():
    src = _read_missing_transfers_flow()
    assert "_PAGE_SIZE = 25" in src
    assert "Prev" in src and "Next" in src


def test_flow_is_a_select_multiple_then_batch_create_screen():
    src = _read_missing_transfers_flow()
    assert 'st.checkbox(' in src
    assert "Select all" in src
    assert "st.progress(" in src
    # one batch action, not a per-row create button
    assert src.count('st.button(f"🚀 Create') == 1


def test_flow_scan_step_never_imports_or_calls_ai_extractor_directly():
    src = _read_missing_transfers_flow()
    assert "ai_extractor" not in src
