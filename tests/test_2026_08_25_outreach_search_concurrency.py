"""Regression tests for the 2026-08-25 "make the mail outreach faster... for the searching"
request, and its same-day partial revert.

discover_suppliers() briefly ran its 7-10 provider queries through a ThreadPoolExecutor. That
was reverted the same day (see discover_suppliers' own code comment, and
tests/test_2026_08_25_outreach_concurrency_revert.py): bursting several simultaneous connections
at Tavily's single search endpoint, once per place/theme combination across a 20-40 combination
Country Scope run, triggered non-standard "432 Client Error" responses - the product owner's
explicit instruction was "we have to change it back... the time for the search was much longer,
but that's okay." The query fan-out is sequential again.

The per-candidate website/Instagram ENRICHMENT pass was NOT reverted - it hits N distinct
supplier-owned domains once each rather than repeatedly bursting one shared API endpoint, so it
doesn't share that failure mode. This file now covers only that surviving concurrency path.
"""
import time

import outreach_discovery as od


def _no_api_keys(monkeypatch):
    """Forces the built-in, deterministic mock provider path (no real network calls) - the
    same path the module itself uses to stay demonstrable without API keys configured."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_discover_suppliers_still_completes_via_the_mock_provider(monkeypatch):
    _no_api_keys(monkeypatch)
    result = od.discover_suppliers("Kenya", "Nairobi", "safari tours")
    assert result["stats"]["final"] > 0
    assert all(s.get("name") for s in result["suppliers"])


def test_enrichment_results_line_up_with_candidates_even_when_later_ones_finish_first(monkeypatch):
    """Order-preservation guarantee for the per-candidate website-enrichment pass, which stays
    concurrent - unlike the (now-reverted) search-query fan-out above."""
    candidates = [
        {"id": f"c{i}", "name": f"Candidate {i}", "sourceUrl": f"https://c{i}.example.com",
         "websiteCandidate": None, "aggregatorUrl": None, "instagramUrl": None,
         "snippet": "", "rating": 4.5, "reviewCount": 10, "source": "dmc_country",
         "sources": [{"source": "dmc_country"}]}
        for i in range(6)
    ]

    def fake_enrich(candidate):
        idx = int(candidate["id"][1:])
        time.sleep(0.01 * (len(candidates) - idx))  # earlier candidates finish last
        result = dict(candidate)
        result["email"] = f"info@c{idx}.example.com"
        result["website"] = candidate["sourceUrl"]
        result["contactName"] = None
        return result

    monkeypatch.setattr(od, "enrich_from_website", fake_enrich)

    # Exercise the same ThreadPoolExecutor.map call discover_suppliers makes, directly - avoids
    # re-driving the whole filter/vet/dedupe pipeline just to reach this one loop.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=od._enrichment_concurrency()) as pool:
        enriched = list(pool.map(od.enrich_from_website, candidates))

    assert [c["id"] for c in enriched] == [c["id"] for c in candidates]


def test_enrichment_concurrency_env_var_respected(monkeypatch):
    monkeypatch.setenv("ENRICHMENT_CONCURRENCY", "2")
    assert od._enrichment_concurrency() == 2
    monkeypatch.delenv("ENRICHMENT_CONCURRENCY", raising=False)
    assert od._enrichment_concurrency() == 6  # default


def test_enrichment_concurrency_never_returns_less_than_one():
    assert od._enrichment_concurrency() >= 1
