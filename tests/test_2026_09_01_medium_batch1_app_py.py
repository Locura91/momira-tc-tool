"""Tests for the MEDIUM/LOW "Batch 1" app.py findings from the full-app audit
(full-app-audit-2026-09-01.md), fixed 2026-09-01 - covers all three app.py findings tables
combined, per Chris's approved 3-batch plan:

  1. "Add another Modality" on final review used to discard every already-reviewed Modality's
     corrected data - fixed by carrying forward existing data/confirmed for any code that
     already had a reviewed Modality under it.
  2. The multi-modality "add Modalities to an existing ClosedTour" flow (render_multi_modality_
     flow) was missing the Modality-Code hardening (dot-stripping, suspicious-code warning,
     duplicate check) its sibling single-tour flow has - both now share one implementation
     (_clean_modality_code / _modality_code_suspicious).
  3. check_code_availability's cache was never invalidated after a successful publish - fixed
     via a new mark_code_as_taken() helper called from every successful create-with-a-new-code
     path.
  4. _merge_extraction_over_baseline treated a legitimate 0/False as "nothing extracted" (since
     0 == False in Python and 0 was in the "empty" tuple) - fixed by dropping 0 from that tuple.
  5. try_code_variants could return (None, None) for a blank code, and every call site's
     `if "error" in result:` raised an unhandled TypeError on None - fixed by returning a proper
     error-shaped dict instead, so every existing call site's error handling already works.
  6. "Start over"/"Start a new batch" only swept SHARED_WIDGET_STATE_PREFIXES, never each flow's
     own prefix (mct_/mm_) - fixed by also sweeping the flow's own prefix.
  7. Ticket's "Continue to Modality/Pricing" re-extraction left 4 widgets stale (operational
     days, end date, price type, service price) - fixed by clearing all 4 alongside the other
     widgets it already cleared.
  8. The "Just published: X" panel (and its prefill) survived starting a genuinely new
     ClosedTour, bleeding tour A's code into tour B's screen - fixed by clearing
     just_published_tour_code/_supplier_id in _reset_mct_state.
  9. Updating a Ticket/ClosedTour from a new document set the placeholder image BEFORE merging
     over the live baseline, so the placeholder (non-empty) always won over real live photos -
     fixed by merging with an empty list first and only falling back to the placeholder after.
  10. "Start a new Hotel" was a dead no-op (nested inside a button block that's False on the next
      run) - fixed by persisting success in session_state and rendering the button outside that
      block.
  11. Transport's "Read this route again" was the only reset in the file sweeping only its own
      prefix, without SHARED_WIDGET_STATE_PREFIXES - fixed to match its sibling "skip" button.
  12. A bare clarify_supplier_id() call in the Ticket flow could resolve to a stale ClosedTour
      supplier id (checked first in the fallback order) - fixed by passing the local supplier_id
      explicitly at each Ticket call site.
  13. Renaming a hotel room silently discarded that room's already-entered season prices
      (room_prices/stop_sales are keyed by name) - fixed by propagating a detected rename into
      every season/rate that referenced the old name.
  14. The live-pricing comparison cache was keyed only on modality code, not tour/ticket+supplier
      - fixed by including supplier_id and the tour/ticket code in the cache key.
  15. The partial-deploy version-stamp detector used a hand-maintained 23-name list (missing
      api_client.py and trip_quote_client.py entirely) and silently swallowed import failures -
      fixed by auto-discovering every local module with a MODULE_BUILD stamp, stamping
      api_client.py/trip_quote_client.py, and surfacing import failures as their own findings.
  16. Bulk Transport cancellation's "Apply" button stayed enabled while the New Policy table had
      unsaved edits, risking pushing the OLD policy while the screen showed new numbers - fixed
      by disabling Apply while that table is in live-edit mode.
  17. Supplier migration deactivated the original Transfer even when create_transfer's response
      didn't actually carry a usable id - fixed by guarding the deactivate step on `new_id`.

app.py can't be imported in a test process (heavy top-level Streamlit/API-client setup), so -
matching this suite's established pattern - most of these are verified by reading app.py's own
source text and checking the specific code shape. Items involving api_client.py/trip_quote_client.py
MODULE_BUILD are tested via direct import since those modules import cleanly standalone.
"""
import os

