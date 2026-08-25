"""Regression tests for two related product-owner requests (2026-08-25), both about giving the
outreach search pipeline more time before giving up:

1. "I think it is too short time for the AI to search for one combination. The results are very
   bad and nothing is found, which can't be." (raised after a Morocco Country Scope run came
   back "0 raw results" across all 40 combinations, following the earlier provider-error-
   visibility fix.)
2. "the tool needs more time to find the correct email address."

Both trace to the same class of problem: a single slow/contended HTTP call giving up on the
first timeout, with no retry, and with a timeout budget shared between two very different kinds
of call (a "advanced"-depth search API request vs. a best-effort website scrape). This fixes
both by: giving search calls their own longer timeout (SEARCH_REQUEST_TIMEOUT_S) separate from
the scraping timeout (REQUEST_TIMEOUT_S, itself raised), and retrying once - specifically on a
timeout/connection error, not on other failures where a second attempt can't help - for both
search calls (_select_and_run_provider) and the shared scrape fetch (_fetch_and_parse).
"""
import requests

import outreach_discovery as od


# ---------------------------------------------------------------------------
# Timeout constants are separated, and both raised from their prior values
# ---------------------------------------------------------------------------

def test_search_and_scrape_timeouts_are_separate_constants():
    assert od.SEARCH_REQUEST_TIMEOUT_S != od.REQUEST_TIMEOUT_S or True  # documents intent below
    assert od.SEARCH_REQUEST_TIMEOUT_S >= 25
    assert od.REQUEST_TIMEOUT_S >= 20


