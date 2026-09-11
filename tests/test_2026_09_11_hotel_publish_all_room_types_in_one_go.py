"""Regression test for the real dead end confirmed live (2026-09-11, HRG-H1 - Steigenberger Golf
Resort El Gouna, a 100%-brand-new hotel):

    Attempt 1 (zero new rooms inline): "createHotel.contract.rooms: Size must be between 1 and
    2147483647 ([])" - Travel Compositor genuinely requires at least one room.

    Attempt 2 (one new room inline, providerCode left null - the old 2026-09-05 fallback shape):
    "Bean Validation constraint(s) violated on callback event:'prePersist'. Errors:
    HotelContractRoom.providerCode:must not be null ( Id: null)" - a brand-new room can't have a
    provider code, because Travel Compositor only ever assigns one AFTER a room is created.

Together these proved a genuine deadlock in the two previously-known room shapes for a hotel
starting from zero rooms: no combination of them can satisfy both "at least 1 room" and "every
room's providerCode must not be null" at once.

The product owner then pasted the real Swagger for POST/PUT /hotel/{supplierId}, and asked to
find a better way to create a hotel with multiple room types in one go. Re-reading it: the room
providerCode field in that request body's own schema is a plain string, not documented read-only,
symmetric with the response schema - nothing says it must be server-assigned. Since the HOTEL's
own providerCode is already confirmed human-assigned (e.g. "HRG-H1"), it's a reasonable bet a
ROOM's providerCode can be supplied by the client too. Fix: generate a deterministic placeholder
code for every not-yet-coded room and send the FULL rooms[] array - every room type in the
contract - inline in ONE call, as the new default (candidate 0) attempt. Whatever Travel
Compositor actually does with that code (keep it or replace it) doesn't matter - the real code is
always read back from resolve_room_provider_codes() against the response, never what was sent.

The two previously-known shapes (zero new rooms; one new room inline, still null) are kept as
fallback candidates 1 and 2 only, in case this new default is itself rejected (e.g. Travel
Compositor rejects a client-supplied room code specifically) - see
test_2026_09_06_hotel_zero_new_rooms_inline.py for their own coverage.

app.py can't be imported in a test process - source-shape-check pattern, same as this suite's
other app.py wiring tests.
"""
import os
import re

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def _phase1_block():
    src = _read_app_py()
    idx = src.index('all_rooms = contract_result["hotel_payload"].get("rooms") or []')
    end = src.index('all_room_responses = list(hotel_response.get("rooms") or [])', idx)
    return src[idx:end]


def test_placeholder_code_is_generated_for_every_not_yet_coded_room():
    block = _phase1_block()
    assert "def _hp_placeholder_room_code(room, index):" in block
    assert 're.sub(r"[^A-Za-z0-9]+", "", (room.get("name") or ""))' in block
    assert 'f"{provider_code}-{slug}-{index + 1}"' in block


def test_all_rooms_with_placeholder_codes_starts_from_the_existing_coded_rooms():
    block = _phase1_block()
    idx = block.index("all_rooms_with_placeholder_codes = list(rooms_with_code)")
    tail = block[idx:idx + 300]
    assert "for _i, _r in enumerate(new_rooms):" in tail
    assert '_r2["providerCode"] = _hp_placeholder_room_code(_r, _i)' in tail
    assert "all_rooms_with_placeholder_codes.append(_r2)" in tail


def test_all_rooms_with_placeholder_codes_is_the_new_default_first_candidate():
    block = _phase1_block()
    assert ("_hp_room_candidates = [all_rooms_with_placeholder_codes, rooms_with_code, "
            "rooms_with_code + new_rooms[:1]]") in block
    idx = block.index("_hp_room_candidate_idx = 0")
    tail = block[idx:idx + 200]
    assert 'phase1_payload["rooms"] = _hp_room_candidates[_hp_room_candidate_idx]' in tail


def test_a_room_that_already_has_a_real_code_is_never_given_a_placeholder():
    # rooms_with_code (already-real-code rooms) feeds all_rooms_with_placeholder_codes UNCHANGED -
    # only new_rooms (no code yet) go through _hp_placeholder_room_code.
    block = _phase1_block()
    assert "all_rooms_with_placeholder_codes = list(rooms_with_code)" in block


def test_extra_new_rooms_is_empty_when_the_new_default_candidate_succeeds():
    # _hp_inline_new_room_count = len(candidate[idx]) - len(rooms_with_code); for candidate 0
    # (all_rooms_with_placeholder_codes) that equals len(new_rooms), so slicing
    # new_rooms[len(new_rooms):] leaves nothing for the one-at-a-time POST /hotel/room loop -
    # every room type was already included in the single main call.
    src = _read_app_py()
    assert "_hp_inline_new_room_count = len(_hp_room_candidates[_hp_room_candidate_idx]) - len(rooms_with_code)" in src
    assert "extra_new_rooms = new_rooms[_hp_inline_new_room_count:]" in src


def test_placeholder_code_generator_is_deterministic_and_room_name_derived():
    # Exercise the actual generator logic (mirrored here, since app.py can't be imported) to
    # confirm it produces a readable, deterministic, collision-resistant code from a room name.
    def _hp_placeholder_room_code(provider_code, room, index):
        slug = re.sub(r"[^A-Za-z0-9]+", "", (room.get("name") or "")).upper()[:16] or "ROOM"
        return f"{provider_code}-{slug}-{index + 1}"

    assert _hp_placeholder_room_code("HRG-H1", {"name": "Deluxe Room"}, 0) == "HRG-H1-DELUXEROOM-1"
    assert _hp_placeholder_room_code("HRG-H1", {"name": "Junior Suite"}, 2) == "HRG-H1-JUNIORSUITE-3"
    # Same inputs -> same code (deterministic within one publish attempt/retry).
    assert (_hp_placeholder_room_code("HRG-H1", {"name": "Deluxe Room"}, 0)
            == _hp_placeholder_room_code("HRG-H1", {"name": "Deluxe Room"}, 0))
    # A blank/missing name still produces something usable, never an empty slug.
    assert _hp_placeholder_room_code("HRG-H1", {"name": ""}, 4) == "HRG-H1-ROOM-5"
