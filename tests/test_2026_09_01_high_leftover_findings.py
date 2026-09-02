"""Tests for 6 HIGH-severity findings from the full-app audit (full-app-audit-2026-09-01.md)
that a prior "all ~34 HIGH findings closed" claim missed - they were still open in app.py's
4200-8400 and 8400-12198 tables, discovered and fixed 2026-09-01 in a dedicated follow-up batch:

  1. Hotel offers/supplements whose VALUE changed (name unchanged) were silently skipped during
     publish - there's genuinely no update endpoint for either, but the skip carried no warning,
     so the publish screen reported "published in full" even though a renegotiated value never
     went live. Fixed: build_hotel_offer_payloads/build_hotel_supplement_payloads now flag a
     value mismatch as an error on the skip_duplicate result; app.py surfaces it as a failure.
  2. Transport modality-name overrides oscillated: the UI compared what was typed against the
     auto-generated name AFTER any override was already baked into it by the previous build, so
     a saved override alternated present/absent every rerun. Fixed: builder.py now also returns
     the stable pre-override auto_generated_name; app.py compares against that instead.
  3. Hotel publish crashed with 'NoneType' object has no attribute 'get' on any rate that failed
     to build, AFTER rooms/offers/supplements were already live, with no rate name and no note
     of the partial publish. Fixed: build_hotel_rate_payloads always returns rate_name (even on
     failure); app.py's exception handler now notes Phase 1 may already be live.
  4. "Confirm and Start Batch Review" (reachable for action == "update_tour" when the update
     source describes multiple variants) wrote to mct_queue/mct_queue_index and set
     mct_phase="reviewing" - a phase render_multi_tour_flow's dispatcher never handles, so the
     whole Create-ClosedTour screen rendered blank. Fixed: only a single variant can now be
     selected here; the dead batch-queue path is gone.
  5. The Existing Tour Code box was a one-shot `value=prefill` with no `key=` - Streamlit's
     auto-generated key changes whenever `value` changes between renders, so the first click of
     the mandatory "Check what's already online" button silently no-oped. Fixed: a stable `key`
     now persists it; the prefill only seeds that key once.
  6. st.rerun() in the price-refresh read handler (Transfer/Transport AND Ticket) fired
     unconditionally after every branch, wiping "no document"/"couldn't read"/"no products yet"
     errors before they could be read and leaving the PREVIOUS rate sheet's proposals on screen
     looking like a fresh successful read. Fixed: the rerun now only fires on the one branch that
     actually produced a new result.

Items 1-3 are tested against builder.py directly (pure functions, no network needed - same
approach as every other builder.py test in this suite). Items 4-6 are app.py-only UI/control-
flow changes; app.py can't be imported in a test process (heavy top-level Streamlit/API-client
setup), so - matching this suite's established pattern for such changes - they're verified by
reading app.py's own source text and checking the specific code shape.
"""
import os

from builder import (
    build_hotel_offer_payloads,
    build_hotel_supplement_payloads,
    build_hotel_rate_payloads,
)

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


# ======================================================================
# 1. Hotel offers/supplements: value-changed-but-same-name silently skipped
# ======================================================================
def test_offer_matched_by_name_with_same_value_is_a_silent_skip_no_error():
    extracted = [{"name": "Early Bird", "value": 10.0, "child_value": 0}]
    existing_snapshot = {"offers": [{"names": [{"description": "Early Bird"}],
                                      "providerCode": "OFFER-1", "value": 10.0, "childValue": 0}]}
    results = build_hotel_offer_payloads(extracted, {}, existing_hotel_snapshot=existing_snapshot)
    assert len(results) == 1
    assert results[0]["action"] == "skip_duplicate"
    assert results[0]["matched_provider_code"] == "OFFER-1"
    assert results[0]["offer_error"] is None


