"""Regression tests for two rules the product owner re-confirmed on 2026-08-24:

1. "when a new Modality is added, the earliest start date can be only the actual day of
   today ... It cannot be in the past." (builder.start_date_or_today already floors this at
   BUILD time - these tests lock down the underlying floor helper it's built on, and the
   ai_extractor + app.py side is covered by manual verification since it's UI wiring.)

2. ORIGINALLY (2026-08-24): "if no specific [cancellation policy is] mentioned, leave the
   standardized Cancellation policy to 30 days or prior for 100% refund. It cannot be better
   than this." - builder._cancellation_ranges_from_tiers floors any document-stated
   100%-refund tier up to 30 days' notice, while leaving stricter (or partial-refund) tiers
   untouched.

STILL THE LIVE BEHAVIOR OF THIS FUNCTION (2026-09-04): _cancellation_ranges_from_tiers itself
keeps this floor-based logic unchanged - it's still the correct behavior for a HUMAN
deliberately typing/editing cancellation tiers by hand (the bulk-cancellation screens' review
tables, cancellation_links.py's saved defaults), where an accidentally-too-generous typed value
should be floored to 30 days but an intentionally stricter/partial value should be honored as
typed.

What changed on 2026-09-04 (given this exact example: "More than 48 hours before the tour: no
fee. Within 48 hours: 50% fee. No show: no refund. --> Do not show as remark, as our internal
cancellation with 30 days or prior is better for our Momira company.") is NOT this function -
it's that every one of builder.py's 5 product builders now passes None into it instead of the
document's own extracted cancellation_policy_tiers, so a document's stated terms never reach
this function at all and every document-driven build always gets the flat 30-day/100%-refund
house standard. See _MIN_FULL_REFUND_NOTICE_DAYS's module-level comment in builder.py for the
full history.
"""
import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import builder


# ---------------------------------------------------------------------------
# Modality start date can never be in the past
# ---------------------------------------------------------------------------

def test_a_past_start_date_floors_to_today():
    assert builder.start_date_or_today("2025-01-01") == datetime.date.today().isoformat()


def test_a_future_start_date_is_left_alone():
    future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    assert builder.start_date_or_today(future) == future


def test_no_stated_date_defaults_to_today():
    assert builder.start_date_or_today("") == datetime.date.today().isoformat()
    assert builder.start_date_or_today(None) == datetime.date.today().isoformat()


# ---------------------------------------------------------------------------
# _cancellation_ranges_from_tiers: still floor-based (human-typed-input path only - see
# module docstring above for why document extraction never reaches this function anymore).
# ---------------------------------------------------------------------------

def test_a_too_generous_full_refund_tier_is_floored_up_to_30_days():
    tiers = builder._cancellation_ranges_from_tiers([{"days": 10, "fee_percentage": 0}])
    assert tiers == [(30, 100.0)]


def test_a_stricter_full_refund_tier_is_honored_as_stated():
    """A tier that already requires MORE notice than the house standard is a real, intentional
    value - honored as typed/stated, not loosened."""
    tiers = builder._cancellation_ranges_from_tiers([{"days": 45, "fee_percentage": 0}])
    assert tiers == [(45, 100.0)]


def test_graduated_schedule_only_floors_the_too_generous_piece():
    """The exact real-world trigger for the 2026-09-04 rule - "more than 48 hours before the
    tour: no fee. Within 48 hours: 50% fee. No show: no refund." (approximated here as
    days/fee_percentage tiers) - only the too-generous full-refund tier gets floored; the
    stricter/partial tiers pass through untouched. (Document extraction no longer feeds this
    function at all - see module docstring - but the function's own logic is unchanged.)"""
    tiers = builder._cancellation_ranges_from_tiers([
        {"days": 15, "fee_percentage": 0},
        {"days": 7, "fee_percentage": 50},
        {"days": 0, "fee_percentage": 100},
    ])
    assert tiers == [(30, 100.0), (7, 50.0), (0, 0.0)]


def test_multiple_tiers_at_the_same_day_after_flooring_keep_the_higher_refund():
    tiers = builder._cancellation_ranges_from_tiers([
        {"days": 30, "fee_percentage": 0},
        {"days": 20, "fee_percentage": 0},
    ])
    assert tiers == [(30, 100.0)]


def test_no_tiers_stated_still_returns_none_for_the_standing_default():
    """The None sentinel (not an empty list) is what tells every caller to fall back to the
    standing 30-day/100% default text/schema default - see _cancellation_ranges_from_tiers'
    own docstring."""
    assert builder._cancellation_ranges_from_tiers(None) is None
    assert builder._cancellation_ranges_from_tiers([]) is None


def test_the_default_voucher_text_already_matches_the_30_day_standard():
    text = builder._cancellation_voucher_text("", None)
    assert "30 days" in text
