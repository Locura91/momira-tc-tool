"""Tests for package_rollover_rules.py — the Package Rollover prototype's pure decision
logic (no API calls). Covers the confirmed business rules (14-day trigger, ~4-month target,
8+ rating gate, 3.5% price cap) and the best-effort field-finding heuristic used because the
real Travel Compositor calendar response shape hasn't been seen yet.

CONFIRMED PRODUCT-OWNER RULES (2026-08-19): "Under 14 days out" trigger; "~4 months ahead"
target; hotel rating 8+; new price no more than 3.5% above the current live price.
"""
from datetime import date, timedelta

import package_rollover_rules as prr


TODAY = date(2026, 8, 19)


# ---- is_departure_closed -------------------------------------------------

def test_departure_13_days_out_is_closed():
    assert prr.is_departure_closed(TODAY + timedelta(days=13), TODAY) is True


def test_departure_14_days_out_is_not_yet_closed():
    # "under 14 days" — exactly 14 does not qualify.
    assert prr.is_departure_closed(TODAY + timedelta(days=14), TODAY) is False


def test_departure_with_no_date_is_not_closed():
    assert prr.is_departure_closed(None, TODAY) is False


# ---- parse_date_loose / parse_number_loose -------------------------------

def test_parse_date_loose_handles_iso_with_time_component():
    assert prr.parse_date_loose("2026-12-01T00:00:00") == date(2026, 12, 1)


def test_parse_date_loose_handles_plain_iso():
    assert prr.parse_date_loose("2026-12-01") == date(2026, 12, 1)


def test_parse_date_loose_returns_none_for_garbage():
    assert prr.parse_date_loose("not a date") is None
    assert prr.parse_date_loose(None) is None


def test_parse_number_loose_handles_plain_number():
    assert prr.parse_number_loose(1234.5) == 1234.5


def test_parse_number_loose_handles_dot_decimal_string():
    assert prr.parse_number_loose("1234.56") == 1234.56


def test_parse_number_loose_handles_comma_decimal_with_currency_symbol():
    assert prr.parse_number_loose("1.234,56 EUR") == 1234.56


def test_parse_number_loose_returns_none_for_garbage():
    assert prr.parse_number_loose("no digits here") is None


# ---- find_candidates: heuristic field-finding ----------------------------

def test_find_candidates_matches_departuredate_price_and_rating_keys():
    calendar_response = {
        "departures": [
            {"departureDate": "2026-12-15", "price": 899.0, "hotelRating": 8.5},
            {"departureDate": "2026-12-22", "price": 950.0, "hotelRating": 7.2},
        ]
    }
    candidates = prr.find_candidates(calendar_response)
    assert len(candidates) == 2
    assert candidates[0]["date"] == date(2026, 12, 15)
    assert candidates[0]["date_field"] == "departureDate"
    assert candidates[0]["price"] == 899.0
    assert candidates[0]["price_field"] == "price"
    assert candidates[0]["rating"] == 8.5
    assert candidates[0]["rating_field"] == "hotelRating"


def test_find_candidates_handles_a_bare_list_response():
    calendar_response = [
        {"startDate": "2026-12-15", "totalPrice": 899.0, "reviewScore": 8.5},
    ]
    candidates = prr.find_candidates(calendar_response)
    assert len(candidates) == 1
    assert candidates[0]["date"] == date(2026, 12, 15)
    assert candidates[0]["price"] == 899.0
    assert candidates[0]["rating"] == 8.5


def test_find_candidates_price_hint_never_matches_a_rating_field():
    # Regression guard: "rate"/"rating" must not be picked up by the price hints, or a rating
    # of e.g. 8 would get misread as an 8-currency-unit price.
    calendar_response = [{"date": "2026-12-15", "rating": 8.5}]
    candidates = prr.find_candidates(calendar_response)
    assert candidates[0]["price"] is None
    assert candidates[0]["rating"] == 8.5


