"""Regression tests for a real recurrence (product owner, 2026-08-25, same day as the
concurrency revert): even with the sequential (one-at-a-time) query fan-out already deployed,
a Morocco Country Scope run still failed all 60 of its search calls - this time Tavily's own
error body (now visible via _raise_for_status_with_body) read "This request exceeds your plan's
set usage limit." Product owner: "we have to change the search time again to 20 seconds per
field."

_search_call_delay_s() / the pacing sleep in discover_suppliers()'s query fan-out is the fix -
see both docstrings for the full story and the caveat that this paces requests but does not by
itself fix a genuinely exhausted plan-level quota.

conftest.py sets SEARCH_CALL_DELAY_S=0 for the whole suite so other tests stay fast; these tests
explicitly override it back on to verify the pacing itself, using monkeypatch on time.sleep
rather than actually sleeping.
"""
import outreach_discovery as od


def test_search_call_delay_defaults_to_20_seconds(monkeypatch):
    monkeypatch.delenv("SEARCH_CALL_DELAY_S", raising=False)
    assert od._search_call_delay_s() == 20.0


def test_search_call_delay_is_configurable_via_env(monkeypatch):
    monkeypatch.setenv("SEARCH_CALL_DELAY_S", "5")
    assert od._search_call_delay_s() == 5.0


def test_search_call_delay_never_goes_negative(monkeypatch):
    monkeypatch.setenv("SEARCH_CALL_DELAY_S", "-3")
    assert od._search_call_delay_s() == 0.0


def test_search_call_delay_falls_back_to_20_on_a_bad_value(monkeypatch):
    monkeypatch.setenv("SEARCH_CALL_DELAY_S", "not-a-number")
    assert od._search_call_delay_s() == 20.0


def test_discover_suppliers_sleeps_between_each_search_call(monkeypatch):
    """One combination's fan-out is several queries (build_queries) - the pause must land
    BETWEEN them, (len(queries) - 1) times, not before the first or as a flat per-call cost
    that would double the delay when summed with the retry-once logic."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.setenv("SEARCH_CALL_DELAY_S", "20")

    sleeps = []
    monkeypatch.setattr(od.time, "sleep", lambda s: sleeps.append(s))

    result = od.discover_suppliers("Morocco", "", "tours")
    queries = od.build_queries("Morocco", "", "tours")
    assert len(sleeps) == len(queries) - 1
    assert all(s == 20.0 for s in sleeps)
    assert isinstance(result, dict)  # sanity - the pipeline still completes


def test_discover_suppliers_does_not_sleep_when_delay_is_zero(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.setenv("SEARCH_CALL_DELAY_S", "0")

    sleeps = []
    monkeypatch.setattr(od.time, "sleep", lambda s: sleeps.append(s))

    od.discover_suppliers("Morocco", "", "tours")
    assert sleeps == []
