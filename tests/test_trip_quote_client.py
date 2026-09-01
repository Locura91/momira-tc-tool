"""Tests for trip_quote_client.py - the request-shape builders and the Quote-endpoint HTTP
wrappers. Every HTTP call is mocked (requests.post/requests.request never actually leave the
machine, following test_api_client.py's own established pattern) - these tests pin down OUR
code's URL construction and payload shaping, not whether Travel Compositor's real account
actually accepts these shapes (see trip_quote_client.py's own module docstring: that is still
unverified, this sandbox has no network route to online.travelcompositor.com at all).
"""
import json as json_module
from unittest.mock import patch

import pytest
import requests

from api_client import TravelCompositorAPI
from trip_quote_client import (
    ADULT_PLACEHOLDER_AGE,
    MAX_PAX,
    MAX_ROOMS,
    TripQuoteClient,
    build_distributions,
    build_persons,
    build_transfer_location,
    build_transport_journey,
)


def make_response(status_code, json_body=None, text=""):
    res = requests.Response()
    res.status_code = status_code
    if json_body is not None:
        res._content = json_module.dumps(json_body).encode("utf-8")
    else:
        res._content = text.encode("utf-8")
    return res


@pytest.fixture
def client():
    api = TravelCompositorAPI()
    api.username, api.password, api.microsite_id = "test_user", "test_pass", "momiratravel"
    api.auth_token = "test-token"  # skip the real authenticate() call in every test
    return TripQuoteClient(api)


# ============================================================
# build_distributions
# ============================================================
def test_single_room_two_adults_no_children():
    dist = build_distributions(adults=2, children_ages=[], rooms=1)
    assert dist == [{"persons": [{"age": ADULT_PLACEHOLDER_AGE}, {"age": ADULT_PLACEHOLDER_AGE}]}]


def test_single_room_with_children_puts_everyone_in_one_room():
    dist = build_distributions(adults=2, children_ages=[6, 9], rooms=1)
    assert len(dist) == 1
    ages = [p["age"] for p in dist[0]["persons"]]
    assert ages == [ADULT_PLACEHOLDER_AGE, ADULT_PLACEHOLDER_AGE, 6, 9]


def test_solo_traveller_defaults_to_one_room():
    dist = build_distributions(adults=1)
    assert dist == [{"persons": [{"age": ADULT_PLACEHOLDER_AGE}]}]


def test_multi_room_round_robins_adults_across_rooms():
    dist = build_distributions(adults=4, children_ages=[], rooms=2)
    assert len(dist) == 2
    assert len(dist[0]["persons"]) == 2
    assert len(dist[1]["persons"]) == 2


def test_multi_room_round_robins_children_onto_the_same_rooms():
    dist = build_distributions(adults=2, children_ages=[5, 7], rooms=2)
    assert len(dist) == 2
    # one adult + one child per room, round-robin order
    assert [p["age"] for p in dist[0]["persons"]] == [ADULT_PLACEHOLDER_AGE, 5]
    assert [p["age"] for p in dist[1]["persons"]] == [ADULT_PLACEHOLDER_AGE, 7]


def test_requested_rooms_cannot_exceed_adult_count():
    # 4 rooms requested but only 2 adults - every room needs at least one adult, so this
    # collapses to 2 rooms actually used rather than sending empty/adult-less rooms.
    dist = build_distributions(adults=2, children_ages=[3], rooms=4)
    assert len(dist) == 2


def test_zero_travellers_raises():
    with pytest.raises(ValueError):
        build_distributions(adults=0, children_ages=[])


def test_exceeding_max_pax_raises():
    with pytest.raises(ValueError):
        build_distributions(adults=9, children_ages=[1])  # 10 > MAX_PAX


def test_exactly_max_pax_is_allowed():
    dist = build_distributions(adults=9, children_ages=[])
    total = sum(len(room["persons"]) for room in dist)
    assert total == MAX_PAX


def test_exceeding_max_rooms_raises():
    with pytest.raises(ValueError):
        build_distributions(adults=9, children_ages=[], rooms=MAX_ROOMS + 1)


