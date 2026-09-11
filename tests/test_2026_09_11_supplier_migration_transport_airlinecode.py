"""Regression test for supplier_migration.py's migrate_transport - same "airlineCode: must not
be null" bug class as cancellation_bulk_transport.py's 2026-09-11 real production failure (see
tests/test_2026_08_28_cancellation_bulk_transport.py's own new tests for the full history).

migrate_transport builds TWO whole-record Transport writes from a raw `record` dict passed in by
the caller: a CREATE under the destination supplier (create_payload) and, once every option has
migrated successfully, a PUT to deactivate the ORIGINAL under the source supplier
(deactivate_payload). Both go through the same ContractTransportVO schema Travel Compositor
validates airlineCode as required on, so both need the same bulk_notes.normalize_for_put fix
applied before this module has any real Transport data to test against directly (it doesn't
maintain its own list-vs-full-record distinction the way cancellation_bulk_transport.py's loader
does - the caller/app.py is responsible for what `record` actually contains).
"""
import supplier_migration


class _FakeClient:
    def __init__(self):
        self.create_calls = []
        self.update_calls = []
        self.option_gets = {}
        self.option_creates = []

    def create_transport(self, supplier_id, payload):
        self.create_calls.append((supplier_id, payload))
        return {"id": "TRANSPORT-NEW-1"}

    def get_transport_option(self, supplier_id, transport_id, code):
        return self.option_gets.get(code, {"code": code})

    def create_transport_option(self, supplier_id, transport_id, payload):
        self.option_creates.append((supplier_id, transport_id, payload))
        return payload

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
        record["airlineCode"] = "MS"  # a real existing value that must survive both writes
    return record


def test_create_payload_gets_a_missing_airline_code_defaulted_not_left_null():
    client = _FakeClient()
    supplier_migration.migrate_transport(client, "SRC1", "DST1", _record(airline_code_present=False))
    _, create_payload = client.create_calls[0]
    assert create_payload["airlineCode"] == ""


def test_create_payload_preserves_a_real_existing_airline_code():
    client = _FakeClient()
    supplier_migration.migrate_transport(client, "SRC1", "DST1", _record(airline_code_present=True))
    _, create_payload = client.create_calls[0]
    assert create_payload["airlineCode"] == "MS"


def test_deactivate_payload_gets_a_missing_airline_code_defaulted_not_left_null():
    client = _FakeClient()
    supplier_migration.migrate_transport(client, "SRC1", "DST1", _record(airline_code_present=False))
    _, deactivate_payload = client.update_calls[0]
    assert deactivate_payload["airlineCode"] == ""
    assert deactivate_payload["active"] is False


def test_deactivate_payload_preserves_a_real_existing_airline_code():
    client = _FakeClient()
    supplier_migration.migrate_transport(client, "SRC1", "DST1", _record(airline_code_present=True))
    _, deactivate_payload = client.update_calls[0]
    assert deactivate_payload["airlineCode"] == "MS"


def test_migration_still_succeeds_end_to_end():
    client = _FakeClient()
    result = supplier_migration.migrate_transport(client, "SRC1", "DST1", _record(airline_code_present=True))
    assert result["ok"] is True
    assert result["new_id"] == "TRANSPORT-NEW-1"
