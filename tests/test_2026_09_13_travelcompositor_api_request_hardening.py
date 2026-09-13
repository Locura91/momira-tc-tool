"""Regression tests for porting api_client.py's hardened `_request()` into
travelcompositor_api.py (2026-09-13, found while looking for duplicated/diverged code across
the codebase per the product owner's own request).

BACKGROUND: travelcompositor_api.TravelCompositorAPI and api_client.TravelCompositorAPI are two
independently-maintained classes with the same name and overlapping methods - kept deliberately
separate (see translation_tool.py's "NOTE ON THE TWO API CLIENTS": re-pointing the Translation
Sync / Package Rollover / Sync Tickets engines at api_client.py is a real regression risk for
zero user-visible gain). But that separation also meant a real crash fix made to api_client.py's
_request() after the fork was never ported to this sibling: a genuine network-level failure
(timeout, DNS failure, connection refused, SSL error) used to raise straight through
travelcompositor_api.TravelCompositorAPI._request(), uncaught - and because Streamlit runs one
process per session, that crashed the WHOLE platform (every tool, not just the one that happened
to be running), losing any in-progress edits, instead of the clean {"error":..., "message":...}
dict every caller already expects and handles gracefully.

These tests mirror the equivalent hardening tests that already exist for api_client.py's version
of this method (same mocking pattern as tests/test_travelcompositor_api_packages.py).
"""
import json
from unittest.mock import patch

import pytest
import requests

from travelcompositor_api import TravelCompositorAPI


@pytest.fixture
def client():
    c = TravelCompositorAPI()
    c.username, c.password, c.microsite_id = "test_user", "test_pass", "momiratravel"
    c.auth_token = "tok-123"  # skip a real authenticate() call
    return c


def _connection_error(*args, **kwargs):
    raise requests.exceptions.ConnectionError("Connection refused")


def test_network_error_no_longer_raises_uncaught():
    """The exact bug: before this fix, a raised ConnectionError propagated straight out of
    _request() - this proves it no longer does."""
    client = TravelCompositorAPI()
    client.username, client.password, client.microsite_id = "u", "p", "m"
    client.auth_token = "tok-123"
    with patch("travelcompositor_api.requests.request", side_effect=_connection_error):
        res = client._request("GET", "https://example.test/resources/hotel/some-supplier")
    assert res.status_code == 599


def test_network_error_response_carries_a_clean_error_dict_body():
    client = TravelCompositorAPI()
    client.username, client.password, client.microsite_id = "u", "p", "m"
    client.auth_token = "tok-123"
    with patch("travelcompositor_api.requests.request", side_effect=_connection_error):
        res = client._request("GET", "https://example.test/resources/hotel/some-supplier")
    body = json.loads(res.text)
    assert body["error"] == "network_error"
    assert "ConnectionError" in body["message"]


def test_get_hotel_returns_a_clean_error_dict_on_network_failure_instead_of_raising(client):
    """End-to-end through a real caller (get_hotel), matching how translation_tool.py /
    package_rollover_tool.py / run_sync_tickets.py actually use this client - none of them
    wrap calls in their own try/except, because every method's own status-code check was always
    assumed to be reachable."""
    with patch("travelcompositor_api.requests.request", side_effect=_connection_error):
        result = client.get_hotel("50696", "CAI-H1")
    assert result["error"] == 599


def test_write_call_retries_on_transient_status_then_succeeds():
    ok_response = requests.Response()
    ok_response.status_code = 200
    ok_response._content = json.dumps({"code": "SUP-1"}).encode("utf-8")

    transient_response = requests.Response()
    transient_response.status_code = 503

    call_count = {"n": 0}

    def fake_request(method, url, **kwargs):
        call_count["n"] += 1
        return transient_response if call_count["n"] == 1 else ok_response

    client = TravelCompositorAPI()
    client.username, client.password, client.microsite_id = "u", "p", "m"
    client.auth_token = "tok-123"
    with patch("travelcompositor_api.requests.request", side_effect=fake_request), \
         patch("travelcompositor_api.time.sleep"):
        res = client._request("POST", "https://example.test/resources/closedtour/x", json={})
    assert res.status_code == 200
    assert call_count["n"] == 2


def test_write_call_does_not_retry_a_non_transient_validation_error():
    """A 400 is a final answer - retrying an unchanged payload against the same validation
    rule can never succeed, and blanket-retrying a CREATE risks a duplicate resource if the
    first attempt actually succeeded server-side but the response was lost."""
    bad_response = requests.Response()
    bad_response.status_code = 400
    bad_response._content = b'{"message": "modality code cannot contain \'/\'"}'

    call_count = {"n": 0}

    def fake_request(method, url, **kwargs):
        call_count["n"] += 1
        return bad_response

    client = TravelCompositorAPI()
    client.username, client.password, client.microsite_id = "u", "p", "m"
    client.auth_token = "tok-123"
    with patch("travelcompositor_api.requests.request", side_effect=fake_request), \
         patch("travelcompositor_api.time.sleep"):
        res = client._request("POST", "https://example.test/resources/closedtour/x", json={})
    assert res.status_code == 400
    assert call_count["n"] == 1


def test_get_call_is_never_retried_even_on_a_transient_status():
    """Retries are deliberately write-only - a GET is often a fast 'does this exist' check
    where a real 404/4xx is an expected, final answer, matching api_client.py's own policy."""
    transient_response = requests.Response()
    transient_response.status_code = 503

    call_count = {"n": 0}

    def fake_request(method, url, **kwargs):
        call_count["n"] += 1
        return transient_response

    client = TravelCompositorAPI()
    client.username, client.password, client.microsite_id = "u", "p", "m"
    client.auth_token = "tok-123"
    with patch("travelcompositor_api.requests.request", side_effect=fake_request), \
         patch("travelcompositor_api.time.sleep"):
        res = client._request("GET", "https://example.test/resources/hotel/x")
    assert res.status_code == 503
    assert call_count["n"] == 1


def test_module_has_a_build_stamp():
    """travelcompositor_api.py never had MODULE_BUILD before this change - a gap that meant a
    partial deploy touching only this file was invisible to app.py's own stale-module check."""
    import travelcompositor_api
    assert hasattr(travelcompositor_api, "MODULE_BUILD")
    assert isinstance(travelcompositor_api.MODULE_BUILD, str) and travelcompositor_api.MODULE_BUILD
