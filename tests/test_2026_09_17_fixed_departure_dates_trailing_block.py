"""Regression tests for a real product-owner follow-up (2026-09-17), on the SAME Movenpick MS
Darakum cruise as test_2026_09_17_closedtour_fixed_departure_dates.py, this time reported with a
screenshot of Travel Compositor's own live booking calendar: dates AFTER the last announced
sailing (23 Oct 2027) were still shown as bookable/priced, with stray crossed-out ("X") days
scattered through November/December that don't correspond to any real departure.

    "when stop sale added. after the last available departure date, we must add stop sales until
    the modality is valid. So one day after last departure day until end of modality max length
    must be last stop sale, to avoid as in picture."

ROOT CAUSE: compute_stop_sales_from_fixed_departure_dates deliberately added nothing after the
LAST departure date (to avoid ever hiding a later season's dates) - but that left every day after
23 Oct 2027, still well inside the Modality's own priced validity window, open and un-blocked.

FIX: a new `valid_until` parameter caps a trailing block from the day after the last departure
through `valid_until` (inclusive). _apply_fixed_departure_dates derives this automatically as the
furthest "endDate" across the extraction's own price_list (there is no other "how far is this
Modality valid" concept anywhere in this codebase - see _max_price_list_end_date) - bounded, not
indefinite, so a LATER document with next season's own price_list/fixed_departure_dates is never
silently locked out by this one.
"""
import datetime

import ai_extractor


# ---------------------------------------------------------------------------------------------
# compute_stop_sales_from_fixed_departure_dates - the new valid_until parameter
# ---------------------------------------------------------------------------------------------

def test_trailing_block_added_when_valid_until_is_later_than_the_last_departure():
    dates = ["2027-02-12", "2027-10-23"]
    today = datetime.date(2026, 9, 17)
    result = ai_extractor.compute_stop_sales_from_fixed_departure_dates(
        dates, today=today, valid_until="2027-12-31")
    assert result[-1] == {"start": "2027-10-24", "end": "2027-12-31"}


def test_real_movenpick_example_with_a_year_end_validity_bound():
    dates = ["2027-02-12", "2027-03-10", "2027-04-05", "2027-05-01", "2027-09-01",
             "2027-09-27", "2027-10-23"]
    today = datetime.date(2026, 9, 17)
    result = ai_extractor.compute_stop_sales_from_fixed_departure_dates(
        dates, today=today, valid_until=datetime.date(2027, 12, 31))
    assert result == [
        {"start": "2026-09-17", "end": "2027-02-11"},
        {"start": "2027-02-13", "end": "2027-03-09"},
        {"start": "2027-03-11", "end": "2027-04-04"},
        {"start": "2027-04-06", "end": "2027-04-30"},
        {"start": "2027-05-02", "end": "2027-08-31"},
        {"start": "2027-09-02", "end": "2027-09-26"},
        {"start": "2027-09-28", "end": "2027-10-22"},
        {"start": "2027-10-24", "end": "2027-12-31"},  # the new trailing block
    ]


def test_no_trailing_block_when_valid_until_is_omitted_unchanged_default_behavior():
    dates = ["2027-02-12", "2027-10-23"]
    today = datetime.date(2026, 9, 17)
    result = ai_extractor.compute_stop_sales_from_fixed_departure_dates(dates, today=today)
    assert result == [
        {"start": "2026-09-17", "end": "2027-02-11"},
        {"start": "2027-02-13", "end": "2027-10-22"},
    ]
    # nothing at all after the last departure date (2027-10-23) - the pre-existing behavior
    assert all(datetime.date.fromisoformat(r["end"]) <= datetime.date(2027, 10, 22) for r in result)


def test_no_trailing_block_when_valid_until_is_on_or_before_the_last_departure():
    dates = ["2027-02-12", "2027-10-23"]
    today = datetime.date(2026, 9, 17)
    # the season "ends" exactly on the last departure itself - nothing trailing to block
    result = ai_extractor.compute_stop_sales_from_fixed_departure_dates(
        dates, today=today, valid_until="2027-10-23")
    assert all(r["end"] != "2027-10-23" or r["start"] != "2027-10-24" for r in result)
    assert len(result) == 2  # unchanged from the no-valid_until case


def test_no_trailing_block_when_valid_until_is_before_the_last_departure():
    dates = ["2027-02-12", "2027-10-23"]
    result = ai_extractor.compute_stop_sales_from_fixed_departure_dates(
        dates, today=datetime.date(2026, 9, 17), valid_until="2027-06-01")
    assert len(result) == 2  # the (invalid/stale) bound is simply ignored, not applied


def test_unparseable_valid_until_is_ignored_rather_than_raising():
    dates = ["2027-02-12", "2027-10-23"]
    result = ai_extractor.compute_stop_sales_from_fixed_departure_dates(
        dates, today=datetime.date(2026, 9, 17), valid_until="not-a-date")
    assert len(result) == 2


def test_single_departure_date_still_gets_a_trailing_block():
    result = ai_extractor.compute_stop_sales_from_fixed_departure_dates(
        ["2027-06-15"], today=datetime.date(2026, 9, 17), valid_until="2027-12-31")
    assert result == [
        {"start": "2026-09-17", "end": "2027-06-14"},
        {"start": "2027-06-16", "end": "2027-12-31"},
    ]


# ---------------------------------------------------------------------------------------------
# _max_price_list_end_date
# ---------------------------------------------------------------------------------------------

