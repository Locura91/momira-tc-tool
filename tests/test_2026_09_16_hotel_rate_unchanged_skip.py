"""Regression tests for a real product-owner request (2026-09-16):

    "when udating a contracted hotel and the human provides the Document for the upload...
    when rechecking the current price data, we only must upload/change the information that
    really was detected as change. Not everything needs a complete update."

Before this, an existing rate matched by name always got sent as an "update" PUT once ANY
document mentioning it was republished, even when the freshly built payload was byte-for-byte
identical to what was already live - exactly the case for a "checking the current period"
document that just confirms nothing changed. build_hotel_rate_payloads now compares the freshly
built payload against the existing rate (both normalized through the same ContractHotelRateVO
schema, so formatting differences never cause a false "changed") and marks the action
"unchanged" instead of "update" when they're truly identical - flows/hotel.py's publish loop
then skips the API call for it entirely (see the "action == unchanged" branch there).
"""
from builder import build_hotel_rate_payloads, _hotel_rate_payload_unchanged


def _identical_setup():
    """An existing rate with one fully-specified season, and a fresh extraction that restates
    it with the exact same values - the "just checking, nothing changed" case."""
    existing_snapshot = {
        "rates": [{
            "id": 501, "name": "Standard Rate", "bookingWindows": [], "offers": [], "supplements": [],
            "stopSales": [], "releaseDays": None, "minimumStay": 1, "maximumStay": None,
            "seasons": [{
                "id": 9001, "name": "Summer Season",
                "dateRanges": [{"start": "2027-06-01", "end": "2027-08-31"}],
                "mealPlans": [],
                "seasonRoomPrices": [{
                    "unitsQuota": 20, "unitsOnRequest": 0, "providerRoomCode": "AUTO123",
                    "distributionPrices": [{"amount": 100.0, "adults": 2, "children": 0}],
                    "basePrice": 100.0, "adultPrices": [], "childPrices": [],
                }],
                "releaseDays": None, "minimumStay": 1, "maximumStay": None, "priceType": "DISTRIBUTION",
            }],
        }],
    }
    rate_data = [{
        "name": "Standard Rate",
        "seasons": [{
            "name": "Summer Season",
            "date_ranges": [{"start": "2027-06-01", "end": "2027-08-31"}],
            "room_prices": [{
                "room_name": "Deluxe Room", "units_quota": 20, "units_on_request": 0,
                "distribution_prices": [{"amount": 100.0, "adults": 2, "children": 0}],
                "base_price": 100.0, "adult_prices": [], "child_prices": [],
            }],
            "meal_plans": [],
        }],
        "offer_names": [], "supplement_names": [], "stop_sales": [],
    }]
    room_name_to_provider_code = {"Deluxe Room": "AUTO123"}
    room_name_to_distributions = {"Deluxe Room": [{"adults": 2, "children": 0}]}
    return existing_snapshot, rate_data, room_name_to_provider_code, room_name_to_distributions


def test_a_rate_republished_with_identical_values_is_marked_unchanged():
    existing_snapshot, rate_data, room_map, dist_map = _identical_setup()
    results = build_hotel_rate_payloads(
        rate_data, room_map, {}, {}, existing_hotel_snapshot=existing_snapshot,
        room_name_to_distributions=dist_map,
    )
    assert len(results) == 1
    assert results[0]["action"] == "unchanged"
    assert results[0]["rate_payload"] is not None  # still built - just not sent


def test_a_rate_with_a_genuinely_different_price_is_still_marked_update():
    existing_snapshot, rate_data, room_map, dist_map = _identical_setup()
    rate_data[0]["seasons"][0]["room_prices"][0]["base_price"] = 150.0
    rate_data[0]["seasons"][0]["room_prices"][0]["distribution_prices"][0]["amount"] = 150.0
    results = build_hotel_rate_payloads(
        rate_data, room_map, {}, {}, existing_hotel_snapshot=existing_snapshot,
        room_name_to_distributions=dist_map,
    )
    assert results[0]["action"] == "update"


def test_a_rate_with_a_new_season_added_is_still_marked_update():
    existing_snapshot, rate_data, room_map, dist_map = _identical_setup()
    rate_data[0]["seasons"].append({
        "name": "Winter Season",
        "date_ranges": [{"start": "2027-12-01", "end": "2028-02-28"}],
        "room_prices": [], "meal_plans": [],
    })
    results = build_hotel_rate_payloads(
        rate_data, room_map, {}, {}, existing_hotel_snapshot=existing_snapshot,
        room_name_to_distributions=dist_map,
    )
    assert results[0]["action"] == "update"


def test_a_brand_new_rate_with_no_existing_match_is_create_not_unchanged():
    _, rate_data, room_map, dist_map = _identical_setup()
    rate_data[0]["name"] = "A Completely New Rate"
    results = build_hotel_rate_payloads(
        rate_data, room_map, {}, {}, existing_hotel_snapshot=None,
        room_name_to_distributions=dist_map,
    )
    assert results[0]["action"] == "create"


# ---------------------------------------------------------------------------------------------
# _hotel_rate_payload_unchanged directly - conservative-by-design edge cases
# ---------------------------------------------------------------------------------------------

def test_unchanged_helper_returns_false_when_there_is_nothing_to_compare_against():
    assert _hotel_rate_payload_unchanged({"name": "X"}, None) is False
    assert _hotel_rate_payload_unchanged({"name": "X"}, {}) is False


def test_unchanged_helper_returns_false_when_rate_payload_is_none():
    assert _hotel_rate_payload_unchanged(None, {"name": "X", "id": 1}) is False


def test_unchanged_helper_returns_false_when_existing_rate_does_not_parse_against_the_schema():
    # A malformed/unexpected existing record must never be treated as "safely proven identical" -
    # conservative failure mode is to say "changed" (send it) rather than silently skip.
    broken_existing = {"this_is_not": "a_valid_rate_shape", "name": None}
    assert _hotel_rate_payload_unchanged({"name": "X"}, broken_existing) is False
