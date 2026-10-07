"""
Tests for eh_availability.py — the Elephant Hills website stop-sale reader.

The browser walk itself cannot run here, so these cover the two parts that decide whether a real
date gets blocked or missed: the interpretation of one month's DOM reading, and the day-61..365
window arithmetic. Both are the places a silent error would be expensive in one direction or the
other (see the module docstring), so they are pinned hard.

The DOM fixtures below are the real shape, taken from the live site on 2026-10-06: every day
button renders a FULL badge, and an open day's badge merely carries class "invisible".
"""
import datetime as dt

import pytest

import eh_availability as eh


# ----------------------------------------------------------------------
# helpers producing the real DOM reading shape
# ----------------------------------------------------------------------
def day(n, closed):
    """One day button as the in-page reader returns it. Both signals always agree on the live
    site; a test that wants them to disagree builds that explicitly."""
    return {"day": n, "disabled": closed, "badgeVisible": closed}


def month(label, total, closed_days):
    return {"label": label, "days": [day(n, n in closed_days) for n in range(1, total + 1)]}


# ----------------------------------------------------------------------
# interpret_month
# ----------------------------------------------------------------------
def test_reads_the_real_november_block_off_a_months_reading():
    """Nov 2026 showed 7, 8 and 9 closed and everything else open on the live site."""
    out = eh.interpret_month(month("Nov 2026", 30, {7, 8, 9}))
    assert out["closed_dates"] == [dt.date(2026, 11, 7), dt.date(2026, 11, 8), dt.date(2026, 11, 9)]
    assert out["total_days"] == 30


def test_a_month_with_nothing_closed_yields_no_dates():
    assert eh.interpret_month(month("Mar 2027", 31, set()))["closed_dates"] == []


def test_the_full_badge_text_alone_never_marks_a_day_closed():
    """Every day button on the live site contains the text FULL - on a sellable day it is just
    hidden by CSS. Reading the text instead of the two real signals would block the whole year."""
    reading = month("Jan 2027", 31, set())
    out = eh.interpret_month(reading)
    assert out["closed_dates"] == []


def test_signals_disagreeing_raises_rather_than_picking_one():
    """If the page is redesigned so disabled and the badge no longer agree, guessing either way is
    silently expensive, so the run must stop and reach a human."""
    reading = month("Nov 2026", 30, set())
    reading["days"][6] = {"day": 7, "disabled": True, "badgeVisible": False}
    with pytest.raises(eh.CalendarShapeChanged) as exc:
        eh.interpret_month(reading)
    assert "7" in str(exc.value)


def test_a_month_that_rendered_no_days_raises_instead_of_reading_as_all_open():
    with pytest.raises(eh.CalendarShapeChanged):
        eh.interpret_month({"label": "Nov 2026", "days": []})


def test_an_unparseable_month_header_raises():
    with pytest.raises(eh.CalendarShapeChanged):
        eh.interpret_month(month("November 2026", 30, {1}))


def test_a_day_number_the_month_cannot_hold_raises():
    reading = {"label": "Feb 2027", "days": [day(30, True)]}
    with pytest.raises(eh.CalendarShapeChanged):
        eh.interpret_month(reading)


# ----------------------------------------------------------------------
# days_to_ranges
# ----------------------------------------------------------------------
def test_consecutive_closed_days_collapse_into_one_range():
    """Three consecutive closed days are ONE stop sale in Travel Compositor, not three."""
    days = [dt.date(2026, 11, 7), dt.date(2026, 11, 8), dt.date(2026, 11, 9)]
    assert eh.days_to_ranges(days) == [{"start": "2026-11-07", "end": "2026-11-09"}]


def test_non_contiguous_days_stay_separate_ranges():
    days = [dt.date(2026, 11, 7), dt.date(2026, 12, 28)]
    assert eh.days_to_ranges(days) == [
        {"start": "2026-11-07", "end": "2026-11-07"},
        {"start": "2026-12-28", "end": "2026-12-28"},
    ]


