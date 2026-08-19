"""Tests for api_client.TravelCompositorAPI - authentication and the _request retry/
re-auth/network-error-handling wrapper. Every HTTP call is mocked (requests.post /
requests.request never actually leave the machine) and time.sleep is patched out so the
retry-loop tests run instantly instead of taking the real ~10-12 seconds.

See _request's own docstring in api_client.py for the exact contract these tests pin down:
  1. Automatic re-authentication on a 401.
  2. Retry (up to 6 attempts, 2s apart) on a TRANSIENT write failure (408/429/5xx/599).
  3. NO retry on a non-transient write failure (400/404/409/...) - fail fast instead.
  4. NO retry at all on GET calls, transient or not.
  5. A raised network exception is converted into a synthetic 599 Response, never propagated.
"""
from unittest.mock import Mock, patch

import pytest
import requests

from api_client import TravelCompositorAPI


def make_response(status_code, json_body=None, headers=None, text=""):
    res = requests.Response()
    res.status_code = status_code
    res.headers.update(headers or {})
    if json_body is not None:
        import json
        res._content = json.dumps(json_body).encode("utf-8")
    else:
        res._content = text.encode("utf-8")
    return res


@pytest.fixture
def client():
    c = TravelCompositorAPI()
    c.username, c.password, c.microsite_id = "test_user", "test_pass", "momiratravel"
    return c


@pytest.fixture(autouse=True)
def no_real_sleep():
    """Every retry test in this file would otherwise take up to 6 x 2s = 12s for real."""
    with patch("api_client.time.sleep") as sleep_mock:
        yield sleep_mock


# ============================================================
# Authentication
# ============================================================
def test_authenticate_reads_the_token_from_the_auth_token_header(client):
    with patch("api_client.requests.post", return_value=make_response(
            200, headers={"auth-token": "tok-123"})) as post_mock:
        token = client.authenticate()
    assert token == "tok-123"
    assert client.auth_token == "tok-123"
    post_mock.assert_called_once()


def test_authenticate_falls_back_to_the_json_body_when_no_header(client):
    with patch("api_client.requests.post", return_value=make_response(200, json_body={"token": "tok-from-body"})):
        token = client.authenticate()
    assert token == "tok-from-body"


def test_authenticate_caches_the_token_and_does_not_re_call_by_default(client):
    with patch("api_client.requests.post", return_value=make_response(
            200, headers={"auth-token": "tok-1"})) as post_mock:
        client.authenticate()
        client.authenticate()  # second call, no force
    post_mock.assert_called_once()


def test_authenticate_force_true_always_re_authenticates(client):
    with patch("api_client.requests.post", return_value=make_response(
            200, headers={"auth-token": "tok-1"})) as post_mock:
        client.authenticate()
        client.authenticate(force=True)
    assert post_mock.call_count == 2


def test_authenticate_raises_on_a_failed_login(client):
    with patch("api_client.requests.post", return_value=make_response(401, text="bad credentials")):
        with pytest.raises(requests.exceptions.HTTPError):
            client.authenticate()


# ============================================================
# _request: automatic re-authentication on 401
# ============================================================
def test_401_triggers_one_re_authentication_and_retry(client):
    client.auth_token = "stale-token"
    responses = [
        make_response(401),                                      # the real call, token expired
        make_response(200, headers={"auth-token": "fresh-token"}),  # authenticate(force=True)
        make_response(200, json_body={"ok": True}),               # the retried real call
    ]
    with patch("api_client.requests.request", side_effect=[responses[0], responses[2]]) as req_mock, \
         patch("api_client.requests.post", return_value=responses[1]) as post_mock:
        res = client._request("GET", "https://example.test/resource")
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    post_mock.assert_called_once()          # exactly one re-authentication
    assert req_mock.call_count == 2          # original attempt + the one retry after re-auth
    assert client.auth_token == "fresh-token"


# ============================================================
# _request: retry behaviour for WRITE calls (POST/PUT)
# ============================================================
@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504, 599])
def test_transient_write_failure_is_retried_then_succeeds(client, status, no_real_sleep):
    client.auth_token = "tok"
    responses = [make_response(status), make_response(200, json_body={"ok": True})]
    with patch("api_client.requests.request", side_effect=responses):
        res = client._request("POST", "https://example.test/resource", json={})
    assert res.status_code == 200
    no_real_sleep.assert_called()  # confirms the retry path (with its 2s wait) was actually taken


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504, 599])
def test_transient_write_failure_gives_up_after_six_attempts(client, status):
    client.auth_token = "tok"
    with patch("api_client.requests.request", return_value=make_response(status)) as req_mock:
        res = client._request("POST", "https://example.test/resource", json={})
    assert res.status_code == status
    assert req_mock.call_count == 6


