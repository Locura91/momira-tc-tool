"""Regression tests for two rules the product owner re-confirmed on 2026-08-24:

1. "when a new Modality is added, the earliest start date can be only the actual day of
   today ... It cannot be in the past." (builder.start_date_or_today already floors this at
   BUILD time - these tests lock down the underlying floor helper it's built on, and the
   ai_extractor + app.py side is covered by manual verification since it's UI wiring.)

2. "if no specific [cancellation policy is] mentioned, leave the standardized Cancellation
   policy to 30 days or prior for 100% refund. It cannot be better than this." -
   builder._cancellation_ranges_from_tiers now floors any document-stated 100%-refund tier
   up to 30 days' notice, while leaving stricter (or partial-refund) tiers untouched.
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
# Cancellation policy is a 30-day/100%-refund FLOOR, not just a fallback default
# ---------------------------------------------------------------------------

def test_a_too_generous_full_refund_tier_is_floored_to_30_days():
    """CONFIRMED RULE: a document offering 100% refund on shorter notice than 30 days must
    not be published as stated - it undercuts the house standard."""
    tiers = builder._cancellation_ranges_from_tiers([{"days": 10, "fee_percentage": 0}])
    assert tiers == [(30, 100.0)]


def test_a_stricter_full_refund_tier_is_honored_as_stated():
    """A document that is MORE conservative than the standard (more days required) is a real
    supplier term, not something to override."""
    tiers = builder._cancellation_ranges_from_tiers([{"days": 45, "fee_percentage": 0}])
    assert tiers == [(45, 100.0)]


def test_graduated_schedule_only_the_full_refund_tier_floors():
    """A partial-refund tier below 30 days is a legitimate graduated step, not 'better than'
    the standard - only the tier that grants a FULL refund gets pushed out."""
    tiers = builder._cancellation_ranges_from_tiers([
        {"days": 15, "fee_percentage": 0},    # too-generous full refund -> floors to 30
        {"days": 7, "fee_percentage": 50},     # partial refund below 30 days -> untouched
        {"days": 0, "fee_percentage": 100},    # no-show / day-of -> untouched
    ])
    assert tiers == [(30, 100.0), (7, 50.0), (0, 0.0)]


def test_flooring_two_tiers_to_the_same_day_keeps_the_higher_refund():
    tiers = builder._cancellation_ranges_from_tiers([
        {"days": 30, "fee_percentage": 0},
        {"days": 20, "fee_percentage": 0},
    ])
    assert tiers == [(30, 100.0)]


def test_no_tiers_stated_still_returns_none_for_the_standing_default():
    """Untouched: the None sentinel (not an empty list) is what tells every caller to fall
    back to the standing 30-day/100% default text/schema default - see
    _cancellation_ranges_from_tiers' own docstring."""
    assert builder._cancellation_ranges_from_tiers(None) is None
    assert builder._cancellation_ranges_from_tiers([]) is None


def test_the_default_voucher_text_already_matches_the_30_day_standard():
    text = builder._cancellation_voucher_text("", None)
    assert "30 days" in text
