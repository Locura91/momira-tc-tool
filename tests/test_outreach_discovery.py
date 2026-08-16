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
   _MAX_COMBINATIONS/_MAX_MERGED_RESULTS constants for the rest of that rule).
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