@pytest.mark.parametrize("status", [400, 404, 409, 422])
def test_non_transient_write_failure_is_not_retried(client, status, no_real_sleep):
    """CONFIRMED REAL ISSUE (internal audit, see _TRANSIENT_STATUS_CODES's docstring): a
    genuine validation error retried 6 times against an unchanged payload wastes ~10-12s and
    risks a duplicate CREATE if the first attempt actually succeeded server-side."""
    client.auth_token = "tok"
    with patch("api_client.requests.request", return_value=make_response(status, text="bad request")) as req_mock:
        res = client._request("POST", "https://example.test/resource", json={})
    assert res.status_code == status
    assert req_mock.call_count == 1
    no_real_sleep.assert_not_called()


# ============================================================
# _request: GET calls never retry, even on a transient status
# ============================================================
@pytest.mark.parametrize("status", [408, 429, 500, 503, 599])
def test_get_calls_are_never_retried(client, status):
    client.auth_token = "tok"
    with patch("api_client.requests.request", return_value=make_response(status)) as req_mock:
        res = client._request("GET", "https://example.test/resource")
    assert res.status_code == status
    assert req_mock.call_count == 1


def test_get_returning_404_is_a_final_answer_not_an_error_worth_retrying(client):
    """A GET is often used as a fast existence check - a real 404 there is expected, final
    data, not a transient failure."""
    client.auth_token = "tok"
    with patch("api_client.requests.request", return_value=make_response(404)) as req_mock:
        res = client._request("GET", "https://example.test/resource")
    assert res.status_code == 404
    assert req_mock.call_count == 1


# ============================================================
# _request: a raised network exception is converted, not propagated
# ============================================================
@pytest.mark.parametrize("exc", [
    requests.exceptions.ConnectTimeout("connection timed out"),
    requests.exceptions.ConnectionError("connection refused"),
    requests.exceptions.SSLError("certificate verify failed"),
])
def test_network_exception_becomes_a_synthetic_599_not_a_crash(client, exc):
    client.auth_token = "tok"
    with patch("api_client.requests.request", side_effect=exc):
        res = client._request("GET", "https://example.test/resource")
    assert res.status_code == 599
    body = res.json()
    assert body["error"] == "network_error"
    assert type(exc).__name__ in body["message"]


def test_network_exception_on_a_write_call_is_still_treated_as_transient_and_retried(client, no_real_sleep):
    client.auth_token = "tok"
    with patch("api_client.requests.request", side_effect=[
            requests.exceptions.ConnectionError("refused"),
            make_response(200, json_body={"ok": True})]):
        res = client._request("POST", "https://example.test/resource", json={})
    assert res.status_code == 200
    no_real_sleep.assert_called()


# ============================================================
# _request: success on the very first attempt makes no extra calls at all
# ============================================================
def test_immediate_success_makes_exactly_one_call(client, no_real_sleep):
    client.auth_token = "tok"
    with patch("api_client.requests.request", return_value=make_response(200, json_body={"ok": True})) as req_mock:
        res = client._request("POST", "https://example.test/resource", json={})
    assert res.status_code == 200
    assert req_mock.call_count == 1
    no_real_sleep.assert_not_called()


def test_extra_headers_are_merged_not_overwritten_and_survive_reauth(client):
    """Callers (e.g. get_closed_tours' pagination headers) pass extra headers alongside the
    real auth headers - these must survive a re-authentication retry too."""
    client.auth_token = "stale"
    calls = []

    def record(method, url, headers=None, **kwargs):
        calls.append(dict(headers or {}))
        if len(calls) == 1:
            return make_response(401)
        return make_response(200, json_body={"ok": True})

    with patch("api_client.requests.request", side_effect=record), \
         patch("api_client.requests.post", return_value=make_response(200, headers={"auth-token": "fresh"})):
        client._request("GET", "https://example.test/resource", headers={"first": "1"})

    assert len(calls) == 2
    for call_headers in calls:
        assert call_headers.get("first") == "1"
        assert "auth-token" in call_headers
    assert calls[1]["auth-token"] == "fresh"


# ======================================================================
# _json - CONFIRMED FIX (2026-08-19 audit): every call site used to call res.json() directly.
# A 2xx response is not a guarantee of a parseable body (proxy hiccup, empty/truncated
# response) - this used to raise a raw json.JSONDecodeError straight through to the UI.
# ======================================================================
def test_json_returns_parsed_body_on_a_normal_response(client):
    res = make_response(200, json_body={"id": "abc123"})
    assert client._json(res) == {"id": "abc123"}


def test_json_raises_a_friendly_runtime_error_on_a_malformed_body(client):
    res = make_response(200, text="<html>not json</html>")
    with pytest.raises(RuntimeError) as exc_info:
        client._json(res)
    # The response body is included so the error is actually debuggable, not just "it broke".
    assert "not json" in str(exc_info.value)