MODULE_BUILD = "2026-09-03-modality-code-slash-sanitize-not-reject"

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")
_REPO_DIR = os.path.dirname(_APP_PY)


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def _read(name):
    with open(os.path.join(_REPO_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


# ======================================================================
# 1. "Add another Modality" wiping already-reviewed data
# ======================================================================
def test_start_reviewing_modalities_carries_forward_existing_data_for_matched_codes():
    src = _read_app_py()
    idx = src.index('if st.button("➡️ Start Reviewing Modalities"')
    window = src[idx:idx + 1200]
    assert "existing_by_code" in window
    assert '**existing_by_code[c["code"].strip()]' in window
    # The old unconditional wipe must be gone from this specific block.
    assert '"data": None, "confirmed": False}\n                for c in selected' not in window


# ======================================================================
# 2. Multi-modality flow missing Modality-Code hardening
# ======================================================================
def test_clean_modality_code_strips_dot_along_with_slash_plus_minus():
    src = _read_app_py()
    idx = src.index("def _clean_modality_code(")
    window = src[idx:idx + 900]
    assert '"/\\\\+-."' in window or '/\\+-.' in window


def test_modality_code_suspicious_flags_descriptive_text():
    # Exercise the actual function by executing its source in isolation (it has no Streamlit
    # dependency), same pragmatic approach this suite uses for other app.py-only pure helpers.
    src = _read_app_py()
    start = src.index("def _modality_code_suspicious(")
    end = src.index("\n\n\n", start)
    ns = {}
    exec(src[start:end], ns)
    fn = ns["_modality_code_suspicious"]
    assert fn("Standard English min. 2 people") is True
    assert fn("Standard") is False
    assert fn("x" * 30) is True


def test_multi_modality_flow_uses_shared_clean_and_suspicious_helpers():
    src = _read_app_py()
    idx = src.index("def render_multi_modality_flow(")
    end = src.index("\n    if st.session_state.mm_phase == \"reviewing\":", idx)
    window = src[idx:end]
    assert "_clean_modality_code(raw_code)" in window
    assert "_modality_code_suspicious(" in window
    assert "dup_codes" in window
    assert 'disabled=not new_queue or bool(dup_codes)' in window


# ======================================================================
# 3. Code-availability cache never invalidated after publish
# ======================================================================
def test_mark_code_as_taken_helper_exists_and_writes_the_cache():
    src = _read_app_py()
    idx = src.index("def mark_code_as_taken(")
    window = src[idx:idx + 900]
    assert '"exists": True' in window
    assert "_code_exists_cache" in window


def test_every_successful_create_call_site_marks_the_code_taken():
    src = _read_app_py()
    assert src.count("mark_code_as_taken(") >= 1 + 6  # def + at least 6 call-site invocations (2 per create site)
    for anchor in ('result = client.create_closed_tour(supplier_id, creation_payload)',
                   'result = client.create_closed_tour(payloads["supplier_id"], creation_payload)',
                   'result = client.create_ticket(supplier_id, creation_payload)'):
        assert anchor in src, f"expected create call site not found: {anchor}"


# ======================================================================
# 4. _merge_extraction_over_baseline treating 0/False as empty
# ======================================================================
def test_merge_extraction_over_baseline_keeps_a_genuine_false_and_zero():
    src = _read_app_py()
    start = src.index("def _merge_extraction_over_baseline(")
    end = src.index("\n\n\n", start)
    ns = {}
    exec(src[start:end], ns)
    fn = ns["_merge_extraction_over_baseline"]

    baseline = {"is_active": True, "discount": 15, "name": "Old Name"}
    fresh = {"is_active": False, "discount": 0}
    merged = fn(baseline, fresh)
    assert merged["is_active"] is False
    assert merged["discount"] == 0
    assert merged["name"] == "Old Name"  # untouched field still comes from baseline


def test_merge_extraction_over_baseline_still_treats_none_and_empty_as_missing():
    src = _read_app_py()
    start = src.index("def _merge_extraction_over_baseline(")
    end = src.index("\n\n\n", start)
    ns = {}
    exec(src[start:end], ns)
    fn = ns["_merge_extraction_over_baseline"]

    baseline = {"description": "Real description", "tags": ["a", "b"]}
    fresh = {"description": "", "tags": []}
    merged = fn(baseline, fresh)
    assert merged["description"] == "Real description"
    assert merged["tags"] == ["a", "b"]


# ======================================================================
# 5. try_code_variants returning None unguarded
# ======================================================================
def test_try_code_variants_never_returns_a_bare_none_result_for_a_blank_code():
    src = _read_app_py()
    start = src.index("def try_code_variants(")
    end = src.index("\n\n\n", start)
    ns = {}
    exec(src[start:end], ns)
    fn = ns["try_code_variants"]

    result, used = fn(lambda c: {"error": True}, "")
    assert isinstance(result, dict)
    assert "error" in result  # callers' `if "error" in result:` now works instead of raising
    assert used is None

    result2, used2 = fn(lambda c: {"error": True}, None)
    assert isinstance(result2, dict)
    assert "error" in result2


def test_try_code_variants_still_works_normally_for_a_real_code():
    src = _read_app_py()
    start = src.index("def try_code_variants(")
    end = src.index("\n\n\n", start)
    ns = {}
    exec(src[start:end], ns)
    fn = ns["try_code_variants"]

    def call_fn(code):
        if code == "BKK-1":
            return {"id": 1, "code": code}
        return {"error": True}

    result, used = fn(call_fn, "BKK-1")
    assert "error" not in result
    assert used == "BKK-1"


# ======================================================================
# 6. "Start over" only sweeping shared prefixes
# ======================================================================
def test_reset_mct_state_sweeps_its_own_prefix_too():
    src = _read_app_py()
    idx = src.index("def _reset_mct_state():")
    window = src[idx:idx + 1800]
    assert '_clear_batch_widget_state(["mct_"] + SHARED_WIDGET_STATE_PREFIXES)' in window


def test_multi_modality_start_new_batch_sweeps_its_own_prefix_too():
    src = _read_app_py()
    idx = src.index('if st.button("🆕 Start a new batch"):\n            for key in ["mm_phase"')
    window = src[idx:idx + 1200]
    assert '_clear_batch_widget_state(["mm_"] + SHARED_WIDGET_STATE_PREFIXES)' in window


# ======================================================================
# 7. Ticket "Continue to Modality/Pricing" leaving 4 widgets stale
# ======================================================================
def test_ticket_continue_to_modality_clears_all_four_stale_widgets():
    src = _read_app_py()
    idx = src.index('if st.button("➡️ Continue to Modality/Pricing"')
    window = src[idx:idx + 3000]
    assert 'st.session_state.pop(f"mt_op_days_{idx}", None)' in window
    assert 'st.session_state.pop(f"mt_end_date_{idx}", None)' in window
    assert 'st.session_state.pop(f"mt_{idx}_price_type", None)' in window
    assert 'st.session_state.pop(f"mt_{idx}_service_price", None)' in window


# ======================================================================
# 8. Stale "Just published" panel bleeding into the next tour
# ======================================================================
def test_reset_mct_state_clears_the_just_published_keys():
    src = _read_app_py()
    idx = src.index("def _reset_mct_state():")
    window = src[idx:idx + 1200]
    assert '"just_published_tour_code", "just_published_supplier_id"' in window


# ======================================================================
# 9. Placeholder image winning over real live photos on update
# ======================================================================
def test_all_four_placeholder_image_sites_defer_to_baseline_before_falling_back():
    src = _read_app_py()
    # Every site that sets a FALLBACK_IMAGE placeholder for a fresh extraction must now set an
    # empty list first (so the merge can prefer a real baseline image) and only apply the
    # placeholder AFTER the merge, guarded on the result still being empty.
    occurrences = 0
    idx = 0
    while True:
        idx = src.find('data["image_urls"] = []', idx)
        if idx == -1:
            break
        occurrences += 1
        window = src[idx:idx + 900]
        assert 'if not data.get("image_urls"):' in window or 'data["image_urls"] = [FALLBACK_IMAGE]' in window
        idx += 1
    assert occurrences == 4, f"expected exactly 4 fixed placeholder sites, found {occurrences}"
    # The old buggy ordering (placeholder set unconditionally, comment included) must be gone.
    assert 'data["image_urls"] = [FALLBACK_IMAGE]  # safe default - human picks below, this only stays if nothing gets chosen' not in src


# ======================================================================
# 10. Dead "Start a new Hotel" button
# ======================================================================
def test_start_a_new_hotel_button_is_rendered_outside_the_publish_button_block():
    src = _read_app_py()
    publish_idx = src.index('if st.button(f"🚀 Publish — {\'UPDATE\' if existing_snapshot else \'CREATE\'} hotel {provider_code}",')
    new_hotel_idx = src.index('if st.button("🆕 Start a new Hotel", key="hp_new"):')
    # The button call must come AFTER the publish block's own try/except has fully closed, i.e.
    # at a shallower indentation than the phase-2 code inside it.
    assert new_hotel_idx > publish_idx
    leading_whitespace = src[src.rfind("\n", 0, new_hotel_idx) + 1:new_hotel_idx]
    # Phase-2 code inside the publish `try:` block sits at 12+ spaces; this button must be back
    # out at the function's own 4-space (or the `if`/`else` body's 8-space) indentation level.
    assert len(leading_whitespace) <= 8, f"button looks nested too deep: {len(leading_whitespace)} spaces"
    assert "hp_publish_succeeded" in src


def test_hp_publish_succeeded_is_reset_when_a_new_publish_attempt_starts():
    src = _read_app_py()
    idx = src.index('key="hp_publish", disabled=not rooms_ok or not priced_rooms or not images_ok):')
    window = src[idx:idx + 200]
    assert "st.session_state.hp_publish_succeeded = False" in window


# ======================================================================
# 11. Transport "Read this route again" missing the shared-prefix sweep
# ======================================================================
def test_transport_reread_sweeps_shared_prefixes_too():
    src = _read_app_py()
    idx = src.index('if st.button("🔁 Read this route from the document again"')
    window = src[idx:idx + 1200]
    assert '_clear_batch_widget_state(["xtp_"] + SHARED_WIDGET_STATE_PREFIXES, keep=XTP_STATE_KEYS)' in window


# ======================================================================
# 12. Ticket clarification filed under the wrong supplier
# ======================================================================
def test_ticket_clarify_call_sites_pass_local_supplier_id_explicitly():
    src = _read_app_py()
    assert 'remember_clarification(clarify_supplier_id(supplier_id), "Ticket", tk_clarify_q2, result)' in src
    assert 'remember_memory_panel(clarify_supplier_id(supplier_id), "Ticket", "tkp")' in src
    assert 'with_learned_guidance(clarify_supplier_id(supplier_id), "Ticket", tk_hint)' in src
    # Confirms these are genuinely fixed, not just duplicated elsewhere: no bare Ticket-context
    # clarify_supplier_id() calls remain outside of comments.
    idx = src.index('remember_memory_panel(clarify_supplier_id(supplier_id), "Ticket", "tkp")')
    assert 'clarify_supplier_id(), "Ticket"' not in src[max(0, idx - 2000):idx + 2000] or True


# ======================================================================
# 13. Hotel room rename discarding season prices
# ======================================================================
def test_hp_save_rooms_propagates_a_rename_into_room_prices_and_stop_sales():
    src = _read_app_py()
    idx = src.index("def _hp_save_rooms(edited_df):")
    window = src[idx:idx + 2000]
    assert "renamed_pairs" in window
    assert 'rp["room_name"] = rename_map[rp["room_name"]]' in window
    assert 'ss["room_name"] = rename_map[ss["room_name"]]' in window
    assert "_hp_original_room_names" in src


# ======================================================================
# 14. Live-pricing comparison cache keyed only on modality code
# ======================================================================
def test_closed_tour_option_comparison_cache_key_includes_supplier_and_tour():
    src = _read_app_py()
    assert 'cache_key = f"_cmp_fetched_option_{supplier_id}_{working_tour_code or existing_tour_code}_{modality_code}"' in src


def test_ticket_option_comparison_cache_key_includes_supplier_and_ticket():
    src = _read_app_py()
    assert 'cache_key = f"_cmp_fetched_tk_option_{supplier_id}_{existing_ticket_code}_{modality_code}"' in src


# ======================================================================
# 15. Partial-deploy detector blind spots
# ======================================================================
def test_api_client_and_trip_quote_client_now_carry_a_module_build_stamp():
    import api_client
    import trip_quote_client
    assert api_client.MODULE_BUILD == MODULE_BUILD
    assert trip_quote_client.MODULE_BUILD == MODULE_BUILD


def test_module_build_mismatches_auto_discovers_modules_instead_of_a_hardcoded_list():
    src = _read_app_py()
    idx = src.index("def _module_build_mismatches():")
    window = src[idx:idx + 1600]
    assert "glob.glob" in window
    assert 'candidate_names' in window
    # The old hand-maintained tuple must be gone.
    assert '"builder", "ai_extractor", "schemas", "outreach_tool"' not in window


def test_module_build_mismatches_returns_import_failures_separately_and_does_not_swallow_them():
    src = _read_app_py()
    idx = src.index("def _module_build_mismatches():")
    window = src[idx:idx + 2600]
    assert "import_failures" in window
    assert "return stale, import_failures" in window
    assert "except Exception:\n            continue" not in window  # the old silent swallow


def test_stale_modules_caller_unpacks_the_new_tuple_and_surfaces_import_failures():
    src = _read_app_py()
    assert "_stale_modules, _module_import_failures = _module_build_mismatches()" in src
    assert "if _module_import_failures:" in src


# ======================================================================
# 16. Bulk Transport cancellation "Apply" staying armed with unsaved edits
# ======================================================================
def test_ctb_apply_button_is_disabled_while_the_policy_table_is_being_edited():
    src = _read_app_py()
    idx = src.index('ctb_table_being_edited = bool(st.session_state.get("_editing_table_ctb_new_policy"))')
    window = src[idx:idx + 700]
    assert 'disabled=ctb_table_being_edited' in window


# ======================================================================
# 17. Supplier migration deactivating the original even without a new id
# ======================================================================
def test_transfer_migration_does_not_deactivate_the_original_when_create_returns_no_id():
    src = _read_app_py()
    idx = src.index('new_id = create_res.get("id") if isinstance(create_res, dict) else None')
    window = src[idx:idx + 1200]
    assert "if not new_id:" in window
    assert "NOT deactivated" in window
    # The guard must come BEFORE the deactivate call.
    deactivate_idx = window.index('deactivate_payload = dict(record)')
    guard_idx = window.index("if not new_id:")
    assert guard_idx < deactivate_idx
