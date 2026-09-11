"""Regression tests for the 2026-09-11 fix to price_refresh.load_supplier_transports/
rebuild_prices: a real bulk price-refresh run against 13 FTS-matched Transport routes (the
FTS matrix flow built earlier this same day - see test_2026_09_11_price_refresh_fts_matrix_bypass.py)
failed ALL 13 Apply attempts with the identical error already diagnosed and fixed once this same
day in cancellation_bulk_transport.load_supplier_transports_for_cancellation:

    {"error":["updateTransport.transport.airlineCode: must not be null"],"status":"BAD_REQUEST"}

Same root cause, same fix: load_supplier_transports used to build `raw` (what rebuild_prices
turns into a whole-record PUT payload) directly from the LIST endpoint's own entry (GET
/transport/{supplierId}), which can genuinely lack a field the transport's own individual
record (GET /transport/{supplierId}/{id}) actually has populated. Fixed by re-fetching each
transport's full individual record first, falling back to the list entry (flagged
full_fetch_failed) only when that re-fetch itself fails, with bulk_notes.normalize_for_put
kept only as the LAST-RESORT default - never the primary fix, since defaulting a genuinely-
present field to "" would silently erase real data (the exact mistake the product owner caught
and corrected in the sibling incident, see
claude/incident-2026-09-11-bulk-cancellation-transport-airlinecode.md).
"""
import price_refresh


class _FakeTransportClient:
    """Mirrors cancellation_bulk_transport's own _FakeTransportClient (same philosophy, same
    full_records/full_fetch_fails_for knobs) so this fix's tests read the same way as the
    sibling incident's tests do."""

    def __init__(self, transports=None, options=None, full_records=None,
                 full_fetch_fails_for=None, update_error_for=None):
        self._transports = transports or []
        self._options = options or {}
        self._full_records = full_records or {}
        self._full_fetch_fails_for = set(full_fetch_fails_for or [])
        self._update_error_for = update_error_for or {}
        self.get_transport_calls = []
        self.update_calls = []

    def get_transports(self, supplier_id):
        return {"transport": self._transports}

    def get_transport(self, supplier_id, transport_id):
        self.get_transport_calls.append(transport_id)
        if transport_id in self._full_fetch_fails_for:
            raise RuntimeError("network blip")
        if transport_id in self._full_records:
            return self._full_records[transport_id]
        return next((t for t in self._transports if t.get("id") == transport_id), {"error": 404})

    def get_transport_option(self, supplier_id, transport_id, code):
        return self._options[(transport_id, code)]

    def update_transport(self, supplier_id, payload):
        self.update_calls.append((supplier_id, payload))
        if payload.get("id") in self._update_error_for:
            return {"error": 400, "message": self._update_error_for[payload["id"]]}
        return {"id": payload.get("id"), "code": 200}

    def update_transport_option(self, supplier_id, transport_id, payload):
        return {"code": payload.get("code")}


def _list_entry(id_="T1", base_adult=42.0):
    return {
        "id": id_, "name": "Airport - Hotel X", "currency": "EUR",
        "pricePerPax": True, "baseAdultPrice": base_adult,
        "baseChildrenPrice": 0.0, "baseInfantPrice": 0.0,
        "optionCodes": ["O1"],
        "segments": [{"departureLocationCode": "SSH", "arrivalLocationCode": "HTL-X"}],
    }


def _option(transport_id, code="O1", min_pax=1, max_pax=4, supplement=0.0):
    return {"code": code, "minPassengers": min_pax, "maxPassengers": max_pax,
            "prices": [{"adultPriceSupplement": supplement}] if supplement else [],
            "translations": {"EN": {"name": code}}}


def test_load_prefers_the_full_individual_record_over_the_list_entry():
    list_entry = _list_entry()
    assert "airlineCode" not in list_entry
    full_record = dict(list_entry)
    full_record["airlineCode"] = "XY"  # the real existing value, absent from the list entry
    client = _FakeTransportClient(transports=[list_entry], full_records={"T1": full_record},
                                  options={("T1", "O1"): _option("T1")})
    routes, err = price_refresh.load_supplier_transports(client, "SUP-X")
    assert err is None
    assert client.get_transport_calls == ["T1"]
    assert routes[0]["raw"]["airlineCode"] == "XY"
    assert routes[0]["full_fetch_failed"] is False


