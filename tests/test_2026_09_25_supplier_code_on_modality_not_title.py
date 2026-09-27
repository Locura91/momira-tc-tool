"""Regression tests for the product-owner rule (2026-09-25, verbatim): "if supplier is
delivering a code for closedtour ticket etc this code must be seen in the modality and not in
the title. Code = connected to Modality code, so its easy for the supplier when receiving
automatic mails."

Real example that triggered this: a Bush Camp Safari ClosedTour document with a "Code" column
(TBC2 for the 2-day version, TBC3 for the 3-day version) - the app's Tour name field ended up
showing "2 Days / 1 Night - Bush Camp Safari (TBC2)", baking the supplier's own per-variant
reference code into the client-facing title instead of onto that variant's Modality Code.

Ticket already had the right shape for this (supplier_code separate from the excursion label,
feeding modality_code - see flows/ticket.py) - these tests cover the two places that did NOT:
ClosedTour's tour_name / ticket's ticket_name extraction (must not copy a supplier code into
the title), and ClosedTour's/Ticket's Modality-detection prompts (must prefer an explicit
supplier code as suggested_code over inventing a generic tier name).

ai_extractor.py can be imported directly (no Streamlit dependency), unlike flows/*.py.
"""
import ai_extractor


def test_closed_tour_name_rule_tells_the_ai_to_exclude_a_supplier_variant_code():
    assert "tour_name" in ai_extractor.EXTRACTION_SYSTEM_PROMPT
    assert "leave that code OUT of tour_name" in ai_extractor.EXTRACTION_SYSTEM_PROMPT
    assert "TBC2" in ai_extractor.EXTRACTION_SYSTEM_PROMPT


def test_ticket_name_rule_tells_the_ai_to_exclude_a_supplier_code_in_both_ticket_prompts():
    for prompt in (ai_extractor.TICKET_EXTRACTION_SYSTEM_PROMPT, ai_extractor.TICKET_MAIN_INFO_SYSTEM_PROMPT):
        assert "leave that code OUT of ticket_name" in prompt
        assert "supplier_code" in prompt


def test_closed_tour_modality_detection_prefers_an_explicit_supplier_code():
    prompt = ai_extractor.MODALITY_DETECTION_PROMPT
    assert "PREFER it over inventing a generic tier name" in prompt
    assert "TBC2" in prompt
    assert "automatic booking confirmation emails" in prompt


def test_ticket_modality_detection_prefers_an_explicit_supplier_code():
    prompt = ai_extractor.TICKET_MODALITY_DETECTION_PROMPT
    assert "PREFER it" in prompt and "over inventing a generic tier name" in prompt
    assert "automatic booking emails" in prompt


def test_modality_code_cleanup_does_not_mangle_a_real_short_supplier_code():
    # TBC2/TBC3 have none of the characters _clean_modality_code strips (/ \ + - .), and are
    # short enough to never trip _modality_code_suspicious's junk-word/length heuristic - both
    # must continue to let a real supplier code like this straight through unchanged.
    import re
    src = open("app_helpers.py", encoding="utf-8").read()
    start = src.index("def _clean_modality_code(")
    end = src.index("\n\n\n", start)
    ns = {}
    exec(src[start:end], ns)
    assert ns["_clean_modality_code"]("TBC2") == "TBC2"

    start2 = src.index("def _modality_code_suspicious(")
    end2 = src.index("\n\n\n", start2)
    ns2 = {}
    exec(src[start2:end2], ns2)
    assert ns2["_modality_code_suspicious"]("TBC2") is False
    assert ns2["_modality_code_suspicious"]("TBC3") is False
