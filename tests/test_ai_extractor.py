"""Tests for the Claude-calling layer in ai_extractor.py.

No real Anthropic API key is used or required - _get_anthropic_client and
_stream_claude_tool_call (the two seams every Claude call in this module funnels through)
are monkeypatched, following the same pattern already used elsewhere in this project's test
suite (see test_clarify_honesty.py). This tests _call_claude's own contract (a forced tool
call returns an already-parsed dict; a truncated reply raises rather than returning partial
data) and detect_tour_variants/detect_multiple_modalities against known sample texts.
"""
import pytest

import ai_extractor as ax


@pytest.fixture(autouse=True)
def restore_seams():
    """_get_anthropic_client and _stream_claude_tool_call are monkeypatched onto the module
    directly (matching this project's existing test convention) - restore the real ones after
    every test so one test's fake response can never leak into the next."""
    real_client_fn = ax._get_anthropic_client
    real_stream_fn = ax._stream_claude_tool_call
    yield
    ax._get_anthropic_client = real_client_fn
    ax._stream_claude_tool_call = real_stream_fn


def fake_claude(response_data, stop_reason="end_turn"):
    """Builds a drop-in replacement for _stream_claude_tool_call that ignores every argument
    and returns the given (already-parsed) dict, exactly the shape a real forced tool call
    would hand back."""
    def _fake(client, model, max_tokens, system, content, tool_name=None, input_schema=None):
        return response_data, stop_reason
    return _fake


# ============================================================
# _call_claude
# ============================================================
def test_call_claude_returns_the_parsed_tool_input():
    ax._get_anthropic_client = lambda: object()
    ax._stream_claude_tool_call = fake_claude({"tour_name": "Test Cruise", "nights": 3})
    result = ax._call_claude("system prompt", "document text", "claude-sonnet-5")
    assert result == {"tour_name": "Test Cruise", "nights": 3}


def test_call_claude_raises_a_friendly_error_when_truncated():
    """A tool call cut off mid-way (stop_reason == 'max_tokens') is a genuinely incomplete
    answer - it must raise rather than silently handing back partial/invalid data."""
    ax._get_anthropic_client = lambda: object()
    ax._stream_claude_tool_call = fake_claude({"tour_name": "Cut off"}, stop_reason="max_tokens")
    with pytest.raises(RuntimeError, match="cut off"):
        ax._call_claude("system prompt", "document text", "claude-sonnet-5")


def test_call_claude_propagates_a_missing_tool_call_as_a_runtime_error():
    """_stream_claude_tool_call itself raises RuntimeError when Claude didn't call the tool at
    all (see its own docstring) - _call_claude must not swallow that."""
    def _fake_no_tool_call(client, model, max_tokens, system, content, tool_name=None, input_schema=None):
        raise RuntimeError("Claude didn't return the expected structured data.")
    ax._get_anthropic_client = lambda: object()
    ax._stream_claude_tool_call = _fake_no_tool_call
    with pytest.raises(RuntimeError, match="structured data"):
        ax._call_claude("system prompt", "document text", "claude-sonnet-5")


def test_call_claude_passes_the_model_and_max_tokens_through():
    seen = {}

    def _fake(client, model, max_tokens, system, content, tool_name=None, input_schema=None):
        seen["model"] = model
        seen["max_tokens"] = max_tokens
        return {}, "end_turn"

    ax._get_anthropic_client = lambda: object()
    ax._stream_claude_tool_call = _fake
    ax._call_claude("sys", "doc", "claude-opus-5", max_tokens=8192)
    assert seen["model"] == "claude-opus-5"
    assert seen["max_tokens"] == 8192


# ============================================================
# detect_tour_variants
# ============================================================
def test_detect_tour_variants_returns_empty_for_a_single_tour():
    ax._get_anthropic_client = lambda: object()
    ax._stream_claude_tool_call = fake_claude({"multiple_variants": False, "variants": []})
    result = ax.detect_tour_variants("A single 7-night Nile cruise, Cairo to Aswan.")
    assert result == []


def test_detect_tour_variants_returns_each_variant_found():
    ax._get_anthropic_client = lambda: object()
    ax._stream_claude_tool_call = fake_claude({
        "multiple_variants": True,
        "variants": [
            {"label": "3-night Nile cruise", "nights": 3},
            {"label": "4-night Nile cruise", "nights": 4},
        ],
    })
    result = ax.detect_tour_variants("A 3-night cruise option AND a 4-night cruise option, same route.")
    assert len(result) == 2
    assert {v["label"] for v in result} == {"3-night Nile cruise", "4-night Nile cruise"}


def test_detect_tour_variants_trusts_the_list_over_a_disagreeing_flag():
    """CONFIRMED REAL FAILURE (product owner, real transfer rate sheet): the model has
    returned a populated list AND multiple_variants=False in the same response - the list
    must win, never the flag, or a real detection silently reads as 'nothing found'."""
    ax._get_anthropic_client = lambda: object()
    ax._stream_claude_tool_call = fake_claude({
        "multiple_variants": False,   # deliberately disagrees with the list below
        "variants": [{"label": "Standard", "nights": 3}],
    })
    result = ax.detect_tour_variants("Some document text.")
    assert len(result) == 1