# ============================================================
# build_persons - the FLAT shape used by Transports/Transfer/Ticket (confirmed 2026-09-01,
# genuinely different from build_distributions()'s room-wrapped shape above)
# ============================================================
def test_build_persons_flat_shape_no_rooms():
    persons = build_persons(adults=2, children_ages=[6, 9])
    assert persons == [
        {"age": ADULT_PLACEHOLDER_AGE},
        {"age": ADULT_PLACEHOLDER_AGE},
        {"age": 6},
        {"age": 9},
    ]


def test_build_persons_zero_travellers_raises():
    with pytest.raises(ValueError):
        build_persons(adults=0, children_ages=[])


def test_build_persons_exceeding_max_pax_raises():
    with pytest.raises(ValueError):
        build_persons(adults=9, children_ages=[1])  # 10 > MAX_PAX


def test_build_persons_exactly_max_pax_is_allowed():
    persons = build_persons(adults=9, children_ages=[])
    assert len(persons) == MAX_PAX


# ============================================================
# build_transfer_location - {accommodationId} or {transportBaseID}, confirmed 2026-09-01
# ============================================================
def test_build_transfer_location_transport_base():
    assert build_transfer_location(transport_base_id="CAI") == {"transportBaseID": "CAI"}


def test_build_transfer_location_accommodation():
    assert build_transfer_location(accommodation_id="HTL123") == {"accommodationId": "HTL123"}


def test_build_transfer_location_requires_exactly_one():
    with pytest.raises(ValueError):
        build_transfer_location()
    with pytest.raises(ValueError):
        build_transfer_location(accommodation_id="HTL123", transport_base_id="CAI")


# ============================================================
# build_transport_journey
# ============================================================
def test_build_transport_journey_shape():
    journey = build_transport_journey("FRA", "TRANSPORT_BASE", "CAI", "DESTINATION", "2027-03-18")
    assert journey == {
        "departure": "FRA",
        "departureType": "TRANSPORT_BASE",
        "arrival": "CAI",
        "arrivalType": "DESTINATION",
        "departureDate": "2027-03-18",
    }


def test_build_transport_journey_rejects_invalid_location_type():
    with pytest.raises(ValueError):
        build_transport_journey("FRA", "AIRPORT", "CAI", "DESTINATION", "2027-03-18")
    with pytest.raises(ValueError):
        build_transport_journey("FRA", "TRANSPORT_BASE", "CAI", "CITY", "2027-03-18")


# ============================================================
# TripQuoteClient - URL construction, payload shape, error handling
# ============================================================
def test_quote_accommodations_posts_the_confirmed_url_and_filter_shape(client):
    # Field names verified 2026-09-01 against Momira's own real online.travelcompositor.com
    # Swagger (ApiAccommodationQuoteRequestVO) - checkIn/checkOut/destinationId/tripType, not the
    # earlier dertourgroup-derived guess (dateFrom/dateTo/destination, and tripType was missing
    # entirely despite being required).
    dist = build_distributions(adults=2)
    with patch("api_client.requests.request", return_value=make_response(200, {"ok": True})) as req_mock:
        result = client.quote_accommodations(dist, "2027-03-18", "2027-03-21", destination_code="CAI")
    assert result == {"ok": True}
    args, kwargs = req_mock.call_args
    assert args[0] == "POST"
    assert args[1] == f"{client.api.api_base_url}/booking/accommodations/quote"
    payload = kwargs["json"]
    assert payload["distributions"] == dist
    assert payload["checkIn"] == "2027-03-18"
    assert payload["checkOut"] == "2027-03-21"
    assert payload["destinationId"] == "CAI"
    assert payload["tripType"] == "ONLY_HOTEL"
    assert payload["filter"] == {"bestCombinations": True, "includeOnRequestOptions": False}


def test_quote_accommodations_includes_max_combinations_only_when_given(client):
    dist = build_distributions(adults=2)
    with patch("api_client.requests.request", return_value=make_response(200, {"ok": True})) as req_mock:
        client.quote_accommodations(dist, "2027-03-18", "2027-03-21", max_combinations=5)
    payload = req_mock.call_args.kwargs["json"]
    assert payload["filter"]["maxCombinations"] == 5


