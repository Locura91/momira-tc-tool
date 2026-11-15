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












def test_discover_suppliers_does_not_sleep_when_delay_is_zero(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.setenv("SEARCH_CALL_DELAY_S", "0")

    sleeps = []
    monkeypatch.setattr(od.time, "sleep", lambda s: sleeps.append(s))

    od.discover_suppliers("Morocco", "", "tours")
    assert sleeps == []
