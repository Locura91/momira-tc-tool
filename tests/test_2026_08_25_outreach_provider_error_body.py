"""Regression test for a real recurrence (product owner, 2026-08-25, AFTER the concurrency
revert already documented in test_2026_08_25_outreach_concurrency_revert.py): a Morocco Country
Scope run reported "60 search call(s) failed... Sample error: `dmc_city: 432 Client Error:  for
url: https://api.tavily.com/search`" - even with the sequential (one-at-a-time) fan-out already
in place, ruling out the earlier burst/WAF theory as the sole explanation. A bare
requests.Response.raise_for_status() message is only ever the status line - it never carries
what the provider's response body actually said, and Tavily/SerpAPI both explain a non-2xx
response in JSON (an invalid/expired key, a plan limit, a malformed request). Without that body,
"432" alone can't be told apart from a rate limit, a WAF block, or a broken API key - three
different problems needing three different fixes.

_raise_for_status_with_body appends the response body to the raised error's message so the next
occurrence tells the operator (and this app's own provider_error_sample surfacing, already wired
in outreach_discovery/outreach_tool - see test_2026_08_25_outreach_provider_error_visibility.py)
exactly why the call failed, instead of a bare, unexplained status code.
"""
import requests

from outreach_discovery import _raise_for_status_with_body, _search_with_tavily, _search_with_serpapi


class _FakeResponse:
    def __init__(self, status_code, text, json_data=None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data if json_data is not None else {}

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"{self.status_code} Client Error:  for url: https://api.tavily.com/search",
                response=self)


def test_raise_for_status_with_body_appends_the_response_text():
    res = _FakeResponse(432, '{"detail": "Your API key has exceeded its plan\'s usage limit."}')
    try:
        _raise_for_status_with_body(res)
        assert False, "expected an HTTPError to be raised"
    except requests.exceptions.HTTPError as e:
        assert "432" in str(e)
        assert "usage limit" in str(e)


def test_raise_for_status_with_body_truncates_a_very_long_body():
    res = _FakeResponse(500, "x" * 5000)
    try:
        _raise_for_status_with_body(res)
        assert False, "expected an HTTPError to be raised"
    except requests.exceptions.HTTPError as e:
        assert len(str(e)) < 1000  # capped at 500 chars of body, not the full 5000


def test_raise_for_status_with_body_falls_back_to_the_bare_message_when_body_is_empty():
    res = _FakeResponse(432, "")
    try:
        _raise_for_status_with_body(res)
        assert False, "expected an HTTPError to be raised"
    except requests.exceptions.HTTPError as e:
        assert "432" in str(e)
        assert "response body" not in str(e)


def test_raise_for_status_with_body_does_nothing_on_success():
    res = _FakeResponse(200, "")
    _raise_for_status_with_body(res)  # must not raise


def test_search_with_tavily_surfaces_the_response_body_on_failure(monkeypatch):
    def fake_post(url, json, timeout):
        return _FakeResponse(432, '{"detail": "Invalid API key."}')

    monkeypatch.setattr("outreach_discovery.requests.post", fake_post)
    try:
        _search_with_tavily("tours Morocco", [], 6)
        assert False, "expected an HTTPError to be raised"
    except requests.exceptions.HTTPError as e:
        assert "Invalid API key" in str(e)


def test_search_with_serpapi_surfaces_the_response_body_on_failure(monkeypatch):
    def fake_get(url, params, timeout):
        return _FakeResponse(401, '{"error": "Invalid API key."}')

    monkeypatch.setattr("outreach_discovery.requests.get", fake_get)
    try:
        _search_with_serpapi("tours Morocco", [], 6)
        assert False, "expected an HTTPError to be raised"
    except requests.exceptions.HTTPError as e:
        assert "Invalid API key" in str(e)