def test_quote_transports_uses_flat_persons_not_room_wrapped_distributions(client):
    # Confirmed 2026-09-01 (live browser session against the real OpenAPI spec): Transports'
    # "persons" field is a flat array of {age}, not build_distributions()'s room-wrapped shape -
    # this was a real bug (a doubly-nested, wrong-keyed payload) before this date.
    persons = build_persons(adults=2)
    journeys = [build_transport_journey("FRA", "TRANSPORT_BASE", "CAI", "DESTINATION", "2027-03-18")]
    with patch("api_client.requests.request", return_value=make_response(200, {"ok": True})) as req_mock:
        client.quote_transports(journeys, persons)
    args, kwargs = req_mock.call_args
    assert args[1] == f"{client.api.api_base_url}/booking/transports/quote"
    payload = kwargs["json"]
    assert payload["journeys"] == journeys
    assert payload["persons"] == persons
    assert payload["tripType"] == "MULTI"
    assert payload["filter"] == {"includeFareFamilies": False}


def test_quote_transfers_posts_the_confirmed_url_and_location_shape(client):
    # Confirmed 2026-09-01: from/to are {accommodationId} or {transportBaseID} objects, not the
    # pickup/pickupType/dropoff/dropoffType string pairs this used to (wrongly) send.
    persons = build_persons(adults=2)
    from_loc = build_transfer_location(transport_base_id="CAI")
    to_loc = build_transfer_location(accommodation_id="HTL123")
    with patch("api_client.requests.request", return_value=make_response(200, {"ok": True})) as req_mock:
        client.quote_transfers(persons, from_loc, to_loc, "2027-03-18T14:00:00")
    args, kwargs = req_mock.call_args
    assert args[1] == f"{client.api.api_base_url}/booking/transfer/quote"
    payload = kwargs["json"]
    assert payload["persons"] == persons
    assert payload["from"] == {"transportBaseID": "CAI"}
    assert payload["to"] == {"accommodationId": "HTL123"}
    assert payload["pickupDateTime"] == "2027-03-18T14:00:00"
    assert "arrivalTransportDateTime" not in payload
    assert "departureTransportDateTime" not in payload


def test_search_tickets_posts_the_destination_search_shape(client):
    persons = build_persons(adults=2)
    with patch("api_client.requests.request", return_value=make_response(200, {"ok": True})) as req_mock:
        client.search_tickets(persons, "2027-03-19", "2027-03-19", destination_code="CAI")
    args, kwargs = req_mock.call_args
    assert args[1] == f"{client.api.api_base_url}/booking/tickets/quote"
    payload = kwargs["json"]
    assert payload["checkIn"] == "2027-03-19"
    assert payload["checkOut"] == "2027-03-19"
    assert payload["persons"] == persons
    assert payload["destinationId"] == "CAI"
    assert payload["filter"] == {}


def test_quote_ticket_puts_ticket_id_in_the_path_not_the_body(client):
    # Confirmed 2026-09-01: ticketId is a path parameter and there is no modalityCode request
    # field at all - both were wrong before this date.
    persons = build_persons(adults=2)
    with patch("api_client.requests.request", return_value=make_response(200, {"ok": True})) as req_mock:
        client.quote_ticket("TICKET-417967", persons, "2027-03-19", "2027-03-19")
    args, kwargs = req_mock.call_args
    assert args[1] == f"{client.api.api_base_url}/booking/tickets/TICKET-417967/quote"
    payload = kwargs["json"]
    assert payload == {"checkIn": "2027-03-19", "checkOut": "2027-03-19", "persons": persons}
    assert "ticketId" not in payload
    assert "modalityCode" not in payload


def test_quote_closed_tour_posts_the_confirmed_url_and_shape(client):
    dist = build_distributions(adults=2)
    with patch("api_client.requests.request", return_value=make_response(200, {"ok": True})) as req_mock:
        client.quote_closed_tour("CT-123", "2027-03-18", dist, origin_code="FRA", pre_nights=1, post_nights=2)
    args, kwargs = req_mock.call_args
    assert args[1] == f"{client.api.api_base_url}/booking/closedtour/CT-123/quote"
    payload = kwargs["json"]
    assert payload == {
        "startDate": "2027-03-18",
        "distributions": dist,
        "originCode": "FRA",
        "preNights": 1,
        "postNights": 2,
    }


def test_a_non_2xx_response_returns_the_standard_error_dict_not_an_exception(client):
    dist = build_distributions(adults=2)
    with patch("api_client.requests.request", return_value=make_response(400, text="bad request")):
        result = client.quote_accommodations(dist, "2027-03-18", "2027-03-21")
    assert result == {"error": 400, "message": "bad request"}
