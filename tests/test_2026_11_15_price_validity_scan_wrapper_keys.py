"""
The weekly expiring-price scan silently reported zero Transfers and Transports (2026-11-15).

FOUND BY the weekly duplicate/error audit. price_validity._list_services named one wrapper
key per product type — "tickets", "transfers", "transports". Travel Compositor returns the
Transfer and Transport lists under the SINGULAR key, so both .get() calls returned None,
became [], and the scan reported nothing for those two types. Only Ticket happened to
match, so the feature looked like it was working.

WHY IT MATTERS: Transfer and Transport are the two types the product owner confirmed carry
the validity code "for sure" (2026-11-15) — precisely the ones the scan was blind to. And
the failure is silent: _list_services swallows everything in a bare except and returns [],
which is indistinguishable from "this supplier has no expiring services".

Same bug class as the one fixed in flows/bulk_stop_sales.py the same day. Both now go
through normalize_list_response, which accepts either spelling or a bare list.
"""
import pytest

import price_validity


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get_tickets(self, supplier_id, first=0, limit=200):
        self.calls.append("tickets")
        return self._payload

    def get_transfers(self, supplier_id):
        self.calls.append("transfers")
        return self._payload

    def get_transports(self, supplier_id):
        self.calls.append("transports")
        return self._payload


@pytest.mark.parametrize("product_type,key", [
    ("Transfer", "transfer"),
    ("Transport", "transport"),
    ("Ticket", "ticket"),
])
def test_singular_wrapper_key_is_read(product_type, key):
    """The shape that was silently dropped for Transfer and Transport."""
    client = _FakeClient({key: [{"code": "X", "name": "One"}]})
    items = price_validity._list_services(client, "123", product_type)
    assert [i["code"] for i in items] == ["X"]


@pytest.mark.parametrize("product_type,key", [
    ("Transfer", "transfers"),
    ("Transport", "transports"),
    ("Ticket", "tickets"),
])
def test_plural_wrapper_key_still_read(product_type, key):
    """Neither account/version spelling may go quietly empty."""
    client = _FakeClient({key: [{"code": "X", "name": "One"}]})
    items = price_validity._list_services(client, "123", product_type)
    assert [i["code"] for i in items] == ["X"]


@pytest.mark.parametrize("product_type", ["Ticket", "Transfer", "Transport"])
def test_bare_list_response_is_read(product_type):
    client = _FakeClient([{"code": "X", "name": "One"}])
    items = price_validity._list_services(client, "123", product_type)
    assert [i["code"] for i in items] == ["X"]


@pytest.mark.parametrize("product_type", ["Ticket", "Transfer", "Transport"])
def test_unrecognized_shape_returns_empty_without_raising(product_type):
    client = _FakeClient({"somethingElse": "nope"})
    assert price_validity._list_services(client, "123", product_type) == []


def test_scan_now_finds_expiring_transfers_end_to_end():
    """The user-visible symptom: an expiring transfer that the scan used to miss entirely."""
    from datetime import date

    client = _FakeClient({"transfer": [
        {"code": "T1", "name": "Airport → Hotel",
         "datasheet": {"EN": {"voucherRemarks": "Prices confirmed (20261201)"}}},
    ]})
    flagged = price_validity.scan_expiring_services(
        client, [{"id": "123", "name": "Momira_Test"}],
        warn_ahead_days=60, today=date(2026, 11, 15))
    transfers = [f for f in flagged if f["product_type"] == "Transfer"]
    assert len(transfers) == 1
    assert transfers[0]["valid_until"] == date(2026, 12, 1)
    assert transfers[0]["days_remaining"] == 16
