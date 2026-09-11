"""Regression tests for the 2026-09-11 fix to price_refresh.py's handling of a modality that
carries MORE THAN ONE price entry, each its own non-overlapping validity window.

Product owner, verbatim, confirming the pricing model this app must understand: "there is a
difference between base price (per Vehicle or per Pax) and if there is a price supplement, it
can have a price supplement for different modalities and it can have different period of times
but the time can not overlap within the same modality. Final price = Base price for all
modalites + price supplement if there is a price supplement per modality added." Confirmed via
follow-up: this already happens on real live transports today (a standard rate now plus an
already-scheduled future/peak-season rate on the SAME modality).

CONFIRMED REAL GAP this fixes: `rebuild_prices` used to REPLACE a modality's entire `prices`
list with a single new-or-updated entry on every write, which would have silently DELETED every
other dated period the moment any price on that modality changed - a genuine, dangerous
data-loss risk once a modality legitimately carries more than one entry. Same root class of bug
as the `option code` pinning fix earlier the same day, just one layer deeper (the array
CONTENTS, not which option the array belongs to).

Scope note (deliberately NOT covered here, and said so to the product owner): matching is
against TODAY's date, not yet against a validity window the SOURCE DOCUMENT itself states - the
AI rate-sheet reading pipeline doesn't extract per-bracket dates yet. This fix's job is
narrower and unconditional: whichever period a refresh touches, every OTHER period on that
modality must survive it, regardless of how that period gets chosen.
"""
from datetime import date, timedelta

import price_refresh

TODAY = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()
NEXT_YEAR = (date.today() + timedelta(days=365)).isoformat()
TWO_YEARS = (date.today() + timedelta(days=730)).isoformat()


def _option_with_periods(code, min_pax, max_pax, periods):
    """periods: list of {"startDate", "endDate", "adultPriceSupplement"} dicts, already
    non-overlapping and sorted - exactly the real live shape being tested."""
    return {"code": code, "min_pax": min_pax, "max_pax": max_pax,
            "unit_price": 0.0, "name": code, "raw": {"code": code, "prices": list(periods)}}


def _base_option():
    # A companion zero-supplement modality, present in every fixture below purely so the
    # option under test is never ITSELF the sole base-selection candidate (see
    # price_refresh._current_base_option) - without this, a lone option always ends up chosen
    # as its own base, which trivially zeroes its own supplement regardless of what's being
    # tested here and has nothing to do with the multi-period fix under test. Deliberately the
    # WIDEST bracket of any fixture below, so it always wins _current_base_option's ambiguity
    # fallback too (relevant when the option under test ALSO currently has zero supplement -
    # e.g. "never had a price entry before" - which would otherwise tie).
    return {"code": "BASE", "min_pax": 1, "max_pax": 99, "unit_price": 100.0, "name": "BASE",
            "raw": {"code": "BASE", "prices": []}}


def _route(options, base_adult=100.0):
    return {"id": "T1", "name": "Cairo - Luxor", "kind": None, "price_per_pax": True,
            "options": [_base_option()] + options,
            "raw": {"id": "T1", "pricePerPax": True, "baseAdultPrice": base_adult,
                    "baseChildrenPrice": 0.0, "baseInfantPrice": 0.0,
                    "startDate": "2020-01-01", "endDate": "2099-12-31"}}


# ----------------------------------------------------------------------
# option_unit_price / _select_price_entry - reading picks the period covering today
# ----------------------------------------------------------------------

def test_reads_the_period_covering_today_not_just_the_last_one_in_the_list():
    option = {"prices": [
        {"startDate": "2020-01-01", "endDate": YESTERDAY, "adultPriceSupplement": 999.0},  # expired
        {"startDate": TODAY, "endDate": NEXT_YEAR, "adultPriceSupplement": 20.0},  # active
    ]}
    assert price_refresh.option_unit_price(option, base_adult=100.0) == 120.0


def test_falls_back_to_the_last_entry_when_none_are_dated_same_as_before_this_fix():
    option = {"prices": [{"adultPriceSupplement": 15.0}]}
    assert price_refresh.option_unit_price(option, base_adult=100.0) == 115.0


def test_falls_back_to_the_last_entry_when_no_period_covers_today():
    # An honest edge case: a gap in the calendar. Same fallback as always - never crashes.
    option = {"prices": [
        {"startDate": "2020-01-01", "endDate": YESTERDAY, "adultPriceSupplement": 5.0},
        {"startDate": NEXT_YEAR, "endDate": TWO_YEARS, "adultPriceSupplement": 40.0},
    ]}
    assert price_refresh.option_unit_price(option, base_adult=100.0) == 140.0  # last entry