def test_max_price_list_end_date_picks_the_furthest_end_across_rows():
    price_list = [
        {"startDate": "2027-01-01", "endDate": "2027-06-30"},
        {"startDate": "2027-07-01", "endDate": "2027-12-31"},
    ]
    assert ai_extractor._max_price_list_end_date(price_list) == datetime.date(2027, 12, 31)


def test_max_price_list_end_date_skips_unparseable_rows():
    price_list = [{"startDate": "2027-01-01", "endDate": ""}, {"endDate": "2027-12-31"}, "not-a-dict"]
    assert ai_extractor._max_price_list_end_date(price_list) == datetime.date(2027, 12, 31)


def test_max_price_list_end_date_returns_none_when_nothing_usable():
    assert ai_extractor._max_price_list_end_date([]) is None
    assert ai_extractor._max_price_list_end_date(None) is None
    assert ai_extractor._max_price_list_end_date([{"endDate": ""}]) is None


# ---------------------------------------------------------------------------------------------
# _apply_fixed_departure_dates - the real wiring, price_list -> valid_until automatically
# ---------------------------------------------------------------------------------------------

def test_apply_derives_valid_until_from_price_list_and_adds_the_trailing_block():
    data = {
        "fixed_departure_dates": ["2027-02-12", "2027-10-23"],
        "stop_sales": [],
        "schedule_notes": "",
        "price_list": [{"startDate": "2027-01-01", "endDate": "2027-12-31"}],
    }
    ai_extractor._apply_fixed_departure_dates(data, today=datetime.date(2026, 9, 17))
    assert {"start": "2027-10-24", "end": "2027-12-31"} in data["stop_sales"]
    assert "priced validity end" in data["schedule_notes"]
    assert "2027-12-31" in data["schedule_notes"]


def test_apply_skips_trailing_block_when_price_list_has_no_usable_end_date():
    data = {
        "fixed_departure_dates": ["2027-02-12", "2027-10-23"],
        "stop_sales": [],
        "schedule_notes": "",
        "price_list": [],
    }
    ai_extractor._apply_fixed_departure_dates(data, today=datetime.date(2026, 9, 17))
    assert all(r["end"] <= "2027-10-22" for r in data["stop_sales"])
    assert "priced validity end" not in data["schedule_notes"]


def test_apply_respects_the_overlap_guard_for_the_trailing_block_too():
    # A manually/AI-extracted stop_sales entry that already covers part of the trailing gap
    # (e.g. a known off-season dry-dock) must not get a redundant, overlapping second entry.
    data = {
        "fixed_departure_dates": ["2027-02-12", "2027-10-23"],
        "stop_sales": [{"start": "2027-11-01", "end": "2027-11-30"}],
        "schedule_notes": "",
        "price_list": [{"startDate": "2027-01-01", "endDate": "2027-12-31"}],
    }
    ai_extractor._apply_fixed_departure_dates(data, today=datetime.date(2026, 9, 17))
    # the whole trailing computed range overlaps the existing Nov entry, so it must be dropped
    assert {"start": "2027-10-24", "end": "2027-12-31"} not in data["stop_sales"]
    assert {"start": "2027-11-01", "end": "2027-11-30"} in data["stop_sales"]


def test_real_movenpick_scenario_through_the_full_create_path(monkeypatch):
    # End-to-end reproduction: the AI extracts the 7 real sailing dates AND a price_list covering
    # the whole year, going through extract_structured_data (the "Create" path Chris actually
    # used) - the trailing block must land in the final stop_sales automatically.
    fake_response = {
        "tour_name": "13 Days Nile Cruise - Aswan to Cairo", "description": "", "hotels_text": "",
        "hotels_count": 1, "supplements": [], "included": "", "excluded": "", "meeting_point": "",
        "policy_remarks": "", "what_to_bring": "", "cancellation_policy_tiers": [],
        "cancellation_policy_text": "", "itinerary_destinations": ["Aswan", "Cairo"], "nights": 12,
        "start_time": "", "end_time": "", "min_child_age": 2, "max_child_age": 12,
        "child_discount_percentage": None, "extra_child_allowed": True,
        "extra_child_max_overrides": {"single": None, "double": None, "triple": None, "quadruple": None},
        "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
        "schedule_notes": "", "pricing_notes": "",
        "stop_sales": [],
        "price_list": [{"name": "2027 season", "startDate": "2027-01-01", "endDate": "2027-12-31",
                         "price": {"singlePrice": {"amount": 6908, "currency": "EUR"}}}],
        "release_days_mentions": [], "guaranteed_departure_rule": None,
        "fixed_departure_dates": ["2027-02-12", "2027-03-10", "2027-04-05", "2027-05-01",
                                   "2027-09-01", "2027-09-27", "2027-10-23"],
    }
    monkeypatch.setattr(ai_extractor, "_call_claude", lambda *a, **k: dict(fake_response))
    data = ai_extractor.extract_structured_data("irrelevant raw text")
    assert {"start": "2027-10-24", "end": "2027-12-31"} in data["stop_sales"]
    # every row is still publish-safe (see test_2026_09_17_closedtour_create_stop_sales_crash_fix.py)
    for row in data["stop_sales"]:
        assert ai_extractor._is_parseable_iso_date(row["start"])
        assert ai_extractor._is_parseable_iso_date(row["end"])
