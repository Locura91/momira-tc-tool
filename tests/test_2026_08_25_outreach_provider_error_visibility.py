"""Regression tests for a real incident (product-owner report, 2026-08-25): a Morocco Country
Scope run (40 place/theme combinations) came back "0 raw results" for every single one - "not a
single supplier found, that can't be" for a country with plenty of real DMCs and guides.

Root cause class: run_provider_search() swallows EVERY exception from the search provider (a
bad/expired API key, a rate limit, a network error, a malformed response) and returns an empty
list either way, printed only to a server console nobody using the app ever sees. "The provider
genuinely found nothing for this query" and "every call to the provider failed with an error"
were indistinguishable from inside the tool - and an all-zero, all-40-combinations result is
exactly what a broken/rate-limited API key looks like (it fails identically for every query,
regardless of country), not what "no suppliers in Morocco" would ever look like.

_run_provider_search_with_diagnostics (a sibling of the existing run_provider_search, which
keeps its own plain list-returning contract for its other callers) now returns (results, error)
instead of swallowing the error, discover_suppliers collects those into
stats["provider_error_count"]/["provider_error_sample"], and outreach_tool.py surfaces a
specific "N search calls failed with an error" warning ahead of the generic "no suppliers
survived filtering" message - both for a single search and for a merged Country Scope run.
"""
import outreach_discovery as od
import outreach_tool as ot


# ---------------------------------------------------------------------------
# outreach_discovery: the diagnostics-returning provider wrapper itself
# ---------------------------------------------------------------------------

def test_diagnostics_wrapper_returns_none_error_on_success(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    results, error = od._run_provider_search_with_diagnostics(
        "dmc_country", "tours Morocco local DMC", "Morocco", "tours", [], 6)
    assert error is None
    assert isinstance(results, list) and len(results) > 0  # mock provider always returns 3


def test_diagnostics_wrapper_captures_the_error_instead_of_swallowing_it(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key-for-test")

    def boom(query, domains, max_results):
        raise RuntimeError("401 Client Error: Unauthorized")

    monkeypatch.setattr(od, "_search_with_tavily", boom)
    results, error = od._run_provider_search_with_diagnostics(
        "dmc_country", "tours Morocco local DMC", "Morocco", "tours", [], 6)
    assert results == []
    assert error is not None
    assert "401" in error


def test_plain_run_provider_search_still_swallows_and_returns_a_bare_list(monkeypatch):
    """run_provider_search itself is untouched - other/future callers still get its original
    plain-list contract, only discover_suppliers' own fan-out uses the diagnostics sibling."""
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key-for-test")

    def boom(query, domains, max_results):
        raise RuntimeError("429 rate limited")

    monkeypatch.setattr(od, "_search_with_tavily", boom)
    result = od.run_provider_search("dmc_country", "x", "Morocco", "tours", [], 6)
    assert result == []


# ---------------------------------------------------------------------------
# discover_suppliers: errors surface in stats, distinct from a genuine empty result
# ---------------------------------------------------------------------------

def test_discover_suppliers_reports_provider_errors_when_every_call_fails(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(od, "_search_with_tavily",
                        lambda query, domains, max_results: (_ for _ in ()).throw(
                            RuntimeError("401 Client Error: Unauthorized")))
    result = od.discover_suppliers("Morocco", "", "tours")
    assert result["stats"]["raw"] == 0
    assert result["stats"]["provider_error_count"] > 0
    assert "401" in result["stats"]["provider_error_sample"]


def test_discover_suppliers_reports_no_provider_errors_on_a_genuinely_clean_empty_result(monkeypatch):
    """A real zero-results case (provider ran fine, just found nothing) must NOT be flagged as
    a provider error - the two need to stay distinguishable in both directions."""
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(od, "_search_with_tavily", lambda query, domains, max_results: [])
    result = od.discover_suppliers("Morocco", "", "tours")
    assert result["stats"]["raw"] == 0
    assert result["stats"]["provider_error_count"] == 0
    assert result["stats"]["provider_error_sample"] is None


# ---------------------------------------------------------------------------
# outreach_tool: merging across a Country Scope run's many combinations
# ---------------------------------------------------------------------------

def _fresh_stats():
    return {
        "raw": 0, "after_prefilter": 0, "after_vetting": 0, "after_dedupe": 0,
        "ai_dropped": 0, "no_contact_dropped": 0, "final": 0, "used_mock_provider": False,
        "provider_error_count": 0, "provider_error_sample": None,
    }


def _job_result(provider_error_count=0, provider_error_sample=None):
    return {
        "suppliers": [],
        "stats": {"raw": 0, "after_prefilter": 0, "after_vetting": 0, "after_dedupe": 0,
                  "ai_dropped": 0, "no_contact_dropped": 0, "final": 0,
                  "used_mock_provider": False,
                  "provider_error_count": provider_error_count,
                  "provider_error_sample": provider_error_sample},
        "drop_log": [],
    }


def test_merge_accumulates_provider_error_count_across_combinations():
    stats = _fresh_stats()
    ot._merge_one_job_result([], set(), stats, "Casablanca · local DMC",
                             _job_result(provider_error_count=7, provider_error_sample="dmc_country: 401 Client Error"))
    ot._merge_one_job_result([], set(), stats, "Marrakech · local DMC",
                             _job_result(provider_error_count=8, provider_error_sample="dmc_country: 401 Client Error"))
    assert stats["provider_error_count"] == 15


def test_merge_keeps_the_first_error_sample_not_the_last():
    stats = _fresh_stats()
    ot._merge_one_job_result([], set(), stats, "Casablanca · local DMC",
                             _job_result(provider_error_count=1, provider_error_sample="dmc_country: 401 Unauthorized"))
    ot._merge_one_job_result([], set(), stats, "Marrakech · local DMC",
                             _job_result(provider_error_count=1, provider_error_sample="agency_country: timeout"))
    assert stats["provider_error_sample"] == "dmc_country: 401 Unauthorized"


def test_a_full_queue_run_where_every_combination_errors_out_is_flagged_not_silent():
    """The exact reported scenario: 40 combinations, every one comes back with raw=0 - but
    because of provider errors, not because Morocco genuinely has no suppliers."""
    merged, seen, stats, drop_log = [], set(), _fresh_stats(), []
    for i in range(40):
        ot._merge_one_job_result(merged, seen, stats, f"combo {i}",
                                 _job_result(provider_error_count=9,
                                             provider_error_sample="dmc_city: 429 Too Many Requests"),
                                 drop_log=drop_log)
    result = ot._finalize_queue_result(merged, stats, [], drop_log=drop_log)
    assert result["suppliers"] == []
    assert result["stats"]["raw"] == 0
    assert result["stats"]["provider_error_count"] == 40 * 9
    assert result["stats"]["provider_error_sample"] == "dmc_city: 429 Too Many Requests"