def test_detect_tour_variants_deduplicates_by_label():
    ax._get_anthropic_client = lambda: object()
    ax._stream_claude_tool_call = fake_claude({
        "multiple_variants": True,
        "variants": [
            {"label": "  Standard Cabin  ", "nights": 3},
            {"label": "standard cabin", "nights": 3},   # same identity, different case/spacing
            {"label": "Deluxe Cabin", "nights": 3},
        ],
    })
    result = ax.detect_tour_variants("doc")
    assert len(result) == 2


# ============================================================
# detect_ticket_variants
# ============================================================
def test_detect_ticket_variants_still_returns_the_single_excursions_own_label():
    """CONFIRMED BUG FIX (product owner, 2026-09-05): "To set up a new ticket, the ticket name
    must be the name of the detected excursion." This used to return an empty list whenever only
    one excursion was found (multiple_excursions: false), so app.py's "Set up this Ticket" screen
    had no detected name to prefill the Ticket Name field with - the human had to type it by hand
    even though the AI clearly knew the excursion's name. The prompt now asks for the single
    excursion's own entry even in the one-excursion case, so the label survives."""
    ax._get_anthropic_client = lambda: object()
    ax._stream_claude_tool_call = fake_claude({
        "multiple_excursions": False,
        "excursions": [{"label": "1 Day Diving Course", "is_private": False, "supplier_code": None}],
    })
    result = ax.detect_ticket_variants("A single one-day diving course excursion.")
    assert len(result) == 1
    assert result[0]["label"] == "1 Day Diving Course"


def test_detect_ticket_variants_returns_each_excursion_found():
    ax._get_anthropic_client = lambda: object()
    ax._stream_claude_tool_call = fake_claude({
        "multiple_excursions": True,
        "excursions": [
            {"label": "City Tour", "is_private": False, "supplier_code": None},
            {"label": "Desert Safari", "is_private": True, "supplier_code": "WT2"},
        ],
    })
    result = ax.detect_ticket_variants("A city tour AND a desert safari, same document.")
    assert len(result) == 2
    assert {e["label"] for e in result} == {"City Tour", "Desert Safari"}


def test_detect_ticket_variants_returns_empty_only_when_no_usable_name_exists_at_all():
    ax._get_anthropic_client = lambda: object()
    ax._stream_claude_tool_call = fake_claude({"multiple_excursions": False, "excursions": []})
    result = ax.detect_ticket_variants("Unreadable/garbled content with no excursion title.")
    assert result == []


def test_detect_ticket_variants_deduplicates_by_label():
    ax._get_anthropic_client = lambda: object()
    ax._stream_claude_tool_call = fake_claude({
        "multiple_excursions": True,
        "excursions": [
            {"label": "  City Tour  ", "is_private": False, "supplier_code": None},
            {"label": "city tour", "is_private": False, "supplier_code": None},
            {"label": "Desert Safari", "is_private": True, "supplier_code": None},
        ],
    })
    result = ax.detect_ticket_variants("doc")
    assert len(result) == 2


# ============================================================
# detect_multiple_modalities
# ============================================================
def test_detect_multiple_modalities_returns_empty_for_one_price_table():
    ax._get_anthropic_client = lambda: object()
    ax._stream_claude_tool_call = fake_claude({"multiple_modalities": False, "modalities": []})
    result = ax.detect_multiple_modalities("One price table: Adult 50, Child 30.")
    assert result == []


def test_detect_multiple_modalities_returns_each_category_found():
    ax._get_anthropic_client = lambda: object()
    ax._stream_claude_tool_call = fake_claude({
        "multiple_modalities": True,
        "modalities": [
            {"label": "Standard Cabin", "suggested_code": "STANDARD_CABIN"},
            {"label": "Superior Cabin", "suggested_code": "SUPERIOR_CABIN"},
        ],
    })
    result = ax.detect_multiple_modalities("Standard Cabin: $500. Superior Cabin: $700.")
    assert len(result) == 2
    codes = {m["suggested_code"] for m in result}
    assert codes == {"STANDARD_CABIN", "SUPERIOR_CABIN"}


def test_detect_multiple_modalities_deduplicates_by_suggested_code_first():
    """Dedup keys off suggested_code when present (falling back to label only if it's
    missing) - two entries with the same code but slightly different labels are one category."""
    ax._get_anthropic_client = lambda: object()
    ax._stream_claude_tool_call = fake_claude({
        "multiple_modalities": True,
        "modalities": [
            {"label": "Standard Cabin", "suggested_code": "STD"},
            {"label": "Standard cabin (refreshed pricing)", "suggested_code": "STD"},
        ],
    })
    result = ax.detect_multiple_modalities("doc")
    assert len(result) == 1


def test_detect_multiple_modalities_ignores_non_dict_entries():
    ax._get_anthropic_client = lambda: object()
    ax._stream_claude_tool_call = fake_claude({
        "multiple_modalities": True,
        "modalities": [{"label": "Standard", "suggested_code": "STD"}, "not a dict", None, 42],
    })
    result = ax.detect_multiple_modalities("doc")
    assert len(result) == 1
