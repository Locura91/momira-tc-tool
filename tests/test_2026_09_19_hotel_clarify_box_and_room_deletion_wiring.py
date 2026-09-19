"""Source-text regression tests for two Hotel-flow additions (product owner, 2026-09-19,
verbatim, in one message):

    "hotel creation: Max occupancy is 9 pax. No room type need ever more than 9 pax per room so
    no more than 9 pax prices. Before puclishing hotel, we also must add as same as in other
    creation tools a AI text field to make some general adjustments."
    "also, we must make it possible to delete a complete room and not only occupancy"

The 9-pax cap itself was already fully in place before this message (see
tests/test_2026_09_18_max_occupancy_caps_triple_quadruple.py and
claude/max-occupancy-2-no-triple-quadruple-price-2026-09-18.md - _room_pax_cap/
_clip_distributions_to_pax_cap/_clip_distributions_to_pax_cap_priced already enforce it
unconditionally at both Phase 1 (room distributions) and Phase 2 (priced seasonRoomPrices), with
or without a room's own stated max_occupancy), so nothing further was needed there. The other two
asks - a "Tell AI what to fix" box before Publish, and a way to delete a whole room - genuinely
didn't exist yet on Hotel and are what this file covers.

build_hotel_contract_payload's own rooms_to_delete behavior is a pure function and is covered
directly in tests/test_2026_09_19_hotel_room_deletion.py; this file only checks the flows/hotel.py
UI wiring (same established source-text-check pattern every other flows/hotel.py wiring test in
this suite uses, e.g. test_2026_09_16_hotel_room_distributions_seeded_from_existing.py) since a
full Streamlit UI can't be driven directly in this test suite.
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_HOTEL_FLOW_PY = os.path.join(os.path.dirname(_HERE), "flows", "hotel.py")


def _read_hotel_flow():
    with open(_HOTEL_FLOW_PY, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------------------------
# "Tell AI what to fix" clarify box - matches Ticket/ClosedTour/Modality's own box
# ---------------------------------------------------------------------------------------------

def test_hotel_has_a_tell_ai_what_to_fix_box():
    src = _read_hotel_flow()
    assert "Tell AI what to fix or clarify" in src


def test_clarify_box_sits_before_the_publish_section():
    src = _read_hotel_flow()
    clarify_idx = src.index("Tell AI what to fix or clarify")
    publish_idx = src.index("PUBLISH - two phases, in order", clarify_idx)
    assert clarify_idx < publish_idx


def test_clarify_box_calls_apply_clarification_with_the_hotel_raw_text_and_data():
    src = _read_hotel_flow()
    clarify_idx = src.index("Tell AI what to fix or clarify")
    call_idx = src.index("apply_clarification(", clarify_idx)
    window = src[call_idx:call_idx + 200]
    assert 'st.session_state.get("hp_raw_text"' in window
    assert "data" in window
    assert "hp_clarify_q" in window


def test_clarify_box_applies_changes_and_renders_the_result():
    src = _read_hotel_flow()
    clarify_idx = src.index("Tell AI what to fix or clarify")
    window = src[clarify_idx:clarify_idx + 3000]
    assert "apply_clarify_changes(data, result, currency)" in window
    assert "render_clarify_result(" in window
    assert "reset_stale_editable_field_widgets(result[\"changes\"])" in window


def test_clarify_box_resets_every_known_hotel_table_on_a_matching_change():
    src = _read_hotel_flow()
    clarify_idx = src.index("Tell AI what to fix or clarify")
    window = src[clarify_idx:clarify_idx + 3000]
    for field, table_key in [("images", "hp_images"), ("rooms", "hp_rooms"),
                              ("meal_plans", "hp_mealplans"), ("offers", "hp_offers"),
                              ("supplements", "hp_supplements")]:
        assert f'"{field}": "{table_key}"' in window


def test_clarify_box_sweeps_the_nested_rate_tables_when_rates_changes():
    src = _read_hotel_flow()
    clarify_idx = src.index("Tell AI what to fix or clarify")
    window = src[clarify_idx:clarify_idx + 3000]
    assert '"rates" in result["changes"]' in window
    for prefix in ("_editing_table_hp_dr_", "_editing_table_hp_dp_", "_editing_table_hp_ss_"):
        assert prefix in window


def test_clarify_box_supports_the_house_rule_shortcut_and_memory_panel():
    src = _read_hotel_flow()
    clarify_idx = src.index("Tell AI what to fix or clarify")
    window = src[clarify_idx:clarify_idx + 3000]
    assert 'render_house_rule_shortcut(hp_clarify_q, "Hotel", "hp_main")' in window
    assert 'remember_clarification(clarify_supplier_id(supplier_id), "Hotel", hp_clarify_q, result)' in window
    assert 'remember_memory_panel(clarify_supplier_id(supplier_id), "Hotel", "hp")' in window


def test_hotel_flow_imports_the_clarify_machinery():
    src = _read_hotel_flow()
    for name in ("apply_clarification", "apply_clarify_changes", "clarify_supplier_id",
                 "remember_clarification", "remember_memory_panel", "render_clarify_result",
                 "render_house_rule_shortcut", "reset_stale_editable_field_widgets",
                 "HOUSE_RULE_CODEWORD"):
        assert name in src, f"{name} not imported into flows/hotel.py"


# ---------------------------------------------------------------------------------------------
# "Delete an existing room entirely"
# ---------------------------------------------------------------------------------------------

def test_hotel_has_a_delete_room_section_gated_on_an_existing_snapshot():
    src = _read_hotel_flow()
    gate_idx = src.index('if existing_snapshot and existing_snapshot.get("rooms"):')
    # The expander itself must come shortly after the gate check - only a room that's already
    # live can be deleted this way; a brand-new hotel or a document-only room needs no special
    # handling at all.
    window = src[gate_idx:gate_idx + 200]
    assert "Delete an existing room entirely" in window


def test_delete_room_section_sits_before_the_publish_section():
    src = _read_hotel_flow()
    delete_idx = src.index("Delete an existing room entirely")
    publish_idx = src.index("PUBLISH - two phases, in order", delete_idx)
    assert delete_idx < publish_idx


def test_delete_room_selection_is_stored_on_data_and_passed_to_the_builder():
    src = _read_hotel_flow()
    assert 'data["rooms_to_delete"] = _hp_rooms_to_delete' in src
    build_idx = src.index("build_hotel_contract_payload(")
    window = src[build_idx:build_idx + 300]
    assert 'rooms_to_delete=data.get("rooms_to_delete")' in window


def test_delete_room_options_come_from_the_existing_snapshot_not_the_fresh_document():
    src = _read_hotel_flow()
    delete_idx = src.index("Delete an existing room entirely")
    window = src[delete_idx:delete_idx + 2200]
    assert 'existing_snapshot.get("rooms")' in window
    assert "st.multiselect(" in window
