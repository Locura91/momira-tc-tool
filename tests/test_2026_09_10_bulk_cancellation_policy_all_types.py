"""Tests for cancellation_bulk.py - generalizing the Transport-only bulk cancellation-policy
tool (cancellation_bulk_transport.py, 2026-08-28) to ClosedTour, Ticket, Transfer and Hotel
(2026-09-10, product-owner request: "bulk update cancellation policy --> this must be usable
for all Services: Hotel; Transfer, Transport, Ticket and ClosedTour - so far it looks like
only Transport can do it.").

Uses the same offline platform_store isolation as test_2026_08_28_cancellation_bulk_transport.py
for the cancellation_links.py interplay tests, and a hand-built fake client (not Mock()) per
product type, mirroring conftest.py's FakeTravelCompositorAPI philosophy.
"""
import cancellation_links as cl
import cancellation_bulk as cb


def _reset_links(product_type, supplier_id=None):
    cl.set_type_link(product_type, [])
    if supplier_id:
        cl.set_supplier_link(supplier_id, product_type, [])


# ----------------------------------------------------------------------
# default_new_tiers
# ----------------------------------------------------------------------

def test_default_new_tiers_falls_back_to_house_default_when_nothing_saved():
    _reset_links("Hotel", "SUP-CB-1")
    tiers, label = cb.default_new_tiers("SUP-CB-1", "Hotel")
    assert tiers == [{"days": 30, "fee_percentage": 0.0}]
    assert "house default" in label


def test_default_new_tiers_supplier_specific_wins_over_type_wide():
    _reset_links("Transfer", "SUP-CB-2")
    cl.set_type_link("Transfer", [{"days": 14, "fee_percentage": 50}])
    cl.set_supplier_link("SUP-CB-2", "Transfer", [{"days": 60, "fee_percentage": 0}])
    tiers, label = cb.default_new_tiers("SUP-CB-2", "Transfer")
    assert tiers == [{"days": 60, "fee_percentage": 0.0}]
    assert "this supplier" in label


# ----------------------------------------------------------------------
# _wire_ranges_to_fee_tiers - field-name parametrization is the whole point here (Ticket's
# cancellationDays/cancellationPercentage differ from ClosedTour's days/percentage)
# ----------------------------------------------------------------------

def test_wire_ranges_to_fee_tiers_closedtour_field_names():
    out = cb._wire_ranges_to_fee_tiers(
        [{"days": 30, "percentage": 100.0, "isBeforeStart": True}], "days", "percentage")
    assert out == [{"days": 30, "fee_percentage": 0.0}]


def test_wire_ranges_to_fee_tiers_ticket_field_names():
    out = cb._wire_ranges_to_fee_tiers(
        [{"cancellationDays": 7, "cancellationPercentage": 25.0}],
        "cancellationDays", "cancellationPercentage")
    assert out == [{"days": 7, "fee_percentage": 75.0}]


def test_wire_ranges_to_fee_tiers_empty_and_garbage_input():
    assert cb._wire_ranges_to_fee_tiers(None, "days", "percentage") == []
    assert cb._wire_ranges_to_fee_tiers([], "days", "percentage") == []
    assert cb._wire_ranges_to_fee_tiers([{"days": "nope", "percentage": 100}], "days", "percentage") == []


# ----------------------------------------------------------------------
# _tiers_equal / _current_cancellation_snippet / _swap_cancellation_block (plain-text blocks,
# NOT html <p> paragraphs - Transfer/Hotel/ClosedTour/Ticket voucherRemarks is plain text)
# ----------------------------------------------------------------------

def test_tiers_equal_same_content_different_order():
    a = [{"days": 30, "fee_percentage": 0}, {"days": 7, "fee_percentage": 100}]
    b = [{"days": 7, "fee_percentage": 100}, {"days": 30, "fee_percentage": 0}]
    assert cb._tiers_equal(a, b) is True


def test_snippet_finds_the_cancellation_block():
    text = "Private transfer from the airport.\n\nFree cancellation up to 30 days before arrival."
    assert cb._current_cancellation_snippet(text) == "Free cancellation up to 30 days before arrival."


