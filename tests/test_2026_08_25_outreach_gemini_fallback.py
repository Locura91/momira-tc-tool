"""Regression tests for a real recurrence, 2026-08-25 (same day as the Tavily 432/plan-limit
incident chain - see test_2026_08_25_outreach_provider_error_body.py and
test_2026_08_25_outreach_search_call_pacing.py's own removal): once Tavily's error body
confirmed a genuine plan usage-limit exhaustion, the product owner asked "what is a free tool I
could use for only this search? Could I use Gemini Free Tier?" - checked against Google's own
pricing page: Gemini 2.5 Flash's free tier includes Grounding with Google Search, free up to 500
requests/day, no credit card required. GEMINI_API_KEY is already a config key this codebase uses
(translator.py's GeminiTranslator) - the same key works here.

CONFIRMED PRODUCT-OWNER CHOICE: automatic fallback (try Tavily first; on failure, retry with
Gemini for that same call), not a manual switch, and not Gemini-as-primary.

_search_with_gemini_grounding builds {"title","url","snippet"} results from Gemini's grounding
metadata (a generated answer + cited source chunks), not a plain results array like Tavily/
SerpAPI - these tests use a fake genai client (via the injectable _get_gemini_client) rather
than the real google-genai SDK or network calls.
"""
import types

import requests

import outreach_discovery as od


# ---------------------------------------------------------------------------
# _search_with_gemini_grounding - parsing the grounding metadata
# ---------------------------------------------------------------------------

def _ns(**kwargs):
    """A tiny attribute-style stand-in for the google-genai SDK's pydantic response objects -
    matches how the real SDK exposes fields (response.candidates[0].grounding_metadata...),
    not dict-style .get()."""
    return types.SimpleNamespace(**kwargs)


def _fake_client(candidates):
    response = _ns(candidates=candidates)
    client = _ns(models=_ns(generate_content=lambda model, contents, config: response))
    return client


def test_builds_results_from_grounding_chunks_with_matching_support_snippets(monkeypatch):
    chunks = [
        _ns(web=_ns(uri="https://example-dmc.com", title="Example DMC")),
        _ns(web=_ns(uri="https://another-guide.com", title="Another Guide")),
    ]
    supports = [
        _ns(segment=_ns(text="Example DMC offers private tours in Marrakech."),
           grounding_chunk_indices=[0]),
        _ns(segment=_ns(text="Another Guide is a highly rated local operator."),
           grounding_chunk_indices=[1]),
    ]
    grounding = _ns(grounding_chunks=chunks, grounding_supports=supports)
    candidates = [_ns(grounding_metadata=grounding)]
    monkeypatch.setattr(od, "_get_gemini_client", lambda: _fake_client(candidates))
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

    results = od._search_with_gemini_grounding("private tours Marrakech", [], 10)
    assert len(results) == 2
    assert results[0] == {"title": "Example DMC", "url": "https://example-dmc.com",
                          "snippet": "Example DMC offers private tours in Marrakech."}
    assert results[1]["url"] == "https://another-guide.com"
    assert "Another Guide is a highly rated" in results[1]["snippet"]


def test_falls_back_to_title_when_no_support_segment_cites_the_chunk(monkeypatch):
    chunks = [_ns(web=_ns(uri="https://example.com", title="Example Co"))]
    grounding = _ns(grounding_chunks=chunks, grounding_supports=[])
    candidates = [_ns(grounding_metadata=grounding)]
    monkeypatch.setattr(od, "_get_gemini_client", lambda: _fake_client(candidates))

    results = od._search_with_gemini_grounding("tours", [], 10)
    assert results == [{"title": "Example Co", "url": "https://example.com", "snippet": "Example Co"}]


def test_a_chunk_with_no_url_is_skipped(monkeypatch):
    chunks = [_ns(web=_ns(uri="", title="No URL Here")),
             _ns(web=_ns(uri="https://real.com", title="Real"))]
    grounding = _ns(grounding_chunks=chunks, grounding_supports=[])
    candidates = [_ns(grounding_metadata=grounding)]
    monkeypatch.setattr(od, "_get_gemini_client", lambda: _fake_client(candidates))

    results = od._search_with_gemini_grounding("tours", [], 10)
    assert len(results) == 1
    assert results[0]["url"] == "https://real.com"


def test_results_are_capped_at_max_results(monkeypatch):
    chunks = [_ns(web=_ns(uri=f"https://site{i}.com", title=f"Site {i}")) for i in range(5)]
    grounding = _ns(grounding_chunks=chunks, grounding_supports=[])
    candidates = [_ns(grounding_metadata=grounding)]
    monkeypatch.setattr(od, "_get_gemini_client", lambda: _fake_client(candidates))

    results = od._search_with_gemini_grounding("tours", [], 2)
    assert len(results) == 2


def test_no_candidates_returns_empty_list(monkeypatch):
    monkeypatch.setattr(od, "_get_gemini_client", lambda: _fake_client([]))
    assert od._search_with_gemini_grounding("tours", [], 10) == []


