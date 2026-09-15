"""Regression test for a real production bug (product owner, 2026-09-16): "i just created new
Tickets in bulk and Christmas surcharge was correctly added, but in the same time also stop
sales - which is useless."

Root cause: the AI extraction prompts that ask for BOTH a Modality's priced extras
(modality_supplements - "a holiday surcharge" is literally given there as an example) AND its
stop_sales (closures) both mention holidays as trigger language - stop_sales' own instructions
included "closed on [a named holiday]" as a phrasing hint. A document that only states a price
increase for a holiday period (e.g. "Christmas Supplement: +20%, Dec 20 - Jan 5") gave the model
two independent, holiday-themed prompts to match against, and it would sometimes populate BOTH
fields for the same period - correctly adding the priced supplement, but ALSO inventing a
stop_sales range nothing in the source actually supports (a priced-higher period is still
bookable, not closed).

Fix: every stop_sales prompt (there are 5 near-identical ones across ClosedTour/Ticket main and
Modality extraction, plus the excursion-specific one) now explicitly states that a holiday/
peak-period SURCHARGE is NOT itself evidence of a stop sale, and must not be duplicated into
stop_sales just because a supplement or higher price applies during that window.
"""
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_AI_EXTRACTOR_PATH = os.path.join(os.path.dirname(_HERE), "ai_extractor.py")


def _read_ai_extractor():
    with open(_AI_EXTRACTOR_PATH, "r", encoding="utf-8") as f:
        return f.read()


def test_every_stop_sales_prompt_warns_against_holiday_surcharge_conflation():
    src = _read_ai_extractor()
    # Every stop_sales field description ends the same way - assert the clarifying sentence sits
    # right before that shared ending, in every occurrence, not just one.
    matches = list(re.finditer(r"Empty list if genuinely none\.", src))
    assert len(matches) == 5  # all 5 stop_sales prompt blocks in this file
    for m in matches:
        preceding = src[max(0, m.start() - 500):m.start()]
        assert "holiday/peak-period SURCHARGE" in preceding
        assert "NOT a stop sale by itself" in preceding


def test_stop_sales_field_count_matches_modality_supplements_holiday_example_count():
    # Sanity check that this isn't accidentally scoped to only SOME of the places a holiday
    # supplement and a stop_sales list can both be extracted for the same record.
    src = _read_ai_extractor()
    assert src.count("- stop_sales: array of") == 5
