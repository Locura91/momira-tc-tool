"""Tests for the 2026-09-11 read-only Transport price consistency report (product owner, right
after the manual %/absolute adjustment shipped): "can the app help then, to identify price errors
at least for contract transport. The logic is known, but once a year we must review the new
prices and we must identify price differences for the transport modalities. No upload from the
app, but the app must be able to help and to calculate what the modalities usually must be."

Confirmed model (AskUserQuestion, same day): the "usual" pattern is a fixed ABSOLUTE markup (not
a ratio) between a route's vehicle/base bracket and each other bracket, compared only within the
SAME supplier's own routes (never pooled across suppliers), returned as a ranked list (no hard
pass/fail threshold) sorted by |deviation| descending.
"""
import price_refresh


def _option(code, min_pax, max_pax, unit_price, name=None, fetch_failed=False):
    return {"code": code, "min_pax": min_pax, "max_pax": max_pax, "unit_price": unit_price,
           "name": name or code, "fetch_failed": fetch_failed, "raw": {}}


def _route(name, options, kind=price_refresh.KIND_TRANSPORT, route_id=None):
    return {"id": route_id or name, "name": name, "kind": kind, "options": options, "raw": {}}


# ----------------------------------------------------------------------
# The core arithmetic: smallest-pax bracket is the vehicle, supplement = modality - vehicle
# ----------------------------------------------------------------------

def test_smallest_bracket_is_treated_as_the_vehicle_and_supplement_is_the_difference():
    routes = [
        _route("A", [_option("S", 1, 3, 35.0), _option("H", 1, 8, 45.0)]),
        _route("B", [_option("S", 1, 3, 40.0), _option("H", 1, 8, 50.0)]),
    ]
    report = price_refresh.transport_price_consistency_report(routes)
    # Both routes agree exactly: supplement is always 10.0 for the 1-8 pax bracket.
    assert len(report) == 2
    for row in report:
        assert row["bracket"] == (1, 8)
        assert row["supplement"] == 10.0
        assert row["typical_supplement"] == 10.0
        assert row["deviation"] == 0.0


def test_a_route_whose_supplement_is_way_off_gets_the_biggest_deviation_and_sorts_first():
    routes = [
        _route("Normal 1", [_option("S", 1, 3, 35.0), _option("H", 1, 8, 45.0)]),   # +10
        _route("Normal 2", [_option("S", 1, 3, 40.0), _option("H", 1, 8, 51.0)]),   # +11
        _route("Normal 3", [_option("S", 1, 3, 30.0), _option("H", 1, 8, 39.0)]),   # +9
        _route("Suspect", [_option("S", 1, 3, 35.0), _option("H", 1, 8, 35.0)]),    # +0 - way off
    ]
    report = price_refresh.transport_price_consistency_report(routes)
    assert report[0]["route_name"] == "Suspect"
    assert report[0]["supplement"] == 0.0
    # median of [0, 9, 10, 11] with even count -> average of the two middle values (9, 10) = 9.5
    assert report[0]["typical_supplement"] == 9.5
    assert report[0]["deviation"] == -9.5


def test_deviation_pct_is_none_when_typical_is_zero_no_division_by_zero_crash():
    routes = [
        _route("A", [_option("S", 1, 3, 35.0), _option("H", 1, 8, 35.0)]),   # supplement 0
        _route("B", [_option("S", 1, 3, 40.0), _option("H", 1, 8, 40.5)]),   # supplement 0.5
    ]
    report = price_refresh.transport_price_consistency_report(routes)
    # median of [0, 0.5] -> 0.25, still nonzero, so let's force an exact zero median instead:
    routes2 = [
        _route("A", [_option("S", 1, 3, 35.0), _option("H", 1, 8, 35.0)]),  # supplement 0
        _route("B", [_option("S", 1, 3, 40.0), _option("H", 1, 8, 40.0)]),  # supplement 0
    ]
    report2 = price_refresh.transport_price_consistency_report(routes2)
    assert all(r["typical_supplement"] == 0.0 for r in report2)
    assert all(r["deviation_pct"] is None for r in report2)


# ----------------------------------------------------------------------
# Grouping is per bracket SIGNATURE, and only across routes that actually share it
# ----------------------------------------------------------------------

