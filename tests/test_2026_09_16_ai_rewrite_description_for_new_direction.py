"""Regression tests for a real product-owner request (2026-09-16), a direct follow-up to the
Transfer duplicate-and-swap name/description fix: a live description ("A private transfer is
available from Cairo Airport (CAI) to your booked accommodation in Cairo or Giza by air-
conditioned vehicle.") never names the arrival point by its own name at all - it describes it by
ROLE ("your booked accommodation"), so builder._swap_route_text_if_found (even with the chunk-
expansion fix) has nothing to literally swap and correctly leaves it unchanged/flagged. Chris,
reviewing the swapped screen: "We must rewrite the description and the description must
understand its meaning. How could we improve that?"

Fix: ai_extractor.rewrite_route_description_for_new_direction(plain_text, old_departure_name,
old_arrival_name, new_departure_name, new_arrival_name) - a genuine AI rewrite for the minority
of descriptions the literal swap can't confidently handle, wired into flows/duplicate_transfer.py
as a "🤖 Rewrite with AI for the new direction" button next to the existing "couldn't auto-swap"
warning (a human-triggered extra step, not run automatically on every load - this is a real API
call with real cost/latency, and the existing free literal-swap path already succeeds silently
whenever the text is well-formed enough for it).

No real Anthropic API key is used or required - _get_anthropic_client/_stream_claude_message (the
two seams this function calls through) are monkeypatched, same convention as test_ai_extractor.py
(which patches the sibling _stream_claude_tool_call seam for the forced-tool-call path instead).
"""
import os

import pytest

import ai_extractor as ax


@pytest.fixture(autouse=True)
def restore_seams():
    real_client_fn = ax._get_anthropic_client
    real_stream_fn = ax._stream_claude_message
    yield
    ax._get_anthropic_client = real_client_fn
    ax._stream_claude_message = real_stream_fn


def test_rewrite_calls_the_model_with_old_and_new_route_context():
    seen = {}

    def _fake_stream(client, model, max_tokens, system_prompt, user_content):
        seen["system_prompt"] = system_prompt
        seen["user_content"] = user_content
        return "A private transfer is available from Cairo City Hotel to your pickup at Cairo Airport (CAI).", "end_turn"

    ax._get_anthropic_client = lambda: object()
    ax._stream_claude_message = _fake_stream

    result = ax.rewrite_route_description_for_new_direction(
        "A private transfer is available from Cairo Airport (CAI) to your booked accommodation "
        "in Cairo or Giza by air-conditioned vehicle.",
        old_departure_name="Cairo Airport (CAI)", old_arrival_name="Cairo City",
        new_departure_name="Cairo City", new_arrival_name="Cairo Airport (CAI)",
    )
    assert result == ("A private transfer is available from Cairo City Hotel to your pickup at "
                       "Cairo Airport (CAI).")
    # the model must be given both the old AND new route so it can genuinely understand the
    # direction change, not just told to swap two literal strings
    assert "Cairo Airport (CAI)" in seen["user_content"]
    assert "Cairo City" in seen["user_content"]
    assert "understand" in seen["system_prompt"].lower()


def test_rewrite_strips_whitespace_from_the_models_reply():
    ax._get_anthropic_client = lambda: object()
    ax._stream_claude_message = lambda client, model, max_tokens, system_prompt, user_content: (
        "\n  Rewritten text.  \n", "end_turn")
    result = ax.rewrite_route_description_for_new_direction(
        "Original.", "A", "B", "B", "A")
    assert result == "Rewritten text."


def test_rewrite_falls_back_to_the_original_text_on_any_error():
    def _raises(*args, **kwargs):
        raise RuntimeError("API down")

    ax._get_anthropic_client = _raises
    result = ax.rewrite_route_description_for_new_direction(
        "Original description.", "A", "B", "B", "A")
    assert result == "Original description."


def test_rewrite_falls_back_to_the_original_text_when_the_model_returns_nothing():
    ax._get_anthropic_client = lambda: object()
    ax._stream_claude_message = lambda client, model, max_tokens, system_prompt, user_content: ("   ", "end_turn")
    result = ax.rewrite_route_description_for_new_direction(
        "Original description.", "A", "B", "B", "A")
    assert result == "Original description."


def test_rewrite_returns_empty_text_unchanged_without_calling_the_model():
    calls = []
    ax._get_anthropic_client = lambda: calls.append("called") or object()
    result = ax.rewrite_route_description_for_new_direction("", "A", "B", "B", "A")
    assert result == ""
    assert calls == []


def test_rewrite_uses_haiku_by_default():
    seen = {}

    def _fake_stream(client, model, max_tokens, system_prompt, user_content):
        seen["model"] = model
        return "rewritten", "end_turn"

    ax._get_anthropic_client = lambda: object()
    ax._stream_claude_message = _fake_stream
    ax.rewrite_route_description_for_new_direction("Original.", "A", "B", "B", "A")
    assert seen["model"] == ax.HAIKU_MODEL


# ---------------------------------------------------------------------------------------------
# flows/duplicate_transfer.py wiring
# ---------------------------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_DUPLICATE_TRANSFER_FLOW_PY = os.path.join(os.path.dirname(_HERE), "flows", "duplicate_transfer.py")


def _read_duplicate_transfer_flow():
    with open(_DUPLICATE_TRANSFER_FLOW_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_flow_imports_the_ai_rewrite_function():
    src = _read_duplicate_transfer_flow()
    assert "from ai_extractor import rewrite_route_description_for_new_direction" in src


def test_flow_offers_an_ai_rewrite_button_for_description_and_pickup_description():
    src = _read_duplicate_transfer_flow()
    assert src.count('st.button("🤖 Rewrite with AI for the new direction"') == 2
    assert 'rewrite_route_description_for_new_direction(' in src


def test_flow_only_offers_the_ai_rewrite_button_when_the_literal_swap_failed():
    # The button must be nested under the "couldn't auto-swap" (swapped is False) branch, not
    # shown unconditionally - it's meant for the minority case the free literal swap can't handle.
    # Both the description's and the pickup info's button must each follow their own "is False:"
    # check within a short distance (i.e. nested directly under it, not just present somewhere
    # earlier in the file).
    src = _read_duplicate_transfer_flow()
    button_positions = [i for i in range(len(src)) if src.startswith(
        'st.button("🤖 Rewrite with AI for the new direction"', i)]
    assert len(button_positions) == 2
    for button_pos in button_positions:
        preceding_is_false = src.rfind("is False:", 0, button_pos)
        assert preceding_is_false != -1
        assert button_pos - preceding_is_false < 1200, (
            "the AI-rewrite button isn't closely nested under its 'is False:' swap-failed check")
