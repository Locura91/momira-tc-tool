"""CONFIRMED PRODUCT-OWNER REQUEST (2026-08-26, forwarding advice from another AI tool that
matches this tool's own real bottleneck - see build_queries' own docstring): consolidate the
outreach search from 10 separate provider calls per place/theme combination down to ~4, by
asking for more results per call instead of firing more calls. Directly reduces quota usage,
total run time, and how many chances a single combination has to hit a timeout.
"""
import outreach_discovery as od


def test_with_city_makes_exactly_four_calls():
    queries = od.build_queries("Morocco", "Marrakech", "tours")
    sources = [q["source"] for q in queries]
    assert sources == ["supplier_city", "supplier_country", "reviews", "instagram"]


def test_without_city_makes_exactly_three_calls():
    """No city given - the whole city-specific call is skipped, same as before consolidation."""
    queries = od.build_queries("Morocco", "", "tours")
    sources = [q["source"] for q in queries]
    assert sources == ["supplier_country", "reviews", "instagram"]


def test_review_sites_are_one_call_across_all_three_domains():
    queries = od.build_queries("Morocco", "", "tours")
    reviews = next(q for q in queries if q["source"] == "reviews")
    assert set(reviews["domains"]) == {"tripadvisor.com", "viator.com", "getyourguide.com"}


def test_instagram_stays_its_own_separate_call():
    """Not folded into the reviews call - is_generic_name()'s source == "instagram" special
    case must keep seeing ONLY genuine Instagram results, never review-site results too."""
    queries = od.build_queries("Morocco", "", "tours")
    instagram = next(q for q in queries if q["source"] == "instagram")
    assert instagram["domains"] == ["instagram.com"]


def test_combined_supplier_queries_still_mention_all_three_supplier_types():
    queries = od.build_queries("Morocco", "Marrakech", "tours")
    city_query = next(q for q in queries if q["source"] == "supplier_city")["query"].lower()
    country_query = next(q for q in queries if q["source"] == "supplier_country")["query"].lower()
    for text in (city_query, country_query):
        assert "dmc" in text
        assert "travel agency" in text
        assert "tour guide" in text


def test_new_source_labels_are_defined():
    assert od.SOURCE_LABELS["supplier_city"] == "Local Supplier (City)"
    assert od.SOURCE_LABELS["supplier_country"] == "Local Supplier (Country)"
    assert od.SOURCE_LABELS["reviews"] == "Review Sites"


def test_old_source_labels_kept_for_previously_cached_results():
    """Old remembered/blocklist data built before the 2026-08-26 consolidation may still carry
    the old per-type source tags - they must keep resolving to a real label, not fall back to
    the raw source string."""
    assert od.SOURCE_LABELS["dmc_city"] == "DMC (City)"
    assert od.SOURCE_LABELS["agency_country"] == "Travel Agency (Country)"
