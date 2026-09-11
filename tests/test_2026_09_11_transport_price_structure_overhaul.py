"""Regression tests for the 2026-09-11 Transport bulk price-refresh overhaul, built from the
product owner's own confirmed 8-question price-structure model (asked after a real "HUGE error"
report against TRANSPORT-418604 - see build_proposals' own comment for the full transcript
reference) and their final explicit authorization: "yes build it now please".

Five confirmed rules, each covered below:
  1. Mandatory human designation of the vehicle/base modality - no auto-detect fallback at all
     once a route has two or more live brackets (covered in the other 2026-09-11 test files that
     were updated for this overhaul; this file focuses on the four rules below, which are new).
  2. Exactly one field written per round: the shared Vehicle/baseAdultPrice field, OR the touched
     modality's own supplement - never both, never any other modality's data.
  3. A computed supplement of exactly 0 is a HARD BLOCK, not a warning.
  4. Full seasonal/multi-period extraction: a document may state more than one price for the same
     bracket, each its own date range, and every stated season must be written to its own row.
"""
import price_refresh


# ----------------------------------------------------------------------
# bracket_periods_for - extracting every season the document states for one bracket
# ----------------------------------------------------------------------

def _finding_with_seasons(seasons, flat_price=None, min_pax=1, max_pax=8, minimum_pax=1):
    bracket = {"min_pax": min_pax, "max_pax": max_pax,
               "price": flat_price if flat_price is not None else (seasons[0]["price"] if seasons else 0.0),
               "child_price": None, "infant_price": None, "seasons": seasons}
    return {"found": True, "minimum_pax": minimum_pax, "currency": "USD", "confidence": "high",
            "note": "", "matched_row": "row", "brackets": [bracket]}


def test_no_seasons_returns_exactly_one_flat_period_with_no_dates():
    finding = _finding_with_seasons([], flat_price=140.0)
    periods = price_refresh.bracket_periods_for(finding, 1, 8, 1)
    assert periods == [{"price": 140.0, "child_price": None, "infant_price": None,
                        "start_date": None, "end_date": None}]


def test_three_seasons_are_all_returned_each_with_its_own_dates():
    # CONFIRMED REAL GAP (product owner, 2026-09-11): "Hiace had three supplements because of a
    # high season and peak season time, but the app never filled out the actual prices."
    seasons = [
        {"start_date": "2026-08-25", "end_date": "2026-09-30", "price": 200.0,
         "child_price": None, "infant_price": None},
        {"start_date": "2026-10-01", "end_date": "2027-04-30", "price": 220.0,
         "child_price": None, "infant_price": None},
        {"start_date": "2027-05-01", "end_date": "2049-12-31", "price": 240.0,
         "child_price": None, "infant_price": None},
    ]
    finding = _finding_with_seasons(seasons)
    periods = price_refresh.bracket_periods_for(finding, 1, 8, 1)
    assert len(periods) == 3
    assert {p["price"] for p in periods} == {200.0, 220.0, 240.0}
    assert {(p["start_date"], p["end_date"]) for p in periods} == {
        ("2026-08-25", "2026-09-30"), ("2026-10-01", "2027-04-30"), ("2027-05-01", "2049-12-31")}


def test_solo_bracket_multiplier_applies_to_every_season_price():
    seasons = [{"start_date": "2026-01-01", "end_date": "2026-12-31", "price": 30.0,
               "child_price": None, "infant_price": None}]
    finding = {"found": True, "minimum_pax": 2, "currency": "USD", "confidence": "high",
              "note": "", "matched_row": "row", "brackets": [
                  {"min_pax": 2, "max_pax": 4, "price": 30.0, "child_price": None,
                   "infant_price": None, "seasons": seasons}]}
    periods = price_refresh.bracket_periods_for(finding, 1, 1, 2, price_per_pax=True)
    assert periods == [{"price": 60.0, "child_price": None, "infant_price": None,
                        "start_date": "2026-01-01", "end_date": "2026-12-31"}]


def test_no_match_returns_empty_exactly_like_bracket_price_for():
    finding = _finding_with_seasons([], flat_price=100.0, min_pax=1, max_pax=3)
    assert price_refresh.bracket_periods_for(finding, 5, 9, 1) == []


def test_not_found_returns_empty():
    finding = {"found": False, "brackets": []}
    assert price_refresh.bracket_periods_for(finding, 1, 8, 1) == []


