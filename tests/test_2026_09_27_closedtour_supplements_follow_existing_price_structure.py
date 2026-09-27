"""Regression tests for a real product-owner request (2026-09-27), verbatim: "once any product
is being updated by new prices, can the app follow the price structure that exists already. so
it is most likely that the price structure is done correctly once it is online."

Before this, "Update an existing tour's details" with a freshly-uploaded price document did two
things wrong for ClosedTour Supplements:

  1. `_map_fetched_supplements` (app_helpers.py) hardcoded `"per_pax": True` when reverse-mapping
     the tour's own live GET response back into the editable shape, even though Per Pax is
     recoverable from the live numbers themselves (see `_infer_supplement_per_pax`).
  2. `_merge_extraction_over_baseline` treated "supplements" as one atomic field - a non-empty
     fresh list from a new AI extraction always won outright, discarding the live (already
     correct, already human-reviewed) Per Pax structure and silently dropping any live
     supplement the new price document just didn't happen to restate.

Both are fixed: `_infer_supplement_per_pax` recovers Per Pax from the live occupancy numbers'
division pattern, and `merge_closedtour_supplements_over_baseline` matches supplements by name
between the live baseline and the fresh extraction, carrying forward Per Pax (and any
un-restated supplement entirely) while letting the fresh price/dates/mandatory/on_request win
for a matched supplement.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app_helpers import (
    _infer_supplement_per_pax,
    _map_fetched_supplements,
    merge_closedtour_supplements_over_baseline,
)


# ======================================================================
# _infer_supplement_per_pax
# ======================================================================
def test_a_clean_division_pattern_is_recognized_as_per_pax_off():
    # 4900 total -> Single 4900, Double 2450 (ceil(2450)), Triple 1634 (ceil(1633.33...)),
    # Quadruple 1225 (ceil(1225)) - the real reported "Private Transfer Surcharge" example.
    assert _infer_supplement_per_pax(4900, 2450, 1634, 1225) is False


def test_all_four_equal_is_recognized_as_per_pax_on():
    assert _infer_supplement_per_pax(50, 50, 50, 50) is True


def test_all_zero_defaults_to_per_pax_on_nothing_to_infer_from():
    assert _infer_supplement_per_pax(0, 0, 0, 0) is True


def test_real_worked_example_213_total_three_nights():
    # rate $71 x 3 nights = $213 total for the room.
    assert _infer_supplement_per_pax(213, 107, 71, 54) is False


# ======================================================================
# _map_fetched_supplements now uses the inference instead of a hardcoded True
# ======================================================================
def test_map_fetched_supplements_infers_per_pax_off_for_a_divided_live_supplement():
    fetched = [{
        "translations": {"EN": {"name": "Private Transfer Surcharge - Chiang Mai Hotel & Airport"}},
        "price": {"singlePrice": 4900, "doublePrice": 2450, "triplePrice": 1634, "quadruplePrice": 1225},
        "mandatory": True,
        "onRequest": False,
        "modalityCodes": [],
    }]
    mapped = _map_fetched_supplements(fetched)
    assert len(mapped) == 1
    assert mapped[0]["per_pax"] is False
    assert mapped[0]["single_price"] == 4900
    assert mapped[0]["double_price"] == 2450


def test_map_fetched_supplements_infers_per_pax_on_for_a_flat_repeated_live_supplement():
    fetched = [{
        "translations": {"EN": {"name": "Optional Excursion"}},
        "price": {"singlePrice": 50, "doublePrice": 50, "triplePrice": 50, "quadruplePrice": 50},
        "mandatory": False,
        "onRequest": True,
        "modalityCodes": [],
    }]
    mapped = _map_fetched_supplements(fetched)
    assert mapped[0]["per_pax"] is True


# ======================================================================
# merge_closedtour_supplements_over_baseline
# ======================================================================
def _supp(name, price, per_pax, **extra):
    single = price
    if per_pax:
        double = triple = quadruple = price
    else:
        double, triple, quadruple = price / 2, price / 3, price / 4
    row = {
        "name": name, "price": price, "single_price": single, "double_price": double,
        "triple_price": triple, "quadruple_price": quadruple, "per_pax": per_pax,
        "mandatory": False, "on_request": False, "travel_start_date": "", "travel_end_date": "",
    }
    row.update(extra)
    return row


def test_matched_supplement_keeps_live_per_pax_but_takes_fresh_price():
    baseline = [_supp("Private Transfer Surcharge - Chiang Mai Hotel & Airport", 4900, False, mandatory=True)]
    fresh = [_supp("Private Transfer Surcharge - Chiang Mai Hotel & Airport", 5200, True)]  # AI re-guessed True
    merged = merge_closedtour_supplements_over_baseline(baseline, fresh)
    assert len(merged) == 1
    row = merged[0]
    assert row["per_pax"] is False  # carried forward from the live baseline, not the AI's fresh guess
    assert row["price"] == 5200  # the new price wins - that's the whole point of a price update
    assert row["single_price"] == 5200.0
    assert row["double_price"] == 2600.0
    assert row["triple_price"] == 1734.0  # ceil(1733.33...)
    assert row["quadruple_price"] == 1300.0


def test_matching_is_case_and_whitespace_insensitive():
    baseline = [_supp("  Single Room Surcharge (SRS)  ", 3660, False)]
    fresh = [_supp("single room surcharge (srs)", 3800, False)]
    merged = merge_closedtour_supplements_over_baseline(baseline, fresh)
    assert len(merged) == 1
    assert merged[0]["price"] == 3800


def test_unrestated_live_supplement_is_carried_forward_unchanged_not_dropped():
    baseline = [
        _supp("Private Transfer Surcharge - Chiang Mai Hotel & Airport", 4900, False),
        _supp("Peak Season Surcharge (Christmas/New Year, Easter, Summer)", 957, True, mandatory=True,
              travel_start_date="2026-12-20", travel_end_date="2027-01-10"),
    ]
    # Fresh price document only mentions the transfer surcharge - the peak season surcharge isn't
    # restated at all.
    fresh = [_supp("Private Transfer Surcharge - Chiang Mai Hotel & Airport", 5200, True)]
    merged = merge_closedtour_supplements_over_baseline(baseline, fresh)
    names = {row["name"] for row in merged}
    assert names == {
        "Private Transfer Surcharge - Chiang Mai Hotel & Airport",
        "Peak Season Surcharge (Christmas/New Year, Easter, Summer)",
    }
    peak = next(r for r in merged if r["name"].startswith("Peak Season"))
    assert peak["price"] == 957
    assert peak["travel_start_date"] == "2026-12-20"


def test_a_genuinely_new_fresh_supplement_with_no_live_match_is_kept_as_extracted():
    baseline = [_supp("Private Transfer Surcharge - Chiang Mai Hotel & Airport", 4900, False)]
    fresh = [_supp("Brand New Add-on Never Seen Before", 100, True)]
    merged = merge_closedtour_supplements_over_baseline(baseline, fresh)
    names = {row["name"] for row in merged}
    assert "Brand New Add-on Never Seen Before" in names
    new_row = next(r for r in merged if r["name"] == "Brand New Add-on Never Seen Before")
    assert new_row["per_pax"] is True
    assert new_row["price"] == 100


def test_empty_baseline_or_fresh_does_not_crash():
    assert merge_closedtour_supplements_over_baseline(None, None) == []
    assert merge_closedtour_supplements_over_baseline([], [_supp("X", 10, True)])[0]["name"] == "X"
    assert merge_closedtour_supplements_over_baseline([_supp("X", 10, True)], [])[0]["name"] == "X"


# ======================================================================
# Wiring: app.py's two "update_tour" extraction paths must both call the dedicated merge -
# _merge_extraction_over_baseline alone (shared with Ticket, which has no per-supplement concept)
# is not enough, since it treats "supplements" as one atomic field.
# ======================================================================
def test_both_update_tour_extraction_paths_call_the_supplement_merge():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")).read()
    assert src.count("data[\"supplements\"] = merge_closedtour_supplements_over_baseline(") == 2
    assert "merge_closedtour_supplements_over_baseline," in src  # imported from app_helpers
