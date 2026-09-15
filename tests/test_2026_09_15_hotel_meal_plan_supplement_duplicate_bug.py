"""Regression test for the confirmed real Hotel overcharge bug (2026-09-15, HRG-H1/Steigenberger
Golf Resort El Gouna): the extraction prompt used to let a meal-plan upgrade charge (e.g. "Half
board - 30") get extracted TWICE - once correctly under MEAL PLANS, and again as a standalone
SUPPLEMENT, because the source document's own table was headed "Supplements" even though the
charge is the same cost as the meal plan itself.

This is a real, live-verified double-charge, not a harmless duplicate: a hotel supplement is
always applied to the whole booking unless its own travel_windows/meal_plans/room_names narrow it
(builder.py has no "only when NOT on this meal plan" concept), so an unscoped "Half Board
Supplement" charges every booking that amount - including a guest who never chose Half Board -
on top of the meal plan's own already-correct price. Confirmed against the real contract: a
Deluxe Room, 2 adults, 5 nights, Low season should total EUR 920 (EUR 92pp/night x 2 x 5); the
live Travel Compositor listing showed EUR 1,282 / EUR 1,768 instead, consistent with the Half
Board and Club Package charges being applied a second time as unconditional supplements.

Like the other hotel-prompt rules (see test_2026_09_02_hotel_extraction_rules.py's own docstring
for why), this rule lives entirely in prompt text handed to the extraction model - there is no
separate Python code path to unit-test, so this asserts on the prompt's source text directly.
"""
import ai_extractor as ax


def _prompt():
    return ax.HOTEL_EXTRACTION_SYSTEM_PROMPT


def test_meal_plan_supplement_duplicate_rule_present():
    p = _prompt()
    assert "CONFIRMED REAL BUG (2026-09-15, HRG-H1/Steigenberger Golf Resort El Gouna" in p
    assert "MEAL-PLAN UPGRADE charge" in p


def _block():
    """The rule's own text, with wrapped newlines flattened to spaces - the prompt is hand-wrapped
    at ~100 chars for readability, so a multi-word phrase can straddle a line break; asserting on
    normalized whitespace tests the actual wording without being brittle to where it wraps."""
    p = _prompt()
    idx = p.index("CONFIRMED REAL BUG (2026-09-15")
    return " ".join(p[idx: idx + 1800].split())


def test_rule_explains_why_it_is_a_real_overcharge_not_a_harmless_duplicate():
    block = _block()
    assert "ALWAYS applied to the whole booking" in block
    assert "never chose Half Board at all" in block


def test_rule_tells_model_not_to_duplicate_the_meal_plan_as_a_supplement():
    block = _block()
    assert "do NOT also create a supplement entry for it" in block
    # must still allow genuinely independent charges (resort fee, dated gala dinner) through
    assert "resort fee" in block
    assert "gala dinner" in block.lower()


def test_rule_names_both_real_example_meal_plans():
    block = _block()
    assert "Half Board" in block
    assert "Club Package" in block
