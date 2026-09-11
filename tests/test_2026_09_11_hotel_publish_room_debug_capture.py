"""Regression test for a real hotel-publish failure (2026-09-11, HRG-H1 - Steigenberger Golf
Resort El Gouna, a 100%-brand-new hotel with zero pre-existing rooms):

    "Couldn't publish hotel HRG-H1: Bean Validation constraint(s) violated on callback
    event:'prePersist'. Errors: HotelContractRoom.providerCode:must not be null ( Id: null)"

The product owner asked directly: "now error with that information. Do you need the swagger
for imporvement?"

Investigation of the Phase 1 room-shape retry loop (app.py's Hotel publish button, "🚀 Publish")
found that its own two candidate room shapes are:
    1. no new rooms inline (rooms_with_code only - can be [] for a 100%-new hotel)
    2. rooms_with_code + exactly one brand-new room inline (providerCode still None)
Shape 2 is EXACTLY the shape schemas.py's own ContractRoomVO docstring already documents as
confirmed-unsafe (2026-09-06 finding: a brand-new room inline, providerCode=None, is rejected
with this same prePersist error) - so if the app ever escalates to shape 2, that attempt is
essentially guaranteed to fail with the identical error, making the user's report entirely
consistent with what the code already predicts, not a new/different bug.

Since the two attempts' raw errors could not be distinguished from the product owner's own
report (show_publish_error's expander only ever showed the LAST attempt's error, and the
escalation warning's own "Technical details - empty-rooms attempt" expander was easy to miss/not
expand), this fix adds a per-attempt debug capture - which rooms[] shape was actually sent and
the exact raw response for EACH attempt - shown together in one expander, on both the success and
the final-failure paths. This mirrors the debug-capture pattern already shipped for
price_refresh.py's per-route request/response capture (2026-09-11, see
claude/transport-supplement-write-not-persisting-debug-capture-2026-09-11.md).

app.py can't be imported in a test process (no Streamlit runtime) - same established
source-shape-check pattern as test_2026_09_11_hotel_masterdata_auto_images.py.
"""
import os

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def _phase1_block(src):
    start = src.index('# ---- PHASE 1: the hotel contract itself (rooms + meal plans inline) ----')
    end = src.index('# Every brand-new room NOT included inline')
    return src[start:end]


def test_each_phase1_attempt_is_captured_with_its_rooms_shape_and_raw_response():
    block = _phase1_block(_read_app_py())
    assert '_hp_phase1_attempts = []' in block
    assert '_hp_phase1_attempts.append({' in block
    assert '"candidate_idx": _hp_room_candidate_idx,' in block
    assert '"rooms_sent":' in block
    assert '"response": hotel_response,' in block


def test_capture_happens_before_the_success_break_so_a_successful_attempt_is_recorded_too():
    block = _phase1_block(_read_app_py())
    append_pos = block.index('_hp_phase1_attempts.append({')
    break_pos = block.index('if not (isinstance(hotel_response, dict) and "error" in hotel_response):\n                    break')
    assert append_pos < break_pos


def test_final_failure_shows_every_captured_attempt_not_just_the_last_ones_raw_error():
    block = _phase1_block(_read_app_py())
    assert '🔍 Raw request/response per attempt (debug)' in block
    # The expander must be shown right before the final show_publish_error call (the dead end
    # this bug report actually hit), not only on the lucky-success path.
    debug_pos = block.index('🔍 Raw request/response per attempt (debug)')
    show_error_pos = block.index('show_publish_error(f"publish hotel **{provider_code}**", hotel_response)\n                return')
    assert debug_pos < show_error_pos


def test_escalation_warning_now_explains_the_fallback_shape_is_a_known_dead_end():
    # The fallback (one new room inline) still has providerCode=None - schemas.py's own
    # ContractRoomVO docstring already proved that's unsafe (2026-09-06) - so the warning should
    # not read as "this next attempt is safe", since it might not be.
    block = _phase1_block(_read_app_py())
    assert 'known dead end' in block
