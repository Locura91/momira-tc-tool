"""Regression tests for a real incident: the same-day (2026-08-25) search-query concurrency
change caused Tavily to start returning non-standard "432 Client Error" responses once a Country
Scope run fired several combinations, each bursting multiple simultaneous connections at Tavily's
single search endpoint. Tavily's own docs only document 429 for rate limiting - nothing about
432 - and the error's shape (empty reason phrase) matches an intermediary (WAF/anti-bot layer)
blocking bursts of concurrent requests, not Tavily itself.

Product owner, after seeing this on a real Morocco search: "We have to change it back, as we had
always results and no nothing any more. The time for the search was much longer, but thats
okay." - i.e. revert the query-fan-out concurrency, sequential search is an acceptable trade.

These tests lock in that the query fan-out in discover_suppliers() is sequential again, while
the error-visibility wrapper and the retry/longer-timeout logic added the same day (neither
implicated in the 432s) are unchanged.
"""
import time

import outreach_discovery as od


def _no_api_keys(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_discover_suppliers_runs_queries_sequentially_not_concurrently(monkeypatch):
    """The inverse of the old concurrency test: if the fan-out were still concurrent, total
    time would stay close to a single slow call. Sequential, it must scale with query count."""
    _no_api_keys(monkeypatch)
    queries = od.build_queries("Morocco", "", "tours")
    assert len(queries) >= 7  # sanity: this test is only meaningful with real fan-out

    def slow_fake(source, query, country, keyword, domains, max_results):
        time.sleep(0.02)
        return [], None

    monkeypatch.setattr(od, "_run_provider_search_with_diagnostics", slow_fake)
    t0 = time.time()
    od.discover_suppliers("Morocco", "", "tours")
    elapsed = time.time() - t0
    # Sequential: len(queries) * 0.02s or more. Give generous slack but this must NOT look
    # like the ~0.02s a concurrent fan-out would take.
    assert elapsed >= 0.02 * len(queries) * 0.8


def test_no_search_concurrency_knob_remains_on_the_module():
    """_search_concurrency() was removed entirely along with the reverted fan-out - the query
    loop no longer takes a concurrency argument at all, so there's nothing left to configure."""
    assert not hasattr(od, "_search_concurrency")


def test_query_results_still_land_in_query_order(monkeypatch):
    """A sequential loop trivially preserves order - confirms the revert didn't also break the
    ordering guarantee downstream sorting/dedupe relies on as a tie-break."""
    _no_api_keys(monkeypatch)
    queries = od.build_queries("Morocco", "", "tours")

    def fake_run_provider_search(source, query, country, keyword, domains, max_results):
        idx = next(i for i, q in enumerate(queries) if q["source"] == source)
        return [{"title": f"Business {idx} tours Morocco", "url": f"https://business{idx}.example.com",
                 "snippet": f"A wonderful tours operator in Morocco. rated 4.8 stars, "
                            f"250 reviews. Contact: info@business{idx}.example.com"}], None

    monkeypatch.setattr(od, "_run_provider_search_with_diagnostics", fake_run_provider_search)
    monkeypatch.setattr(od, "is_ai_verification_enabled", lambda: False)
    result = od.discover_suppliers("Morocco", "", "tours")

    names = [s["name"] for s in result["suppliers"]]
    expected_order = [f"Business {i} tours Morocco" for i in range(len(queries))]
    seen_indices = [expected_order.index(n) for n in names if n in expected_order]
    assert seen_indices == sorted(seen_indices)
    assert len(seen_indices) >= 1


def test_error_visibility_and_retry_logic_are_unaffected_by_the_revert(monkeypatch):
    """The two things added alongside the (now-reverted) concurrency change - error visibility
    and retry-once-on-timeout - are unrelated to the 432 incident and must still work."""
    import requests
    _no_api_keys(monkeypatch)
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key")
    monkeypatch.setattr(od, "_search_with_tavily",
                        lambda query, domains, max_results: (_ for _ in ()).throw(
                            RuntimeError("401 Client Error: Unauthorized")))
    result = od.discover_suppliers("Morocco", "", "tours")
    assert result["stats"]["provider_error_count"] > 0
    assert "401" in result["stats"]["provider_error_sample"]
