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

FOLLOW-UP THE SAME DAY: a Gemini-only fallback then hit a second real limit - its free tier is
ALSO capped at just 5 requests/MINUTE for gemini-2.5-flash (confirmed by the actual 429
RESOURCE_EXHAUSTED error), and since Tavily's plan quota was still exhausted, nearly every call
was falling through to Gemini and blowing straight past that limit. Product owner: "I have
SERPAPI Key with me" - confirmed choice was to insert SerpAPI as a middle step: Tavily -> SerpAPI
-> Gemini, each tried in order, so Gemini is reached only once BOTH real search APIs have
failed. See the chain tests at the bottom of this file.

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


def test_generate_content_is_called_with_a_client_side_timeout(monkeypatch):
    """CONFIRMED PRODUCT-OWNER REPORT (2026-08-25): "now each search combination is taking
    around 1 to 3 minutes when I search for outreach Mails, is that normal?" - traced to
    _search_with_gemini_grounding's generate_content call having NO client-side timeout at all,
    unlike _search_with_tavily/_search_with_serpapi (both pass timeout=SEARCH_REQUEST_TIMEOUT_S).
    Fixed by adding http_options={"timeout": <ms>} to the call - NOT a bare "timeout" key, which
    fails pydantic validation entirely (see translator.py's module docstring for that exact
    bug). This test locks in that the timeout is actually passed through to the SDK call, capped
    at the same SEARCH_REQUEST_TIMEOUT_S budget the other two providers already use."""
    seen_config = {}

    def fake_generate_content(model, contents, config):
        seen_config.update(config)
        return _ns(candidates=[])

    client = _ns(models=_ns(generate_content=fake_generate_content))
    monkeypatch.setattr(od, "_get_gemini_client", lambda: client)

    od._search_with_gemini_grounding("tours", [], 10)

    assert seen_config.get("http_options") == {"timeout": od.SEARCH_REQUEST_TIMEOUT_S * 1000}


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


# ---------------------------------------------------------------------------
# Three-way chain: Tavily -> SerpAPI -> Gemini (2026-08-25, same day, after the 5 RPM finding)
# ---------------------------------------------------------------------------

def test_full_chain_falls_through_tavily_and_serpapi_to_gemini(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-tavily-key")
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-serpapi-key")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    calls = []

    def tavily_boom(q, d, m):
        calls.append("tavily")
        raise requests.exceptions.HTTPError("432 plan usage limit exceeded")

    def serpapi_boom(q, d, m):
        calls.append("serpapi")
        raise requests.exceptions.HTTPError("429 rate limited")

    def gemini_ok(q, d, m):
        calls.append("gemini")
        return [{"title": "Gemini saved it", "url": "https://x.com", "snippet": ""}]

    monkeypatch.setattr(od, "_search_with_tavily", tavily_boom)
    monkeypatch.setattr(od, "_search_with_serpapi", serpapi_boom)
    monkeypatch.setattr(od, "_search_with_gemini_grounding", gemini_ok)

    result = od._select_and_run_provider("dmc_country", "q", "Morocco", "tours", [], 5)
    assert calls == ["tavily", "serpapi", "gemini"]  # tried in this exact order, no skips
    assert result == [{"title": "Gemini saved it", "url": "https://x.com", "snippet": ""}]


def test_serpapi_succeeding_stops_the_chain_before_gemini(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-tavily-key")
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-serpapi-key")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    def tavily_boom(q, d, m):
        raise requests.exceptions.HTTPError("432 plan usage limit exceeded")

    def serpapi_ok(q, d, m):
        return [{"title": "SerpAPI result", "url": "https://y.com", "snippet": ""}]

    def gemini_should_not_be_called(q, d, m):
        raise AssertionError("Gemini must not be reached when SerpAPI already succeeded")

    monkeypatch.setattr(od, "_search_with_tavily", tavily_boom)
    monkeypatch.setattr(od, "_search_with_serpapi", serpapi_ok)
    monkeypatch.setattr(od, "_search_with_gemini_grounding", gemini_should_not_be_called)

    result = od._select_and_run_provider("dmc_country", "q", "Morocco", "tours", [], 5)
    assert result == [{"title": "SerpAPI result", "url": "https://y.com", "snippet": ""}]


def test_all_three_configured_and_all_three_fail_raises_the_last_error(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-tavily-key")
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-serpapi-key")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    monkeypatch.setattr(od, "_search_with_tavily",
                        lambda q, d, m: (_ for _ in ()).throw(requests.exceptions.HTTPError("tavily 432")))
    monkeypatch.setattr(od, "_search_with_serpapi",
                        lambda q, d, m: (_ for _ in ()).throw(requests.exceptions.HTTPError("serpapi 429")))
    monkeypatch.setattr(od, "_search_with_gemini_grounding",
                        lambda q, d, m: (_ for _ in ()).throw(
                            requests.exceptions.HTTPError("gemini 429 RESOURCE_EXHAUSTED")))

    try:
        od._select_and_run_provider("dmc_country", "q", "Morocco", "tours", [], 5)
        assert False, "expected an error to propagate once every provider in the chain failed"
    except requests.exceptions.HTTPError as e:
        assert "gemini" in str(e)  # the LAST provider tried is what's surfaced


def test_a_provider_with_no_key_is_skipped_entirely_not_counted_as_failed(monkeypatch):
    """SerpAPI is unset here - it must never be attempted, even indirectly, so the chain goes
    straight from a failing Tavily to Gemini."""
    monkeypatch.setenv("TAVILY_API_KEY", "fake-tavily-key")
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    def tavily_boom(q, d, m):
        raise requests.exceptions.HTTPError("432 plan usage limit exceeded")

    def serpapi_should_not_be_called(q, d, m):
        raise AssertionError("SerpAPI must not be called - no SERPAPI_API_KEY is configured")

    monkeypatch.setattr(od, "_search_with_tavily", tavily_boom)
    monkeypatch.setattr(od, "_search_with_serpapi", serpapi_should_not_be_called)
    monkeypatch.setattr(od, "_search_with_gemini_grounding",
                        lambda q, d, m: [{"title": "Gemini", "url": "https://x.com", "snippet": ""}])

    result = od._select_and_run_provider("dmc_country", "q", "Morocco", "tours", [], 5)
    assert result == [{"title": "Gemini", "url": "https://x.com", "snippet": ""}]
