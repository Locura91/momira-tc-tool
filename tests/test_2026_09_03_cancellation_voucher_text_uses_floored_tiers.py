"""Fix for a real customer-facing bug (product owner, 2026-09-03):

    "We can't write this information on the Conditions shown to the customer: More than 24 hours
    before the excursion: no cancellation fee. Less than 24 hours before the excursion: 100% of
    the excursion price charged. --> We have our own condition which is 30 days or prior for 100%
    and we use tour condition because we could make money out of it."

builder.py's _cancellation_voucher_text() builds the plain-text cancellation policy shown to the
customer/staff on the voucher (Condition/Voucher Remarks). Its `cancellation_tiers` argument always
arrives ALREADY FLOORED to Momira's 30-day/100%-refund house standard (_cancellation_ranges_from_
tiers pushes any supplier tier offering a full refund on shorter notice than 30 days out to 30 days
- see that function's own docstring). But this function used to return the SOURCE's raw
cancellation_policy_text verbatim FIRST, before ever looking at the floored tiers - so a supplier's
real, more lenient wording (e.g. "24 hours' notice, no fee") could still reach the customer even
though the structured policy Travel Compositor actually enforces had already been floored to 30
days. That handed the customer a MORE GENEROUS window than what's actually in effect, undercutting
the exact revenue the house floor exists to protect.

Fixed priority: (1) synthesize the text from the (floored) tiers whenever any exist - guaranteed to
match the policy actually enforced; (2) the source's raw text, only when there are no structured
tiers at all; (3) the standing 30-day/100%-refund default text.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import builder

MODULE_BUILD = "2026-09-04-pptx-text-and-image-extraction"


def test_lenient_raw_text_is_overridden_by_the_floored_tiers_synthesis():
    # The exact real-world example from the report: supplier's own wording says 24 hours, but the
    # (already-floored) structured tiers say 30 days/100% and 0 days/0% - the text shown to the
    # customer must reflect THAT, never the supplier's 24-hour wording.
    raw_supplier_text = (
        "More than 24 hours before the excursion: no cancellation fee. Less than 24 hours before "
        "the excursion: 100% of the excursion price charged."
    )
    floored_tiers = [(30, 100.0), (0, 0.0)]  # what _cancellation_ranges_from_tiers produces after flooring
    result = builder._cancellation_voucher_text(raw_supplier_text, floored_tiers)
    assert "24 hour" not in result
    assert "30 days" in result
    assert result != raw_supplier_text


def test_tiers_synthesis_is_used_even_when_raw_text_is_present_and_unfloored():
    # Even a raw text that's already stricter than 30 days must not be trusted verbatim - the
    # (floored, here unchanged) tiers are always the source of truth for what's shown.
    raw_text = "Free cancellation up to 45 days before arrival, 100% fee thereafter."
    tiers = [(45, 100.0), (0, 0.0)]
    result = builder._cancellation_voucher_text(raw_text, tiers)
    assert "45 days" in result
    assert result != raw_text


def test_no_tiers_falls_back_to_raw_text():
    raw_text = "Some genuinely freeform policy statement with no structured tiers behind it."
    result = builder._cancellation_voucher_text(raw_text, None)
    assert result == raw_text


def test_no_tiers_and_no_text_falls_back_to_the_default():
    result = builder._cancellation_voucher_text("", None)
    assert result == builder._DEFAULT_CANCELLATION_VOUCHER_TEXT


def test_empty_tiers_list_also_falls_back_to_raw_text_then_default():
    assert builder._cancellation_voucher_text("some text", []) == "some text"
    assert builder._cancellation_voucher_text("", []) == builder._DEFAULT_CANCELLATION_VOUCHER_TEXT
