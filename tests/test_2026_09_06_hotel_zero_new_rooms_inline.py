"""Regression test for a real production failure (product owner, 2026-09-06, HRG-H1 -
Steigenberger Golf Resort El Gouna - the very first 100%-brand-new hotel this tool published
after the geolocation and image-rejection fixes shipped earlier the same day):

    Publishing the main hotel contract, with exactly ONE brand-new room inline (providerCode
    still None) as the 2026-09-05 fix intended, failed with:

        Bean Validation constraint(s) violated on callback event:'prePersist'.
        Errors: HotelContractRoom.providerCode:must not be null ( Id: null)

Root cause: the 2026-09-05 fix's assumption that "at most one new room inline is safe" rested on
a single real precedent (CAI-H1, Four Seasons Cairo) that turned out - on closer inspection of
schemas.py's and api_client.py's own comments - to only ever have been observed via a GET of an
already-populated hotel record, never an actual create-time success. So it never actually proved
Travel Compositor accepts a null-providerCode room inline at all, for any hotel.

Fix: Phase 1 now tries the main create_hotel()/update_hotel() call FIRST with ZERO new rooms
inline (only rooms that already carry a real providerCode - which can be an empty list for a
100%-new hotel). EVERY brand-new room, including the very first, is then added afterward one at a
time via the existing POST /hotel/room endpoint (client.create_hotel_room) - the same mechanism
the 2026-09-05 fix already built for "extra" rooms beyond the first, now used for all of them.
Because ContractHotelVO's own Swagger docs claim rooms are "required, min 1 item" inline - a claim
never actually confirmed against a real create call either - the code keeps one fallback: if TC's
server instead rejects the empty-rooms shape with a rooms-related complaint that ISN'T the
null-providerCode error, it retries once with the old one-new-room-inline shape.

app.py can't be imported in a test process (heavy top-level Streamlit/API-client setup), so this
is verified by reading its own source text, per this suite's established pattern.
"""
import os

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def _phase1_block():
    src = _read_app_py()
    idx = src.index("all_rooms = contract_result[\"hotel_payload\"].get(\"rooms\") or []")
    end = src.index("all_room_responses = list(hotel_response.get(\"rooms\") or [])", idx)
    return src[idx:end]


def test_default_room_candidate_has_zero_new_rooms_inline():
    window = _phase1_block()
    assert '_hp_room_candidates = [rooms_with_code, rooms_with_code + new_rooms[:1]]' in window
    assert '_hp_room_candidate_idx = 0' in window
    assert 'phase1_payload["rooms"] = _hp_room_candidates[_hp_room_candidate_idx]' in window


def test_escalates_to_one_new_room_inline_only_on_a_non_providercode_rooms_error():
    window = _phase1_block()
    idx = window.index("_hp_room_candidate_idx < len(_hp_room_candidates) - 1")
    tail = window[idx:idx + 1200]
    assert '"room" in _hp_error_text.lower()' in tail
    assert '"providerCode" not in _hp_error_text' in tail
    assert "_hp_room_candidate_idx += 1" in tail
    assert 'phase1_payload["rooms"] = _hp_room_candidates[_hp_room_candidate_idx]' in tail
    assert "continue" in tail


def test_does_not_escalate_forever_bounded_by_candidate_list_length():
    window = _phase1_block()
    assert "len(_hp_room_candidates) - 1" in window


def test_extra_new_rooms_computed_from_whichever_candidate_actually_succeeded():
    window = _phase1_block()
    idx = window.index("_hp_inline_new_room_count = len(_hp_room_candidates[_hp_room_candidate_idx]) - len(rooms_with_code)")
    assert idx > 0
    tail = window[idx:idx + 200]
    assert "extra_new_rooms = new_rooms[_hp_inline_new_room_count:]" in tail


def test_room_error_text_is_extracted_via_the_shared_helper():
    window = _phase1_block()
    assert "_hp_error_text = str(_extract_error_message_detail(hotel_response) or \"\")" in window
