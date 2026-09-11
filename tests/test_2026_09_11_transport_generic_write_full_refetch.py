"""Tests for the real production bug (product owner, 2026-09-11): "i manually updatet a field
[Voucher remarks] for all MOMIRA_TEST transport voucher remakrs, the app says all good, but
nothing seen on travel compositor side." Verified by the product owner setting a Transport's
live Voucher remarks to "Test" in Travel Compositor first, then running this module's generic
"Replace the field completely" write against it with new text - the app reported "Sent N
service(s) updated", but the field in Travel Compositor still read "Test": the write never
actually landed.

Root cause: bulk_notes.PRODUCTS["Transport"]["full_in_list"] was True, so plan() (the generic
"Text into an existing field" flow, and the two one-off Transport repair tools) trusted the
LIST endpoint's own entry (GET /transport/{supplierId}) as a complete record and PUT it straight
back with one field changed - never re-fetching each Transport's own individual record (GET
/transport/{supplierId}/{id}). Transport's list entry has ALREADY been proven unreliable for a
whole-record PUT by the real "airlineCode: must not be null" incident (2026-09-10/11) - this is
the exact same asymmetry, just surfacing as a silently-dropped write instead of a validation
error this time. Every OTHER Transport whole-record-PUT path already re-fetches individually
(price_refresh.py, cancellation_bulk_transport.py, _plan_transport_price_increase) - this was
the one generic path (plus the two newer one-off repair tools sharing its list_services() call)
still trusting the list entry directly.

Fix: PRODUCTS["Transport"]["full_in_list"] is now False (plan()'s existing per-record fallback,
previously only exercised for Hotel, now also re-fetches every Transport individually before
building its PUT payload) - and the two repair tools (_plan_transport_voucher_code_repair,
_plan_transport_cancellation_text_repair) now share a new _refetch_full_transport helper that
does the same. apply() (the generic write path) also now captures the exact request/response
under "debug" per item, mirroring price_refresh.apply_proposals's own 2026-09-11 debug-capture
fix for the same class of "app says success, Travel Compositor shows nothing" report.
"""
import bulk_notes


# ---------------------------------------------------------------------------
# PRODUCTS table
# ---------------------------------------------------------------------------

def test_transport_is_no_longer_trusted_as_full_in_list():
    assert bulk_notes.PRODUCTS["Transport"]["full_in_list"] is False


def test_transfer_and_ticket_are_unaffected_still_full_in_list():
    # Deliberately scoped to Transport only - Transfer/Ticket have shown no evidence of this
    # asymmetry, and re-fetching them individually would just be extra unnecessary requests.
    assert bulk_notes.PRODUCTS["Transfer"]["full_in_list"] is True
    assert bulk_notes.PRODUCTS["Ticket"]["full_in_list"] is True


# ---------------------------------------------------------------------------
# _refetch_full_transport
# ---------------------------------------------------------------------------

class _RefetchClient:
    def __init__(self, full_records=None, fails_for=None):
        self._full_records = full_records or {}
        self._fails_for = set(fails_for or [])
        self.get_transport_calls = []

    def get_transport(self, supplier_id, transport_id):
        self.get_transport_calls.append(transport_id)
        if transport_id in self._fails_for:
            raise RuntimeError("network blip")
        if transport_id in self._full_records:
            return self._full_records[transport_id]
        return {"error": 404, "message": "not found"}


def test_refetch_prefers_the_individual_full_record_over_the_summary():
    summary = {"id": "T1", "name": "Route", "datasheets": {"EN": {"description": "stale"}}}
    full = {"id": "T1", "name": "Route", "airlineCode": "XY",
            "datasheets": {"EN": {"description": "live", "voucherRemarks": "current"}}}
    client = _RefetchClient(full_records={"T1": full})
    record, failed = bulk_notes._refetch_full_transport(client, "SUP-X", summary)
    assert failed is False
    assert record == full
    assert client.get_transport_calls == ["T1"]


