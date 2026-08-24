"""Unit tests for builder.start_date_or_today.

CONFIRMED REAL BUG (product owner, 2026-08-24): "A starting date of a new created ClosedTour,
Ticket or Hotel can be earliest the actual day today. Somehow the starting date is always shown
from 2025... this can't be" - a document's own stated start date used to pass straight through
even when it was in the past. See start_date_or_today's docstring in builder.py for the full rule
and its deliberately narrow scope (Ticket/Transfer/Transport's single Valid From field only -
Closed Tour/Hotel's seasonal price_list rows are untouched by this function).
"""
import datetime

from builder import start_date_or_today


def test_missing_start_date_defaults_to_today():
    assert start_date_or_today(None) == datetime.date.today().isoformat()
    assert start_date_or_today("") == datetime.date.today().isoformat()
    assert start_date_or_today("   ") == datetime.date.today().isoformat()


def test_a_past_stated_date_is_floored_to_today():
    """CONFIRMED REAL BUG: an old rate sheet literally stating "valid from 01.01.2025" used to
    publish that exact past date unchanged - a product can never make sense being valid starting
    on a date that already passed, no matter what the document says."""
    assert start_date_or_today("2025-01-01") == datetime.date.today().isoformat()


def test_todays_stated_date_passes_through_unchanged():
    today = datetime.date.today().isoformat()
    assert start_date_or_today(today) == today


def test_a_future_stated_date_passes_through_unchanged():
    future = (datetime.date.today() + datetime.timedelta(days=90)).isoformat()
    assert start_date_or_today(future) == future


def test_accepts_non_iso_input_and_still_applies_the_floor():
    # to_iso_date is expected to normalize common formats like DD/MM/YYYY before the floor check.
    assert start_date_or_today("01/01/2025") == datetime.date.today().isoformat()
