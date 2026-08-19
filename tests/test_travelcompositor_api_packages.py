"""Tests for travelcompositor_api.TravelCompositorAPI's two new Package Rollover-prototype
methods: get_holiday_package_day_to_day and get_holiday_package_calendar.

CONTEXT (2026-08-19): added for the "Package Rollover" prototype - a human enters a Package
ID, the tool looks up its current departure/price/hotel and candidate future departures, and
proposes a roll for the human to review. These two methods are thin, unopinionated GET
wrappers (same pattern as the existing get_holiday_package_info / get_holiday_packages) since
the real response SHAPE (where departure date / price / hotel rating actually live) is not
yet confirmed - see the "package-auto-rollover-rules" project note. These tests only pin the
URL/params built and the success/error-dict contract, not any field-shape assumptions.
"""
import json
from unittest.mock import patch

import pytest
import requests

from travelcompositor_api import TravelCompositorAPI


def make_response(status_code, json_body=None, text=""):
    res = requests.Response()
    res.status_code = status_code
    if json_body is not None:
        res._content = json.dumps(json_body).encode("utf-8")
    else:
        res._content = text.encode("utf-8")
    return res


@pytest.fixture
def client():
    c = TravelCompositorAPI()
    c.username, c.password, c.microsite_id = "test_user", "test_pass", "momiratravel"
    c.auth_token = "tok-123"  # skip a real authenticate() call
    return c


def test_get_holiday_package_day_to_day_hits_the_expected_url(client):
    with patch("travelcompositor_api.requests.request",
               return_value=make_response(200, {"id": "59582825", "some": "shape"})) as req_mock:
        result = client.get_holiday_package_day_to_day("momiratravel", "59582825")

    assert result == {"id": "59582825", "some": "shape"}
    called_url = req_mock.call_args.args[1]
    assert called_url == f"{client.api_base_url}/package/momiratravel/59582825"
    assert req_mock.call_args.kwargs["params"] == {"lang": "EN"}


def test_get_holiday_package_day_to_day_returns_error_dict_on_non_200(client):
    with patch("travelcompositor_api.requests.request",
               return_value=make_response(404, text="not found")):
        result = client.get_holiday_package_day_to_day("momiratravel", "does-not-exist")

    assert result == {"error": 404, "message": "not found"}


def test_get_holiday_package_calendar_hits_the_expected_url(client):
    with patch("travelcompositor_api.requests.request",
               return_value=make_response(200, {"departures": []})) as req_mock:
        result = client.get_holiday_package_calendar("momiratravel", "59582825", lang="DE")

    assert result == {"departures": []}
    called_url = req_mock.call_args.args[1]
    assert called_url == f"{client.api_base_url}/package/calendar/momiratravel/59582825"
    assert req_mock.call_args.kwargs["params"] == {"lang": "DE"}


def test_get_holiday_package_calendar_returns_error_dict_on_non_200(client):
    with patch("travelcompositor_api.requests.request",
               return_value=make_response(500, text="boom")):
        result = client.get_holiday_package_calendar("momiratravel", "59582825")

    assert result == {"error": 500, "message": "boom"}