def test_snippet_returns_none_when_nothing_mentions_cancellation():
    text = "Private transfer from the airport.\n\nWhat to bring:\nPassport"
    assert cb._current_cancellation_snippet(text) is None


def test_swap_replaces_existing_block_leaving_others_untouched():
    text = ("Private transfer from the airport.\n\n"
           "Free cancellation up to 30 days before arrival.\n\n"
           "What to bring:\nPassport")
    new_text, found = cb._swap_cancellation_block(text, "NEW POLICY TEXT")
    assert found is True
    assert new_text == ("Private transfer from the airport.\n\n"
                        "NEW POLICY TEXT\n\n"
                        "What to bring:\nPassport")


def test_swap_inserts_when_nothing_found():
    text = "Private transfer from the airport."
    new_text, found = cb._swap_cancellation_block(text, "NEW POLICY TEXT")
    assert found is False
    assert new_text == "NEW POLICY TEXT\n\nPrivate transfer from the airport."


def test_swap_handles_completely_empty_text():
    new_text, found = cb._swap_cancellation_block("", "NEW POLICY TEXT")
    assert found is False
    assert new_text == "NEW POLICY TEXT"
    new_text2, found2 = cb._swap_cancellation_block(None, "NEW POLICY TEXT")
    assert found2 is False
    assert new_text2 == "NEW POLICY TEXT"


# ----------------------------------------------------------------------
# _text_entries / _set_text_entries - the two storage shapes
# ----------------------------------------------------------------------

def test_text_entries_datasheets_shape():
    record = {"datasheets": {"EN": {"voucherRemarks": "en text"}, "DE": {"voucherRemarks": "de text"}}}
    entries = dict(cb._text_entries(record, "datasheets"))
    assert entries == {"EN": "en text", "DE": "de text"}


def test_set_text_entries_datasheets_shape_updates_in_place():
    record = {"datasheets": {"EN": {"voucherRemarks": "old"}, "DE": {"voucherRemarks": "alt"}}}
    cb._set_text_entries(record, "datasheets", {"EN": "new", "DE": "neu"})
    assert record["datasheets"]["EN"]["voucherRemarks"] == "new"
    assert record["datasheets"]["DE"]["voucherRemarks"] == "neu"


def test_text_entries_translation_list_shape():
    record = {"voucherRemarks": [{"language": "EN", "description": "en text"},
                                 {"language": "DE", "description": "de text"}]}
    entries = dict(cb._text_entries(record, "translation_list"))
    assert entries == {"EN": "en text", "DE": "de text"}


def test_set_text_entries_translation_list_shape_updates_and_adds():
    record = {"voucherRemarks": [{"language": "EN", "description": "old"}]}
    cb._set_text_entries(record, "translation_list", {"EN": "new", "FR": "nouveau"})
    by_lang = {e["language"]: e["description"] for e in record["voucherRemarks"]}
    assert by_lang == {"EN": "new", "FR": "nouveau"}


# ----------------------------------------------------------------------
# A tiny fake client covering the 4 product types
# ----------------------------------------------------------------------

