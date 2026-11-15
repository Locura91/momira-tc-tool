"""
The price-validity code extended to ClosedTour and Hotel (2026-11-15).

PRODUCT-OWNER STANDING RULE, verbatim: "if we developed one thing for a specific product,
to add it to all the other products as well for the long run."

The feature was scoped to Ticket/Transfer/Transport on 2026-09-08. That was a scope
decision, not an API limit: ClosedTourDatasheetVO, ContractHotelCompletedVO and
ContractHotelDetailedVO all carry voucherRemarks, so the code has somewhere to live on
both. The weekly expiring-price scan now covers all five types.

Two shape hazards this pins, both of which fail SILENTLY — a record whose text cannot be
found simply reads as "no code" and is skipped, so the scan would under-report rather
than error:
  - the datasheet key is spelled 'datasheet' on some live responses and 'datasheets' in
    the API reference; both are accepted rather than guessed at.
  - Hotel has no plain `code` field, only `providerCode`. Without that fallback every
    hotel in the weekly alert would read "(unnamed)" with an empty code.
"""
from datetime import date

import pytest

import price_validity


class _FakeClient:
    """Serves one payload per product type, so a scan over all five can be exercised."""

    def __init__(self, by_type=None):
        self.by_type = by_type or {}

    def get_tickets(self, supplier_id, first=0, limit=200):
        return self.by_type.get("Ticket", {})

    def get_transfers(self, supplier_id):
        return self.by_type.get("Transfer", {})

    def get_transports(self, supplier_id):
        return self.by_type.get("Transport", {})

    def get_closed_tours(self, supplier_id, first=0, limit=200):
        return self.by_type.get("ClosedTour", {})

    def get_hotels(self, supplier_id):
        return self.by_type.get("Hotel", {})


SUPPLIER = [{"id": "123", "name": "Momira_Test"}]
TODAY = date(2026, 11, 15)


def test_all_five_product_types_are_in_scope():
    assert set(price_validity.PRODUCT_TYPES) == {
        "Ticket", "Transfer", "Transport", "ClosedTour", "Hotel"}


@pytest.mark.parametrize("ptype,payload", [
    ("ClosedTour", {"closedTour": [{"code": "CT1", "name": "Bali 5D"}]}),
    ("Hotel", {"hotel": [{"providerCode": "H1", "name": "Nile View"}]}),
])
def test_new_types_are_listed(ptype, payload):
    items = price_validity._list_services(_FakeClient({ptype: payload}), "123", ptype)
    assert len(items) == 1


# ── the datasheet key is spelled both ways ──────────────────────────────────

@pytest.mark.parametrize("key", ["datasheet", "datasheets"])
@pytest.mark.parametrize("ptype", ["Ticket", "Transfer", "ClosedTour", "Hotel"])
def test_voucher_remarks_found_under_either_datasheet_key(key, ptype):
    rec = {key: {"EN": {"voucherRemarks": "Prices confirmed (20271231)"}}}
    assert price_validity.extract_price_validity_date(
        price_validity._voucher_text_of(rec, ptype)) == date(2027, 12, 31)


@pytest.mark.parametrize("key", ["datasheet", "datasheets"])
def test_transport_reads_description_under_either_key(key):
    """Transport has no voucherRemarks field at all (2026-09-11 revert)."""
    rec = {key: {"EN": {"description": "Valid (20271231)"}}}
    assert price_validity.extract_price_validity_date(
        price_validity._voucher_text_of(rec, "Transport")) == date(2027, 12, 31)


def test_lowercase_language_key_is_accepted():
    rec = {"datasheet": {"en": {"voucherRemarks": "(20271231)"}}}
    assert price_validity.extract_price_validity_date(
        price_validity._voucher_text_of(rec, "ClosedTour")) == date(2027, 12, 31)


def test_flat_top_level_remarks_still_work():
    rec = {"voucherRemarks": "(20271231)"}
    assert price_validity.extract_price_validity_date(
        price_validity._voucher_text_of(rec, "Hotel")) == date(2027, 12, 31)


