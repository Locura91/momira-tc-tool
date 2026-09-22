"""Regression tests for a real product-owner request (2026-09-22, verbatim):

    "For the loac l Supplier search: The search result is too much focused on DMC, we are
    missing a bit local suppliers with a speciality - like Cruise Sailing in Thailand, I only
    get results for DMC"

Root cause: build_queries() appended a fixed, generic operator-type phrase ("local DMC, travel
agency, tour operator, or private tour guide") directly after the specialty keyword with equal
weight - e.g. "Cruise Sailing Bangkok Thailand local DMC, travel agency, tour operator, or
private tour guide". Search providers latch onto "DMC" as a strong, distinctive industry term
and surface generic DMC directories/aggregators over a niche specialty match. Fixed by rewording
the query so the specialty keyword is the clear subject ("<keyword> specialist in <place>") and
the operator types are a parenthetical hint, not a second co-equal subject - all while staying
within the same call budget established by the 2026-08-26 consolidation (still ONE combined call
per scope; see test_2026_08_26_outreach_query_consolidation.py, which this file does not
duplicate).
"""
import outreach_discovery as od


def test_specialty_keyword_leads_the_city_query_as_the_subject():
    queries = od.build_queries("Thailand", "", "Cruise Sailing")
    country_query = next(q for q in queries if q["source"] == "supplier_country")["query"]
    assert country_query.startswith("Cruise Sailing specialist")


def test_specialty_keyword_leads_the_city_query_when_city_given():
    queries = od.build_queries("Thailand", "Phuket", "Cruise Sailing")
    city_query = next(q for q in queries if q["source"] == "supplier_city")["query"]
    assert city_query.startswith("Cruise Sailing specialist")
    assert "Phuket" in city_query
    assert "Thailand" in city_query


def test_operator_types_are_still_mentioned_as_a_hint_not_the_subject():
    # The fix must not drop DMC/agency/guide phrasing entirely (that's still a useful signal
    # for the search provider) - it just must not lead the query or dominate it.
    queries = od.build_queries("Thailand", "Phuket", "Cruise Sailing")
    for source in ("supplier_city", "supplier_country"):
        q = next(x for x in queries if x["source"] == source)["query"]
        assert "dmc" in q.lower()
        assert "travel agency" in q.lower()
        assert "tour guide" in q.lower()
        # not leading the query anymore
        assert not q.lower().startswith("local dmc")


def test_call_count_budget_is_unchanged():
    """CONFIRMED PRODUCT-OWNER REQUEST (2026-08-26): must stay at the consolidated call count -
    this fix is a rewording only, never a new API call."""
    with_city = od.build_queries("Thailand", "Phuket", "Cruise Sailing")
    assert len(with_city) == 4
    without_city = od.build_queries("Thailand", "", "Cruise Sailing")
    assert len(without_city) == 3