def test_no_grounding_metadata_returns_empty_list(monkeypatch):
    candidates = [_ns(grounding_metadata=None)]
    monkeypatch.setattr(od, "_get_gemini_client", lambda: _fake_client(candidates))
    assert od._search_with_gemini_grounding("tours", [], 10) == []


# ---------------------------------------------------------------------------
# _select_and_run_provider - automatic Tavily -> Gemini fallback
# ---------------------------------------------------------------------------

def test_gemini_is_tried_automatically_when_tavily_fails_outright(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-tavily-key")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)

    def boom(query, domains, max_results):
        raise requests.exceptions.HTTPError(
            "432 Client Error — response body: usage limit exceeded")

    calls = []
    monkeypatch.setattr(od, "_search_with_tavily", boom)
    monkeypatch.setattr(od, "_search_with_gemini_grounding",
                        lambda q, d, m: calls.append("gemini") or [{"title": "Fallback Result",
                                                                     "url": "https://x.com",
                                                                     "snippet": ""}])
    result = od._select_and_run_provider("dmc_country", "q", "Morocco", "tours", [], 5)
    assert calls == ["gemini"]
    assert result == [{"title": "Fallback Result", "url": "https://x.com", "snippet": ""}]


def test_gemini_fallback_only_tried_after_tavilys_own_timeout_retry_is_exhausted(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-tavily-key")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)

    tavily_calls = []

    def always_times_out(query, domains, max_results):
        tavily_calls.append(1)
        raise requests.exceptions.Timeout("timed out")

    gemini_calls = []
    monkeypatch.setattr(od, "_search_with_tavily", always_times_out)
    monkeypatch.setattr(od, "_search_with_gemini_grounding",
                        lambda q, d, m: gemini_calls.append(1) or [])
    od._select_and_run_provider("dmc_country", "q", "Morocco", "tours", [], 5)
    assert len(tavily_calls) == 2  # the existing retry-once-on-timeout behavior, unchanged
    assert len(gemini_calls) == 1  # THEN gemini, once, after that retry is exhausted


def test_no_gemini_fallback_when_gemini_api_key_is_not_configured(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-tavily-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)

    def boom(query, domains, max_results):
        raise requests.exceptions.HTTPError("401 unauthorized")

    monkeypatch.setattr(od, "_search_with_tavily", boom)
    try:
        od._select_and_run_provider("dmc_country", "q", "Morocco", "tours", [], 5)
        assert False, "expected the original error to propagate with no Gemini key configured"
    except requests.exceptions.HTTPError as e:
        assert "401" in str(e)


def test_tavily_success_never_calls_gemini_at_all(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-tavily-key")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    monkeypatch.setattr(od, "_search_with_tavily",
                        lambda q, d, m: [{"title": "Real result", "url": "https://x.com", "snippet": ""}])

    def gemini_should_not_be_called(q, d, m):
        raise AssertionError("Gemini must not be called when the primary provider succeeds")

    monkeypatch.setattr(od, "_search_with_gemini_grounding", gemini_should_not_be_called)
    result = od._select_and_run_provider("dmc_country", "q", "Morocco", "tours", [], 5)
    assert result == [{"title": "Real result", "url": "https://x.com", "snippet": ""}]


def test_gemini_becomes_primary_when_it_is_the_only_key_configured(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    monkeypatch.setattr(od, "_search_with_gemini_grounding",
                        lambda q, d, m: [{"title": "Gemini primary", "url": "https://x.com", "snippet": ""}])
    result = od._select_and_run_provider("dmc_country", "q", "Morocco", "tours", [], 5)
    assert result == [{"title": "Gemini primary", "url": "https://x.com", "snippet": ""}]


def test_gemini_as_primary_failing_raises_with_no_further_fallback(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    def boom(q, d, m):
        raise requests.exceptions.HTTPError("500 server error")

    monkeypatch.setattr(od, "_search_with_gemini_grounding", boom)
    try:
        od._select_and_run_provider("dmc_country", "q", "Morocco", "tours", [], 5)
        assert False, "expected the error to propagate - Gemini has no fallback of its own"
    except requests.exceptions.HTTPError as e:
        assert "500" in str(e)


def test_serpapi_primary_also_falls_back_to_gemini(monkeypatch):
    """When TAVILY_API_KEY is unset, SerpAPI is the primary (unchanged rule) - Gemini fallback
    applies there too, not just after Tavily specifically."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-serpapi-key")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    def boom(q, d, m):
        raise requests.exceptions.HTTPError("429 rate limited")

    monkeypatch.setattr(od, "_search_with_serpapi", boom)
    monkeypatch.setattr(od, "_search_with_gemini_grounding",
                        lambda q, d, m: [{"title": "Gemini saved it", "url": "https://x.com", "snippet": ""}])
    result = od._select_and_run_provider("dmc_country", "q", "Morocco", "tours", [], 5)
    assert result == [{"title": "Gemini saved it", "url": "https://x.com", "snippet": ""}]
