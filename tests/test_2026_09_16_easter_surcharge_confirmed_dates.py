"""Regression test for a real product-owner instruction (2026-09-16), sent as a direct follow-up
in the same conversation as the duplicate-transfer Name/Description question: "When a New
product, or a new update mention 'Easter surchagre' something like easter holiday surcharge, the
app shall use following dates 30th or march 2027 until 8th of april 2027 and 10th april 2028 to
25th april 2028."

Context: every extraction prompt already told the AI to invent its "best real-world date range"
(or, for Ticket, to leave the surcharge undated) whenever a source names a peak-season/holiday
surcharge without giving its own exact dates - see the CONFIRMED RULE around stop_sales/
surcharge conflation (test_2026_09_16_stop_sales_holiday_surcharge_conflation.py) for the same
pattern applied to a different bug. Easter is common enough in real supplier contracts
(Christmas/NYE/Easter surcharges - see flows/manual_information.py's own captions) that the
product owner wants it pinned to two SPECIFIC confirmed windows instead of an AI guess, so every
Easter surcharge the app extracts (ClosedTour, ClosedTour Modality, Ticket, Ticket Modality,
Transfer, Hotel) lands on the exact same dates rather than drifting per-document.

NOTE: these dates are NOT derived from the Easter Sunday +/- offset formula already used
elsewhere in this codebase for package rollover blackouts (package_rollover_rules.py's
easter_sunday() + EASTER_WINDOW_DAYS_BEFORE/AFTER, a -5/+4 day window) - that's a different,
narrower window for a different purpose (blackout detection, not a surcharge default), and the
two years' windows below aren't even symmetric around Easter Sunday themselves (2027's window is
Easter Sunday +2 to +11; Easter Sunday 2027 is 28 March per dateutil.easter.easter(2027)). These
are literal, explicitly-confirmed dates, not a computed rule - hardcode them exactly as given.
"""
import ai_extractor

_CONFIRMED_START_2027 = "2027-03-30"
_CONFIRMED_END_2027 = "2027-04-08"
_CONFIRMED_START_2028 = "2028-04-10"
_CONFIRMED_END_2028 = "2028-04-25"

_PROMPTS_THAT_MUST_CARRY_THE_RULE = {
    "EXTRACTION_SYSTEM_PROMPT": ai_extractor.EXTRACTION_SYSTEM_PROMPT,
    "MODALITY_EXTRACTION_SYSTEM_PROMPT": ai_extractor.MODALITY_EXTRACTION_SYSTEM_PROMPT,
    "TICKET_EXTRACTION_SYSTEM_PROMPT": ai_extractor.TICKET_EXTRACTION_SYSTEM_PROMPT,
    "TICKET_MODALITY_SYSTEM_PROMPT": ai_extractor.TICKET_MODALITY_SYSTEM_PROMPT,
    "TRANSFER_EXTRACTION_SYSTEM_PROMPT": ai_extractor.TRANSFER_EXTRACTION_SYSTEM_PROMPT,
    "HOTEL_EXTRACTION_SYSTEM_PROMPT": ai_extractor.HOTEL_EXTRACTION_SYSTEM_PROMPT,
}


def test_every_relevant_extraction_prompt_states_the_confirmed_easter_rule():
    for prompt_name, prompt_text in _PROMPTS_THAT_MUST_CARRY_THE_RULE.items():
        assert "CONFIRMED EASTER DATES RULE" in prompt_text, (
            f"{prompt_name} is missing the confirmed Easter dates rule")


def test_every_relevant_extraction_prompt_states_both_confirmed_windows():
    for prompt_name, prompt_text in _PROMPTS_THAT_MUST_CARRY_THE_RULE.items():
        assert _CONFIRMED_START_2027 in prompt_text, f"{prompt_name} missing {_CONFIRMED_START_2027}"
        assert _CONFIRMED_END_2027 in prompt_text, f"{prompt_name} missing {_CONFIRMED_END_2027}"
        assert _CONFIRMED_START_2028 in prompt_text, f"{prompt_name} missing {_CONFIRMED_START_2028}"
        assert _CONFIRMED_END_2028 in prompt_text, f"{prompt_name} missing {_CONFIRMED_END_2028}"


def test_rule_defers_to_the_sources_own_explicit_easter_dates_when_stated():
    # The confirmed windows are a DEFAULT for when the source is silent on exact dates - a
    # document that states its own explicit Easter dates must still win. Every insertion says so.
    for prompt_name, prompt_text in _PROMPTS_THAT_MUST_CARRY_THE_RULE.items():
        assert "explicit Easter dates" in prompt_text, (
            f"{prompt_name}'s Easter rule doesn't defer to the source's own stated dates")


def test_transport_extraction_prompt_is_not_expected_to_carry_the_rule():
    # Transport supplements come from a season/rate GRID with real dates already in the document
    # (see TRANSPORT_EXTRACTION_SYSTEM_PROMPT's own "STACKED DATE RANGES" instructions) - there is
    # no "name a holiday with no dates" case for Transport the way there is for the others, so
    # this prompt deliberately doesn't need the confirmed-Easter-dates fallback.
    assert "CONFIRMED EASTER DATES RULE" not in ai_extractor.TRANSPORT_EXTRACTION_SYSTEM_PROMPT
