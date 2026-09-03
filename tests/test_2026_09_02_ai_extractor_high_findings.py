"""Tests for the 4 leftover HIGH findings from the full-app audit
(full-app-audit-2026-09-01.md), approved by Chris ("pleas start to clean it up") and fixed
2026-09-02. These were explicitly flagged-but-deferred when the earlier HIGH batches closed -
never part of any completed batch, not silently missed.

  1. stop_sales is a *required* schema field in 3 extractors (ClosedTour main info, Ticket main
     info, Ticket Modality) whose prompts never explained it - the model had to guess what
     counts as a blackout date from the bare field name alone, with no "don't invent one" guard.
     Two sibling prompts (OPTION_ONLY_SYSTEM_PROMPT, MODALITY_EXTRACTION_SYSTEM_PROMPT) already
     had good explanatory text - adapted the same shape into the 3 gap prompts.
  2. min_billable_pax escaped extraction_memory's "don't auto-apply, this looks confident but
     isn't" block-list even though it has the exact same fixed-default failure shape as its
     neighbours min_pax/max_pax right above it in the same set - two corrections in a row would
     have silently applied a stale minimum-headcount rule to every future Transfer/Transport
     from that supplier, double-charging a solo traveller who should be bookable at 1 pax.
  3. EXTRACTION_TOOL_SCHEMA's itinerary_destinations was typed as an array of OBJECTS while the
     prompt explicitly instructs "a list of plain place names" (a list of STRINGS) - a real
     contradiction that would crash builder.py's resolve_destination(loc_name) call downstream
     with an AttributeError/TypeError the moment the model actually followed the schema instead
     of the prompt text.
  4. Every detect_* function (multi-product/multi-modality/multi-variant detection) ran through
     _detect_items, which called _call_claude_with_stop with NO input_schema at all - the fully
     permissive schema, the exact failure pattern this file already fixed for extraction
     (_required_keys_schema) but left open for detection. A dropped list_key came back reading
     as "only one product found" on screen, indistinguishable from a genuinely single-product
     document, with nothing raised anywhere.
"""
import inspect

import ai_extractor as ax


# ======================================================================
# 1. stop_sales is now explained in all 3 previously-silent prompts
# ======================================================================

def test_closed_tour_main_prompt_explains_stop_sales():
    text = ax.EXTRACTION_SYSTEM_PROMPT
    assert "stop_sales:" in text
    # Must actually explain what counts as a blackout date, not just name the field.
    assert "blackout" in text.lower() or "closures" in text.lower()
    assert "do not invent" in text.lower() or "do not invent one" in text.lower() or "not invent" in text.lower()


def test_ticket_main_prompt_explains_stop_sales():
    text = ax.TICKET_EXTRACTION_SYSTEM_PROMPT
    assert "stop_sales:" in text
    assert "blackout" in text.lower() or "closures" in text.lower()


def test_ticket_modality_prompt_explains_stop_sales():
    text = ax.TICKET_MODALITY_SYSTEM_PROMPT
    assert "stop_sales:" in text
    assert "blackout" in text.lower() or "closures" in text.lower()
    assert "do not invent" in text.lower() or "not invent" in text.lower()


def test_stop_sales_explanation_warns_against_inventing_dates_in_all_three():
    """Regression guard for the actual risk in the finding: without a 'don't invent' guardrail,
    a model that pattern-matches on the bare field name could hallucinate closures that were
    never in the source. Every fixed prompt must carry an explicit anti-invention instruction
    for this specific field, not just a definition."""
    for prompt_name in ("EXTRACTION_SYSTEM_PROMPT", "TICKET_EXTRACTION_SYSTEM_PROMPT",
                        "TICKET_MODALITY_SYSTEM_PROMPT"):
        text = getattr(ax, prompt_name)
        # Isolate the stop_sales bullet so this doesn't just match some unrelated "don't invent"
        # instruction elsewhere in a multi-thousand-line prompt.
        idx = text.index("stop_sales:")
        bullet = text[idx:idx + 1400]
        assert "invent" in bullet.lower(), f"{prompt_name} stop_sales bullet has no anti-invention guard"


