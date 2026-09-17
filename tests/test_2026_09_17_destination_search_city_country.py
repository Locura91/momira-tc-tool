"""Regression tests for a real product-owner follow-up (2026-09-17), on the destination-first
hotel master-data search added 2026-09-16 (see test_2026_09_16_destination_confirmed_hotel_
masterdata_search.py):

    "when searching for destinations in Travel C, please search with destinatin, Country. Like
    Cairo, Egypt. So we can avoid to include a false destiantion"

ROOT CAUSE: the calling screen's own placeholder already suggested a "City, Country" format
(e.g. "El Gouna, Egypt" - itself from an earlier, analogous product-owner rule for geocoding
queries, 2026-09-03), but find_destination_candidates never actually parsed the country part out
- it stayed glued onto the query string, so typing exactly what the placeholder suggested matched
nothing at all (no real Travel Compositor destination is literally named "El Gouna, Egypt"). A
bare city name with no country, meanwhile, matches every same-named destination worldwide with no
way to narrow it down - the "false destination" risk Chris is flagging.

FIX: find_destination_candidates now splits an optional ", Country" suffix off the query (on the
LAST comma) and uses it as an extra filter against the destination's own `country` field, matched
case-insensitively as a substring in either direction against both the raw value (2-letter ISO
code or full name - Travel Compositor's own inconsistency, already documented in geocoding_
client.country_display_name) and its normalized display name - so both "Cairo, Egypt" and
"Cairo, EG" work regardless of which form Travel Compositor itself returns for that record.
"""
from unittest.mock import Mock

import pytest

from api_client import TravelCompositorAPI, _destination_country_matches


@pytest.fixture
def client():
    c = TravelCompositorAPI()
    c.username, c.password, c.microsite_id = "test_user", "test_pass", "momiratravel"
    return c


# Two different real-world destinations that could plausibly share a bare city name, to prove
# the country part actually disambiguates rather than just being ignored.
_DESTINATIONS = [
    {"code": "CAI", "name": "Cairo", "country": "EG", "latitude": 30.04, "longitude": 31.24},
    {"code": "CAIUS", "name": "Cairo", "country": "US", "latitude": 37.00, "longitude": -89.18},
    {"code": "HRG", "name": "El Gouna", "country": "EG", "latitude": 27.39, "longitude": 33.67},
    # Same country stored as a full name instead of a code - Travel Compositor's own
    # inconsistency (see geocoding_client.country_display_name's docstring).
    {"code": "LUX", "name": "Luxor", "country": "Egypt", "latitude": 25.68, "longitude": 32.64},
]


def test_city_comma_country_narrows_to_the_matching_country_only(client):
    client._get_all_destinations = Mock(return_value=_DESTINATIONS)
    results = client.find_destination_candidates("Cairo, Egypt")
    assert [r["code"] for r in results] == ["CAI"]


def test_city_comma_country_matches_a_us_result_too_when_that_country_is_typed(client):
    client._get_all_destinations = Mock(return_value=_DESTINATIONS)
    results = client.find_destination_candidates("Cairo, United States")
    assert [r["code"] for r in results] == ["CAIUS"]


def test_bare_city_name_with_no_comma_is_unaffected_still_returns_every_match(client):
    client._get_all_destinations = Mock(return_value=_DESTINATIONS)
    results = client.find_destination_candidates("Cairo")
    assert {r["code"] for r in results} == {"CAI", "CAIUS"}


def test_country_typed_as_a_full_name_matches_a_destination_stored_as_a_code(client):
    client._get_all_destinations = Mock(return_value=_DESTINATIONS)
    results = client.find_destination_candidates("El Gouna, Egypt")
    assert [r["code"] for r in results] == ["HRG"]


def test_country_typed_as_a_code_matches_a_destination_stored_as_a_full_name(client):
    client._get_all_destinations = Mock(return_value=_DESTINATIONS)
    results = client.find_destination_candidates("Luxor, EG")
    assert [r["code"] for r in results] == ["LUX"]


def test_country_that_matches_nothing_returns_no_results_rather_than_falling_back(client):
    # The whole point is to avoid a false/wrong-country match slipping through - a country that
    # doesn't match the real record must not silently ignore the country filter.
    client._get_all_destinations = Mock(return_value=_DESTINATIONS)
    assert client.find_destination_candidates("Cairo, Thailand") == []


def test_is_case_insensitive_on_both_parts(client):
    client._get_all_destinations = Mock(return_value=_DESTINATIONS)
    results = client.find_destination_candidates("cairo, egypt")
    assert [r["code"] for r in results] == ["CAI"]


def test_trailing_comma_with_nothing_after_it_is_treated_as_no_country(client):
    client._get_all_destinations = Mock(return_value=_DESTINATIONS)
    results = client.find_destination_candidates("Cairo,   ")
    assert {r["code"] for r in results} == {"CAI", "CAIUS"}


# ---------------------------------------------------------------------------------------------
# _destination_country_matches
# ---------------------------------------------------------------------------------------------

def test_matches_raw_code_against_typed_code():
    assert _destination_country_matches({"country": "EG"}, "eg")


def test_matches_raw_name_against_typed_name():
    assert _destination_country_matches({"country": "Egypt"}, "egypt")


def test_matches_raw_code_against_typed_full_name():
    assert _destination_country_matches({"country": "EG"}, "egypt")


def test_matches_raw_name_against_typed_code():
    assert _destination_country_matches({"country": "Egypt"}, "eg")


def test_no_match_for_an_unrelated_country():
    assert not _destination_country_matches({"country": "EG"}, "thailand")


def test_false_for_a_destination_with_no_country_at_all():
    assert not _destination_country_matches({"country": None}, "egypt")
    assert not _destination_country_matches({}, "egypt")
