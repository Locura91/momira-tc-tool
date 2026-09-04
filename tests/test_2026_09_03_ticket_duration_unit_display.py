"""Tests for a real product-owner request (2026-09-03): "estimated duration must be seen within
the app if used days, minutes or hours. If nothing is mentioned, it is not requiered field."

Before this, the two Ticket review screens showed a plain number under a hardcoded "Duration
(hours)" label - even when the document actually stated a duration in DAYS (duration_type
"DAYS" is extracted and published as-is, see builder.py's durationType=extracted_ticket_data.
get("duration_type", "HOURS")), so the human had no way to see that on screen, and no way to
enter a duration in minutes at all (only "HOURS"/"DAYS" existed anywhere in the app).

ui_components.render_duration_editor(data, key_prefix) now shows the number AND a Hours/Days/
Minutes unit selector together, always reflecting whatever duration_type is currently stored,
and never requires a value - a 0/blank duration is left exactly as extracted, with an
informational caption, never flagged as an error the way a genuinely required field (Ticket
name/Description) is.

app.py can't be imported in a test process (heavy top-level Streamlit/API-client setup), so its
wiring is verified by reading its own source text, per this suite's established pattern.
Streamlit widgets can't be exercised outside a running app, so render_duration_editor itself is
checked via its source (inspect.getsource), the same pattern this codebase already uses for
render_child_age_band (see test_2026_09_02_medium_batch5_support.py).
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_extractor
import ui_components

MODULE_BUILD = "2026-09-04-pptx-text-and-image-extraction"

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


# ======================================================================
# ui_components.render_duration_editor
# ======================================================================
def test_all_three_units_are_offered():
    assert ui_components.DURATION_UNIT_OPTIONS == ["HOURS", "DAYS", "MINUTES"]
    assert set(ui_components.DURATION_UNIT_LABELS) == {"HOURS", "DAYS", "MINUTES"}


def test_editor_shows_a_unit_selector_reflecting_the_stored_duration_type():
    source = inspect.getsource(ui_components.render_duration_editor)
    assert 'st.selectbox(' in source
    assert 'DURATION_UNIT_OPTIONS' in source
    assert 'data.get(duration_type_key)' in source


def test_editor_never_forces_a_non_zero_value():
    source = inspect.getsource(ui_components.render_duration_editor)
    assert "min_value=0.0" in source
    # Unlike render_child_age_band (which floors an unset value at a house default), duration
    # must stay genuinely 0 when nothing was extracted - never substituted.
    assert "not a required field" in source or "not required" in source.lower()


def test_editor_uses_a_float_step_not_truncating_to_whole_numbers():
    # CONFIRMED real audit finding (full-app-audit-2026-08-22, U-5): "A 2.5-hour excursion is
    # silently saved as 2 hours" - `int(current_value)` truncated fractional durations. The new
    # editor must read/write duration as a float, not an int.
    source = inspect.getsource(ui_components.render_duration_editor)
    assert "float(raw)" in source
    assert "int(" not in source


# ======================================================================
# app.py wiring - both Ticket duration sites use the new editor, not the old hardcoded label
# ======================================================================
def test_render_duration_editor_is_imported():
    src = _read_app_py()
    assert "render_duration_editor" in src
    assert 'from ui_components import (' in src


def test_neither_ticket_screen_still_uses_the_old_hardcoded_hours_label():
    src = _read_app_py()
    assert '"Duration (hours)"' not in src


def test_multi_ticket_flow_uses_the_new_duration_editor():
    src = _read_app_py()
    assert 'render_duration_editor(data, f"mt_{idx}")' in src


def test_legacy_single_ticket_flow_uses_the_new_duration_editor():
    src = _read_app_py()
    assert 'render_duration_editor(data, "legacy_ticket")' in src


# ======================================================================
# ai_extractor.py - extraction prompts allow MINUTES and say duration is optional
# ======================================================================
def test_ticket_main_info_prompt_offers_minutes_and_says_not_required():
    assert '"HOURS"/"DAYS"/"MINUTES"' in ai_extractor.TICKET_EXTRACTION_SYSTEM_PROMPT
    assert "required field" in ai_extractor.TICKET_EXTRACTION_SYSTEM_PROMPT


def test_ticket_batch_main_info_prompt_offers_minutes_and_says_not_required():
    assert '"HOURS"/"DAYS"/"MINUTES"' in ai_extractor.TICKET_MAIN_INFO_SYSTEM_PROMPT
    assert "required field" in ai_extractor.TICKET_MAIN_INFO_SYSTEM_PROMPT
