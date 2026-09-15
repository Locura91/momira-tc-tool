"""Regression test for supplier_migration.py's migrate_transport - same "airlineCode: must not
be null" bug class as cancellation_bulk_transport.py's 2026-09-11 real production failure (see
tests/test_2026_08_28_cancellation_bulk_transport.py's own new tests for the full history).

UPDATED 2026-09-16: migrate_transport no longer does a create-under-destination +
deactivate-the-original dance (see supplier_migration.py's module docstring for why - product
owner correction: "just exchanging the supplier and NOT creating new services"). It now does
exactly one whole-record PUT, straight to the DESTINATION supplier's URL, same record id. That
PUT still goes through the same ContractTransportVO schema Travel Compositor validates
airlineCode as required on, so the normalize_for_put fix is still needed - just on one call
instead of two.
"""
import supplier_migration


class _FakeClient:
    def __init__(self):
        self.update_calls = []

    def update_transport(self, supplier_id, payload):
        self.update_calls.append((supplier_id, payload))
        return {"id": payload.get("id")}


def _record(airline_code_present=False):
    record = {
        "id": "TRANSPORT-OLD-1",
        "name": "Cairo - Luxor",
        "active": True,
        "optionCodes": ["OPT1"],
        "segments": [{"departureLocationCode": "CAI", "arrivalLocationCode": "LXR"}],
    }
    if airline_code_present:
        record["airlineCode"] = "MS"  # a real existing value that must survive the write
    return record


def test_move_payload_gets_a_missing_airline_code_defaulted_not_left_null():
    client = _FakeClient()
    supplier_migration.migrate_transport(client, "SRC1", "DST1", _record(airline_code_present=False))
    _, payload = client.update_calls[0]
    assert payload["airlineCode"] == ""


def test_move_payload_preserves_a_real_existing_airline_code():
    client = _FakeClient()
    supplier_migration.migrate_transport(client, "SRC1", "DST1", _record(airline_code_present=True))
    _, payload = client.update_calls[0]
    assert payload["airlineCode"] == "MS"


def test_move_is_a_single_put_to_the_destination_supplier_with_the_same_id():
    client = _FakeClient()
    result = supplier_migration.migrate_transport(client, "SRC1", "DST1", _record(airline_code_present=True))
    assert len(client.update_calls) == 1
    dest_supplier, payload = client.update_calls[0]
    assert dest_supplier == "DST1"  # PUT goes straight to the destination supplier's URL
    assert payload["id"] == "TRANSPORT-OLD-1"  # same id - nothing new created
    assert result["ok"] is True
    assert result["new_id"] == "TRANSPORT-OLD-1"
    assert result["moved_in_place"] is True
