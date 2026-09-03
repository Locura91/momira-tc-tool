"""Tests for the new "minimum pax for guaranteed departure" rule (product owner, 2026-09-03):

    "add to remarks, if there is a minimum pax number needed for guaranteed departure. If Ticket
    or Closedtour has minimum of 3 pax or higher, we must set the ticket or closedtour on
    request."

Two parts:
1. A new `min_pax_guaranteed_departure` field, extracted per Modality/Option (product owner's own
   choice among the clarifying options offered), added to every relevant ai_extractor.py prompt/
   defaults dict for Ticket (TICKET_EXTRACTION_SYSTEM_PROMPT/extract_ticket_data,
   TICKET_MODALITY_SYSTEM_PROMPT/extract_ticket_modality_data, TICKET_OPTION_ONLY_SYSTEM_PROMPT/
   extract_ticket_option_only_data) and ClosedTour (MODALITY_EXTRACTION_SYSTEM_PROMPT/
   extract_modality_data, OPTION_ONLY_SYSTEM_PROMPT/extract_option_only_data).
2. Two shared pure helpers in ai_extractor.py - min_pax_guaranteed_departure_note() (the remarks
   text) and min_pax_forces_on_request() (the >=3 threshold) - used by app.py's
   _apply_min_pax_guaranteed_departure_note() to fold the note into Condition/Voucher Remarks
   (Ticket, both fields per the product owner's answer) or Policy remarks (ClosedTour, its only
   one), and to force On Request at every Ticket/ClosedTour creation publish call site regardless
   of the human's own On Request checkbox.

app.py can't be imported in a test process (heavy top-level Streamlit/API-client setup), so its
wiring is verified by reading its own source text, per this suite's established pattern.
ai_extractor.py has no such import-time side effects, so its helpers and prompt/defaults changes
are tested directly.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_extractor

MODULE_BUILD = "2026-09-03-new-batch-currency-image-state-and-geo-country"

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def _function_source(src, def_line):
    start = src.index(def_line)
    end = src.index("\ndef ", start + len(def_line))
    return src[start:end]


# ======================================================================
# ai_extractor.py - shared pure helpers
# ======================================================================
def test_note_text_for_a_real_minimum():
    assert ai_extractor.min_pax_guaranteed_departure_note(3) == \
        "Minimum 3 passengers required for guaranteed departure."
    assert ai_extractor.min_pax_guaranteed_departure_note("4") == \
        "Minimum 4 passengers required for guaranteed departure."


def test_note_text_is_empty_for_missing_or_non_numeric_or_non_positive():
    assert ai_extractor.min_pax_guaranteed_departure_note(None) == ""
    assert ai_extractor.min_pax_guaranteed_departure_note("") == ""
    assert ai_extractor.min_pax_guaranteed_departure_note(0) == ""
    assert ai_extractor.min_pax_guaranteed_departure_note(-1) == ""
    assert ai_extractor.min_pax_guaranteed_departure_note("not a number") == ""


def test_forces_on_request_threshold_is_exactly_3_and_higher():
    assert ai_extractor.MIN_PAX_FOR_MANDATORY_ON_REQUEST == 3
    assert ai_extractor.min_pax_forces_on_request(3) is True
    assert ai_extractor.min_pax_forces_on_request(4) is True
    assert ai_extractor.min_pax_forces_on_request(10) is True
    assert ai_extractor.min_pax_forces_on_request(2) is False
    assert ai_extractor.min_pax_forces_on_request(1) is False


def test_forces_on_request_is_false_for_missing_or_non_numeric():
    assert ai_extractor.min_pax_forces_on_request(None) is False
    assert ai_extractor.min_pax_forces_on_request("") is False
    assert ai_extractor.min_pax_forces_on_request("not a number") is False


# ======================================================================
# ai_extractor.py - field present in every relevant prompt + defaults dict
# ======================================================================
def test_field_present_in_all_five_relevant_prompts():
    assert '"min_pax_guaranteed_departure": null' in ai_extractor.OPTION_ONLY_SYSTEM_PROMPT
    assert '"min_pax_guaranteed_departure": null' in ai_extractor.MODALITY_EXTRACTION_SYSTEM_PROMPT
    assert '"min_pax_guaranteed_departure": null' in ai_extractor.TICKET_MODALITY_SYSTEM_PROMPT
    assert '"min_pax_guaranteed_departure": null' in ai_extractor.TICKET_OPTION_ONLY_SYSTEM_PROMPT
    assert '"min_pax_guaranteed_departure": null' in ai_extractor.TICKET_EXTRACTION_SYSTEM_PROMPT


def test_field_absent_from_ticket_main_info_prompt():
    # TICKET_MAIN_INFO_SYSTEM_PROMPT only covers name/description/voucher/cancellation - no
    # schedule/pricing fields at all - min_pax_guaranteed_departure belongs on the Modality-level
    # extraction instead (TICKET_MODALITY_SYSTEM_PROMPT), never duplicated here.
    assert "min_pax_guaranteed_departure" not in ai_extractor.TICKET_MAIN_INFO_SYSTEM_PROMPT


def test_field_defaulted_in_extract_modality_data(monkeypatch):
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: {"price_list": []})
    data = ai_extractor.extract_modality_data("some raw text")
    assert data["min_pax_guaranteed_departure"] is None


def test_field_defaulted_in_extract_option_only_data(monkeypatch):
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: {"price_list": []})
    data = ai_extractor.extract_option_only_data("some raw text")
    assert data["min_pax_guaranteed_departure"] is None


def test_field_defaulted_in_extract_ticket_modality_data(monkeypatch):
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: {})
    data = ai_extractor.extract_ticket_modality_data("some raw text")
    assert data["min_pax_guaranteed_departure"] is None


def test_field_defaulted_in_extract_ticket_option_only_data(monkeypatch):
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: {})
    data = ai_extractor.extract_ticket_option_only_data("some raw text")
    assert data["min_pax_guaranteed_departure"] is None


def test_field_passed_through_when_extracted(monkeypatch):
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: {"price_list": [], "min_pax_guaranteed_departure": 4})
    data = ai_extractor.extract_modality_data("some raw text")
    assert data["min_pax_guaranteed_departure"] == 4


# ======================================================================
# app.py wiring
# ======================================================================
def test_helpers_are_imported():
    src = _read_app_py()
    assert "from ai_extractor import min_pax_guaranteed_departure_note, min_pax_forces_on_request" in src


def test_apply_note_helper_is_defined_and_skips_duplicate_notes():
    src = _read_app_py()
    idx = src.index("def _apply_min_pax_guaranteed_departure_note(")
    window = src[idx:idx + 2200]
    assert "min_pax_guaranteed_departure_note(min_pax)" in window
    assert "if note in existing:" in window
    assert "continue" in window


def test_multi_ticket_flow_applies_note_after_modality_merge_and_forces_on_request():
    src = _read_app_py()
    window = _function_source(
        src,
        'def render_multi_ticket_flow(client, supplier_id, currency, on_request, release_days, '
        'tk_url, tk_files, min_passengers=1, max_passengers=9, default_ticket_code=""):')
    merge_idx = window.index("data.update(modality_data)")
    apply_idx = window.index("_apply_min_pax_guaranteed_departure_note(\n                        data,")
    assert merge_idx < apply_idx
    assert '"cancellation_policy_text", "voucher_remarks"' in window
    assert 'on_request=on_request or min_pax_forces_on_request(q["data"].get("min_pax_guaranteed_departure"))' in window
    assert "This Ticket will be published **On Request**" in window


def test_multi_tour_flow_applies_note_to_policy_remarks_with_modality_label():
    src = _read_app_py()
    window = _function_source(
        src, "def render_multi_tour_flow(client, supplier_id, currency, on_request, release_days, url, uploaded_files,")
    assert 'tour["main_data"], ("policy_remarks",),' in window
    assert 'mod["data"].get("min_pax_guaranteed_departure"), label=mod["code"]' in window
    # All three publish/preview call sites (preview, base modality, additional modalities) force
    # On Request from the respective item's own min_pax_guaranteed_departure.
    assert 'min_pax_forces_on_request(combined_data.get("min_pax_guaranteed_departure"))' in window
    assert 'min_pax_forces_on_request(m["data"].get("min_pax_guaranteed_departure"))' in window
    assert "This Modality will be published **On Request**" in window


def test_multi_modality_flow_forces_on_request_and_shows_a_warning():
    src = _read_app_py()
    window = _function_source(src, "def render_multi_modality_flow(client, url=None, uploaded_files=None):")
    assert 'min_pax_forces_on_request(q["data"].get("min_pax_guaranteed_departure"))' in window
    assert "doesn't edit the tour's Policy remarks" in window
