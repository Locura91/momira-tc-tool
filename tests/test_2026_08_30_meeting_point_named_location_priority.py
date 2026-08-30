"""Tests for the meeting_points prompt fix made 2026-08-30 (reported: "Meeting Points - I never get
any results" on a Ticket extracted from https://masonstravel.com/packages/reef-safari/).

Investigation: that page genuinely states a specific fixed departure point ("Marine Charter
Association, Mahe" - the itinerary literally says "10:00 - Anahita departs Marine Charter") AND
separately advertises complimentary hotel pickup ("Hotel transfers are included in this excursion").
The OLD prompt wording described the Hotel Lobby default as applying "If the source only says pickup
is from the guest's own hotel/accommodation" - which reads correctly on paper, but in practice the
model was defaulting to the generic Hotel Lobby placeholder on pages like this one instead of the real
named marina, most likely because the prominent "hotel pickup included" phrasing reads as the whole
answer unless the prompt is explicit that a hotel-pickup mention does NOT override a separately-named
fixed location.

Fix: strengthened TICKET_EXTRACTION_SYSTEM_PROMPT and TICKET_MAIN_INFO_SYSTEM_PROMPT's meeting_points
instructions to state explicitly that a named location always wins over hotel pickup, gave the exact
real-world pattern (boat/marina excursions: hotel pickup is the transfer TO the meeting point, not a
replacement for it) as a concrete example, and told the model to check itinerary/schedule tables (not
just a "Meeting Point" heading) for a stated departure place - since here it appears as a schedule row,
"10:00 - Departs Marine Charter", not under its own heading.

This can't be verified against the real model end-to-end without a live ANTHROPIC_API_KEY (not
available in this environment) - these tests instead pin the prompt content itself, so the
clarification can't be silently lost in a future edit, and confirm both Ticket extraction prompts
(full extraction and main-info-only) carry the same fix, since they previously had to be fixed in
lockstep (both had the exact same block, independently duplicated).
"""
import ai_extractor as ae


def _assert_prompt_prioritizes_named_location_over_hotel_pickup(prompt: str):
    assert "meeting_points" in prompt
    # The named-location-wins rule must be present and stated unconditionally, not just as an
    # aside - this is the exact instruction that was missing before the fix. (Substrings below are
    # each taken from a single source line, since the prompt is a wrapped triple-quoted string with
    # real newlines - a phrase spanning a line wrap would never match with a plain `in` check.)
    assert "ALWAYS takes priority over the generic" in prompt
    assert "Hotel Lobby default, even when the source ALSO offers complimentary hotel pickup/transfer to get there." in prompt
    # The concrete real-world example that was actually missed - keeps the instruction grounded
    # instead of drifting back into abstract wording a model can rationalize past.
    assert "marina" in prompt.lower()
    assert "not a substitute for it." in prompt
    # Told to look beyond a literal "Meeting Point" heading - the real page had this in a schedule row.
    assert "itinerary/schedule table" in prompt
    # The Hotel Lobby fallback must now be explicitly scoped to "no separate named place at all".
    assert "Only fall back to the guest's own hotel/accommodation as the meeting point when the source gives NO" in prompt
    assert "separate named place at all" in prompt
    assert '{"description": "Hotel Lobby", "variable_location": true}' in prompt


def test_ticket_extraction_prompt_prioritizes_named_meeting_point_over_hotel_pickup():
    _assert_prompt_prioritizes_named_location_over_hotel_pickup(ae.TICKET_EXTRACTION_SYSTEM_PROMPT)


def test_ticket_main_info_prompt_prioritizes_named_meeting_point_over_hotel_pickup():
    _assert_prompt_prioritizes_named_location_over_hotel_pickup(ae.TICKET_MAIN_INFO_SYSTEM_PROMPT)


def test_both_ticket_prompts_carry_the_identical_meeting_points_instruction():
    # These two prompts started as an accidental exact duplicate of this whole block (confirmed via
    # grep before the fix) - pin that they're STILL identical after editing, so a future change to
    # one doesn't silently leave the other on the old, buggy wording.
    def _extract_block(prompt: str) -> str:
        start = prompt.index("- meeting_points: list of")
        end = prompt.index("- meeting_point_summary:")
        return prompt[start:end]

    block_full = _extract_block(ae.TICKET_EXTRACTION_SYSTEM_PROMPT)
    block_main_info = _extract_block(ae.TICKET_MAIN_INFO_SYSTEM_PROMPT)
    assert block_full == block_main_info
