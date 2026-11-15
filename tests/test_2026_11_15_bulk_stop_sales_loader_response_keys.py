"""
Regression test for the Bulk Stop Sales product loaders (2026-11-15).

REPORTED: the human ticked Transfer, ClosedTour and Hotel alongside Ticket and only
Tickets ever appeared in the picker — "The product loading works only for tickets, but
not for closedtours and not for transfers and not for hotel."

ROOT CAUSE: each loader named a single wrapper key for the list response. Travel
Compositor returns the list under a SINGULAR key ("transfer", "closedTour", "hotel")
while the loaders asked for the plural ("transfers", "closedTours", "hotels"). Ticket
was the one that happened to match. A missing key is not an error — .get() returned
[], the loader reported success with zero products, and the human saw a silently
empty list.

SECOND BUG, same area: _load_tours read `optionCodes` for a closed tour's modality
list and preferred `providerCode` as its id. ContractClosedTourVO has neither —
it carries `modalityCodes` and `code`. So even once a tour loaded, applying a stop
sale would iterate an empty modality list and write nothing.

These tests pin the response shapes so a key rename elsewhere can't silently empty
the picker again.
"""
import pytest

from flows import bulk_stop_sales as bss


class _FakeClient:
    """Returns one canned payload for whichever listing endpoint is called."""

    def __init__(self, payload):
        self._payload = payload

    def get_transfers(self, supplier_id):
        return self._payload

    def get_tickets(self, supplier_id, first=0, limit=200):
        return self._payload

    def get_closed_tours(self, supplier_id, first=0, limit=200):
        return self._payload

    def get_hotels(self, supplier_id):
        return self._payload


# ── the singular wrapper key TC actually sends ──────────────────────────────

def test_transfers_load_from_singular_transfer_key():
    client = _FakeClient({"transfer": [{"id": "T1", "name": "Airport → Hotel"}]})
    items, err = bss._load_transfers(client, "123")
    assert err is None
    assert [i["name"] for i in items] == ["Airport → Hotel"]
    assert items[0]["id"] == "T1"
    assert items[0]["kind"] == "Transfer"


def test_closed_tours_load_from_singular_closedtour_key():
    client = _FakeClient({"closedTour": [{"code": "CT1", "name": "Bali 5D"}]})
    items, err = bss._load_tours(client, "123")
    assert err is None
    assert [i["name"] for i in items] == ["Bali 5D"]
    assert items[0]["kind"] == "ClosedTour"


def test_hotels_load_from_singular_hotel_key():
    client = _FakeClient({"hotel": [{"providerCode": "CAI-H1", "name": "Nile View"}]})
    items, err = bss._load_hotels(client, "123")
    assert err is None
    assert [i["name"] for i in items] == ["Nile View"]
    assert items[0]["kind"] == "Hotel"


def test_tickets_still_load_from_plural_tickets_key():
    """The one shape that already worked must keep working."""
    client = _FakeClient({"tickets": [{"code": "TK1", "name": "Bali Eco Walk"}]})
    items, err = bss._load_tickets(client, "123")
    assert err is None
    assert [i["name"] for i in items] == ["Bali Eco Walk"]
    assert items[0]["kind"] == "Ticket"


@pytest.mark.parametrize("loader,payload,expected", [
    (bss._load_transfers, {"transfers": [{"id": "T1", "name": "A"}]}, "A"),
    (bss._load_tours, {"closedTours": [{"code": "C1", "name": "B"}]}, "B"),
    (bss._load_hotels, {"hotels": [{"providerCode": "H1", "name": "C"}]}, "C"),
])
def test_plural_wrapper_key_also_accepted(loader, payload, expected):
    """Both spellings resolve, so neither account/version shape goes silently empty."""
    items, err = loader(_FakeClient(payload), "123")
    assert err is None
    assert [i["name"] for i in items] == [expected]


@pytest.mark.parametrize("loader", [
    bss._load_transfers, bss._load_tickets, bss._load_tours, bss._load_hotels,
])
def test_bare_list_response_accepted(loader):
    payload = [{"id": "X", "code": "X", "providerCode": "X", "name": "Bare"}]
    items, err = loader(_FakeClient(payload), "123")
    assert err is None
    assert [i["name"] for i in items] == ["Bare"]


# ── closed tour id / modality field names ───────────────────────────────────

def test_closed_tour_uses_code_not_provider_code_as_id():
    """GET /closedtours/{supplierId}/{code} keys on `code`; `providerCode` is the
    DMC's own reference and does not resolve."""
    client = _FakeClient({"closedTour": [
        {"code": "CT1", "providerCode": "DMC-XYZ", "name": "Bali 5D"},
    ]})
    items, _ = bss._load_tours(client, "123")
    assert items[0]["id"] == "CT1"


def test_closed_tour_reads_modality_codes():
    """_apply_tour iterates product["modalities"]; reading the wrong field leaves it
    empty and the stop sale silently writes nothing."""
    client = _FakeClient({"closedTour": [
        {"code": "CT1", "name": "Bali 5D", "modalityCodes": ["STD", "DLX"]},
    ]})
    items, _ = bss._load_tours(client, "123")
    assert items[0]["modalities"] == ["STD", "DLX"]


def test_ticket_reads_modality_codes():
    client = _FakeClient({"tickets": [
        {"code": "TK1", "name": "Bali Eco Walk", "modalityCodes": ["AM", "PM"]},
    ]})
    items, _ = bss._load_tickets(client, "123")
    assert items[0]["modalities"] == ["AM", "PM"]


# ── unrecognized shapes stay non-fatal ──────────────────────────────────────

@pytest.mark.parametrize("loader", [
    bss._load_transfers, bss._load_tickets, bss._load_tours, bss._load_hotels,
])
def test_unrecognized_shape_returns_empty_without_raising(loader):
    items, err = loader(_FakeClient({"somethingElse": "nope"}), "123")
    assert items == []
    assert err is None