def test_refetch_falls_back_to_the_summary_and_flags_it_when_the_fetch_fails():
    summary = {"id": "T1", "name": "Route"}
    client = _RefetchClient(fails_for={"T1"})
    record, failed = bulk_notes._refetch_full_transport(client, "SUP-X", summary)
    assert failed is True
    assert record == summary


def test_refetch_falls_back_to_the_summary_and_flags_it_when_id_is_missing():
    summary = {"name": "Route with no id"}
    client = _RefetchClient()
    record, failed = bulk_notes._refetch_full_transport(client, "SUP-X", summary)
    assert failed is True
    assert record == summary
    assert client.get_transport_calls == []


def test_refetch_falls_back_when_the_individual_fetch_itself_errors():
    summary = {"id": "T1", "name": "Route"}
    client = _RefetchClient(full_records={"T1": {"error": 500, "message": "boom"}})
    record, failed = bulk_notes._refetch_full_transport(client, "SUP-X", summary)
    assert failed is True
    assert record == summary


# ---------------------------------------------------------------------------
# plan()/apply(): the generic "Text into an existing field" flow now re-fetches Transport
# individually, and apply() carries debug info.
# ---------------------------------------------------------------------------

class _GenericFlowClient:
    """Deliberately gives a DIFFERENT (fuller/live) record per id via get_transport than what
    get_transports' own list entry has - mirrors the real production shape: the list entry can
    be stale/incomplete relative to the individual GET."""
    def __init__(self, list_entries, full_records):
        self._list_entries = list_entries
        self._full_records = full_records
        self.update_calls = []

    def get_transports(self, supplier_id):
        return {"transport": self._list_entries}

    def get_transport(self, supplier_id, transport_id):
        return self._full_records.get(transport_id, {"error": 404})

    def update_transport(self, supplier_id, payload):
        self.update_calls.append(payload)
        return {"id": payload.get("id"), "code": 200}


def test_plan_uses_the_individually_fetched_record_not_the_stale_list_entry():
    # The LIST entry's voucherRemarks is stale ("Test", manually set before the run, same as
    # the real report) - the individually-fetched record has the CURRENT live value. plan()'s
    # before/after diff (and the record it stages for apply()) must come from the fresh fetch.
    list_entry = {"id": "T1", "name": "Nuweiba - Dahab",
                 "datasheets": {"EN": {"name": "Nuweiba - Dahab", "voucherRemarks": "Test"}}}
    full_record = {"id": "T1", "name": "Nuweiba - Dahab", "airlineCode": "",
                   "datasheets": {"EN": {"name": "Nuweiba - Dahab", "voucherRemarks": "Test"}}}
    client = _GenericFlowClient(list_entries=[list_entry], full_records={"T1": full_record})
    planned = bulk_notes.plan(client, "SUP-X", "Transport", "Voucher remarks",
                              "Brand new replacement text", mode=bulk_notes.MODE_REPLACE)
    assert planned["will_change"] == 1
    item = planned["items"][0]
    assert item["record"]["datasheets"]["EN"]["voucherRemarks"] == "Brand new replacement text"
    # The staged record is built from the FULL fetch (has airlineCode), not the bare list entry.
    assert "airlineCode" in item["record"]


def test_plan_marks_item_failed_when_the_individual_fetch_fails_and_does_not_crash():
    list_entry = {"id": "T1", "name": "Route"}
    client = _GenericFlowClient(list_entries=[list_entry], full_records={})  # T1 -> {"error": 404}
    planned = bulk_notes.plan(client, "SUP-X", "Transport", "Voucher remarks", "New text",
                              mode=bulk_notes.MODE_REPLACE)
    assert planned["failed"] == 1
    assert planned["items"][0]["status"] == "failed"


def test_apply_carries_the_exact_request_and_response_as_debug():
    list_entry = {"id": "T1", "name": "Route"}
    full_record = {"id": "T1", "name": "Route", "datasheets": {"EN": {"voucherRemarks": "old"}}}
    client = _GenericFlowClient(list_entries=[list_entry], full_records={"T1": full_record})
    planned = bulk_notes.plan(client, "SUP-X", "Transport", "Voucher remarks", "new text",
                              mode=bulk_notes.MODE_REPLACE)
    result = bulk_notes.apply(client, "SUP-X", planned)
    assert len(result["updated"]) == 1
    dbg = result["updated"][0]["debug"]
    assert dbg["request"]["datasheets"]["EN"]["voucherRemarks"] == "new text"
    assert dbg["response"] == {"id": "T1", "code": 200}


