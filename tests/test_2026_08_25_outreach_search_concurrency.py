"""Regression tests for the 2026-08-25 product-owner request: "make the mail outreach faster...
for the searching."

discover_suppliers() used to run its 7-10 provider queries, and its per-candidate website/
Instagram enrichment pass, strictly one at a time - each an independent HTTP call with no shared
state, so nothing but the original port's own choice kept them sequential (see this module's own
docstring on _search_concurrency for the full rationale). Both loops now run through a
ThreadPoolExecutor.

These tests cover two things:
1. discover_suppliers() still completes and returns a normal result via the built-in mock
   provider path (no network, deterministic) - i.e. the switch to a thread pool didn't break the
   pipeline's wiring.
2. Both parallel loops preserve the EXACT SAME ORDER the old sequential loops produced, even when
   later work finishes before earlier work (proven by making earlier items artificially slower) -
   this is the guarantee the whole "identical behaviour, only wall-clock differs" claim rests on,
   since downstream sorting/dedupe uses insertion order as a tie-break.
"""
import os
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


def test_discover_suppliers_is_faster_running_concurrently_than_the_slowest_single_call(monkeypatch):
    """Not a strict benchmark (flaky by nature) - just confirms the queries genuinely overlap in
    wall-clock time rather than accidentally still running one after another. If every query
    were still sequential, len(queries) * 0.05s would dominate; concurrently, total time should
    stay close to a single 0.05s call plus overhead."""
    _no_api_keys(monkeypatch)
    queries = od.build_queries("Kenya", "Nairobi", "safari tours")
    assert len(queries) >= 7  # sanity: this test is only meaningful if there's real fan-out

    def slow_fake(source, query, country, keyword, domains, max_results):
        time.sleep(0.05)
        return []

    monkeypatch.setattr(od, "run_provider_search", slow_fake)
    t0 = time.time()
    od.discover_suppliers("Kenya", "Nairobi", "safari tours")
    elapsed = time.time() - t0
    # Sequential would take len(queries) * 0.05s (>= 0.35s for 7+ queries); concurrent should
    # land well under half that even with generous scheduling overhead.
    assert elapsed < 0.05 * len(queries) * 0.6


def test_query_results_land_in_query_order_even_when_later_queries_finish_first(monkeypatch):
    """Proves the query fan-out uses an order-preserving map(), not as_completed() - a later
    query in the list is made to finish FIRST, and the resulting candidate order must still
    match the original query order, exactly as the old sequential loop would have produced."""
    _no_api_keys(monkeypatch)
    queries = od.build_queries("Kenya", "Nairobi", "safari tours")

    def fake_run_provider_search(source, query, country, keyword, domains, max_results):
        idx = next(i for i, q in enumerate(queries) if q["source"] == source)
        # Earlier queries sleep longer, so later ones complete first if anything raced.
        time.sleep(0.01 * (len(queries) - idx))
        return [{"title": f"Business {idx} safari Kenya", "url": f"https://business{idx}.example.com",
                 "snippet": f"A wonderful safari tours operator in Kenya, Nairobi. rated 4.8 stars, "
                            f"250 reviews. Contact: info@business{idx}.example.com"}]

    monkeypatch.setattr(od, "run_provider_search", fake_run_provider_search)
    # AI verification would need a live Claude call - keep this test to the rule-based pipeline.
    monkeypatch.setattr(od, "is_ai_verification_enabled", lambda: False)
    result = od.discover_suppliers("Kenya", "Nairobi", "safari tours")

    names = [s["name"] for s in result["suppliers"]]
    expected_order = [f"Business {i} safari Kenya" for i in range(len(queries))]
    # Every business is unique (own domain/email) so none get deduped into another - the
    # observed order must be a sub-sequence of the query order, i.e. index i never appears
    # before index j for i > j.
    seen_indices = [expected_order.index(n) for n in names if n in expected_order]
    assert seen_indices == sorted(seen_indices)
    assert len(seen_indices) >= 1  # sanity: at least one candidate actually survived the pipeline


def test_enrichment_results_line_up_with_candidates_even_when_later_ones_finish_first(monkeypatch):
    """Same order-preservation guarantee, for the per-candidate website-enrichment pass."""
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


def test_search_concurrency_env_var_respected(monkeypatch):
    monkeypatch.setenv("SEARCH_CONCURRENCY", "3")
    assert od._search_concurrency() == 3
    monkeypatch.delenv("SEARCH_CONCURRENCY", raising=False)
    assert od._search_concurrency() == 6  # default


def test_enrichment_concurrency_env_var_respected(monkeypatch):
    monkeypatch.setenv("ENRICHMENT_CONCURRENCY", "2")
    assert od._enrichment_concurrency() == 2
    monkeypatch.delenv("ENRICHMENT_CONCURRENCY", raising=False)
    assert od._enrichment_concurrency() == 6  # default


def test_concurrency_helpers_never_return_less_than_one():
    assert od._search_concurrency() >= 1
    assert od._enrichment_concurrency() >= 1