def test_find_candidates_reports_none_for_fields_it_cant_find():
    calendar_response = [{"someUnrecognizedKey": "2026-12-15"}]
    candidates = prr.find_candidates(calendar_response)
    assert candidates[0]["date"] is None
    assert candidates[0]["date_field"] is None


def test_find_candidates_returns_empty_list_for_unrecognized_top_level_shape():
    assert prr.find_candidates({"foo": "bar"}) == []
    assert prr.find_candidates("not even a dict") == []


# ---- Real-data regression pins (package 56355178, confirmed 2026-08-19) ------------------
# CONFIRMED BUG: Travel Compositor represents money as {"amount": x, "currency": "EUR"}, not a
# flat number. The original heuristic handed the whole dict to a generic string parser, which
# read Python's str()-repr of the dict and produced 291912.0 for a real per-person price of
# 1459.56 (Chris reported "1460 Euro per Person"; totalPrice for 2 adults was 2919.12, and
# str({"amount": 2919.12, "currency": "EUR"}) parses to "291912.0" after non-digit stripping -
# exactly the wrong number that was shown). Also confirmed: a hotel's rating is a LIST of
# per-source objects with different scales (Tripadvisor is /5, not /10).

def test_find_price_unwraps_a_real_travel_compositor_money_object():
    # This exact shape is what GET .../info/{id} actually returned for pricePerPerson.
    key, price = prr.find_price({"pricePerPerson": {"amount": 1459.56, "currency": "EUR"}})
    assert key == "pricePerPerson"
    assert price == 1459.56


def test_find_price_prefers_priceperperson_over_totalprice():
    # Regression guard for the real bug: totalPrice used to win the priority race and get
    # misread as a per-person price.
    entry = {
        "pricePerPerson": {"amount": 1459.56, "currency": "EUR"},
        "totalPrice": {"amount": 2919.12, "currency": "EUR"},
    }
    key, price = prr.find_price(entry)
    assert key == "pricePerPerson"
    assert price == 1459.56


def test_find_price_falls_back_to_totalprice_when_priceperperson_is_absent():
    key, price = prr.find_price({"totalPrice": {"amount": 2919.12, "currency": "EUR"}})
    assert key == "totalPrice"
    assert price == 2919.12


def test_find_candidates_unwraps_a_money_object_price_in_a_calendar_entry():
    calendar_response = [
        {"departureDate": "2026-12-15", "pricePerPerson": {"amount": 1459.56, "currency": "EUR"}},
    ]
    candidates = prr.find_candidates(calendar_response)
    assert candidates[0]["price"] == 1459.56


def test_find_candidates_unwraps_a_real_multi_source_rating_list_preferring_booking_com():
    # This exact shape is what GET .../{id} (day-to-day) actually returned for a hotel's
    # ratings - three sources, three different scales.
    calendar_response = [{
        "departureDate": "2026-12-15",
        "ratings": [
            {"score": "8.6", "source": "Booking.com", "numReviews": 3279},
            {"score": "4.5", "source": "Tripadvisor", "numReviews": 1003},
            {"score": "7.2", "source": "Expedia", "numReviews": 437},
        ],
    }]
    candidates = prr.find_candidates(calendar_response)
    assert candidates[0]["rating"] == 8.6


def test_find_candidates_rating_falls_back_to_the_first_source_when_booking_com_is_absent():
    calendar_response = [{
        "departureDate": "2026-12-15",
        "ratings": [{"score": "7.2", "source": "Expedia", "numReviews": 437}],
    }]
    candidates = prr.find_candidates(calendar_response)
    assert candidates[0]["rating"] == 7.2


# ---- find_price / find_departure_date: public wrappers for a single entry ----------------

def test_find_price_matches_a_price_like_field_on_a_single_entry():
    key, price = prr.find_price({"currentPrice": "899.00 EUR"})
    assert key == "currentPrice"
    assert price == 899.0


def test_find_price_returns_none_key_and_value_when_nothing_matches():
    assert prr.find_price({"unrelated": "value"}) == (None, None)