def test_the_3_gap_extractors_still_genuinely_require_stop_sales_via_schema():
    """Confirms the finding's premise still holds after the fix - stop_sales remains a
    schema-required field in all 3 (the fix adds explanation, it doesn't relax enforcement)."""
    assert '"stop_sales"' in inspect.getsource(ax).split("EXTRACTION_TOOL_SCHEMA = {")[1].split("\n\n\n")[0]

    src = inspect.getsource(ax.extract_ticket_data)
    assert "stop_sales" in src.split("defaults = {")[1].split("_call_claude")[0]

    src2 = inspect.getsource(ax.extract_ticket_modality_data)
    assert "stop_sales" in src2.split("defaults = {")[1].split("_call_claude")[0]


# ======================================================================
# 2. min_billable_pax now blocked from auto-apply in extraction_memory
# ======================================================================

def test_min_billable_pax_is_never_auto_applied():
    import extraction_memory as em
    assert em._apply_blocked("min_billable_pax") is True


def test_min_billable_pax_blocked_case_insensitively():
    import extraction_memory as em
    assert em._apply_blocked("Min_Billable_Pax") is True


def test_sibling_pax_fields_still_blocked_alongside_it():
    """Regression guard: adding the new field must not have disturbed the two it sits next to."""
    import extraction_memory as em
    assert em._apply_blocked("min_pax") is True
    assert em._apply_blocked("max_pax") is True


def test_unrelated_field_still_not_blocked():
    """Sanity check the block-list is still narrow, not accidentally widened to match everything."""
    import extraction_memory as em
    assert em._apply_blocked("pickup_point") is False
    assert em._apply_blocked("vehicle_type") is False


# ======================================================================
# 3. itinerary_destinations schema now matches the prompt (array of strings)
# ======================================================================

def test_itinerary_destinations_schema_is_array_of_strings_not_objects():
    schema = ax.EXTRACTION_TOOL_SCHEMA["properties"]["itinerary_destinations"]
    assert schema == {"type": "array", "items": {"type": "string"}}


def test_itinerary_destinations_prompt_instruction_still_says_plain_strings():
    """Regression guard for the OTHER half of the contradiction - confirms the schema was the
    thing that was wrong, not the prompt (which already correctly asked for plain names)."""
    assert "plain place names" in ax.EXTRACTION_SYSTEM_PROMPT


def test_itinerary_destinations_still_required_in_schema():
    """The fix must not have accidentally dropped the field from 'required' while retyping it."""
    assert "itinerary_destinations" in ax.EXTRACTION_TOOL_SCHEMA["required"]


# ======================================================================
# 4. Detection calls now carry a real input_schema (list_key/flag_key required)
# ======================================================================

def test_detection_schema_requires_flag_and_list_keys():
    schema = ax._detection_schema("multiple_transfers", "transfers")
    assert schema["required"] == ["multiple_transfers", "transfers"]
    assert schema["properties"]["transfers"]["type"] == "array"
    assert schema["properties"]["multiple_transfers"]["type"] == "boolean"


def test_detection_schema_list_items_stay_untyped():
    """Must only enforce presence, never distort/restrict individual candidate shape - matching
    _required_keys_schema's own philosophy for extraction."""
    schema = ax._detection_schema("flag", "items")
    assert schema["properties"]["items"]["items"] == {"type": "object", "additionalProperties": True}


def test_detect_items_passes_a_real_input_schema_not_none(monkeypatch):
    seen = {}

    def fake_call_with_stop(system_prompt, user_content, model, max_tokens, input_schema=None):
        seen["input_schema"] = input_schema
        return {"my_list": [{"label": "a"}], "my_flag": True}, "end_turn"

    monkeypatch.setattr(ax, "_call_claude_with_stop", fake_call_with_stop)
    ax._detect_items("sys", "some text", "model", "my_flag", "my_list", lambda i: i.get("label"))

    assert seen["input_schema"] is not None
    assert seen["input_schema"]["required"] == ["my_flag", "my_list"]


def test_every_detect_function_call_site_relies_on_detect_items_for_the_schema():
    """Regression guard: every detect_* function must still go through _detect_items (the single
    choke point the fix lives in), rather than one of them growing its own direct
    _call_claude_with_stop call that would silently bypass the new schema."""
    for fn_name in ("detect_multiple_modalities", "detect_tour_variants", "detect_ticket_variants",
                    "detect_ticket_modalities", "detect_transfer_products",
                    "detect_transport_products", "detect_hotel_products"):
        src = inspect.getsource(getattr(ax, fn_name))
        assert "_detect_items(" in src, f"{fn_name} no longer routes through _detect_items"
        assert "_call_claude_with_stop(" not in src, (
            f"{fn_name} calls _call_claude_with_stop directly, bypassing _detect_items' schema")
