"""Regression test for a real reported bug (screenshot, 2026-09-18):

    "how can this error show up, even when the upload was working fine?"

The screenshot showed, on the SAME screen at the same time: a red "🚫 Tour Code `ASW-4` is
ALREADY TAKEN by an existing tour ("4 Days Nile Cruise - Aswan to Luxor (3 Nights)")" error, a
disabled "🚀 Publish to Travel Compositor" button, AND "Just published: CLOSEDTOUR-425935
(Supplier 50951)" with next-step buttons — all for the exact same tour/code, in flows/
multi_tour.py's batch ClosedTour wizard's "publishing" phase.

Root cause: once a tour code is actually published, Travel Compositor genuinely DOES have a
tour with that code from then on — so any LATER rerun of this same "publishing" phase (any
widget interaction while still on this screen) re-ran the duplicate-code check
(check_code_availability) against Travel Compositor, which now, correctly, finds the code taken
— because THIS tour is what just took it. The pre-publish UI (Tour Code input, duplicate-code
error, destination preview, Publish button) kept rendering and showing that as a blocking
"change it before publishing" error, as if publishing hadn't happened yet, directly above the
accurate "Just published" confirmation panel for the very same code.

Fix: the entire pre-publish section is now skipped once this exact tour code (for this exact
supplier) has already been published successfully this session — only the "Just published"
panel (with its own next-step buttons) renders on any further rerun.

This is a source-text check (same established pattern every other flows/multi_tour.py wiring
test in this suite uses) - the publishing phase can't be exercised directly without a live
Streamlit session.
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_MULTI_TOUR_PY = os.path.join(os.path.dirname(_HERE), "flows", "multi_tour.py")


def _read_multi_tour():
    with open(_MULTI_TOUR_PY, "r", encoding="utf-8") as f:
        return f.read()


def _publishing_phase_body():
    src = _read_multi_tour()
    start = src.index('if st.session_state.mct_phase == "publishing":')
    # The next top-level (8-space-indented, matching this if's own body start) "if
    # st.session_state.mct_phase ==" marks a sibling phase branch - there isn't one after
    # "publishing" in this file, so fall back to end of file if none is found.
    try:
        next_phase = src.index('if st.session_state.mct_phase ==', start + 10)
        return src[start:next_phase]
    except ValueError:
        return src[start:]


def test_already_published_guard_exists():
    body = _publishing_phase_body()
    assert "_mct_already_published_this_code" in body
    assert 'st.session_state.get("just_published_tour_code") == tour["tour_code"]' in body
    assert 'st.session_state.get("just_published_supplier_id") == supplier_id' in body


def test_pre_publish_section_is_gated_on_the_guard():
    body = _publishing_phase_body()
    guard_idx = body.index("if not _mct_already_published_this_code:")
    subheader_idx = body.index('st.subheader(f"Ready to publish')
    # The subheader (start of the pre-publish UI) must be the very next meaningful thing after
    # the guard - i.e. actually inside its `if`, not just declared nearby.
    assert guard_idx < subheader_idx
    between = body[guard_idx:subheader_idx]
    assert between.count("\n") <= 2


def test_duplicate_code_check_and_publish_button_are_inside_the_guarded_section():
    body = _publishing_phase_body()
    guard_idx = body.index("if not _mct_already_published_this_code:")
    just_published_idx = body.index('if st.session_state.get("just_published_tour_code"):')
    assert guard_idx < just_published_idx
    window = body[guard_idx:just_published_idx]
    assert "check_code_availability(client, \"tour\", supplier_id, tour[\"tour_code\"])" in window
    assert 'st.button("🚀 Publish to Travel Compositor"' in window


def test_just_published_panel_remains_unconditional_after_the_guarded_section():
    body = _publishing_phase_body()
    just_published_idx = body.index('if st.session_state.get("just_published_tour_code"):')
    # It must sit at the SAME indentation as the guard itself (8 spaces, directly under the
    # "publishing" phase branch) - not nested one level deeper inside the guarded section -
    # so it still renders even when the guarded section above was skipped entirely.
    line_start = body.rindex("\n", 0, just_published_idx) + 1
    indent = len(body[line_start:just_published_idx]) - len(body[line_start:just_published_idx].lstrip(" "))
    assert indent == 8


def test_just_published_panel_still_shows_next_step_buttons():
    body = _publishing_phase_body()
    just_published_idx = body.index('if st.session_state.get("just_published_tour_code"):')
    window = body[just_published_idx:just_published_idx + 2500]
    assert "Just published:" in window
    assert "Start a new ClosedTour" in window
    assert "Add another Modality to this same ClosedTour" in window
    assert "Do something else with this Code" in window