def test_a_range_spanning_a_month_boundary_is_not_split():
    days = [dt.date(2026, 11, 30), dt.date(2026, 12, 1)]
    assert eh.days_to_ranges(days) == [{"start": "2026-11-30", "end": "2026-12-01"}]


def test_ranges_are_deduped_and_sorted_regardless_of_input_order():
    days = [dt.date(2026, 12, 2), dt.date(2026, 12, 1), dt.date(2026, 12, 1)]
    assert eh.days_to_ranges(days) == [{"start": "2026-12-01", "end": "2026-12-02"}]


def test_no_closed_days_produces_no_ranges():
    assert eh.days_to_ranges([]) == []


# ----------------------------------------------------------------------
# the day-61..365 window
# ----------------------------------------------------------------------
TODAY = dt.date(2026, 10, 7)


def test_day_one_is_tomorrow_so_day_61_is_today_plus_61():
    first, last = eh.scan_window(TODAY)
    assert first == dt.date(2026, 12, 7)
    assert last == dt.date(2027, 10, 7)


# ----------------------------------------------------------------------
# per-tour release periods (CONFIRMED, product owner, 2026-10-07)
# ----------------------------------------------------------------------
def test_a_45_day_release_starts_the_scan_on_day_46():
    """CNX-3 and CNX-4. Day 45 is the last unsellable day, so day 46 is the first candidate."""
    assert eh.first_scan_day(45) == 46


def test_a_60_day_release_starts_the_scan_on_day_61():
    """HKT-2 and HKT-3."""
    assert eh.first_scan_day(60) == 61


def test_no_release_days_configured_falls_back_to_the_safer_60_day_default():
    """A tour added to the config without release_days must not silently scan from day 1."""
    assert eh.first_scan_day(None) == eh.SCAN_FROM_DAY == eh.DEFAULT_RELEASE_DAYS + 1


def test_a_nonsense_release_length_raises_instead_of_being_coerced():
    """Silently treating a typo as 0 would scan from tomorrow and block unreleased dates."""
    with pytest.raises(ValueError):
        eh.first_scan_day("forty-five")
    with pytest.raises(ValueError):
        eh.first_scan_day(-5)


def test_the_45_day_release_opens_up_days_46_to_60_that_the_global_61_had_skipped():
    """The whole point of the per-tour change: a closure in that 15-day sliver is now written."""
    day46 = TODAY + dt.timedelta(days=46)
    day60 = TODAY + dt.timedelta(days=60)
    buckets = eh.split_by_window([day46, day60], today=TODAY, from_day=eh.first_scan_day(45))
    assert buckets["in_window"] == [day46, day60]
    assert buckets["skipped_near_term"] == []


def test_the_real_november_block_stays_skipped_even_at_a_45_day_release():
    """2026-11-07..09 on the CNX tours is day 31..33 - inside the 45-day release, so still not
    ours to sell and still correctly unwritten. Shortening the window must not drag it in."""
    days = [dt.date(2026, 11, 7), dt.date(2026, 11, 8), dt.date(2026, 11, 9)]
    buckets = eh.split_by_window(days, today=TODAY, from_day=eh.first_scan_day(45))
    assert buckets["in_window"] == []
    assert buckets["skipped_near_term"] == days


def test_day_45_is_still_skipped_at_a_45_day_release():
    day45 = TODAY + dt.timedelta(days=45)
    buckets = eh.split_by_window([day45], today=TODAY, from_day=eh.first_scan_day(45))
    assert buckets["in_window"] == []
    assert buckets["skipped_near_term"] == [day45]


def test_the_365_day_horizon_is_unchanged_by_a_shorter_release():
    day365 = TODAY + dt.timedelta(days=365)
    day366 = TODAY + dt.timedelta(days=366)
    buckets = eh.split_by_window([day365, day366], today=TODAY, from_day=eh.first_scan_day(45))
    assert buckets["in_window"] == [day365]