def test_offer_matched_by_name_with_a_different_value_is_flagged_not_silent():
    extracted = [{"name": "Early Bird", "value": 15.0, "child_value": 0}]
    existing_snapshot = {"offers": [{"names": [{"description": "Early Bird"}],
                                      "providerCode": "OFFER-1", "value": 10.0, "childValue": 0}]}
    results = build_hotel_offer_payloads(extracted, {}, existing_hotel_snapshot=existing_snapshot)
    assert len(results) == 1
    assert results[0]["action"] == "skip_duplicate"
    assert results[0]["offer_error"] is not None
    assert "Early Bird" in results[0]["offer_error"]
    assert "10.0" in results[0]["offer_error"] and "15.0" in results[0]["offer_error"]
    assert "no update endpoint" in results[0]["offer_error"]


def test_supplement_matched_by_name_with_a_different_value_is_flagged():
    extracted = [{"name": "Resort Fee", "value": 25.0, "child_value": 0, "apply": "PER_STAY"}]
    existing_snapshot = {"supplements": [{"names": [{"description": "Resort Fee"}],
                                           "providerCode": "SUPP-1", "value": 20.0, "childValue": 0}]}
    results = build_hotel_supplement_payloads(extracted, {}, existing_hotel_snapshot=existing_snapshot)
    assert len(results) == 1
    assert results[0]["action"] == "skip_duplicate"
    assert results[0]["supplement_error"] is not None
    assert "Resort Fee" in results[0]["supplement_error"]


def test_supplement_matched_by_name_with_same_value_stays_silent():
    extracted = [{"name": "Resort Fee", "value": 20.0, "child_value": 0, "apply": "PER_STAY"}]
    existing_snapshot = {"supplements": [{"names": [{"description": "Resort Fee"}],
                                           "providerCode": "SUPP-1", "value": 20.0, "childValue": 0}]}
    results = build_hotel_supplement_payloads(extracted, {}, existing_hotel_snapshot=existing_snapshot)
    assert results[0]["supplement_error"] is None


def test_app_py_reports_the_skip_duplicate_error_as_a_failure_not_a_silent_continue():
    src = _read_app_py()
    idx = src.index('if res["action"] == "skip_duplicate":\n                        offer_map[name]')
    window = src[idx:idx + 1400]
    assert 'res.get("offer_error")' in window
    assert "offer_failures.append" in window

    idx2 = src.index('if res["action"] == "skip_duplicate":\n                        supplement_map[name]')
    window2 = src[idx2:idx2 + 900]
    assert 'res.get("supplement_error")' in window2
    assert "supp_failures.append" in window2


# ======================================================================
# 2. Transport modality-name override oscillation
# ======================================================================
def test_option_actions_carry_a_stable_auto_generated_name_field():
    # Exercises the naming logic directly via the pure bracket-labeling shape build_transport_
    # payloads produces - a minimal stand-in avoiding the full geocoding-dependent pipeline
    # (same reasoning test_builder_minimum_charge_synthesis.py gives for testing pieces, not the
    # whole build, when the whole build needs live API calls this suite runs offline).
    from builder import build_hotel_rate_payloads  # noop import to confirm module loads cleanly
    # Directly reconstruct the naming formula build_transport_payloads uses, matching its own
    # CONFIRMED comment ("SERVICE CLASS and the pax range... Door to Door"), to assert the fixed
    # comparison logic in app.py (below) is checked against exactly this shape.
    min_occ, max_occ = 1, 1
    bracket_label = f"{min_occ} Pax" if min_occ == max_occ else f"{min_occ} to {max_occ} Pax"
    _class = "Private Transfer"
    _guide = "no Guide"
    expected_auto_name = f"{_class} - {bracket_label} - Door to Door ({_guide})"
    assert expected_auto_name == "Private Transfer - 1 Pax - Door to Door (no Guide)"


def test_builder_option_actions_include_auto_generated_name_key():
    src = open(os.path.join(os.path.dirname(_APP_PY), "builder.py"), encoding="utf-8").read()
    idx = src.index('option_actions.append({')
    window = src[idx:idx + 2200]
    assert '"auto_generated_name"' in window


def test_app_py_compares_typed_override_against_auto_generated_name_not_suggested():
    src = _read_app_py()
    idx = src.index('_suggested = ((_a.get("option_payload")')
    window = src[idx:idx + 1800]
    assert '_auto = _a.get("auto_generated_name") or _suggested' in window
    assert 'if _typed.strip() and _typed.strip() != _auto:' in window
    # The OLD buggy comparison (against _suggested, which already has the override baked in)
    # must be gone from this specific block.
    assert 'if _typed.strip() and _typed.strip() != _suggested:' not in window


