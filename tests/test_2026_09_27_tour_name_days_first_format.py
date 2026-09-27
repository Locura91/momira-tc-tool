"""Regression tests for a real product-owner naming rule (2026-09-27, verbatim): "closed tour
supplier name shall be overall the same: X Days NAME TOUR. Keep this name structure as the number
of nights do not need t be mentioned. put the number of days always at the beginning."

Every ClosedTour's tour_name is now deterministically reshaped to "<N> Days <clean name>" after
extraction, via ai_extractor._format_tour_name_days_first - superseding the narrower
_fix_days_count_in_tour_name (kept standalone, still tested by its own pre-existing behavior) in
the actual pipeline (extract_structured_data).
"""
from ai_extractor import _format_tour_name_days_first


def test_no_existing_day_count_gets_one_prepended():
    # 4 nights -> 5 Days, per the existing Nights-vs-Days convention.
    assert _format_tour_name_days_first("Bush Camp Safari", 4) == "5 Days Bush Camp Safari"


def test_trailing_day_count_is_moved_to_the_front():
    assert _format_tour_name_days_first("Bush Camp Safari - 5 Days", 4) == "5 Days Bush Camp Safari"


def test_parenthetical_day_count_is_moved_and_parens_cleaned_up():
    assert _format_tour_name_days_first("Bush Camp Safari (5 Days)", 4) == "5 Days Bush Camp Safari"


def test_wrong_day_count_in_source_title_is_corrected_not_just_moved():
    # Source says "4 Days" but 4 nights actually means 5 Days - both the count AND position fix.
    assert _format_tour_name_days_first("4 Days Bush Camp Safari", 4) == "5 Days Bush Camp Safari"


def test_nights_mention_is_stripped_entirely():
    assert _format_tour_name_days_first("4 Nights / 5 Days Safari", 4) == "5 Days Safari"


def test_nights_only_mention_with_no_days_token_is_stripped_and_days_prepended():
    assert _format_tour_name_days_first("Safari (4 Nights)", 4) == "5 Days Safari"


def test_zero_nights_product_is_left_untouched():
    # Single-day (Ticket-type) products don't use this convention.
    assert _format_tour_name_days_first("City Walking Tour", 0) == "City Walking Tour"


def test_empty_name_is_left_untouched():
    assert _format_tour_name_days_first("", 4) == ""


def test_non_numeric_nights_is_left_untouched():
    assert _format_tour_name_days_first("Bush Camp Safari", None) == "Bush Camp Safari"


def test_already_correctly_formatted_name_is_unchanged():
    assert _format_tour_name_days_first("5 Days Bush Camp Safari", 4) == "5 Days Bush Camp Safari"


def test_extraction_pipeline_calls_the_new_formatter_not_the_old_one():
    src = open("ai_extractor.py", encoding="utf-8").read()
    assert 'defaults["tour_name"] = _format_tour_name_days_first(defaults.get("tour_name", ""), defaults.get("nights"))' in src
    assert 'defaults["tour_name"] = _fix_days_count_in_tour_name(defaults.get("tour_name", ""), defaults.get("nights"))' not in src


def test_house_rule_text_present_in_the_extraction_prompt():
    src = open("ai_extractor.py", encoding="utf-8").read()
    assert "put the number of days always at the beginning" in src
    assert '"<N> Days <clean product name>"' in src