def test_record_with_no_text_reads_as_no_code_rather_than_raising():
    assert price_validity._voucher_text_of({}, "Hotel") == ""
    assert price_validity._voucher_text_of({"datasheet": "not a dict"}, "Hotel") == ""


# ── labels and codes for the new types ──────────────────────────────────────

def test_hotel_label_falls_back_to_provider_code():
    """Hotel has no plain `code`; without this every hotel reads '(unnamed)'."""
    assert price_validity._service_label({"providerCode": "H1"}) == "H1"


def test_datasheet_name_wins_over_the_bare_name():
    rec = {"name": "bare", "datasheets": {"EN": {"name": "From datasheet"}}}
    assert price_validity._service_label(rec) == "From datasheet"


# ── end to end over the new types ───────────────────────────────────────────

def test_scan_flags_an_expiring_closed_tour():
    client = _FakeClient({"ClosedTour": {"closedTour": [
        {"code": "CT1", "name": "Bali 5D",
         "datasheets": {"EN": {"voucherRemarks": "Rates valid (20261201)"}}},
    ]}})
    flagged = price_validity.scan_expiring_services(
        client, SUPPLIER, warn_ahead_days=60, today=TODAY)
    tours = [f for f in flagged if f["product_type"] == "ClosedTour"]
    assert len(tours) == 1
    assert tours[0]["valid_until"] == date(2026, 12, 1)
    assert tours[0]["code"] == "CT1"
    assert tours[0]["label"] == "Bali 5D"


def test_scan_flags_an_expiring_hotel_with_its_provider_code():
    client = _FakeClient({"Hotel": {"hotel": [
        {"providerCode": "CAI-H1", "name": "Nile View",
         "datasheet": {"EN": {"voucherRemarks": "(20261201)"}}},
    ]}})
    flagged = price_validity.scan_expiring_services(
        client, SUPPLIER, warn_ahead_days=60, today=TODAY)
    hotels = [f for f in flagged if f["product_type"] == "Hotel"]
    assert len(hotels) == 1
    assert hotels[0]["code"] == "CAI-H1"
    assert hotels[0]["label"] == "Nile View"


def test_already_expired_is_reported_with_negative_days():
    client = _FakeClient({"Hotel": {"hotel": [
        {"providerCode": "H1", "datasheet": {"EN": {"voucherRemarks": "(20251231)"}}},
    ]}})
    flagged = price_validity.scan_expiring_services(
        client, SUPPLIER, warn_ahead_days=60, today=TODAY)
    assert flagged[0]["days_remaining"] < 0


def test_records_without_a_code_are_skipped_not_guessed():
    """The scan only ever reports what a human actually stated."""
    client = _FakeClient({
        "ClosedTour": {"closedTour": [{"code": "CT1", "name": "No code here"}]},
        "Hotel": {"hotel": [{"providerCode": "H1", "name": "Also none"}]},
    })
    assert price_validity.scan_expiring_services(
        client, SUPPLIER, warn_ahead_days=60, today=TODAY) == []


def test_a_far_future_code_is_not_flagged():
    client = _FakeClient({"ClosedTour": {"closedTour": [
        {"code": "CT1", "datasheets": {"EN": {"voucherRemarks": "(20301231)"}}},
    ]}})
    assert price_validity.scan_expiring_services(
        client, SUPPLIER, warn_ahead_days=60, today=TODAY) == []


def test_one_product_type_failing_does_not_sink_the_whole_scan():
    """_list_services is best-effort per type; a broken endpoint must not hide the rest."""
    class _PartlyBroken(_FakeClient):
        def get_hotels(self, supplier_id):
            raise RuntimeError("hotel endpoint down")

    client = _PartlyBroken({"ClosedTour": {"closedTour": [
        {"code": "CT1", "datasheets": {"EN": {"voucherRemarks": "(20261201)"}}},
    ]}})
    flagged = price_validity.scan_expiring_services(
        client, SUPPLIER, warn_ahead_days=60, today=TODAY)
    assert [f["product_type"] for f in flagged] == ["ClosedTour"]