class _FakeClient:
    def __init__(self, records=None, get_error=None, update_error_for=None, fetch_errors=None):
        self._records = records or []
        self._get_error = get_error
        self._update_error_for = update_error_for or {}
        self._fetch_errors = fetch_errors or {}
        self.update_calls = []

    # ClosedTour / Transfer / Ticket - full_in_list=True paths
    def get_closed_tour(self, supplier_id, code):
        if code in self._fetch_errors:
            return {"error": 400, "message": self._fetch_errors[code]}
        return next((r for r in self._records if r.get("code") == code), {"error": 404, "message": "not found"})

    def update_closed_tour(self, supplier_id, payload):
        self.update_calls.append((supplier_id, payload))
        if payload.get("code") in self._update_error_for:
            return {"error": 400, "message": self._update_error_for[payload["code"]]}
        return {"code": payload.get("code"), "status": 200}

    def get_transfers(self, supplier_id):
        if self._get_error:
            return {"error": 500, "message": self._get_error}
        return {"transfer": self._records}

    def update_transfer(self, supplier_id, payload):
        self.update_calls.append((supplier_id, payload))
        if payload.get("id") in self._update_error_for:
            return {"error": 400, "message": self._update_error_for[payload["id"]]}
        return {"id": payload.get("id"), "code": 200}

    def get_tickets(self, supplier_id, first=0, limit=100):
        if self._get_error:
            return {"error": 500, "message": self._get_error}
        if first > 0:
            return {"tickets": []}
        return {"tickets": self._records}

    def update_ticket(self, supplier_id, payload):
        self.update_calls.append((supplier_id, payload))
        if payload.get("code") in self._update_error_for:
            return {"error": 400, "message": self._update_error_for[payload["code"]]}
        return {"code": payload.get("code"), "status": 200}

    # Hotel - full_in_list=False path
    def get_hotels(self, supplier_id):
        if self._get_error:
            return {"error": 500, "message": self._get_error}
        return {"hotel": [{"providerCode": r["providerCode"]} for r in self._records]}

    def get_hotel(self, supplier_id, provider_code):
        if provider_code in self._fetch_errors:
            return {"error": 400, "message": self._fetch_errors[provider_code]}
        return next((r for r in self._records if r.get("providerCode") == provider_code),
                   {"error": 404, "message": "not found"})

    def update_hotel(self, supplier_id, payload):
        self.update_calls.append((supplier_id, payload))
        if payload.get("providerCode") in self._update_error_for:
            return {"error": 400, "message": self._update_error_for[payload["providerCode"]]}
        return {"providerCode": payload.get("providerCode"), "code": 200}


def _datasheet_record(id_field, id_value, name="Test Service",
                      text="Private transfer from the airport.\n\nFree cancellation up to 30 days before arrival.",
                      days=30, refund_pct=100.0, tier_fields=None):
    record = {
        id_field: id_value,
        "datasheets": {"EN": {"name": name, "voucherRemarks": text}},
    }
    if tier_fields:
        days_key, pct_key = tier_fields
        record["cancellationRanges"] = [{days_key: days, pct_key: refund_pct, "isBeforeStart": True}]
    return record


def _hotel_record(provider_code, name="Test Hotel",
                  text="Book direct.\n\nFree cancellation up to 30 days before arrival."):
    return {
        "providerCode": provider_code,
        "hotelname": name,
        "voucherRemarks": [{"language": "EN", "description": text}],
    }


# ----------------------------------------------------------------------
# load_supplier_services_for_cancellation - per product type
# ----------------------------------------------------------------------

def test_load_closedtour_uses_supplied_codes():
    client = _FakeClient(records=[_datasheet_record("code", "CT1", tier_fields=("days", "percentage"))])
    rows, err = cb.load_supplier_services_for_cancellation(client, "SUP-X", "ClosedTour", codes=["CT1"])
    assert err is None
    assert len(rows) == 1
    assert rows[0]["id"] == "CT1"
    assert rows[0]["current_fee_tiers"] == [{"days": 30, "fee_percentage": 0.0}]


def test_load_ticket_uses_ticket_field_names():
    client = _FakeClient(records=[
        _datasheet_record("code", "TK1", tier_fields=("cancellationDays", "cancellationPercentage"))
    ])
    rows, err = cb.load_supplier_services_for_cancellation(client, "SUP-X", "Ticket")
    assert err is None
    assert rows[0]["current_fee_tiers"] == [{"days": 30, "fee_percentage": 0.0}]


def test_load_transfer_has_no_structured_tiers():
    client = _FakeClient(records=[_datasheet_record("id", "TR1")])
    rows, err = cb.load_supplier_services_for_cancellation(client, "SUP-X", "Transfer")
    assert err is None
    assert rows[0]["current_fee_tiers"] is None
    assert rows[0]["current_cancellation_snippet"] == "Free cancellation up to 30 days before arrival."


def test_load_hotel_fetches_each_record_in_full():
    client = _FakeClient(records=[_hotel_record("H1"), _hotel_record("H2", name="Second Hotel")])
    rows, err = cb.load_supplier_services_for_cancellation(client, "SUP-X", "Hotel")
    assert err is None
    assert len(rows) == 2
    assert {r["id"] for r in rows} == {"H1", "H2"}
    assert rows[0]["current_fee_tiers"] is None


