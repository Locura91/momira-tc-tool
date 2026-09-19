"""Source-text regression tests for a real product-owner request (2026-09-19, verbatim):

    "when creating new hotel room tytpes, ask human first which one he would like to add.
    Similar to the modailities in closedtour, because we do not need to include all rooms that
    are avaialable."

Before this, extract_hotel_data's own "rooms" list went straight into the Step 4 review screen's
editable "Room types" table with every AI-detected room already included - the only way to drop
one the human didn't want was to delete its row from that table by hand, after the fact. Mirrors
flows/multi_tour.py's own select_modalities phase (AI-detected candidates, each pre-ticked,
human unticks/removes/adds before anything is built): a new "select_rooms" hp_phase sits between
extraction and the full review screen, gated so it's skipped entirely when extraction found no
rooms at all (a rate-only update to an already-existing hotel legitimately extracts zero - see
test_2026_09_16_hotel_room_distributions_seeded_from_existing.py).

Same established source-text-check pattern every other flows/hotel.py wiring test in this suite
uses (a live Streamlit session can't be driven directly here).
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_HOTEL_FLOW_PY = os.path.join(os.path.dirname(_HERE), "flows", "hotel.py")


def _read_hotel_flow():
    with open(_HOTEL_FLOW_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_extraction_routes_to_select_rooms_when_rooms_were_extracted():
    src = _read_hotel_flow()
    idx = src.index('st.session_state.hp_room_candidates = None')
    window = src[idx:idx + 300]
    assert '"select_rooms" if (st.session_state.hp_data.get("rooms") or []) else "reviewing"' in window


def test_select_rooms_phase_exists_and_precedes_the_review_phase():
    src = _read_hotel_flow()
    select_idx = src.index('if st.session_state.hp_phase == "select_rooms":')
    review_idx = src.index("PHASE 2: review everything, then publish", select_idx)
    assert select_idx < review_idx


def test_select_rooms_phase_seeds_candidates_from_extracted_rooms_pre_selected():
    src = _read_hotel_flow()
    select_idx = src.index('if st.session_state.hp_phase == "select_rooms":')
    window = src[select_idx:select_idx + 1200]
    assert 'st.session_state.hp_data.get("rooms") or []' in window
    assert '"selected": True' in window


def test_select_rooms_phase_supports_bulk_select_all_none():
    src = _read_hotel_flow()
    select_idx = src.index('if st.session_state.hp_phase == "select_rooms":')
    window = src[select_idx:select_idx + 1800]
    assert 'render_candidate_filter(candidates, "hp_roomcand", "room type")' in window


def test_select_rooms_phase_supports_include_toggle_rename_and_removal():
    src = _read_hotel_flow()
    select_idx = src.index('if st.session_state.hp_phase == "select_rooms":')
    window = src[select_idx:select_idx + 2500]
    assert 'cand["selected"] = st.checkbox("Include"' in window
    assert 'cand["name"] = st.text_input("Room name"' in window
    assert 'candidates.pop(i)' in window
    assert '_clear_batch_widget_state(["hp_roomcand_"])' in window


def test_select_rooms_phase_supports_adding_a_room_manually():
    src = _read_hotel_flow()
    select_idx = src.index('if st.session_state.hp_phase == "select_rooms":')
    window = src[select_idx:select_idx + 3200]
    assert 'candidates.append({"name": "", "distributions": [], "type_id": None, "selected": True})' in window


def test_continuing_filters_data_rooms_down_to_only_the_selected_named_candidates():
    src = _read_hotel_flow()
    select_idx = src.index('if st.session_state.hp_phase == "select_rooms":')
    continue_idx = src.index('"➡️ Continue to full review"', select_idx)
    window = src[continue_idx:continue_idx + 500]
    assert 'st.session_state.hp_data["rooms"] = [' in window
    assert 'for c in candidates if c["selected"] and (c["name"] or "").strip()' in window
    assert 'st.session_state.hp_phase = "reviewing"' in window


def test_hp_room_candidates_is_swept_on_start_over():
    src = _read_hotel_flow()
    hp_state_keys_idx = src.index("HP_STATE_KEYS = [")
    window = src[hp_state_keys_idx:hp_state_keys_idx + 400]
    assert '"hp_room_candidates"' in window
