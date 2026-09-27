"""Regression test for a real product-owner complaint (2026-09-27), verbatim: "when creating new
closedtour, we shall have just one modality to be created: One main information and one modality.
Different prices of the same modality must be listed all in the modality price tabel with
different start and end dates, so no missing dates in the modality price. But if there is another
modalitye like Standard = Modality 1 and Deluxe =Modality 2 the human shall see the difference...
I am not happy with the current modality work."

MODALITY_DETECTION_PROMPT (ai_extractor.py) already distinguished "multiple pricing categories"
from "tour variants (different itineraries/durations)", but never explicitly ruled out the exact
confusion being reported: a different price for a different TIME PERIOD (season/date range) for
the SAME category being mistaken for a genuinely different Modality. This test locks in the new
explicit rule added to close that gap.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_extractor


def test_prompt_explicitly_rules_out_season_based_splitting():
    prompt = ai_extractor.MODALITY_DETECTION_PROMPT
    assert "NEVER a separate" in prompt
    assert "SAME Modality priced differently across dates" in prompt
    assert "price table as additional dated rows" in prompt


def test_prompt_still_requires_a_genuine_category_difference_for_a_new_modality():
    prompt = ai_extractor.MODALITY_DETECTION_PROMPT
    assert "DIFFERENT PRODUCT/CATEGORY a customer chooses between" in prompt
    assert "Standard" in prompt and "Deluxe" in prompt


def test_prompt_gives_a_concrete_disambiguation_rule_name_vs_date():
    # The exact tie-breaker: a category/tier NAME attached to a price block means it's a genuine
    # Modality candidate; a date range alone with no category name means it's seasonal pricing on
    # one Modality.
    prompt = ai_extractor.MODALITY_DETECTION_PROMPT
    assert "category/tier NAME attached" in prompt
    assert "treat them as ONE Modality" in prompt


def test_new_rule_sits_before_the_code_format_rule_not_appended_after_the_schema():
    # Placement matters for prompt-following reliability - it must be read as part of the core
    # decision, not as an afterthought tacked on past the JSON schema.
    prompt = ai_extractor.MODALITY_DETECTION_PROMPT
    new_rule_idx = prompt.index("NEVER a separate")
    schema_idx = prompt.index('"multiple_modalities": true or false')
    critical_code_rule_idx = prompt.index("CRITICAL - CONFIRMED REAL FAILURE TO AVOID")
    assert new_rule_idx < critical_code_rule_idx < schema_idx