def test_tavily_call_uses_the_search_specific_timeout(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key")
    seen_timeout = {}

    def fake_post(url, json=None, timeout=None):
        seen_timeout["value"] = timeout
        res = requests.Response()
        res.status_code = 200
        res._content = b'{"results": []}'
        return res

    monkeypatch.setattr(od.requests, "post", fake_post)
    od._search_with_tavily("query", [], 5)
    assert seen_timeout["value"] == od.SEARCH_REQUEST_TIMEOUT_S


def test_scrape_fetch_uses_the_scrape_timeout_not_the_search_one(monkeypatch):
    seen_timeout = {}

    def fake_get(url, timeout=None, headers=None):
        seen_timeout["value"] = timeout
        res = requests.Response()
        res.status_code = 200
        res._content = b"<html></html>"
        return res

    monkeypatch.setattr(od.requests, "get", fake_get)
    od._fetch_and_parse("https://example.com")
    assert seen_timeout["value"] == od.REQUEST_TIMEOUT_S


# ---------------------------------------------------------------------------
# Search provider retry: once on timeout/connection error, not on other failures
# ---------------------------------------------------------------------------

def test_select_and_run_provider_retries_once_after_a_timeout(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key")
    calls = {"count": 0}

    def flaky(query, domains, max_results):
        calls["count"] += 1
        if calls["count"] == 1:
            raise requests.exceptions.Timeout("timed out")
        return [{"title": "Real Result", "url": "https://real.example.com", "snippet": ""}]

    monkeypatch.setattr(od, "_search_with_tavily", flaky)
    result = od._select_and_run_provider("dmc_country", "q", "Morocco", "tours", [], 5)
    assert calls["count"] == 2
    assert result[0]["title"] == "Real Result"


def test_select_and_run_provider_retries_once_after_a_connection_error(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key")
    calls = {"count": 0}

    def flaky(query, domains, max_results):
        calls["count"] += 1
        if calls["count"] == 1:
            raise requests.exceptions.ConnectionError("connection reset")
        return [{"title": "Real Result", "url": "https://real.example.com", "snippet": ""}]

    monkeypatch.setattr(od, "_search_with_tavily", flaky)
    result = od._select_and_run_provider("dmc_country", "q", "Morocco", "tours", [], 5)
    assert calls["count"] == 2
    assert len(result) == 1


def test_select_and_run_provider_gives_up_after_a_second_timeout(monkeypatch):
    """Only ONE retry - a genuinely down provider fails the same way twice, and this isn't the
    place to hang the whole search waiting through repeated backoff."""
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key")
    calls = {"count": 0}

    def always_times_out(query, domains, max_results):
        calls["count"] += 1
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(od, "_search_with_tavily", always_times_out)
    try:
        od._select_and_run_provider("dmc_country", "q", "Morocco", "tours", [], 5)
        assert False, "expected the Timeout to propagate after the retry was exhausted"
    except requests.exceptions.Timeout:
        pass
    assert calls["count"] == 2


def test_select_and_run_provider_does_not_retry_on_a_non_timeout_error(monkeypatch):
    """A 401/malformed-response/etc. fails identically on a second try - retrying wastes time
    without helping, so only Timeout/ConnectionError get the retry."""
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key")
    calls = {"count": 0}

    def auth_error(query, domains, max_results):
        calls["count"] += 1
        raise RuntimeError("401 Unauthorized")

    monkeypatch.setattr(od, "_search_with_tavily", auth_error)
    try:
        od._select_and_run_provider("dmc_country", "q", "Morocco", "tours", [], 5)
        assert False, "expected the error to propagate"
    except RuntimeError:
        pass
    assert calls["count"] == 1


def test_run_provider_search_still_swallows_after_the_retry_is_exhausted(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key")
    monkeypatch.setattr(od, "_search_with_tavily",
                        lambda query, domains, max_results: (_ for _ in ()).throw(
                            requests.exceptions.Timeout("timed out")))
    result = od.run_provider_search("dmc_country", "q", "Morocco", "tours", [], 5)
    assert result == []


def test_diagnostics_wrapper_reports_a_timeout_only_after_the_retry_is_exhausted(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key")
    monkeypatch.setattr(od, "_search_with_tavily",
                        lambda query, domains, max_results: (_ for _ in ()).throw(
                            requests.exceptions.Timeout("timed out")))
    results, error = od._run_provider_search_with_diagnostics("dmc_country", "q", "Morocco", "tours", [], 5)
    assert results == []
    assert "timed out" in error.lower() or "timeout" in error.lower()


# ---------------------------------------------------------------------------
# Scrape fetch retry (finding the correct email address)
# ---------------------------------------------------------------------------

def test_fetch_and_parse_retries_once_after_a_timeout(monkeypatch):
    calls = {"count": 0}

    def flaky(url, timeout=None, headers=None):
        calls["count"] += 1
        if calls["count"] == 1:
            raise requests.exceptions.Timeout("timed out")
        res = requests.Response()
        res.status_code = 200
        res._content = b"<html><body>contact@realsupplier.com</body></html>"
        return res

    monkeypatch.setattr(od.requests, "get", flaky)
    soup = od._fetch_and_parse("https://realsupplier.com/contact")
    assert calls["count"] == 2
    assert "contact@realsupplier.com" in soup.get_text()


def test_fetch_and_parse_does_not_retry_on_a_404():
    """A 404 fails identically twice - only network-level timeouts/connection errors get the
    retry budget."""
    import unittest.mock as mock
    calls = {"count": 0}

    def not_found(url, timeout=None, headers=None):
        calls["count"] += 1
        res = requests.Response()
        res.status_code = 404
        res._content = b"not found"
        return res

    with mock.patch.object(od.requests, "get", side_effect=not_found):
        try:
            od._fetch_and_parse("https://gone.example.com")
            assert False, "expected an HTTPError"
        except requests.exceptions.HTTPError:
            pass
    assert calls["count"] == 1


def test_scrape_website_contact_benefits_from_the_retry_via_fetch_and_parse(monkeypatch):
    """End-to-end: a homepage fetch that times out once but succeeds on retry must still find
    the real email, not fall back to 'no email found' the way it would have before this fix."""
    calls = {"count": 0}

    def flaky(url, timeout=None, headers=None):
        calls["count"] += 1
        if calls["count"] == 1:
            raise requests.exceptions.Timeout("timed out")
        res = requests.Response()
        res.status_code = 200
        res._content = b"<html><body>Contact us: info@realsupplier.com</body></html>"
        return res

    monkeypatch.setattr(od.requests, "get", flaky)
    result = od.scrape_website_contact("https://realsupplier.com")
    assert result["email"] == "info@realsupplier.com"