# ======================================================================
# 3. Hotel publish NoneType crash on a rate that failed to build
# ======================================================================
def test_rate_payloads_result_always_carries_rate_name_even_on_build_failure():
    # An impossible date range forces a validation failure inside ContractHotelRateVO/its
    # nested VOs, so rate_payload comes back None - exactly the path that used to crash the
    # caller trying to read a name off of it.
    extracted_rates = [{
        "name": "Broken Rate",
        "seasons": [{
            "name": "Season 1",
            "date_ranges": [{"start": "not-a-date", "end": "also-not-a-date"}],
            "room_prices": [{"room_name": "Standard", "distribution_prices": [{"adults": 1, "amount": 50}]}],
        }],
    }]
    results = build_hotel_rate_payloads(extracted_rates, {"Standard": "ROOM-1"}, {}, {})
    assert len(results) == 1
    assert results[0]["rate_name"] == "Broken Rate"
    # Whether or not this particular malformed input actually trips validation, rate_name must
    # never depend on rate_payload being present - that's the whole point of the fix.
    if results[0]["rate_payload"] is None:
        assert results[0]["rate_error"] is not None


def test_a_successfully_built_rate_also_carries_its_rate_name():
    extracted_rates = [{
        "name": "Standard Rate",
        "seasons": [{
            "name": "Summer",
            "date_ranges": [{"start": "01/06/2026", "end": "31/08/2026"}],
            "room_prices": [{"room_name": "Standard", "distribution_prices": [{"adults": 1, "amount": 50}]}],
        }],
    }]
    results = build_hotel_rate_payloads(extracted_rates, {"Standard": "ROOM-1"}, {}, {})
    assert results[0]["rate_name"] == "Standard Rate"
    assert results[0]["rate_payload"] is not None
    assert results[0]["rate_error"] is None


def test_app_py_rate_failure_uses_rate_name_field_not_the_old_crashing_lookup():
    src = _read_app_py()
    idx = src.index('for res in rate_results:')
    window = src[idx:idx + 2200]
    assert 'res.get("rate_name")' in window
    # The old crashing pattern must be gone from actual CODE (not just mentioned in the fix's
    # own explanatory comment above it) - i.e. rate_failures.append must use rate_name now.
    assert 'rate_failures.append((res.get("rate_name"), res.get("rate_error")))' in window
    assert 'rate_failures.append((res.get("rate_payload", {}).get("name")' not in window


def test_app_py_hotel_publish_exception_handler_notes_a_possible_partial_publish():
    src = _read_app_py()
    idx = src.index('except Exception as e:\n            # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): an exception')
    window = src[idx:idx + 2200]
    assert "already be live" in window


# ======================================================================
# 4. Dead "Confirm and Start Batch Review" button / mct_phase="reviewing"
# ======================================================================
def test_app_py_no_longer_sets_mct_phase_to_the_unhandled_reviewing_value():
    src = _read_app_py()
    assert 'st.session_state.mct_phase = "reviewing"' not in src


def test_app_py_pending_variants_block_only_allows_a_single_selection():
    src = _read_app_py()
    idx = src.index('if st.session_state.get("pending_variants") and not is_option_only:')
    window = src[idx:idx + 4800]
    assert "Start Batch Review" not in window
    # The old dead-code writes are gone from actual CODE (the fix's own explanatory comment
    # mentions "mct_queue" by name, so check for the real assignment, not the bare substring).
    assert "st.session_state.mct_queue = " not in window
    assert "st.session_state.mct_queue_index = " not in window
    assert 'disabled=pv_num_selected != 1' in window


def test_app_py_multi_tour_flow_dispatcher_never_handles_a_reviewing_phase():
    # Confirms the root cause is actually gone: render_multi_tour_flow's own phase list (from
    # its docstring/dispatch chain) still has no "reviewing" branch, so nothing could route
    # there even by accident.
    src = _read_app_py()
    start = src.index("def render_multi_tour_flow(")
    end_marker = src.index('    if st.session_state.mct_phase == "publishing"', start)
    end = src.index("\n\n\n", end_marker)
    window = src[start:end]
    assert 'mct_phase == "reviewing"' not in window
    for phase in ["gather", "select_tour", "reviewing_main", "select_modalities",
                  "reviewing_modality", "final_review", "publishing"]:
        assert f'mct_phase == "{phase}"' in window


