"""Tests for the "(YYYYMMDD)" price-validity code (product owner request, 2026-09-08):

    "we will add a code like '(20271031)' to the services. This code would mean: The service is
    valid until 31. Oct. 2027, after that we have no confirmed prices from the supplier... AI
    will detect the code in the voucher remarks and will send once a week an update to the
    human, that following services XY need soon an update on the price."

Confirmed scope: Transfer, Ticket, Transport (not ClosedTour/Hotel - neither has a schema field
this would map onto in the same way, and weren't asked for). Confirmed delivery: both an in-app
banner and an email, 60 days warn-ahead.

Uses the same offline platform_store isolation every other durable-storage test relies on
(see conftest.py: PLATFORM_STORE_PATH is a fresh temp SQLite file, no DATABASE_URL).
"""
from datetime import date, timedelta

import pytest

import platform_store
import price_validity as pv


@pytest.fixture(autouse=True)
def _clean_price_validity_namespace():
    for key in list(platform_store.get_namespace(pv._NAMESPACE).keys()):
        platform_store.delete(pv._NAMESPACE, key)
    yield
    for key in list(platform_store.get_namespace(pv._NAMESPACE).keys()):
        platform_store.delete(pv._NAMESPACE, key)


# ---------------------------------------------------------------------------
# encode / extract / strip round-trip
# ---------------------------------------------------------------------------

def test_encode_matches_the_product_owners_own_real_example():
    assert pv.encode_price_validity_code("2027-10-31") == "(20271031)"


def test_encode_accepts_a_real_date_object_too():
    assert pv.encode_price_validity_code(date(2027, 10, 31)) == "(20271031)"


def test_encode_returns_empty_string_for_blank_or_unparseable_input():
    assert pv.encode_price_validity_code("") == ""
    assert pv.encode_price_validity_code(None) == ""
    assert pv.encode_price_validity_code("not a date") == ""


def test_extract_finds_the_code_inside_real_voucher_text():
    text = "Meet at the harbor gate 30 minutes early.\n(20271031)"
    assert pv.extract_price_validity_date(text) == date(2027, 10, 31)


def test_extract_ignores_an_8_digit_parenthetical_that_isnt_a_plausible_date():
    # e.g. a phone number or booking reference that happens to be 8 digits in parens.
    assert pv.extract_price_validity_date("Call us: (12345678)") is None


def test_extract_returns_none_for_no_code_at_all():
    assert pv.extract_price_validity_date("Just a normal voucher remark.") is None
    assert pv.extract_price_validity_date("") is None
    assert pv.extract_price_validity_date(None) is None


def test_extract_prefers_the_last_plausible_code_when_more_than_one_is_present():
    text = "Old note (20250101) still here.\n(20271031)"
    assert pv.extract_price_validity_date(text) == date(2027, 10, 31)


def test_strip_removes_the_code_and_leaves_the_rest_of_the_text_intact():
    text = "Meet at the harbor gate 30 minutes early.\n(20271031)"
    assert pv.strip_price_validity_code(text) == "Meet at the harbor gate 30 minutes early."


def test_strip_leaves_an_implausible_parenthetical_number_untouched():
    text = "Call us: (12345678)"
    assert pv.strip_price_validity_code(text) == text


# ---------------------------------------------------------------------------
# with_price_validity_code - the shared voucher-text composition step
# ---------------------------------------------------------------------------

def test_with_price_validity_code_appends_the_code_when_a_date_is_set():
    result = pv.with_price_validity_code("Standard voucher text.",
                                          {"price_valid_until_date": "2027-10-31"})
    assert result == "Standard voucher text.\n(20271031)"


def test_with_price_validity_code_replaces_rather_than_duplicates_an_existing_code():
    # The real production risk: re-publishing an already-live service must REPLACE the old date,
    # never pile up a second "(YYYYMMDD)" alongside it.
    existing_text = "Standard voucher text.\n(20250101)"
    result = pv.with_price_validity_code(existing_text, {"price_valid_until_date": "2027-10-31"})
    assert result.count("(") == 1
    assert "(20271031)" in result
    assert "(20250101)" not in result


def test_with_price_validity_code_strips_an_old_code_when_the_date_field_is_cleared():
    existing_text = "Standard voucher text.\n(20250101)"
    result = pv.with_price_validity_code(existing_text, {"price_valid_until_date": ""})
    assert "(" not in result
    assert result == "Standard voucher text."


