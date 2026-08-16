"""Tests for the Find & Contact Suppliers selection/dedupe rules.

CONFIRMED PRODUCT-OWNER REQUEST (2026-08-16):
1. "Tick all result supplier only if there is a mail found for the Email. Otherwise it is
   not needed" - a supplier row is only pre-ticked for sending when a real email address was
   actually found, never just because a website or social link exists (see to_supplier_record
   and merge_supplier_records in outreach_discovery.py).
2. "only one supplier at all, even if the supplier has multiple matches. We can contact each
   supplier only once" - dedupe_suppliers_by_contact() is the mechanism that collapses two rows
   sharing an email or social link into one merged row (also exercised a second time, across an
   entire combination run, by outreach_tool._run_queued_searches - see that module's own
   _PER_COMBINATION_RESULTS/_MAX_MERGED_RESULTS constants for the rest of that rule).
3. "Please no limitation, but search per each combination only one supplier, so the search is
   faster" - a follow-up: the country-scope screen's earlier hard cap on how many combinations
   can run at once was removed, and discover_suppliers() gained a `max_results` override that
   caps candidates down BEFORE the expensive AI-verification/website-enrichment steps rather
   than only at the end, so passing max_results=1 per combination genuinely saves time rather
   than just trimming the display.
"""
import outreach_discovery as od


def _candidate(**overrides):
    base = {"name": "Nile Adventures", "email": None, "website": None,
            "instagramUrl": None, "facebookUrl": None, "aggregatorUrl": None,
            "rating": None, "reviewCount": None, "snippet": None, "sources": []}
    base.update(overrides)
    return base


def test_to_supplier_record_selected_true_only_with_email():
    record = od.to_supplier_record(_candidate(email="info@nile.com"), "Egypt", "Nile Cruise")
    assert record["selected"] is True


def test_to_supplier_record_not_selected_with_only_website():
    record = od.to_supplier_record(_candidate(website="https://nile.com"), "Egypt", "Nile Cruise")
    assert record["selected"] is False


def test_to_supplier_record_not_selected_with_only_social():
    record = od.to_supplier_record(_candidate(instagramUrl="https://instagram.com/nile"),
                                    "Egypt", "Nile Cruise")
    assert record["selected"] is False


def test_to_supplier_record_not_selected_with_nothing_at_all():
    record = od.to_supplier_record(_candidate(), "Egypt", "Nile Cruise")
    assert record["selected"] is False


def _supplier(**overrides):
    base = {"name": "Nile Adventures", "email": None, "social": None, "socialPlatform": None,
            "website": None, "listingUrl": None, "listingSource": None, "selectionReason": "",
            "reviewSummary": "", "rating": None, "reviewCount": None, "sources": [],
            "selected": False, "isMock": False}
    base.update(overrides)
    return base


def test_merge_supplier_records_selected_true_when_either_side_has_email():
    merged = od.merge_supplier_records(
        _supplier(website="https://nile.com"),
        _supplier(email="info@nile.com"),
    )
    assert merged["email"] == "info@nile.com"
    assert merged["selected"] is True


def test_merge_supplier_records_not_selected_when_neither_side_has_email():
    merged = od.merge_supplier_records(
        _supplier(website="https://nile.com"),
        _supplier(social="https://instagram.com/nile"),
    )
    assert merged["email"] is None
    assert merged["selected"] is False


def test_dedupe_suppliers_by_contact_collapses_same_email():
    suppliers = [
        _supplier(name="Nile Adventures", email="info@nile.com", website="https://nile.com"),
        _supplier(name="Nile Adventures Egypt", email="INFO@Nile.com", rating=4.5),
    ]
    result = od.dedupe_suppliers_by_contact(suppliers)
    assert len(result) == 1
    # The higher-rated side's rating survives the merge, but the email-bearing website is kept too.
    assert result[0]["rating"] == 4.5
    assert result[0]["website"] == "https://nile.com"


def test_dedupe_suppliers_by_contact_collapses_same_social():
    suppliers = [
        _supplier(name="Nile Adventures", social="https://instagram.com/nileadventures/"),
        _supplier(name="Nile Adv.", social="https://www.instagram.com/nileadventures"),
    ]
    result = od.dedupe_suppliers_by_contact(suppliers)
    assert len(result) == 1


def test_dedupe_suppliers_by_contact_keeps_distinct_suppliers_separate():
    suppliers = [
        _supplier(name="Nile Adventures", email="info@nile.com"),
        _supplier(name="Luxor Tours", email="info@luxortours.com"),
    ]
    result = od.dedupe_suppliers_by_contact(suppliers)
    assert len(result) == 2


# ======================================================================
# cap_candidates_by_rating - the "one supplier per combination, so it's faster" mechanism
# ======================================================================
def test_cap_candidates_by_rating_keeps_the_single_highest_rated():
    candidates = [
        {"name": "Low Rated Co", "rating": 3.2},
        {"name": "Best Nile Tours", "rating": 4.9},
        {"name": "Mid Co", "rating": 4.0},
    ]
    capped = od.cap_candidates_by_rating(candidates, 1)
    assert len(capped) == 1
    assert capped[0]["name"] == "Best Nile Tours"


def test_cap_candidates_by_rating_puts_missing_ratings_last():
    candidates = [
        {"name": "No Rating Co", "rating": None},
        {"name": "Rated Co", "rating": 4.1},
    ]
    capped = od.cap_candidates_by_rating(candidates, 1)
    assert capped[0]["name"] == "Rated Co"


def test_cap_candidates_by_rating_never_returns_zero_even_if_n_is_zero():
    candidates = [{"name": "Only Co", "rating": 4.5}]
    capped = od.cap_candidates_by_rating(candidates, 0)
    assert len(capped) == 1


def test_cap_candidates_by_rating_returns_everything_if_n_exceeds_the_list():
    candidates = [{"name": "A", "rating": 4.0}, {"name": "B", "rating": 3.0}]
    capped = od.cap_candidates_by_rating(candidates, 10)
    assert len(capped) == 2