# ----------------------------------------------------------------------
# rebuild_prices - writing a new price must NEVER delete a sibling period
# ----------------------------------------------------------------------

def test_updating_todays_period_leaves_a_future_scheduled_period_completely_untouched():
    # rebuild_prices JSON round-trips the whole payload (json.loads(json.dumps(...))), so the
    # OUTPUT dicts are never the same object as the INPUT fixture dicts - match by content
    # (the future period's own supplement, 999.0, which must survive verbatim), not by identity.
    option = _option_with_periods("A", 1, 4, [
        {"startDate": "2020-01-01", "endDate": None, "adultPriceSupplement": 20.0},
        {"startDate": NEXT_YEAR, "endDate": TWO_YEARS, "adultPriceSupplement": 999.0},
    ])
    route = _route([option])
    payloads = price_refresh.rebuild_prices(route, {"A": 150.0})  # base 100 + new supplement 50
    by_code = {o["code"]: o for o in payloads["options"]}
    prices = by_code["A"]["payload"]["prices"]
    assert len(prices) == 2
    future = next(p for p in prices if p["adultPriceSupplement"] == 999.0)
    assert future["startDate"] == NEXT_YEAR and future["endDate"] == TWO_YEARS  # byte-for-byte untouched
    todays = next(p for p in prices if p["adultPriceSupplement"] != 999.0)
    assert todays["adultPriceSupplement"] == 50.0


def test_three_periods_only_the_one_covering_today_changes():
    past = {"startDate": "2020-01-01", "endDate": YESTERDAY, "adultPriceSupplement": 5.0}
    current = {"startDate": TODAY, "endDate": NEXT_YEAR, "adultPriceSupplement": 10.0}
    future = {"startDate": NEXT_YEAR, "endDate": TWO_YEARS, "adultPriceSupplement": 30.0}
    option = _option_with_periods("A", 1, 4, [past, current, future])
    route = _route([option])
    payloads = price_refresh.rebuild_prices(route, {"A": 200.0})  # base 100 + new supplement 100
    by_code = {o["code"]: o for o in payloads["options"]}
    prices = by_code["A"]["payload"]["prices"]
    assert past in prices
    assert future in prices
    assert len(prices) == 3
    updated = [p for p in prices if p not in (past, future)][0]
    assert updated["adultPriceSupplement"] == 100.0
    assert updated["startDate"] == TODAY and updated["endDate"] == NEXT_YEAR  # own dates preserved


def test_a_period_dropping_to_exactly_base_price_removes_only_that_entry_not_its_siblings():
    future_period = {"startDate": NEXT_YEAR, "endDate": TWO_YEARS, "adultPriceSupplement": 999.0}
    option = _option_with_periods("A", 1, 4, [
        {"startDate": "2020-01-01", "endDate": None, "adultPriceSupplement": 20.0},
        future_period,
    ])
    route = _route([option])
    payloads = price_refresh.rebuild_prices(route, {"A": 100.0})  # == base -> today's period drops
    by_code = {o["code"]: o for o in payloads["options"]}
    assert by_code["A"]["payload"]["prices"] == [future_period]


def test_single_period_with_no_dates_still_behaves_exactly_as_before_this_fix():
    # The common real case (most transports today): one entry, no dates at all. Must still end
    # up as exactly ONE entry (not multiplied or dropped) - the missing-date backfill itself is
    # pre-existing behavior from the earlier same-day fix (see test_2026_09_11_price_refresh_
    # base_option_and_price_dates.py), not something this multi-period fix changes.
    option = {"code": "A", "min_pax": 1, "max_pax": 4, "unit_price": 120.0, "name": "A",
              "raw": {"code": "A", "prices": [{"adultPriceSupplement": 20.0}]}}
    route = _route([option])
    payloads = price_refresh.rebuild_prices(route, {"A": 150.0})
    by_code = {o["code"]: o for o in payloads["options"]}
    prices = by_code["A"]["payload"]["prices"]
    assert len(prices) == 1
    assert prices[0]["adultPriceSupplement"] == 50.0


def test_a_brand_new_supplement_on_a_previously_supplement_free_option_still_gets_real_dates():
    option = _option_with_periods("A", 1, 4, [])  # no price entries at all yet
    route = _route([option])
    payloads = price_refresh.rebuild_prices(route, {"A": 130.0})
    by_code = {o["code"]: o for o in payloads["options"]}
    entry = by_code["A"]["payload"]["prices"][0]
    assert entry["adultPriceSupplement"] == 30.0
    assert entry["startDate"] == "2020-01-01"  # parent's own dates, never invented
    assert entry["endDate"] == "2099-12-31"