# ----------------------------------------------------------------------
# lookup_prices - the AI's seasons array survives parsing
# ----------------------------------------------------------------------

def test_lookup_prices_carries_seasons_through_to_the_finding(monkeypatch):
    route = {"options": [{"min_pax": 1, "max_pax": 8}], "currency": "USD",
             "departure_name": "A", "arrival_name": "B", "price_per_pax": False}

    def _fake_call(*a, **kw):
        return {"routes": [{
            "index": 0, "found": True, "matched_row": "row", "currency": "USD",
            "minimum_pax": 1, "confidence": "high", "note": "",
            "brackets": [{"min_pax": 1, "max_pax": 8, "price": 200.0,
                         "seasons": [
                             {"start_date": "2026-10-01", "end_date": "2027-04-30", "price": 220.0},
                             {"start_date": "bad", "end_date": "also-bad", "price": -1},  # dropped
                         ]}],
        }]}
    monkeypatch.setattr(price_refresh.ai_extractor, "_call_claude", _fake_call)
    findings = price_refresh.lookup_prices([route], "some document text")
    seasons = findings[0]["brackets"][0]["seasons"]
    assert len(seasons) == 1  # the negative-price season is dropped, same rule as top-level price
    assert seasons[0]["start_date"] == "2026-10-01"
    assert seasons[0]["price"] == 220.0


# ----------------------------------------------------------------------
# build_proposals - the full end-to-end write_kind / zero-supplement / seasonal model
# ----------------------------------------------------------------------

def _route_two_modalities(sedan_price=100.0, hiace_price=140.0, override=(1, 3)):
    route = {
        "id": "TRANSPORT-1", "name": "Cairo - Luxor", "kind": None, "price_per_pax": False,
        "currency": "USD",
        "options": [
            {"code": "Sedan", "min_pax": 1, "max_pax": 3, "unit_price": sedan_price,
             "name": "Sedan", "raw": {"code": "Sedan", "prices": []}},
            {"code": "Hiace", "min_pax": 1, "max_pax": 8, "unit_price": hiace_price,
             "name": "Hiace", "raw": {"code": "Hiace",
                                      "prices": [{"adultPriceSupplement": hiace_price - sedan_price}]}},
        ],
        "raw": {"id": "TRANSPORT-1", "pricePerPax": False, "vehiclePrice": sedan_price,
                "baseAdultPrice": 0.0, "baseChildrenPrice": 0.0, "baseInfantPrice": 0.0,
                "startDate": "2026-01-01", "endDate": "2049-12-31"},
    }
    if override is not None:
        route["base_bracket_override"] = override
    return route


def test_two_or_more_modalities_with_no_designation_is_blocked_and_writable():
    route = _route_two_modalities(override=None)
    finding = _finding_with_seasons([], flat_price=110.0, min_pax=1, max_pax=3)
    proposal = price_refresh.build_proposals([route], {0: finding})[0]
    assert proposal["status"] == "blocked_needs_base_designation"
    assert proposal["changes"] == []
    assert proposal["accepted"] is False


def test_a_computed_zero_supplement_is_a_hard_block():
    # CONFIRMED ABSOLUTE RULE (product owner, 2026-09-11): "Supplement cannot be 0, if it is 0
    # there is an error." Hiace priced at exactly the Sedan (vehicle) price - never legitimate.
    route = _route_two_modalities(sedan_price=100.0, hiace_price=140.0, override=(1, 3))
    finding = _finding_with_seasons([], flat_price=100.0, min_pax=1, max_pax=8)  # == vehicle price
    proposal = price_refresh.build_proposals([route], {0: finding}, scoped_brackets=[(1, 8)])[0]
    assert proposal["status"] == "blocked_zero_supplement"
    assert proposal["accepted"] is False
    assert len(proposal["zero_supplement_errors"]) == 1
    assert proposal["zero_supplement_errors"][0]["code"] == "Hiace"


