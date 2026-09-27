"""Regression test for a real reported bug (product owner, 2026-09-27, verbatim):

    "when i add a new closedtour, the information from the document does not exclude the
    information form the url. If human adds both informations to the app, the App must read
    both of them complete each other and they are not excluded each others information. If
    human adds both or multiple documents, the app shall get all information together to one
    closed tour. It would be better to have one complete closedtour with multiple modalities,
    rather than having multiple closedtours."

Root cause: flows/multi_tour.py already combines a URL and every uploaded document into ONE
raw_text blob (PHASE 1, "gather"), and already refuses to ever create more than one ClosedTour
per run (the whole point of the 2026-0x redesign documented in render_multi_tour_flow's own
docstring). PHASE 2 ("select_tour") even already warns the human when every AI-detected
candidate reports the SAME length ("these all report the same length... really ONE tour with
different Modalities... just pick any one below").

But PHASE 3 ("reviewing_main") still passed that candidate's own label to
extract_structured_data as a variant_hint whenever is_genuine_variant was True - and it was
True for EVERY AI-detected candidate, including the "same nights" case the warning above was
about. variant_hint tells the AI to focus ONLY on the named label and ignore the rest of the
combined text - so even after telling the human "these are really the same tour", the code
still silently threw away whatever the OTHER source (the URL, or the other document) had
contributed, because the extraction was still narrowed to just one candidate's content.

Fix: when every detected candidate reports the same length (distinct_nights <= 1, the exact
condition the on-screen warning already checks), is_genuine_variant is now forced to False on
the chosen candidate before building the tour's state - so PHASE 3 extracts from the FULL
combined text with no variant_hint at all, and every source's information is merged into the
one tour instead of one excluding the other. The narrowing is kept ONLY for the case where
candidates genuinely differ in length - a real signal of different tour PRODUCTS, where picking
one and running the flow again for the other remains the right call.

Source-text check (same established pattern as
test_2026_09_18_mct_stale_code_taken_after_publish.py and every other flows/multi_tour.py wiring
test in this suite) - the Streamlit wizard can't be exercised directly outside the real app.
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_MULTI_TOUR_PY = os.path.join(os.path.dirname(_HERE), "flows", "multi_tour.py")


def _read_multi_tour():
    with open(_MULTI_TOUR_PY, "r", encoding="utf-8") as f:
        return f.read()


def _select_tour_phase_body():
    src = _read_multi_tour()
    start = src.index('if st.session_state.mct_phase == "select_tour":')
    end = src.index('if st.session_state.mct_phase == "reviewing_main":')
    return src[start:end]


def test_choosing_a_candidate_forces_is_genuine_variant_false_when_nights_all_match():
    body = _select_tour_phase_body()
    assert "if len(distinct_nights) <= 1:" in body
    assert 'chosen_candidate["is_genuine_variant"] = False' in body


def test_the_forced_override_happens_before_new_mct_tour_is_built():
    body = _select_tour_phase_body()
    override_pos = body.index('chosen_candidate["is_genuine_variant"] = False')
    build_pos = body.index("_new_mct_tour(chosen_candidate, default_tour_code)")
    assert override_pos < build_pos


def test_a_copy_of_the_candidate_is_mutated_not_the_original_list_entry():
    # candidates[choice_idx] is session state shared across reruns (e.g. "Not what you wanted?"
    # -> Start over rebuilds it, but a stray extra rerun before that must not have silently
    # mutated the original candidate dict in place).
    body = _select_tour_phase_body()
    assert "chosen_candidate = dict(candidates[choice_idx])" in body


def test_genuinely_different_length_candidates_still_get_a_variant_hint():
    # The narrowing behavior (is_genuine_variant left as detected, True) must be preserved for
    # the case the warning does NOT fire - two candidates of different length are a real signal
    # of different tour products, not complementary sources of the same tour.
    body = _select_tour_phase_body()
    # is_genuine_variant is only ever forced to False inside the same-nights branch - it is
    # never unconditionally overwritten outside that guard.
    unguarded = body.replace(
        "            if len(distinct_nights) <= 1:\n"
        '                chosen_candidate["is_genuine_variant"] = False\n', "")
    assert 'chosen_candidate["is_genuine_variant"] = False' not in unguarded


def test_reviewing_main_still_only_applies_variant_hint_for_genuine_variants():
    # Unchanged downstream behavior: PHASE 3 must still gate variant_hint on
    # is_genuine_variant, so forcing it False upstream is what actually disables narrowing.
    src = _read_multi_tour()
    assert 'variant_hint = tour["label"] if tour.get("is_genuine_variant") else None' in src
