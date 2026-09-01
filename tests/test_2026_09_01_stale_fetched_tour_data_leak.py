"""Regression tests for full-app-audit CRITICAL #3 (2026-09-01): stale "check what's online"
data leaking between ClosedTours.

CONFIRMED REAL BUG: st.session_state.fetched_tour_provider_code/min_pax/max_pax/currency were
set once, by whichever tour code Step 3's "Check what's already online" button was last clicked
for, and never cleared or re-validated. Every downstream guard and usage site tested only
whether these were PRESENT (truthy), never whether they actually belonged to the tour currently
being configured. Real failure mode: check tour A, click "Change details", type in tour B's
code, forget to click "Check" again (or the re-check fails) - the stale fields still read as
"present," so tour B silently published with tour A's currency, provider code, and pax capacity,
with nothing on the review screen to show it.

Fix: a new fetched_tour_for_code session key records which code the fetch was actually run
against (set on both success and failure of the fetch), and a new
fetched_tour_matches_code(existing_tour_code) helper - which checks that key against the code
currently being worked on, AND that the fetch didn't error - replaces every bare truthiness
check on fetched_tour_provider_code across Step 3's gating, the module-level
currency/min/max/provider-code re-derivation that runs on every render of Steps 4+, the
HumanPreConfig() construction before building payloads, the multi-modality batch publish loop,
and the Step 7 publish-time guard.

app.py can't be safely imported in a test process (it runs top-level Streamlit calls on import -
same constraint noted in test_2026_08_31_closedtour_child_discount_visibility.py and
test_2026_09_01_update_no_longer_deactivates.py) - these tests read its source text instead.
"""
import os
import re

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_fetched_tour_matches_code_helper_exists_and_checks_both_code_and_error():
    src = _read_app_py()
    assert "def fetched_tour_matches_code(existing_tour_code):" in src
    # Extract the function body up to the next top-level def.
    start = src.index("def fetched_tour_matches_code(existing_tour_code):")
    end = src.index("\ndef ", start + 1)
    body = src[start:end]
    assert 'st.session_state.get("fetched_tour_for_code") == existing_tour_code' in body
    assert '"error" not in fetched' in body


def test_fetch_handler_records_which_code_it_was_for():
    src = _read_app_py()
    marker = 'st.session_state.fetched_tour = fetched'
    idx = src.index(marker)
    window = src[idx:idx + 900]
    assert 'st.session_state.fetched_tour_for_code = existing_tour_code_in' in window, (
        "the fetch handler must record fetched_tour_for_code unconditionally (success AND "
        "failure), so a failed re-check for a new tour code doesn't leave a previous tour's "
        "stale data looking valid"
    )


def test_step3_continue_gate_uses_the_match_helper_not_bare_truthiness():
    src = _read_app_py()
    # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): "update_option" was added to this
    # tuple - it was missing, letting an operator continue past Step 3 for an option-only
    # update without ever fetching the live tour, silently defaulting currency to EUR. See the
    # comment on this line in app.py.
    marker = 'if action in ("update_tour", "add_option", "update_option") and not fetched_tour_matches_code(existing_tour_code_in):'
    assert marker in src, (
        "Step 3's 'Continue to Step 4' gate must require the fetch to match the CURRENTLY "
        "entered tour code, not just be present - a bare truthiness check on "
        "fetched_tour_provider_code lets a previous tour's stale fetch through"
    )
    # Guard against a regression that re-introduces the bare truthiness check anywhere in the
    # ClosedTour update/add_option flow.
    assert 'not st.session_state.get("fetched_tour_provider_code")' not in src, (
        "a bare truthiness check on fetched_tour_provider_code has crept back in somewhere - "
        "every usage must be validated via fetched_tour_matches_code() instead"
    )


def test_module_level_rederivation_block_is_gated_on_the_match_helper():
    src = _read_app_py()
    marker = (
        'if action in ("update_tour", "update_option", "add_option") and '
        'fetched_tour_matches_code(existing_tour_code):'
    )
    assert marker in src, (
        "the block that re-pulls currency/min_pax/max_pax/provider_code from the fetched tour "
        "runs on EVERY render of Steps 4+ - it must re-validate the match every time, not just "
        "once at Step 3's confirm click, since a stale fetch would otherwise keep being blended "
        "in on every subsequent render"
    )


def test_no_more_raw_real_provider_code_fallback_reads():
    src = _read_app_py()
    # The old `_real_provider_code = st.session_state.get("fetched_tour_provider_code", "")`
    # pattern re-read the raw, un-validated global as a fallback when the module-level
    # `provider_code` was empty - defeating the fix above whenever they actually differed
    # (i.e. exactly when the fetch was stale). It must be gone.
    assert "_real_provider_code" not in src, (
        "a raw, unvalidated fallback read of fetched_tour_provider_code was reintroduced - "
        "this exact pattern was the CRITICAL #3 leak at the HumanPreConfig() construction site"
    )


def test_publish_time_guard_uses_the_match_helper():
    src = _read_app_py()
    marker = (
        'missing_provider_code_for_update = (\n'
        '            publish_action == "Update an existing tour\'s details"\n'
        '            and not fetched_tour_matches_code(existing_tour_code)\n'
        '        )'
    )
    assert marker in src, (
        "Step 7's publish-time guard (the last line of defence before a PUT) must also require "
        "the fetch to match the tour actually being published"
    )