def test_find_departure_date_matches_a_date_like_field_on_a_single_entry():
    key, dep_date = prr.find_departure_date({"departureDate": "2026-09-01"})
    assert key == "departureDate"
    assert dep_date == date(2026, 9, 1)


def test_find_departure_date_returns_none_key_and_value_when_nothing_matches():
    assert prr.find_departure_date({"unrelated": "value"}) == (None, None)


# ---- propose_rollover: applying the confirmed rules ----------------------

def _candidate(days_out, price=None, rating=None):
    return {"date": TODAY + timedelta(days=days_out), "date_field": "departureDate",
            "price": price, "price_field": "price", "rating": rating,
            "rating_field": "hotelRating", "raw": {}}


def test_propose_rollover_picks_the_candidate_closest_to_the_4_month_target():
    candidates = [
        _candidate(90, price=100, rating=9),
        _candidate(122, price=100, rating=9),   # exact target
        _candidate(200, price=100, rating=9),
    ]
    result = prr.propose_rollover(candidates, current_price=100, today=TODAY)
    assert result["status"] == "proposed"
    assert result["proposed"]["date"] == TODAY + timedelta(days=122)


def test_propose_rollover_rejects_a_candidate_below_the_rating_gate():
    candidates = [_candidate(122, price=100, rating=7.9)]
    result = prr.propose_rollover(candidates, current_price=100, today=TODAY)
    assert result["status"] == "no_qualifying_candidates"
    assert "rating 7.9 < 8" in result["rejected"][0]["rejected_because"][0]


def test_propose_rollover_accepts_a_candidate_exactly_at_the_rating_gate():
    candidates = [_candidate(122, price=100, rating=8)]
    result = prr.propose_rollover(candidates, current_price=100, today=TODAY)
    assert result["status"] == "proposed"


def test_propose_rollover_rejects_a_candidate_over_the_price_cap():
    # current price 100, cap is +3.5% = 103.5
    candidates = [_candidate(122, price=104, rating=9)]
    result = prr.propose_rollover(candidates, current_price=100, today=TODAY)
    assert result["status"] == "no_qualifying_candidates"
    assert "price 104" in result["rejected"][0]["rejected_because"][0]


def test_propose_rollover_accepts_a_candidate_exactly_at_the_price_cap():
    candidates = [_candidate(122, price=103.5, rating=9)]
    result = prr.propose_rollover(candidates, current_price=100, today=TODAY)
    assert result["status"] == "proposed"


def test_propose_rollover_flags_rating_unverifiable_when_no_rating_field_was_found():
    candidates = [_candidate(122, price=100, rating=None)]
    result = prr.propose_rollover(candidates, current_price=100, today=TODAY)
    assert result["status"] == "proposed"
    assert result["rating_unverifiable"] is True


def test_propose_rollover_flags_price_unverifiable_when_no_price_field_was_found():
    candidates = [_candidate(122, price=None, rating=9)]
    result = prr.propose_rollover(candidates, current_price=100, today=TODAY)
    assert result["status"] == "proposed"
    assert result["price_unverifiable"] is True


def test_propose_rollover_skips_the_price_cap_entirely_when_current_price_is_unknown():
    # No current_price to compare against at all — can't enforce the cap, but should not
    # crash or silently reject everything either.
    candidates = [_candidate(122, price=99999, rating=9)]
    result = prr.propose_rollover(candidates, current_price=None, today=TODAY)
    assert result["status"] == "proposed"


def test_propose_rollover_ignores_past_dated_candidates():
    candidates = [_candidate(-5, price=100, rating=9)]
    result = prr.propose_rollover(candidates, current_price=100, today=TODAY)
    assert result["status"] == "no_dated_candidates"


def test_propose_rollover_reports_no_dated_candidates_when_nothing_has_a_parsed_date():
    candidates = [{"date": None, "date_field": None, "price": 100, "price_field": "price",
                  "rating": 9, "rating_field": "rating", "raw": {}}]
    result = prr.propose_rollover(candidates, current_price=100, today=TODAY)
    assert result["status"] == "no_dated_candidates"
