"""Regression tests for a product-owner request (2026-09-16):

    "also, please ignore prices, offers and supplements, which are already in the past. We only
    need to sell in the future"

builder._hp_all_windows_entirely_past(windows) is the shared helper: True only when `windows` is
non-empty AND every window's own "end" date has already passed - deliberately conservative, since
an EMPTY windows list means no dates were stated at all (e.g. a compulsory, undated supplement
that runs indefinitely), which must NOT be treated as "past" (that would be guessing, not reading
the document).

Wired into:
- build_hotel_offer_payloads / build_hotel_supplement_payloads: an offer/supplement whose
  travel_windows are all past is skipped entirely (action="skipped_past"), never built or sent.
- build_hotel_rate_payloads: a season whose date_ranges are all past is skipped entirely (never
  matched against an existing season, never built), reported via season_actions.
"""
import datetime

from builder import (
    _hp_all_windows_entirely_past,
    build_hotel_offer_payloads,
    build_hotel_supplement_payloads,
    build_hotel_rate_payloads,
)

_PAST_START = "2020-01-01"
_PAST_END = "2020-01-31"
_FUTURE_START = "2099-01-01"
_FUTURE_END = "2099-01-31"


# ---------------------------------------------------------------------------------------------
# 1. the shared helper
# ---------------------------------------------------------------------------------------------

def test_entirely_past_window_is_detected():
    assert _hp_all_windows_entirely_past([{"start": _PAST_START, "end": _PAST_END}]) is True


def test_entirely_future_window_is_not_past():
    assert _hp_all_windows_entirely_past([{"start": _FUTURE_START, "end": _FUTURE_END}]) is False


def test_a_mix_of_past_and_future_windows_is_not_entirely_past():
    assert _hp_all_windows_entirely_past(
        [{"start": _PAST_START, "end": _PAST_END}, {"start": _FUTURE_START, "end": _FUTURE_END}]
    ) is False


def test_empty_windows_list_is_not_treated_as_past():
    # No dates stated at all (e.g. an undated, always-on supplement) - must NOT be dropped.
    assert _hp_all_windows_entirely_past([]) is False
    assert _hp_all_windows_entirely_past(None) is False


def test_a_window_ending_today_is_not_yet_past():
    today_iso = datetime.date.today().isoformat()
    assert _hp_all_windows_entirely_past([{"start": _PAST_START, "end": today_iso}]) is False


# ---------------------------------------------------------------------------------------------
# 2. offers/supplements
# ---------------------------------------------------------------------------------------------

def test_offer_entirely_in_the_past_is_skipped_not_created():
    offers = [{"name": "Old Early Bird", "type": "PERCENT", "apply": "LODGING", "value": 10.0,
               "travel_windows": [{"start": _PAST_START, "end": _PAST_END}]}]
    results = build_hotel_offer_payloads(offers, {"Deluxe Room": "AUTO123"})
    assert results[0]["action"] == "skipped_past"
    assert results[0]["offer_payload"] is None
    assert results[0]["offer_error"] is None  # not a failure


def test_offer_in_the_future_is_still_created_normally():
    offers = [{"name": "New Early Bird", "type": "PERCENT", "apply": "LODGING", "value": 10.0,
               "travel_windows": [{"start": _FUTURE_START, "end": _FUTURE_END}]}]
    results = build_hotel_offer_payloads(offers, {"Deluxe Room": "AUTO123"})
    assert results[0]["action"] == "create"
    assert results[0]["offer_payload"] is not None


def test_undated_offer_is_not_skipped():
    offers = [{"name": "Always-On Discount", "type": "PERCENT", "apply": "LODGING", "value": 5.0,
               "travel_windows": []}]
    results = build_hotel_offer_payloads(offers, {"Deluxe Room": "AUTO123"})
    assert results[0]["action"] != "skipped_past"


def test_supplement_entirely_in_the_past_is_skipped_not_created():
    supplements = [{"name": "Old Resort Fee", "type": "ABSOLUTE", "apply": "PER_STAY", "value": 10.0,
                     "travel_windows": [{"start": _PAST_START, "end": _PAST_END}]}]
    results = build_hotel_supplement_payloads(supplements, {"Deluxe Room": "AUTO123"})
    assert results[0]["action"] == "skipped_past"
    assert results[0]["supplement_payload"] is None
    assert results[0]["supplement_error"] is None


# ---------------------------------------------------------------------------------------------
# 3. rate seasons
# ---------------------------------------------------------------------------------------------

_ROOM_MAP = {"Deluxe Room": "AUTO123"}
_DISTRIBUTIONS = {"Deluxe Room": [{"adults": 2, "children": 0}]}


def _rate_data_with_season(start, end):
    return [{
        "name": "Standard Rates",
        "seasons": [{
            "name": "Season 1",
            "date_ranges": [{"start": start, "end": end}],
            "room_prices": [{
                "room_name": "Deluxe Room",
                "distribution_prices": [{"amount": 200.0, "adults": 2, "children": 0}],
                "base_price": 200.0, "adult_prices": [], "child_prices": [],
            }],
            "meal_plans": [],
        }],
        "offer_names": [], "supplement_names": [], "stop_sales": [],
    }]


def test_season_entirely_in_the_past_is_skipped_entirely():
    results = build_hotel_rate_payloads(
        _rate_data_with_season(_PAST_START, _PAST_END), _ROOM_MAP, {}, {},
        room_name_to_distributions=_DISTRIBUTIONS)
    assert results[0]["rate_payload"]["seasons"] == []
    season_actions = results[0]["season_actions"]
    assert len(season_actions) == 1
    assert season_actions[0]["action"] == "skipped_past"


def test_season_in_the_future_is_still_built_normally():
    results = build_hotel_rate_payloads(
        _rate_data_with_season(_FUTURE_START, _FUTURE_END), _ROOM_MAP, {}, {},
        room_name_to_distributions=_DISTRIBUTIONS)
    assert len(results[0]["rate_payload"]["seasons"]) == 1
    assert results[0]["season_actions"][0]["action"] == "create"


def test_an_already_live_past_season_is_left_untouched_not_rebuilt():
    # The document restates an old, now-past season for an existing hotel - it must be skipped
    # (not matched, not rebuilt-then-sent) and the EXISTING live season carried forward as-is.
    existing_snapshot = {
        "rates": [{
            "id": 501, "name": "Standard Rates", "bookingWindows": [], "offers": [], "supplements": [],
            "stopSales": [], "releaseDays": 0, "minimumStay": 1, "maximumStay": None,
            "seasons": [{
                "id": 9001, "name": "Season 1",
                "dateRanges": [{"start": _PAST_START, "end": _PAST_END}],
                "mealPlans": [], "seasonRoomPrices": [], "releaseDays": 0,
                "minimumStay": 1, "maximumStay": None, "priceType": "DISTRIBUTION",
            }],
        }],
    }
    results = build_hotel_rate_payloads(
        _rate_data_with_season(_PAST_START, _PAST_END), _ROOM_MAP, {}, {},
        existing_hotel_snapshot=existing_snapshot, room_name_to_distributions=_DISTRIBUTIONS)
    seasons = results[0]["rate_payload"]["seasons"]
    assert len(seasons) == 1
    assert seasons[0]["id"] == 9001  # carried forward unchanged, not rebuilt