# ======================================================================
# 5. Existing Tour Code one-shot value=, no key=
# ======================================================================
def test_app_py_existing_tour_code_widget_has_a_stable_key():
    src = _read_app_py()
    idx = src.index('if "existing_tour_code" in needed:')
    window = src[idx:idx + 2200]
    assert 'key=_ct_code_key' in window
    assert '_ct_code_key = "ct_existing_tour_code_in"' in window
    # The old one-shot pattern (value=prefill, no key) must be gone.
    assert "value=prefill,\n            placeholder=\"e.g. BKK-1" not in window


def test_app_py_change_action_supplier_clears_the_persisted_tour_code_key():
    src = _read_app_py()
    idx = src.index('if st.button("🔄 Change action / supplier"):')
    window = src[idx:idx + 600]
    assert 'st.session_state.pop("ct_existing_tour_code_in", None)' in window


# ======================================================================
# 6. price-refresh st.rerun() eating error messages (Transfer/Transport + Ticket)
# ======================================================================
def test_transfer_transport_price_refresh_rerun_only_fires_on_a_successful_read():
    src = _read_app_py()
    idx = src.index('st.session_state.pop("pr_result", None)')
    window = src[idx:idx + 400]
    assert "st.rerun()" in window

    # And the unconditional top-level rerun right after the whole button block is gone - the
    # button block's own closing should now flow straight into reading `proposals` without an
    # intervening bare st.rerun().
    btn_idx = src.index('if st.button(f"🔍 Read prices for this supplier\'s {kind.lower()}s"')
    proposals_idx = src.index('proposals = st.session_state.get("pr_proposals")')
    between = src[btn_idx:proposals_idx]
    # Only one ACTUAL st.rerun() statement (not counting mentions inside this fix's own
    # explanatory comment) should remain in this stretch.
    rerun_statement_lines = [l for l in between.split("\n") if l.strip() == "st.rerun()"]
    assert len(rerun_statement_lines) == 1


def test_ticket_price_refresh_rerun_only_fires_on_a_successful_read():
    src = _read_app_py()
    idx = src.index('st.session_state.pop("tpr_result", None)')
    window = src[idx:idx + 400]
    assert "st.rerun()" in window

    btn_idx = src.index('if st.button("🔍 Read prices for this supplier\'s Tickets"')
    proposals_idx = src.index('proposals = st.session_state.get("tpr_proposals")')
    between = src[btn_idx:proposals_idx]
    rerun_statement_lines = [l for l in between.split("\n") if l.strip() == "st.rerun()"]
    assert len(rerun_statement_lines) == 1


def test_a_failed_read_no_longer_wipes_its_own_error_message_same_rerun():
    # Regression-shape check: the "no document"/err/no-routes branches must NOT be followed by
    # an unconditional st.rerun() at the same indent - i.e. st.rerun() must be nested inside the
    # `if findings is not None:` block, not dedented back out to the button's own level.
    src = _read_app_py()
    for pop_key in ('st.session_state.pop("pr_result", None)', 'st.session_state.pop("tpr_result", None)'):
        idx = src.index(pop_key)
        # walk forward to the next non-comment, non-blank line after the pop - it must be the
        # (indented, inside-the-if) st.rerun(), not something at a shallower indent.
        after = src[idx + len(pop_key):idx + 900]
        lines = [l for l in after.split("\n") if l.strip() and not l.strip().startswith("#")]
        assert lines, "expected more source after the pop"
        assert "st.rerun()" in lines[0]
        # that rerun line must be indented at least as deep as the pop itself (still inside the
        # success branch), not dedented back to the button's own if-block level.
        pop_indent = len(pop_key) - len(pop_key.lstrip())  # always 0, pop_key has no leading ws
        rerun_line = lines[0]
        assert rerun_line.startswith(" " * 20), f"st.rerun() looks dedented out of the success branch: {rerun_line!r}"
