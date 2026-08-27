"""CONFIRMED REAL FOLLOW-UP (2026-08-26): a Morocco Country Scope run reported "120 search
call(s) failed... Sample error: `dmc_city: The read operation timed out`" - a plain
requests.Timeout, unlike an HTTPError, carries no provider name in its own text, so there was no
way to tell from the message alone whether Tavily, SerpAPI, or Gemini actually produced it (or
whether only one of those keys was even configured). _run_provider_search_with_diagnostics now
appends which provider(s) were actually in the fallback chain to both the printed server log and
the message returned to the UI - see outreach_discovery._configured_provider_chain's own
docstring."""
import outreach_discovery as od


def test_error_message_names_the_configured_chain_on_timeout(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-tavily-key")
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-serpapi-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    def timeout(query, domains, max_results):
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr(od, "_search_with_tavily", timeout)
    monkeypatch.setattr(od, "_search_with_serpapi", timeout)

    results, error = od._run_provider_search_with_diagnostics(
        "dmc_city", "tours Morocco local DMC", "Morocco", "tours", [], 6)
    assert results == []
    assert "read operation timed out" in error
    assert "tavily" in error and "serpapi" in error
    assert "gemini" not in error  # not configured - must not be listed as tried


def test_error_message_says_no_provider_configured_when_chain_is_empty(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    def boom(*a, **k):
        raise RuntimeError("should never be reached - mock provider runs instead")

    monkeypatch.setattr(od, "_select_and_run_provider", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    results, error = od._run_provider_search_with_diagnostics(
        "dmc_city", "tours Morocco local DMC", "Morocco", "tours", [], 6)
    assert results == []
    assert "no provider key configured" in error


def test_configured_provider_chain_reflects_env_in_order(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setenv("SERPAPI_API_KEY", "fake")
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    assert od._configured_provider_chain() == ["serpapi", "gemini"]
