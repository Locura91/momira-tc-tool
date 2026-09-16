"""Regression tests for the destination-first hotel master-data search (2026-09-16). Product
owner, screenshots of the hotel-creation Step 3 screen and Travel Compositor's own "Search
Accommodation" screen, red-circled:

    "existing Hotels in Masterdata are not found by the App. Human must first select the
    destination, which must be confirmed by Travel C. Then we add the name of the hotel we are
    creation. The country is not needed, as this information is provided by the destination code
    from travel compositor: So after checking, travel c first GET the Destination information
    and only when human confirms the destination the human can add the name of the hotel he is
    searching. Human will always first start with searching in hotel masterdata, only if nothing
    found, then the app shall ask if human wants to create a hotel withouth masterdata
    information."

Clarified via a round-trip (AskUserQuestion):
  - Multiple/no destination matches: show every real Travel Compositor destination that matches,
    human clicks to confirm - never auto-pick or require an exact match.
  - Once confirmed, the destination's country is a HARD filter on the hotel search (product
    owner's explicit choice - see masterdata_matcher.find_candidates' strict_country parameter).
  - GIATA code stays as an alternative path that skips destination confirmation entirely (a
    known id needs no destination check).

Covers: api_client.find_destination_candidates (a real GET /destination/{micrositeId} search,
returning every match - not resolve_destination()'s single-best-guess), masterdata_matcher.
find_candidates' new strict_country flag (no fallback pass when set), and app_helpers.py's
_render_hotel_masterdata_step wiring (destination confirmation gates the hotel-name field, old
free-text Destination/Country fields are gone).
"""
import os
from unittest.mock import Mock

import pytest

from api_client import TravelCompositorAPI
import masterdata_matcher as mm


# ---------------------------------------------------------------------------------------------
# api_client.find_destination_candidates
# ---------------------------------------------------------------------------------------------

@pytest.fixture
def client():
    c = TravelCompositorAPI()
    c.username, c.password, c.microsite_id = "test_user", "test_pass", "momiratravel"
    return c


_DESTINATIONS = [
    {"code": "FAY", "name": "Faiyum", "country": "EG", "latitude": 29.31, "longitude": 30.84},
    {"code": "FAYC", "name": "Faiyum City", "country": "EG", "lat": 29.30, "lng": 30.83},
    {"code": "HRG", "name": "El Gouna", "country": "EG", "latitude": 27.39, "longitude": 33.67},
    {"code": "BKK", "name": "Bangkok", "country": "TH"},  # no coordinates at all
]


def test_returns_every_destination_whose_name_contains_the_query_not_just_the_best_one(client):
    client._get_all_destinations = Mock(return_value=_DESTINATIONS)
    results = client.find_destination_candidates("Faiyum")
    codes = {r["code"] for r in results}
    assert codes == {"FAY", "FAYC"}


def test_exact_name_match_is_ordered_before_partial_matches(client):
    client._get_all_destinations = Mock(return_value=_DESTINATIONS)
    results = client.find_destination_candidates("Faiyum")
    assert results[0]["code"] == "FAY"  # exact "Faiyum" before "Faiyum City"


def test_is_case_insensitive(client):
    client._get_all_destinations = Mock(return_value=_DESTINATIONS)
    results = client.find_destination_candidates("el gouna")
    assert [r["code"] for r in results] == ["HRG"]


def test_extracts_coordinates_regardless_of_field_name_variant(client):
    client._get_all_destinations = Mock(return_value=_DESTINATIONS)
    results = client.find_destination_candidates("Faiyum")
    by_code = {r["code"]: r for r in results}
    assert by_code["FAY"]["latitude"] == 29.31 and by_code["FAY"]["longitude"] == 30.84
    assert by_code["FAYC"]["latitude"] == 29.30 and by_code["FAYC"]["longitude"] == 30.83


def test_missing_coordinates_are_reported_as_none_not_an_error(client):
    client._get_all_destinations = Mock(return_value=_DESTINATIONS)
    results = client.find_destination_candidates("Bangkok")
    assert results[0]["latitude"] is None and results[0]["longitude"] is None


def test_no_match_returns_empty_list(client):
    client._get_all_destinations = Mock(return_value=_DESTINATIONS)
    assert client.find_destination_candidates("Atlantis") == []


def test_blank_query_returns_empty_list_without_hitting_the_network(client):
    client._get_all_destinations = Mock(side_effect=AssertionError("must not be called"))
    assert client.find_destination_candidates("") == []
    assert client.find_destination_candidates("   ") == []


def test_a_fetch_failure_returns_empty_list_rather_than_raising(client):
    import requests
    client._get_all_destinations = Mock(side_effect=requests.RequestException("down"))
    assert client.find_destination_candidates("Faiyum") == []