def test_apply_carries_debug_on_a_failed_write_too():
    list_entry = {"id": "T1", "name": "Route"}
    full_record = {"id": "T1", "name": "Route", "datasheets": {"EN": {"voucherRemarks": "old"}}}
    client = _GenericFlowClient(list_entries=[list_entry], full_records={"T1": full_record})

    def _failing_update(supplier_id, payload):
        return {"error": 400, "message": "rejected"}
    client.update_transport = _failing_update

    planned = bulk_notes.plan(client, "SUP-X", "Transport", "Voucher remarks", "new text",
                              mode=bulk_notes.MODE_REPLACE)
    result = bulk_notes.apply(client, "SUP-X", planned)
    assert len(result["failed"]) == 1
    assert result["failed"][0]["debug"]["request"] is not None


# ---------------------------------------------------------------------------
# The two one-off Transport repair tools now also re-fetch individually.
# ---------------------------------------------------------------------------

class _RepairClient:
    def __init__(self, list_entries, full_records):
        self._list_entries = list_entries
        self._full_records = full_records

    def get_transports(self, supplier_id):
        return {"transport": self._list_entries}

    def get_transport(self, supplier_id, transport_id):
        return self._full_records.get(transport_id, {"error": 404})


def test_voucher_code_repair_uses_the_individually_fetched_record():
    list_entry = {"id": "T1", "name": "Route",
                 "datasheets": {"EN": {"description": "STALE, no code here"}}}
    full_record = {"id": "T1", "name": "Route", "airlineCode": "XY",
                   "datasheets": {"EN": {"description": "Private car.\n(20270430)"}}}
    client = _RepairClient(list_entries=[list_entry], full_records={"T1": full_record})
    plan = bulk_notes._plan_transport_voucher_code_repair(client, "SUP-X")
    assert plan["will_change"] == 1
    item = plan["items"][0]
    assert item["record"]["airlineCode"] == "XY"
    assert item["full_fetch_failed"] is False
    assert "(20270430)" not in item["record"]["datasheets"]["EN"]["description"]
    assert item["record"]["datasheets"]["EN"]["voucherRemarks"] == "(20270430)"


def test_voucher_code_repair_flags_full_fetch_failed_and_falls_back():
    list_entry = {"id": "T1", "name": "Route",
                 "datasheets": {"EN": {"description": "Private car.\n(20270430)"}}}
    client = _RepairClient(list_entries=[list_entry], full_records={})  # fetch fails -> {"error"}
    plan = bulk_notes._plan_transport_voucher_code_repair(client, "SUP-X")
    assert plan["will_change"] == 1
    assert plan["items"][0]["full_fetch_failed"] is True


def test_cancellation_text_repair_uses_the_individually_fetched_record():
    list_entry = {"id": "T1", "name": "Route",
                 "datasheets": {"EN": {"description": "STALE, nothing here"}}}
    full_record = {"id": "T1", "name": "Route", "airlineCode": "XY",
                   "datasheets": {"EN": {
                       "description": "<p>Private car.</p><p>Cancellation Policy: Free "
                                     "cancellation if cancelled at least 30 days before "
                                     "arrival.</p>",
                   }}}
    client = _RepairClient(list_entries=[list_entry], full_records={"T1": full_record})
    plan = bulk_notes._plan_transport_cancellation_text_repair(client, "SUP-X")
    assert plan["will_change"] == 1
    item = plan["items"][0]
    assert item["record"]["airlineCode"] == "XY"
    assert item["full_fetch_failed"] is False
    assert "Cancellation Policy" not in item["record"]["datasheets"]["EN"]["description"]
    assert "Free cancellation" in item["record"]["datasheets"]["EN"]["voucherRemarks"]