def test_load_hotel_surfaces_a_per_item_fetch_failure_without_dropping_the_others():
    client = _FakeClient(records=[_hotel_record("H1"), _hotel_record("H2")],
                         fetch_errors={"H2": "temporarily unavailable"})
    rows, err = cb.load_supplier_services_for_cancellation(client, "SUP-X", "Hotel")
    assert len(rows) == 1
    assert rows[0]["id"] == "H1"
    assert "temporarily unavailable" in err


def test_load_surfaces_a_client_error():
    client = _FakeClient(get_error="boom")
    rows, err = cb.load_supplier_services_for_cancellation(client, "SUP-X", "Transfer")
    assert rows == []
    assert "boom" in err


# ----------------------------------------------------------------------
# build_proposals - structured (ClosedTour/Ticket) vs text-only (Transfer/Hotel)
# ----------------------------------------------------------------------

def test_build_proposals_closedtour_includes_structured_ranges():
    client = _FakeClient(records=[_datasheet_record("code", "CT1", tier_fields=("days", "percentage"))])
    rows, _ = cb.load_supplier_services_for_cancellation(client, "SUP-X", "ClosedTour", codes=["CT1"])
    proposals = cb.build_proposals(rows, [{"days": 14, "fee_percentage": 50.0}], "ClosedTour")
    assert proposals[0]["new_fee_tiers"] == [{"days": 14, "fee_percentage": 50.0}]
    assert proposals[0]["new_ranges_wire"] == [{"days": 14, "percentage": 50.0, "isBeforeStart": True}]
    assert proposals[0]["unchanged"] is False


def test_build_proposals_transfer_has_no_structured_ranges():
    client = _FakeClient(records=[_datasheet_record("id", "TR1")])
    rows, _ = cb.load_supplier_services_for_cancellation(client, "SUP-X", "Transfer")
    proposals = cb.build_proposals(rows, [{"days": 14, "fee_percentage": 50.0}], "Transfer")
    assert proposals[0]["new_fee_tiers"] is None
    assert proposals[0]["new_ranges_wire"] is None
    assert "50% cancellation fee if cancelled less than 14 days before arrival" in proposals[0]["new_cancellation_text"]


def test_build_proposals_flags_unchanged_when_text_and_tiers_already_match():
    matching_text = ("Book direct.\n\n"
                     "Cancellation Policy:\n- Free cancellation if cancelled at least 30 days before arrival.")
    client = _FakeClient(records=[_hotel_record("H1", text=matching_text)])
    rows, _ = cb.load_supplier_services_for_cancellation(client, "SUP-X", "Hotel")
    proposals = cb.build_proposals(rows, [{"days": 30, "fee_percentage": 0.0}], "Hotel")
    assert proposals[0]["unchanged"] is True


def test_build_proposals_floors_a_too_generous_new_tier_to_30_days():
    client = _FakeClient(records=[_datasheet_record("code", "TK1", tier_fields=("cancellationDays", "cancellationPercentage"))])
    rows, _ = cb.load_supplier_services_for_cancellation(client, "SUP-X", "Ticket")
    proposals = cb.build_proposals(rows, [{"days": 5, "fee_percentage": 0.0}], "Ticket")
    assert proposals[0]["new_ranges_wire"] == [
        {"cancellationDays": 30, "cancellationPercentage": 100.0, "isBeforeStart": True}
    ]


def test_build_proposals_marks_paragraph_not_found_when_no_entries():
    client = _FakeClient(records=[{"id": "TR1", "datasheets": {}}])
    rows, _ = cb.load_supplier_services_for_cancellation(client, "SUP-X", "Transfer")
    proposals = cb.build_proposals(rows, [{"days": 14, "fee_percentage": 50.0}], "Transfer")
    assert proposals[0]["existing_paragraph_found"] is False
    assert proposals[0]["new_text_by_lang"]["EN"] == proposals[0]["new_cancellation_text"]


