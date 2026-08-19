"""Tests for trip_prompt_extractor.py's pure logic — NOT the live Claude call itself (that
needs a real ANTHROPIC_API_KEY, which conftest.py deliberately strips from every test run; see
test_trip_prompt_extractor.py at the repo root — note the different, non-test_-prefixed name
outside tests/ — for the manual runner used to try it live).

This only covers extract_trip_criteria's empty-input fallback, which is pure Python with no API
call, and the schema's shape.
"""
import trip_prompt_extractor as tpe


def test_extract_trip_criteria_empty_prompt_returns_a_safe_fallback_with_no_api_call():
    result = tpe.extract_trip_criteria("")
    assert result["confidence"] == "low"
    assert result["clarification_needed"]
    assert result["destination_country"] == ""
    assert result["adults"] == 2


def test_extract_trip_criteria_whitespace_only_prompt_also_short_circuits():
    result = tpe.extract_trip_criteria("   ")
    assert result["confidence"] == "low"


def test_schema_requires_the_fields_a_search_is_meaningless_without():
    assert set(tpe.TRIP_PROMPT_SCHEMA["required"]) == {
        "destination_country", "adults", "themes", "budget_tier", "confidence",
    }


def test_schema_and_system_prompt_reference_the_same_tool_name():
    # If these ever drift, _stream_claude_tool_call would force a tool call by a name the
    # schema wasn't written for - a silent mismatch, not an error, so worth pinning directly.
    assert tpe.TRIP_PROMPT_TOOL_NAME == "provide_trip_criteria"
    assert "provide_trip_criteria" in tpe.TRIP_PROMPT_SYSTEM_PROMPT


def test_schema_now_requires_budget_tier_too():
    # CONFIRMED PRODUCT-OWNER RULE (2026-08-19): budget_tier drives the hotel star/board/
    # review-score rules in trip_search_rules.py - a dropped field there is a silently
    # unapplied business rule, not just a cosmetic gap, so it's required like the others.
    assert "budget_tier" in tpe.TRIP_PROMPT_SCHEMA["required"]
    assert tpe.TRIP_PROMPT_SCHEMA["properties"]["budget_tier"]["enum"] == [
        "budget", "superior", "luxury", "unspecified",
    ]


def test_empty_prompt_fallback_includes_unspecified_budget_tier():
    result = tpe.extract_trip_criteria("")
    assert result["budget_tier"] == "unspecified"
    assert result["car_wanted"] is False
