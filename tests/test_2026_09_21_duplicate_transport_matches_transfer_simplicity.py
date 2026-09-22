"""Regression tests for a real product-owner request (2026-09-21, verbatim):

    "make sure, the Transport duplicate is workking similar like transfers duplicate. Transports
    are now easaliy copied, but the search is easy as transfer but not as transport."

flows/duplicate_transport.py used to offer a "How do you want to find it?" radio (search by
departure/arrival, or paste a known id) plus a mandatory "Duplicate check" gate before Publish -
both of which flows/duplicate_transfer.py had ALREADY dropped on 2026-09-16, per this exact same
product-owner reasoning ("Search by departure/arrival can be delete... Not needed", "'duplicate
Check' not needed, it is always safe to duplicate" - see
tests/test_2026_09_16_duplicate_transfer_swap_destinations.py's own equivalent tests). This file
mirrors that suite's own regression tests for the Transport side of the same simplification.
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_DUPLICATE_TRANSPORT_PY = os.path.join(os.path.dirname(_HERE), "flows", "duplicate_transport.py")


def _read_duplicate_transport_flow():
    with open(_DUPLICATE_TRANSPORT_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_search_by_departure_arrival_picker_is_removed():
    src = _read_duplicate_transport_flow()
    assert "dtp_search_dep" not in src
    assert "dtp_search_arr" not in src
    assert "dtp_search_results" not in src
    assert "suggest_existing_transport_matches(" not in src


def test_pick_source_keeps_only_the_manual_id_paste_path():
    src = _read_duplicate_transport_flow()
    assert "dtp_manual_id" in src
    assert "dtp_fetch_manual" in src
    assert "dtp_pick_mode" not in src
    assert "pick_mode" not in src
    pick_source_fn = src[src.index("def _render_pick_source("):src.index(
        "def _render_review_and_publish(")]
    assert "st.text_input(" in pick_source_fn
    assert "st.text_input(\"Departure" not in pick_source_fn
    assert "st.text_input(\"Arrival" not in pick_source_fn
    assert pick_source_fn.count("st.button(") == 1


def test_duplicate_check_section_is_removed():
    src = _read_duplicate_transport_flow()
    assert "dtp_checkmatch" not in src
    assert "resolve_transport_match(" not in src
    assert "match_checked" not in src
    assert "blocks_as_duplicate" not in src
    assert "dtp_match_result" not in src
    assert "dtp_match_route_fingerprint" not in src


def test_publish_disabled_only_checks_dates_and_segments_now():
    src = _read_duplicate_transport_flow()
    assert "publish_disabled = not dates_ok or not segments_ok" in src


def test_session_state_reset_lists_no_longer_reference_removed_duplicate_check_keys():
    src = _read_duplicate_transport_flow()
    assert "dtp_match_result" not in src
    assert "dtp_match_route_fingerprint" not in src
    assert "dtp_search_results" not in src
    # the core keys must still be reset everywhere (supplier change, restart, and success paths)
    for key in ("dtp_source", "dtp_source_options", "dtp_payload", "dtp_swap_report",
                "dtp_route_info", "dtp_options"):
        assert src.count(key) >= 3


def test_remember_transport_id_is_still_called_on_publish_success():
    # The duplicate-CHECK helper (resolve_transport_match) is gone, but remembering a newly
    # published id for FUTURE matching (used by price_refresh.py/multi_transport.py elsewhere)
    # must not have been swept away along with it.
    src = _read_duplicate_transport_flow()
    assert "transport_matcher.remember_transport_id(" in src