def test_a_bracket_only_one_route_has_is_skipped_entirely_nothing_to_compare():
    routes = [
        _route("Only one with this bracket", [_option("S", 1, 3, 35.0), _option("H", 1, 8, 45.0)]),
    ]
    report = price_refresh.transport_price_consistency_report(routes)
    assert report == []


def test_different_bracket_signatures_are_compared_separately():
    routes = [
        _route("A", [_option("S", 1, 3, 35.0), _option("H", 1, 8, 45.0)]),
        _route("B", [_option("S", 1, 3, 40.0), _option("H", 1, 8, 50.0)]),
        _route("C", [_option("S", 1, 4, 35.0), _option("V", 1, 12, 70.0)]),
        _route("D", [_option("S", 1, 4, 40.0), _option("V", 1, 12, 74.0)]),
    ]
    report = price_refresh.transport_price_consistency_report(routes)
    brackets = {r["bracket"] for r in report}
    assert brackets == {(1, 8), (1, 12)}
    # The (1,12) group's typical supplement (35, 34 -> median irrelevant here, just not mixed
    # in with the (1,8) group's ~10 values).
    for row in report:
        if row["bracket"] == (1, 12):
            assert row["typical_supplement"] in (34.0, 35.0, 34.5)


def test_a_route_with_only_one_live_bracket_is_skipped_nothing_to_compare_within_the_route():
    routes = [
        _route("Single bracket", [_option("S", 1, 3, 35.0)]),
        _route("Two brackets", [_option("S", 1, 3, 35.0), _option("H", 1, 8, 45.0)]),
    ]
    report = price_refresh.transport_price_consistency_report(routes)
    assert all(r["route_name"] != "Single bracket" for r in report)


def test_a_route_with_three_brackets_compares_every_non_vehicle_bracket_to_the_smallest():
    routes = [
        _route("A", [_option("S", 1, 3, 35.0), _option("H", 1, 8, 45.0), _option("V", 1, 12, 70.0)]),
        _route("B", [_option("S", 1, 3, 40.0), _option("H", 1, 8, 51.0), _option("V", 1, 12, 76.0)]),
    ]
    report = price_refresh.transport_price_consistency_report(routes)
    brackets = {r["bracket"] for r in report}
    assert brackets == {(1, 8), (1, 12)}
    assert not any(r["bracket"] == (1, 3) for r in report)  # the vehicle bracket never appears


def test_fetch_failed_options_are_excluded_from_both_the_vehicle_and_comparison_pools():
    routes = [
        _route("A", [_option("S", 1, 3, 35.0), _option("H", 1, 8, 45.0, fetch_failed=True)]),
        _route("B", [_option("S", 1, 3, 40.0), _option("H", 1, 8, 50.0)]),
    ]
    report = price_refresh.transport_price_consistency_report(routes)
    # Route A effectively has only one usable bracket once the failed option is dropped, so it
    # contributes nothing - and with only route B left for (1,8), there's no baseline either.
    assert report == []


def test_transfer_routes_are_ignored_entirely_transport_only_report():
    routes = [
        _route("Transfer route", [_option("S", 1, 3, 35.0), _option("H", 1, 8, 45.0)],
              kind=price_refresh.KIND_TRANSFER),
        _route("Transfer route 2", [_option("S", 1, 3, 40.0), _option("H", 1, 8, 50.0)],
              kind=price_refresh.KIND_TRANSFER),
    ]
    report = price_refresh.transport_price_consistency_report(routes)
    assert report == []


# ----------------------------------------------------------------------
# Sorting
# ----------------------------------------------------------------------

def test_rows_are_sorted_by_absolute_deviation_descending():
    routes = [
        _route("A", [_option("S", 1, 3, 35.0), _option("H", 1, 8, 45.0)]),   # +10
        _route("B", [_option("S", 1, 3, 35.0), _option("H", 1, 8, 46.0)]),   # +11
        _route("C", [_option("S", 1, 3, 35.0), _option("H", 1, 8, 30.0)]),   # -5, biggest gap
        _route("D", [_option("S", 1, 3, 35.0), _option("H", 1, 8, 44.0)]),   # +9
    ]
    report = price_refresh.transport_price_consistency_report(routes)
    deviations = [abs(r["deviation"]) for r in report]
    assert deviations == sorted(deviations, reverse=True)
    assert report[0]["route_name"] == "C"