# ----------------------------------------------------------------------
# apply_proposals - per-type dispatch: update_fn, cancellationRanges only where applicable,
# every-language text swap, StateStore.clear_state with the correct entity_type
# ----------------------------------------------------------------------

def test_apply_closedtour_updates_structured_and_text():
    client = _FakeClient(records=[_datasheet_record("code", "CT1", tier_fields=("days", "percentage"))])
    rows, _ = cb.load_supplier_services_for_cancellation(client, "SUP-X", "ClosedTour", codes=["CT1"])
    proposals = cb.build_proposals(rows, [{"days": 14, "fee_percentage": 50.0}], "ClosedTour")
    results = cb.apply_proposals(client, "SUP-X", "ClosedTour", proposals)
    assert results == [{"id": "CT1", "name": "Test Service", "ok": True, "detail": ""}]

    supplier_id, payload = client.update_calls[0]
    assert supplier_id == "SUP-X"
    assert payload["cancellationRanges"] == [{"days": 14, "percentage": 50.0, "isBeforeStart": True}]
    new_text = payload["datasheets"]["EN"]["voucherRemarks"]
    assert "50% cancellation fee if cancelled less than 14 days before arrival" in new_text
    assert "Free cancellation up to 30 days before arrival" not in new_text
    assert "Private transfer from the airport." in new_text


def test_apply_transfer_updates_text_only_no_cancellation_ranges_key_added():
    client = _FakeClient(records=[_datasheet_record("id", "TR1")])
    rows, _ = cb.load_supplier_services_for_cancellation(client, "SUP-X", "Transfer")
    proposals = cb.build_proposals(rows, [{"days": 14, "fee_percentage": 50.0}], "Transfer")
    cb.apply_proposals(client, "SUP-X", "Transfer", proposals)
    _, payload = client.update_calls[0]
    assert "cancellationRanges" not in payload


def test_apply_hotel_updates_translation_list_shape():
    client = _FakeClient(records=[_hotel_record("H1")])
    rows, _ = cb.load_supplier_services_for_cancellation(client, "SUP-X", "Hotel")
    proposals = cb.build_proposals(rows, [{"days": 14, "fee_percentage": 50.0}], "Hotel")
    cb.apply_proposals(client, "SUP-X", "Hotel", proposals)
    _, payload = client.update_calls[0]
    en_entry = next(e for e in payload["voucherRemarks"] if e["language"] == "EN")
    assert "50% cancellation fee if cancelled less than 14 days before arrival" in en_entry["description"]


def test_apply_surfaces_a_per_row_error_without_stopping_the_others():
    client = _FakeClient(
        records=[_datasheet_record("id", "TR1", name="Route One"), _datasheet_record("id", "TR2", name="Route Two")],
        update_error_for={"TR1": "inactive"},
    )
    rows, _ = cb.load_supplier_services_for_cancellation(client, "SUP-X", "Transfer")
    proposals = cb.build_proposals(rows, [{"days": 14, "fee_percentage": 50.0}], "Transfer")
    results = cb.apply_proposals(client, "SUP-X", "Transfer", proposals)
    by_id = {r["id"]: r for r in results}
    assert by_id["TR1"]["ok"] is False
    assert "inactive" in by_id["TR1"]["detail"]
    assert by_id["TR2"]["ok"] is True
    assert len(client.update_calls) == 2


def test_apply_clears_translation_state_with_correct_entity_type_per_product():
    from state_store import StateStore
    client = _FakeClient(records=[_datasheet_record("id", "TR1")])
    rows, _ = cb.load_supplier_services_for_cancellation(client, "SUP-X", "Transfer")
    proposals = cb.build_proposals(rows, [{"days": 14, "fee_percentage": 50.0}], "Transfer")

    store = StateStore()
    store.upsert_state("transfer", "SUP-X", "TR1", "somehash", ["DE", "FR"])
    assert store.get_state("transfer", "SUP-X", "TR1")

    cb.apply_proposals(client, "SUP-X", "Transfer", proposals)
    assert not store.get_state("transfer", "SUP-X", "TR1")