def test_every_configured_tour_declares_its_release_length():
    """A missing release_days is not a crash, it is a silently wrong window - so the shipped
    config is checked rather than trusted."""
    import json
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "eh_stop_sales_config.json")
    if not os.path.exists(path):
        pytest.skip("no live config in this checkout")
    cfg = json.load(open(path, encoding="utf-8"))
    expected = {"CNX-3": 45, "CNX-4": 45, "HKT-2": 60, "HKT-3": 60}
    for tour in cfg["tours"]:
        code = tour["tour_code"]
        assert tour.get("release_days") == expected[code], f"{code} has the wrong release_days"


def test_the_live_december_block_falls_inside_the_window_and_is_written():
    days = [dt.date(2026, 12, 28), dt.date(2026, 12, 29), dt.date(2026, 12, 30)]
    buckets = eh.split_by_window(days, today=TODAY)
    assert buckets["in_window"] == days
    assert buckets["skipped_near_term"] == []


def test_the_live_november_block_is_inside_the_release_window_and_is_deliberately_skipped():
    """CONFIRMED RULE (product owner, 2026-10-07): a single global day-61 start, accepting that a
    real closure inside days 1-60 is not written. Nov 7-9 2026 was exactly that case when the rule
    was set. It must be reported, never silently dropped, and never written."""
    days = [dt.date(2026, 11, 7), dt.date(2026, 11, 8), dt.date(2026, 11, 9)]
    buckets = eh.split_by_window(days, today=TODAY)
    assert buckets["in_window"] == []
    assert buckets["skipped_near_term"] == days


def test_the_release_window_dates_are_skipped_not_lost():
    """The whole point of returning them: the run log can show a human what the window hid."""
    days = [dt.date(2026, 10, 20), dt.date(2026, 11, 8)]
    buckets = eh.split_by_window(days, today=TODAY)
    assert buckets["skipped_near_term"] == days
    assert buckets["in_window"] == []


def test_elapsed_dates_are_never_treated_as_closures():
    """The calendar greys out days that have already passed. Those are not stop sales."""
    buckets = eh.split_by_window([dt.date(2026, 10, 1), dt.date(2026, 10, 6)], today=TODAY)
    assert buckets["skipped_past"] == [dt.date(2026, 10, 1), dt.date(2026, 10, 6)]
    assert buckets["in_window"] == []


def test_the_boundary_days_60_and_61_land_in_the_right_buckets():
    day60 = TODAY + dt.timedelta(days=60)
    day61 = TODAY + dt.timedelta(days=61)
    buckets = eh.split_by_window([day60, day61], today=TODAY)
    assert buckets["skipped_near_term"] == [day60]
    assert buckets["in_window"] == [day61]


def test_day_365_is_included_and_day_366_is_dropped():
    day365 = TODAY + dt.timedelta(days=365)
    day366 = TODAY + dt.timedelta(days=366)
    buckets = eh.split_by_window([day365, day366], today=TODAY)
    assert buckets["in_window"] == [day365]
    assert day366 not in buckets["in_window"]


def test_today_itself_is_not_a_stop_sale_candidate():
    buckets = eh.split_by_window([TODAY], today=TODAY)
    assert buckets["in_window"] == []
    assert buckets["skipped_near_term"] == [TODAY]


# ----------------------------------------------------------------------
# the module's own stated rules
# ----------------------------------------------------------------------
def test_the_scan_window_constants_are_the_confirmed_61_to_365():
    assert eh.SCAN_FROM_DAY == 61
    assert eh.SCAN_TO_DAY == 365


def test_the_module_documents_why_the_near_term_is_skipped_and_what_it_costs():
    """This consequence is invisible at the call site, so it must stay written down."""
    doc = eh.__doc__ or ""
    assert "release" in doc.lower()
    assert "2026-11-07" in doc


def test_parse_month_label_reads_the_calendars_own_header():
    assert eh.parse_month_label("Oct 2026") == (2026, 10)
    assert eh.parse_month_label("Jan 2027") == (2027, 1)