def test_a_seasonal_supplement_document_produces_one_change_per_season():
    seasons = [
        {"start_date": "2026-08-25", "end_date": "2026-09-30", "price": 200.0,
         "child_price": None, "infant_price": None},
        {"start_date": "2026-10-01", "end_date": "2027-04-30", "price": 220.0,
         "child_price": None, "infant_price": None},
        {"start_date": "2027-05-01", "end_date": "2049-12-31", "price": 240.0,
         "child_price": None, "infant_price": None},
    ]
    route = _route_two_modalities(sedan_price=100.0, hiace_price=140.0, override=(1, 3))
    finding = _finding_with_seasons(seasons, min_pax=1, max_pax=8)
    proposal = price_refresh.build_proposals([route], {0: finding}, scoped_brackets=[(1, 8)])[0]
    assert proposal["status"] == "changed"
    hiace_changes = [c for c in proposal["changes"] if c["code"] == "Hiace"]
    assert len(hiace_changes) == 3
    assert {c["new"] for c in hiace_changes} == {200.0, 220.0, 240.0}
    assert all(c["write_kind"] == "supplement" for c in hiace_changes)
    assert {(c["start_date"], c["end_date"]) for c in hiace_changes} == {
        ("2026-08-25", "2026-09-30"), ("2026-10-01", "2027-04-30"), ("2027-05-01", "2049-12-31")}


def test_a_vehicle_bracket_change_still_produces_a_single_flat_change():
    route = _route_two_modalities(sedan_price=100.0, hiace_price=140.0, override=(1, 3))
    finding = _finding_with_seasons([], flat_price=120.0, min_pax=1, max_pax=3)
    proposal = price_refresh.build_proposals([route], {0: finding}, scoped_brackets=[(1, 3)])[0]
    assert proposal["status"] == "changed"
    assert len(proposal["changes"]) == 1
    assert proposal["changes"][0]["code"] == "Sedan"
    assert proposal["changes"][0]["write_kind"] == "vehicle"
    assert proposal["changes"][0]["start_date"] is None


def test_rebuild_prices_writes_every_seasonal_period_for_the_touched_modality():
    seasons = [
        {"start_date": "2026-08-25", "end_date": "2026-09-30", "price": 200.0,
         "child_price": None, "infant_price": None},
        {"start_date": "2026-10-01", "end_date": "2027-04-30", "price": 220.0,
         "child_price": None, "infant_price": None},
    ]
    route = _route_two_modalities(sedan_price=100.0, hiace_price=140.0, override=(1, 3))
    finding = _finding_with_seasons(seasons, min_pax=1, max_pax=8)
    proposal = price_refresh.build_proposals([route], {0: finding}, scoped_brackets=[(1, 8)])[0]
    payloads = price_refresh.rebuild_prices(route, proposal["changes"])
    assert payloads["write_kind"] == "supplement"
    hiace_payload = next(o for o in payloads["options"] if o["code"] == "Hiace")
    prices = hiace_payload["payload"]["prices"]
    assert len(prices) == 2  # both seasons written, the old flat entry replaced
    by_dates = {(p["startDate"], p["endDate"]): p["adultPriceSupplement"] for p in prices}
    assert by_dates[("2026-08-25", "2026-09-30")] == round(200.0 - 100.0, 2)
    assert by_dates[("2026-10-01", "2027-04-30")] == round(220.0 - 100.0, 2)


def test_rebuild_prices_preserves_a_period_this_round_never_touched():
    # A season the document doesn't mention (or belongs to a different round) survives
    # byte-for-byte - the same multi-period preservation guarantee built earlier the same day,
    # now extended rather than reverted.
    route = _route_two_modalities(sedan_price=100.0, hiace_price=140.0, override=(1, 3))
    route["options"][1]["raw"]["prices"] = [
        {"startDate": "2020-01-01", "endDate": "2026-08-24", "adultPriceSupplement": 999.0},
    ]
    change = {"code": "Hiace", "min_pax": 1, "max_pax": 8, "old": None, "new": 220.0,
             "name": "Hiace", "write_kind": "supplement",
             "start_date": "2026-10-01", "end_date": "2027-04-30"}
    payloads = price_refresh.rebuild_prices(route, [change])
    hiace_payload = next(o for o in payloads["options"] if o["code"] == "Hiace")
    prices = hiace_payload["payload"]["prices"]
    assert len(prices) == 2
    untouched = next(p for p in prices if p["adultPriceSupplement"] == 999.0)
    assert untouched["startDate"] == "2020-01-01" and untouched["endDate"] == "2026-08-24"
    new_one = next(p for p in prices if p["adultPriceSupplement"] != 999.0)
    assert new_one["startDate"] == "2026-10-01" and new_one["endDate"] == "2027-04-30"
    assert new_one["adultPriceSupplement"] == round(220.0 - 100.0, 2)