def test_load_falls_back_to_the_list_entry_and_flags_it_when_the_full_fetch_fails():
    client = _FakeTransportClient(transports=[_list_entry()], full_fetch_fails_for={"T1"},
                                  options={("T1", "O1"): _option("T1")})
    routes, err = price_refresh.load_supplier_transports(client, "SUP-X")
    assert err is None  # one row's fetch failure must not fail the whole load
    assert len(routes) == 1
    assert routes[0]["id"] == "T1"  # fell back to the list entry, which has this much
    assert routes[0]["full_fetch_failed"] is True


def test_load_reports_progress_per_transport_same_as_before_the_fix():
    client = _FakeTransportClient(
        transports=[_list_entry(id_="T1"), _list_entry(id_="T2")],
        options={("T1", "O1"): _option("T1"), ("T2", "O1"): _option("T2")})
    seen = []
    price_refresh.load_supplier_transports(
        client, "SUP-X", progress=lambda done, total, name: seen.append((done, total, name)))
    assert seen == [(1, 2, "Airport - Hotel X"), (2, 2, "Airport - Hotel X")]


def test_apply_preserves_the_real_airline_code_instead_of_blanking_it():
    # End-to-end: rebuild_prices/apply_proposals must PUT the real existing airlineCode back,
    # not silently overwrite it with "" - the exact mistake already caught once this same day.
    list_entry = _list_entry()
    full_record = dict(list_entry)
    full_record["airlineCode"] = "XY"
    client = _FakeTransportClient(transports=[list_entry], full_records={"T1": full_record},
                                  options={("T1", "O1"): _option("T1")})
    routes, _ = price_refresh.load_supplier_transports(client, "SUP-X")
    proposal = {"route": routes[0], "changes": [{"code": "O1", "new": 50.0}], "accepted": True}
    price_refresh.apply_proposals(client, "SUP-X", [proposal])
    _, payload = client.update_calls[0]
    assert payload["airlineCode"] == "XY"


def test_apply_falls_back_to_empty_string_only_when_airline_code_is_genuinely_absent_everywhere():
    # No full_records entry given -> get_transport() returns the list entry itself (the "no
    # fuller record exists" case): airlineCode is genuinely missing everywhere, so the
    # last-resort normalize_for_put default ("") is correct here, only as the LAST resort.
    client = _FakeTransportClient(transports=[_list_entry()],
                                  options={("T1", "O1"): _option("T1")})
    routes, _ = price_refresh.load_supplier_transports(client, "SUP-X")
    proposal = {"route": routes[0], "changes": [{"code": "O1", "new": 50.0}], "accepted": True}
    price_refresh.apply_proposals(client, "SUP-X", [proposal])
    _, payload = client.update_calls[0]
    assert payload["airlineCode"] == ""


def test_a_client_without_get_transport_falls_back_gracefully():
    # Backward compat: a fake/older client that has no get_transport at all must not crash the
    # whole load - AttributeError is caught the same as any other fetch failure.
    class _NoIndividualFetchClient:
        def get_transports(self, supplier_id):
            return {"transport": [_list_entry()]}

        def get_transport_option(self, supplier_id, transport_id, code):
            return _option(transport_id)

    routes, err = price_refresh.load_supplier_transports(_NoIndividualFetchClient(), "SUP-X")
    assert err is None
    assert len(routes) == 1
    assert routes[0]["full_fetch_failed"] is True
    assert routes[0]["id"] == "T1"


def test_apply_still_surfaces_a_real_validation_error_when_a_field_genuinely_cant_be_filled():
    # normalize_for_put is only defined for airlineCode today - a DIFFERENT missing required
    # field must still surface as a real failure rather than being silently swallowed.
    client = _FakeTransportClient(transports=[_list_entry()], update_error_for={"T1": "boom"},
                                  options={("T1", "O1"): _option("T1")})
    routes, _ = price_refresh.load_supplier_transports(client, "SUP-X")
    proposal = {"route": routes[0], "changes": [{"code": "O1", "new": 50.0}], "accepted": True}
    result = price_refresh.apply_proposals(client, "SUP-X", [proposal])
    assert result["failed"] and "boom" in result["failed"][0]["detail"]
