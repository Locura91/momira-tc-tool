"""Tests for the second batch of app.py ClosedTour/Ticket-flow HIGH findings (full-app audit,
fixed 2026-09-01):

  1. "Re-extract with updated hint" (ClosedTour Modality batch) kept the PREVIOUS extraction's
     Operational Days and Child Discount % on screen - fixed via widget-generation scoping
     (widget_state.py's established mechanism, already used elsewhere in this codebase).
  2. Ticket batch ("mt_"): the "I've checked this location" tick survived a location/city
     change - fixed with a twin of the existing _tk_clear_geo_confirmation() helper.
  3. Ticket batch: editing City after picking coordinates manually silently relabeled the OLD
     coordinates with the NEW city name - fixed by invalidating stale manual coordinates (and
     un-confirming geo_confirmed) whenever the City the coordinates were chosen for no longer
     matches the current City field.
  4. A failed ClosedTour publish (every option POST fails) used to leave the tour LIVE and
     ACTIVE with zero bookable Modalities and its Tour Code permanently taken - fixed by
     explicitly deactivating the tour when nothing was created successfully, instead of
     skipping the follow-up update entirely.
  5. Cross-boundary: `update_option` (both ClosedTour and Ticket) omitted currency from the
     pre-flight "Continue to Step 4" guard, letting an operator proceed without ever fetching
     the live record - fixed by adding "update_option" to both gate conditions.

app.py can't be safely imported in a test process (it runs top-level Streamlit calls on
import), so these read its source text directly - same approach as every other app.py-side
regression test in this suite (see test_2026_08_31_closedtour_child_discount_visibility.py).
"""
import os

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


# --- Finding 1: ClosedTour Modality re-extract staleness ---

def test_reextract_button_bumps_this_modalitys_own_widget_generation():
    source = _read_app_py()
    marker = 'if st.button("🔄 Re-extract with updated hint", key=f"mct_mod_reextract_{midx}"):'
    idx = source.index(marker)
    window = source[idx:idx + 1500]
    assert 'mod["data"] = None' in window
    assert 'bump_widget_generation(f"mct_mod_{midx}")' in window
    assert 'st.rerun()' in window


def test_operational_days_widget_uses_the_generation_scoped_key():
    source = _read_app_py()
    assert 'key=flow_widget_key(f"mct_mod_{midx}", "days")' in source
    # The old bare (never-cleared) key must be gone.
    assert 'key=f"mct_mod_days_{midx}"' not in source


def test_child_discount_editor_call_site_uses_the_generation_scoped_prefix():
    source = _read_app_py()
    assert 'render_child_discount_editor(data, flow_widget_key(f"mct_mod_{midx}", "cde"), currency)' in source


# --- Finding 2 & 3: Ticket batch geo-confirm / stale manual coordinates ---

def test_mt_clear_geo_confirmation_helper_exists_and_clears_both_the_flag_and_the_checkbox():
    source = _read_app_py()
    marker = "def _mt_clear_geo_confirmation(current, idx):"
    assert marker in source
    idx = source.index(marker)
    window = source[idx:idx + 1500]
    assert 'current["geo_confirmed"] = False' in window
    assert 'st.session_state.pop(f"mt_geo_confirm_{idx}", None)' in window


def test_search_pick_and_manual_entry_both_use_the_clear_helper_not_the_flag_alone():
    source = _read_app_py()
    # The search-result "Use this" button.
    pick_idx = source.index('if st.button("Use this", key=f"mt_geo_pick_{idx}_{gi}"):')
    pick_window = source[pick_idx:pick_idx + 400]
    assert '_mt_clear_geo_confirmation(current, idx)' in pick_window
    assert 'data["manual_coords_for_city"] = mt_city' in pick_window

    # The manual lat/lng entry button.
    manual_idx = source.index('if st.button("📍 Use these coordinates", key=f"mt_geo_manual_btn_{idx}"')
    manual_window = source[manual_idx:manual_idx + 400]
    assert '_mt_clear_geo_confirmation(current, idx)' in manual_window
    assert 'data["manual_coords_for_city"] = mt_city' in manual_window


def test_city_change_since_manual_coordinates_were_set_invalidates_them():
    source = _read_app_py()
    marker = 'data.get("manual_coords_for_city") != mt_city'
    assert marker in source
    idx = source.index(marker)
    window = source[max(0, idx - 300):idx + 400]
    assert 'data["manual_latitude"] = None' in window
    assert 'data["manual_longitude"] = None' in window
    assert '_mt_clear_geo_confirmation(current, idx)' in window


# --- Finding 4: failed ClosedTour publish left live/active with zero Modalities ---

def test_failed_batch_publish_deactivates_the_tour_instead_of_skipping_the_update():
    source = _read_app_py()
    marker = "no Modality options were created"
    assert marker in source
    idx = source.index(marker)
    window = source[max(0, idx - 700):idx + 1600]
    assert 'deactivate_payload["active"] = False' in window
    assert 'client.update_closed_tour(supplier_id, deactivate_payload)' in window
    # The old silent no-op (a warning with no corrective action) must be gone.
    assert 'skipped the follow-up update' not in source


# --- Finding 5: update_option missing from the currency pre-flight guard ---

def test_closedtour_update_option_is_now_in_the_step3_currency_gate():
    source = _read_app_py()
    marker = 'if action in ("update_tour", "add_option", "update_option") and not fetched_tour_matches_code(existing_tour_code_in):'
    assert marker in source
    # The old, narrower tuple that let update_option through unchecked must be gone.
    assert 'if action in ("update_tour", "add_option") and not fetched_tour_matches_code(existing_tour_code_in):' not in source


def test_ticket_update_option_is_now_in_the_step3_currency_gate():
    source = _read_app_py()
    marker = 'if action in ("add_option", "update_ticket", "update_option") and not st.session_state.get("tk_fetched_currency"):'
    assert marker in source
    assert 'if action in ("add_option", "update_ticket") and not st.session_state.get("tk_fetched_currency"):' not in source
