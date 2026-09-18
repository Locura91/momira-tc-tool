"""Regression tests for a real product-owner request (2026-09-18, verbatim): "Within creating a
new product, human selects max occupancy by 2 or 3 pax for example, we must make sure that the
contracts reads max double or triple occupancy for supplements and modalities - it saves time and
AI reader time, becuase it focuses only on the occupancy and can ignore the rest."

Clarified via follow-up (AskUserQuestion, both confirmed):
  - inject the human's ALREADY-CHOSEN Max Pax straight into the AI extraction prompt itself
    (not just enforced afterward as a builder.py safety net - that's a separate, pre-existing
    2026-09-18 feature, see test_2026_09_18_max_occupancy_caps_triple_quadruple.py);
  - scoped to ClosedTour only - the only product where a Max Pax choice already exists as an
    upfront step, before extraction, in app.py's Step 3 (wired through render_multi_tour_flow's
    max_pax parameter into flows/multi_tour.py).

Implementation: ai_extractor._max_occupancy_focus_clause(max_occupancy_hint) builds the prompt
text; extract_structured_data and extract_modality_data both take a new max_occupancy_hint kwarg
and prepend that clause to user_content (same "IMPORTANT: ..." prefix mechanism already used for
variant_hint/human_hint). flows/multi_tour.py passes max_pax through at both call sites, but only
when it's been genuinely narrowed below 9 (app.py's Step 3 selectbox default, list(range(2,10)),
index=7 -> 9 -> "no constraint chosen").
"""
import ai_extractor


# ---------------------------------------------------------------------------------------------
# _max_occupancy_focus_clause - the pure clause-building function
# ---------------------------------------------------------------------------------------------

def test_returns_empty_string_for_falsy_input():
    assert ai_extractor._max_occupancy_focus_clause(None) == ""
    assert ai_extractor._max_occupancy_focus_clause(0) == ""
    assert ai_extractor._max_occupancy_focus_clause("") == ""


def test_returns_empty_string_for_a_value_outside_the_1_to_4_occupancy_range():
    # This app's occupancy columns only ever go up to quadruple - a Max Pax of e.g. 9 (the
    # unconstrained default) or anything above 4 has nothing meaningful to say here.
    assert ai_extractor._max_occupancy_focus_clause(9) == ""
    assert ai_extractor._max_occupancy_focus_clause(5) == ""


def test_names_the_correct_occupancy_word_for_2_and_3():
    clause_2 = ai_extractor._max_occupancy_focus_clause(2)
    assert "double occupancy" in clause_2
    clause_3 = ai_extractor._max_occupancy_focus_clause(3)
    assert "triple occupancy" in clause_3


def test_tells_the_ai_which_columns_can_be_skipped():
    clause_2 = ai_extractor._max_occupancy_focus_clause(2)
    assert "triple, quadruple-occupancy" in clause_2 or ("triple" in clause_2 and "quadruple" in clause_2)
    clause_4 = ai_extractor._max_occupancy_focus_clause(4)
    # Nothing above quadruple exists in this schema, so there's nothing left to tell the AI to skip.
    assert "skip/ignore" not in clause_4


def test_explicitly_distinguishes_from_the_separate_document_stated_max_occupancy_field():
    # CRITICAL: this hint must never be confused with (or silently override) the separate,
    # extraction-output max_occupancy field, which reflects what the SOURCE DOCUMENT itself
    # states a room/cabin can hold - the two are independent and can legitimately disagree.
    clause = ai_extractor._max_occupancy_focus_clause(2)
    assert "does NOT change the separate max_occupancy field" in clause


# ---------------------------------------------------------------------------------------------
# extract_structured_data / extract_modality_data wiring
# ---------------------------------------------------------------------------------------------

def test_extract_structured_data_prepends_the_clause_when_hint_given(monkeypatch):
    captured = {}

    def fake_call_claude(system_prompt, user_content, model, **kwargs):
        captured["user_content"] = user_content
        return {}

    monkeypatch.setattr(ai_extractor, "_call_claude", fake_call_claude)
    ai_extractor.extract_structured_data("irrelevant raw text", max_occupancy_hint=2)
    assert "double occupancy" in captured["user_content"]
    assert "irrelevant raw text" in captured["user_content"]


def test_extract_structured_data_adds_nothing_when_hint_is_none(monkeypatch):
    captured = {}

    def fake_call_claude(system_prompt, user_content, model, **kwargs):
        captured["user_content"] = user_content
        return {}

    monkeypatch.setattr(ai_extractor, "_call_claude", fake_call_claude)
    ai_extractor.extract_structured_data("irrelevant raw text")
    assert captured["user_content"] == "irrelevant raw text"


def test_extract_modality_data_prepends_the_clause_when_hint_given(monkeypatch):
    captured = {}

    def fake_call_claude(system_prompt, user_content, model, **kwargs):
        captured["user_content"] = user_content
        return {}

    monkeypatch.setattr(ai_extractor, "_call_claude", fake_call_claude)
    ai_extractor.extract_modality_data("irrelevant raw text", max_occupancy_hint=3)
    assert "triple occupancy" in captured["user_content"]
    assert "irrelevant raw text" in captured["user_content"]


def test_extract_modality_data_combines_hint_with_a_human_hint():
    # Both prefixes must survive together - the human_hint branch builds its own user_content
    # string, so the occupancy clause has to be threaded into that branch too, not just the
    # plain no-human-hint path.
    import inspect
    source = inspect.getsource(ai_extractor.extract_modality_data)
    assert source.count("_occupancy_clause") >= 2


def test_extract_modality_data_adds_nothing_when_hint_is_none(monkeypatch):
    captured = {}

    def fake_call_claude(system_prompt, user_content, model, **kwargs):
        captured["user_content"] = user_content
        return {}

    monkeypatch.setattr(ai_extractor, "_call_claude", fake_call_claude)
    ai_extractor.extract_modality_data("irrelevant raw text")
    assert captured["user_content"] == "irrelevant raw text"


# ---------------------------------------------------------------------------------------------
# flows/multi_tour.py wiring - ClosedTour only, gated on a genuinely narrowed Max Pax
# ---------------------------------------------------------------------------------------------

def _read_multi_tour_source():
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "flows", "multi_tour.py")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_multi_tour_flow_passes_max_pax_through_to_both_extraction_calls():
    src = _read_multi_tour_source()
    assert src.count("max_occupancy_hint=max_pax if max_pax and max_pax < 9 else None") == 2


def test_multi_tour_flow_wires_it_into_extract_structured_data_call():
    src = _read_multi_tour_source()
    call_start = src.index("tour[\"main_data\"] = extract_structured_data(")
    call_text = src[call_start:call_start + 1400]
    assert "max_occupancy_hint=max_pax if max_pax and max_pax < 9 else None" in call_text


def test_multi_tour_flow_wires_it_into_extract_modality_data_call():
    src = _read_multi_tour_source()
    call_start = src.index("mod[\"data\"] = extract_modality_data(")
    call_text = src[call_start:call_start + 700]
    assert "max_occupancy_hint=max_pax if max_pax and max_pax < 9 else None" in call_text