def test_with_price_validity_code_is_a_no_op_on_blank_text_and_blank_date():
    assert pv.with_price_validity_code("", {}) == ""
    assert pv.with_price_validity_code(None, {"price_valid_until_date": None}) == ""


# ---------------------------------------------------------------------------
# scan_expiring_services
# ---------------------------------------------------------------------------

class _FakeClient:
    def __init__(self, tickets=None, transfers=None, transports=None):
        self._tickets = tickets or {}
        self._transfers = transfers or {}
        self._transports = transports or {}

    def get_tickets(self, supplier_id, first=0, limit=200):
        return {"tickets": self._tickets.get(supplier_id, [])}

    def get_transfers(self, supplier_id):
        return {"transfers": self._transfers.get(supplier_id, [])}

    def get_transports(self, supplier_id):
        return {"transports": self._transports.get(supplier_id, [])}


def _ticket(code, name, voucher_remarks):
    return {"code": code, "datasheet": {"EN": {"name": name, "voucherRemarks": voucher_remarks}}}


def test_scan_flags_an_expired_service_and_one_expiring_soon_but_not_a_healthy_one():
    today = date(2026, 9, 8)
    tickets = {
        "1": [
            _ticket("RAK-T1", "Atlas Day Trip", "Some remark.\n(20260801)"),   # expired
            _ticket("RAK-T2", "Desert Tour", "Some remark.\n(20261015)"),      # 37 days out - within 60
            _ticket("RAK-T3", "City Walk", "Some remark.\n(20280101)"),        # healthy, far out
            _ticket("RAK-T4", "No Code Tour", "Just a plain remark, no code at all."),
        ]
    }
    client = _FakeClient(tickets=tickets)
    suppliers = [{"id": "1", "commercialName": "Momira_Test"}]

    flagged = pv.scan_expiring_services(client, suppliers, today=today)
    codes = [f["code"] for f in flagged]

    assert "RAK-T1" in codes
    assert "RAK-T2" in codes
    assert "RAK-T3" not in codes
    assert "RAK-T4" not in codes
    # Soonest/most-overdue first.
    assert flagged[0]["code"] == "RAK-T1"
    assert flagged[0]["days_remaining"] < 0


def test_scan_reads_transport_from_description_not_voucher_remarks():
    today = date(2026, 9, 8)
    transports = {
        "1": [{"code": "TRP-1", "datasheet": {"EN": {"name": "Airport Run",
                                                        "description": "Standard transfer.\n(20260901)"}}}]
    }
    client = _FakeClient(transports=transports)
    suppliers = [{"id": "1", "commercialName": "Momira_Test"}]

    flagged = pv.scan_expiring_services(client, suppliers, today=today)
    assert len(flagged) == 1
    assert flagged[0]["product_type"] == "Transport"
    assert flagged[0]["code"] == "TRP-1"


def test_scan_never_raises_when_one_suppliers_listing_fails():
    class _BrokenClient(_FakeClient):
        def get_tickets(self, supplier_id, first=0, limit=200):
            raise RuntimeError("simulated API failure")

    client = _BrokenClient()
    suppliers = [{"id": "1", "commercialName": "Momira_Broken"}]
    # Must not raise - a failing supplier/product-type contributes nothing, the scan continues.
    assert pv.scan_expiring_services(client, suppliers) == []


def test_scan_with_no_suppliers_returns_empty():
    assert pv.scan_expiring_services(_FakeClient(), []) == []


# ---------------------------------------------------------------------------
# weekly cadence (is_due / mark_reviewed) - same pattern as weekly_review.py
# ---------------------------------------------------------------------------

def test_is_due_on_first_ever_check():
    assert pv.is_due() is True


def test_mark_reviewed_makes_is_due_false_immediately_after():
    pv.mark_reviewed(flagged_count=3)
    assert pv.is_due() is False


def test_is_due_again_after_the_review_interval_has_passed():
    from datetime import datetime, timezone
    state = {"last_reviewed_at": (datetime.now(timezone.utc) - timedelta(days=pv.REVIEW_INTERVAL_DAYS + 1)).isoformat()}
    platform_store.set(pv._NAMESPACE, pv._STATE_KEY, state)
    assert pv.is_due() is True