def test_results_are_capped_at_max_results(client):
    many = [{"code": f"D{i}", "name": f"Destination {i}", "country": "EG"} for i in range(30)]
    client._get_all_destinations = Mock(return_value=many)
    results = client.find_destination_candidates("Destination", max_results=5)
    assert len(results) == 5


def test_duplicate_destination_codes_across_languages_are_not_repeated(client):
    dupes = [
        {"code": "HRG", "name": "El Gouna", "country": "EG"},
        {"code": "HRG", "name": "El Gouna", "country": "EG"},  # e.g. seen again in another lang pass
    ]
    client._get_all_destinations = Mock(return_value=dupes)
    results = client.find_destination_candidates("El Gouna")
    assert len(results) == 1


# ---------------------------------------------------------------------------------------------
# masterdata_matcher.find_candidates(strict_country=True)
# ---------------------------------------------------------------------------------------------

_INDEX = [
    {"id": "TC-1", "giataId": 111, "name": "Steigenberger Golf Resort El Gouna",
     "geolocation": {"latitude": 27.394, "longitude": 33.679}, "countryCode": "EG"},
    {"id": "TC-2", "giataId": 222, "name": "Steigenberger Golf Resort",  # near-exact name, wrong country
     "geolocation": {"latitude": 25.2, "longitude": 55.3}, "countryCode": "AE"},
]


def test_strict_country_true_never_runs_the_whole_index_fallback_pass():
    # A near-perfect name match exists, but in the WRONG country - the default (soft) behavior
    # would surface it flagged as a country_mismatch; strict_country=True must not.
    results = mm.find_candidates("Steigenberger Golf Resort", _INDEX, country_code="EG",
                                 strict_country=True)
    assert all(r["countryCode"] == "EG" for r in results)
    assert not any(r.get("country_mismatch") for r in results)


def test_strict_country_false_default_still_finds_the_out_of_country_fallback():
    # Confirms the default (existing, already-shipped) behavior is unchanged by adding the flag:
    # searching within a country with NO records at all still falls back to the whole index and
    # surfaces the real (differently-countried) hotel, flagged rather than hidden.
    results = mm.find_candidates("Steigenberger Golf Resort El Gouna", _INDEX, country_code="FR")
    assert any(r.get("country_mismatch") for r in results)


def test_strict_country_true_finds_nothing_when_the_country_has_no_records_at_all():
    # Same search, strict this time: no EG-or-anywhere fallback, so a country with zero records
    # in the index returns zero candidates - a human just sees "no match", not a wrong-country one.
    results = mm.find_candidates("Steigenberger Golf Resort El Gouna", _INDEX, country_code="FR",
                                 strict_country=True)
    assert results == []


def test_strict_country_has_no_effect_when_no_country_code_given():
    results_strict = mm.find_candidates("Steigenberger", _INDEX, strict_country=True)
    results_default = mm.find_candidates("Steigenberger", _INDEX, strict_country=False)
    assert {r["id"] for r in results_strict} == {r["id"] for r in results_default}


# ---------------------------------------------------------------------------------------------
# app_helpers.py wiring
# ---------------------------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_HELPERS_PY = os.path.join(os.path.dirname(_HERE), "app_helpers.py")


def _read_app_helpers():
    with open(_APP_HELPERS_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_old_free_text_destination_and_country_fields_are_gone():
    src = _read_app_helpers()
    assert "hp_md_search_destination" not in src
    assert "hp_md_search_country" not in src
    assert 'st.text_input("Country code (optional, e.g. EG)", value="", key="hp_md_search_country"' not in src


def test_destination_confirmation_step_is_wired_to_the_real_travel_compositor_lookup():
    src = _read_app_helpers()
    assert "client.find_destination_candidates(" in src
    assert "hp_md_confirmed_destination" in src
    assert 'st.button("Confirm", key=f"hp_md_dest_confirm_' in src


def test_hotel_name_search_is_scoped_to_the_confirmed_destination_with_a_hard_country_filter():
    src = _read_app_helpers()
    assert "strict_country=True" in src
    assert "confirmed_destination.get(\"country\")" in src


def test_giata_code_still_skips_destination_confirmation():
    src = _read_app_helpers()
    assert "skips destination confirmation entirely" in src
    giata_idx = src.index('key="hp_md_search_giata"')
    confirm_idx = src.index("hp_md_confirmed_destination")
    # the GIATA field must be declared/checked before the destination-confirmation gate runs
    assert giata_idx < confirm_idx


def test_change_destination_button_clears_the_confirmed_destination_and_stale_candidates():
    src = _read_app_helpers()
    change_block_start = src.index('st.button("↩️ Change destination"')
    change_block = src[change_block_start:change_block_start + 400]
    assert "hp_md_confirmed_destination" in change_block
    assert "hp_md_candidates" in change_block
