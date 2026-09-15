"""Regression tests for a real customer-facing bug (product owner, 2026-09-16):

    Supplier migration (Move Supplier flow) failed at the create step for every Transfer
    route tried, with:
        java.lang.IllegalArgumentException: An instance of a null PK has been incorrectly
        provided for this find operation.
    Reported live: "Cairo - Alexandria" and "Alexandria - Cairo", both 0% moved.

Root cause: migrate_transfer/migrate_transport build their create payload as a wholesale
`dict(record)` copy of a raw GET response (the same "wholesale copy" pattern already known
to trip Travel Compositor validation once before, for Transport's airlineCode - see
test_2026_09_11_supplier_migration_transport_airlinecode.py). A raw GET response includes
"id": null on nested child rows (price brackets, supplements, properties, etc. -
Hibernate-assigned child-row PKs that the write-side Pydantic schemas in schemas.py never
model at all, since they only matter on reads). Travel Compositor's backend treats the mere
PRESENCE of an "id" key on any nested object as "look this child entity up by primary key",
and throws the null-PK IllegalArgumentException when that key is present but null - harmless
to echo back on an UPDATE of the SAME parent (there's an existing row to resolve it against),
but fatal on CREATE, where there is no existing parent for that lookup to attach to.

Fix (2026-09-16): supplier_migration._strip_nested_null_ids recursively deletes every "id"
key whose value is None, at every nesting depth EXCEPT the top level. Applied to
migrate_transfer's payload (the actually-reported-failing function) and, preventively, to
migrate_transport's (same wholesale-copy shape/risk).

ALSO UPDATED 2026-09-16 (same day, later correction): migrate_transfer/migrate_transport no
longer create-then-deactivate - per product-owner correction ("just exchanging the supplier and
NOT creating new services") they now do a single in-place PUT to the destination supplier, same
record id. The top-level id is therefore the record's REAL existing id now, not None - still
correctly left untouched by _strip_nested_null_ids (which only ever touches the top level by
skipping it, regardless of what the id's value is).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supplier_migration import _strip_nested_null_ids, migrate_transfer, migrate_transport


# ======================================================================
# _strip_nested_null_ids - unit-level behaviour
# ======================================================================
def test_strips_nested_null_id_but_keeps_top_level_null_id():
    payload = {
        "id": None,
        "name": "Cairo - Alexandria",
        "pricesByOccupancy": [{"id": None, "minPax": 1, "maxPax": 2, "price": {"id": None, "amount": 50}}],
    }
    _strip_nested_null_ids(payload)
    assert payload["id"] is None  # top level preserved
    assert "id" not in payload["pricesByOccupancy"][0]
    assert "id" not in payload["pricesByOccupancy"][0]["price"]


def test_keeps_non_null_nested_id_untouched():
    payload = {"id": None, "supplements": [{"id": 555, "name": "Christmas surcharge"}]}
    _strip_nested_null_ids(payload)
    assert payload["supplements"][0]["id"] == 555


def test_strips_nulls_in_deeply_nested_lists_and_dicts():
    payload = {
        "id": None,
        "properties": [
            {"id": None, "translations": [{"id": None, "language": "en", "text": "x"}]},
        ],
    }
    _strip_nested_null_ids(payload)
    assert "id" not in payload["properties"][0]
    assert "id" not in payload["properties"][0]["translations"][0]


# ======================================================================
# _FakeClient - same test-double pattern as
# test_2026_09_11_supplier_migration_transport_airlinecode.py
# ======================================================================
class _FakeClient:
    def __init__(self):
        self.update_transfer_calls = []
        self.update_transport_calls = []

    def update_transfer(self, supplier_id, payload):
        self.update_transfer_calls.append((supplier_id, payload))
        return {"id": payload.get("id")}

    def update_transport(self, supplier_id, payload):
        self.update_transport_calls.append((supplier_id, payload))
        return {"id": payload.get("id")}


def _transfer_record():
    return {
        "id": "TRANSFER-OLD-1",
        "name": "Cairo - Alexandria",
        "active": True,
        "departure": {"name": "Cairo"},
        "arrival": {"name": "Alexandria"},
        "pricesByOccupancy": [
            {"id": None, "minPax": 1, "maxPax": 2, "price": {"id": None, "amount": 50, "currency": "EUR"}},
        ],
        "supplements": [{"id": None, "name": "Christmas surcharge"}],
    }


def _transport_record():
    return {
        "id": "TRANSPORT-OLD-1",
        "name": "Cairo - Luxor",
        "active": True,
        "optionCodes": ["OPT1"],
        "segments": [{"id": None, "departureLocationCode": "CAI", "arrivalLocationCode": "LXR"}],
    }


def test_migrate_transfer_move_payload_has_nested_null_ids_stripped():
    client = _FakeClient()
    record = _transfer_record()
    result = migrate_transfer(client, "SRC-1", "DEST-1", record)

    assert result["ok"] is True
    assert len(client.update_transfer_calls) == 1
    dest_supplier, payload = client.update_transfer_calls[0]

    # single PUT straight to the destination supplier, same real id preserved
    assert dest_supplier == "DEST-1"
    assert payload["id"] == "TRANSFER-OLD-1"
    # nested null ids stripped - this is the fix
    assert "id" not in payload["pricesByOccupancy"][0]
    assert "id" not in payload["pricesByOccupancy"][0]["price"]
    assert "id" not in payload["supplements"][0]


def test_migrate_transport_move_payload_has_nested_null_ids_stripped():
    client = _FakeClient()
    record = _transport_record()
    result = migrate_transport(client, "SRC-1", "DEST-1", record)

    assert result["ok"] is True
    assert len(client.update_transport_calls) == 1
    dest_supplier, payload = client.update_transport_calls[0]

    assert dest_supplier == "DEST-1"
    assert payload["id"] == "TRANSPORT-OLD-1"
    assert "id" not in payload["segments"][0]
