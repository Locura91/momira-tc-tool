"""Tests for builder.build_hotel_rate_payloads' carry-forward behavior (full-app audit HIGH,
2026-09-01, was builder.py:4294-4305).

CONFIRMED BUG: a rate UPDATE (PUT) is a full-body replace. Any live season/offer/supplement/
stop-sale a rate currently has, that the fresh document simply doesn't restate (a Winter-only
update naturally says nothing about the already-live Summer season, an unrelated linked offer,
an unrelated supplement, an existing Christmas blackout), used to vanish from the published rate
entirely. Verified real example: a Winter-only update wiped live Summer pricing, a linked offer,
a supplement, and a Christmas blackout.

The fix: every existing season not matched by this run's fresh data, and every existing offer/
supplement/stop-sale not already present in this run's own fresh data, is carried forward
unchanged - the same "don't destroy what a partial update didn't restate" pattern already used
elsewhere (hotel room/meal-plan carry-forward, Transfer/Transport's _locked_on_update).

build_hotel_rate_payloads is a pure function (no geolocation/network needed), so it's tested
directly here, same approach as test_2026_08_30_hotel_matcher_and_room_merge.py.
"""
from builder import build_hotel_rate_payloads


def _base_existing_snapshot(**overrides):
    snapshot = {
        "rates": [{
            "id": 501,
            "name": "Standard Rate",
            "offers": ["AUTO_offer_summer"],
            "supplements": ["AUTO_supp_towel"],
            "seasons": [{
                "id": 9001,
                "name": "Summer Season",
                "dateRanges": [{"start": "2027-06-01", "end": "2027-08-31"}],
                "mealPlans": [],
                "seasonRoomPrices": [],
                "minimumStay": 1,
                "priceType": "DISTRIBUTION",
            }],
            "stopSales": [{
                "roomName": "Deluxe Room",
                "stopSales": [{"start": "2027-12-24", "end": "2027-12-26"}],
            }],
        }],
    }
    snapshot.update(overrides)
    return snapshot


def _minimal_winter_only_rate_data():
    """A fresh document that only ever mentions a Winter season for the same rate name - exactly
    the real verified example that used to wipe everything else."""
    return [{
        "name": "Standard Rate",
        "seasons": [{
            "name": "Winter Season",
            "date_ranges": [{"start": "2027-12-01", "end": "2028-02-28"}],
            "room_prices": [],
            "meal_plans": [],
        }],
        "offer_names": [],
        "supplement_names": [],
        "stop_sales": [],
    }]


def test_unmatched_existing_season_is_carried_forward_unchanged():
    existing = _base_existing_snapshot()
    results = build_hotel_rate_payloads(
        _minimal_winter_only_rate_data(), {}, {}, {}, existing_hotel_snapshot=existing,
    )
    assert len(results) == 1
    rate_payload = results[0]["rate_payload"]
    assert rate_payload is not None
    season_names = {s["name"] for s in rate_payload["seasons"]}
    assert "Winter Season" in season_names   # the fresh season
    assert "Summer Season" in season_names   # carried forward, not wiped

    carried = next(a for a in results[0]["season_actions"] if a["season_name"] == "Summer Season")
    assert carried["action"] == "carried_forward_unchanged"
    assert carried["matched_season_id"] == 9001


def test_existing_offer_and_supplement_not_restated_are_carried_forward():
    existing = _base_existing_snapshot()
    results = build_hotel_rate_payloads(
        _minimal_winter_only_rate_data(), {}, {}, {}, existing_hotel_snapshot=existing,
    )
    rate_payload = results[0]["rate_payload"]
    assert "AUTO_offer_summer" in rate_payload["offers"]
    assert "AUTO_supp_towel" in rate_payload["supplements"]


def test_existing_stop_sale_not_restated_is_carried_forward():
    existing = _base_existing_snapshot()
    results = build_hotel_rate_payloads(
        _minimal_winter_only_rate_data(), {}, {}, {}, existing_hotel_snapshot=existing,
    )
    rate_payload = results[0]["rate_payload"]
    room_names = {ss["roomName"] for ss in rate_payload["stopSales"]}
    assert "Deluxe Room" in room_names
    blackout = next(ss for ss in rate_payload["stopSales"] if ss["roomName"] == "Deluxe Room")
    assert blackout["stopSales"] == [{"start": "2027-12-24", "end": "2027-12-26"}]


def test_a_season_that_IS_restated_by_name_is_updated_in_place_not_duplicated():
    existing = _base_existing_snapshot()
    rate_data = [{
        "name": "Standard Rate",
        "seasons": [{
            "name": "Summer Season",  # matches existing by name
            "date_ranges": [{"start": "2027-06-01", "end": "2027-08-31"}],
            "room_prices": [],
            "meal_plans": [],
            "minimum_stay": 3,
        }],
        "offer_names": [],
        "supplement_names": [],
        "stop_sales": [],
    }]
    results = build_hotel_rate_payloads(rate_data, {}, {}, {}, existing_hotel_snapshot=existing)
    rate_payload = results[0]["rate_payload"]
    summer_seasons = [s for s in rate_payload["seasons"] if s["name"] == "Summer Season"]
    assert len(summer_seasons) == 1  # not duplicated
    assert summer_seasons[0]["id"] == 9001  # updated in place, reusing the existing id
    assert summer_seasons[0]["minimumStay"] == 3  # fresh data wins for a matched season


def test_a_stop_sale_that_IS_restated_for_the_same_room_is_not_duplicated():
    existing = _base_existing_snapshot()
    rate_data = [{
        "name": "Standard Rate",
        "seasons": [],
        "offer_names": [],
        "supplement_names": [],
        "stop_sales": [{
            "room_name": "Deluxe Room",
            "date_ranges": [{"start": "2028-01-01", "end": "2028-01-05"}],
        }],
    }]
    results = build_hotel_rate_payloads(rate_data, {}, {}, {}, existing_hotel_snapshot=existing)
    rate_payload = results[0]["rate_payload"]
    deluxe_entries = [ss for ss in rate_payload["stopSales"] if ss["roomName"] == "Deluxe Room"]
    assert len(deluxe_entries) == 1
    assert deluxe_entries[0]["stopSales"] == [{"start": "2028-01-01", "end": "2028-01-05"}]


def test_brand_new_rate_with_no_existing_snapshot_carries_forward_nothing():
    results = build_hotel_rate_payloads(_minimal_winter_only_rate_data(), {}, {}, {}, existing_hotel_snapshot=None)
    rate_payload = results[0]["rate_payload"]
    assert [s["name"] for s in rate_payload["seasons"]] == ["Winter Season"]
    assert rate_payload["offers"] == []
    assert rate_payload["supplements"] == []
    assert rate_payload["stopSales"] == []
    assert results[0]["action"] == "create"


def test_a_malformed_existing_season_is_skipped_not_crashed_on():
    existing = _base_existing_snapshot()
    existing["rates"][0]["seasons"].append({"id": 9002, "name": "Broken Season"})  # missing required dateRanges
    results = build_hotel_rate_payloads(
        _minimal_winter_only_rate_data(), {}, {}, {}, existing_hotel_snapshot=existing,
    )
    rate_payload = results[0]["rate_payload"]
    assert rate_payload is not None
    season_names = {s["name"] for s in rate_payload["seasons"]}
    assert "Broken Season" not in season_names
    assert "Summer Season" in season_names  # the well-formed one still carries forward
