"""Regression tests for extending the 2026-09-11 "don't overwrite an already-stricter existing
cancellation policy" rule to Transfer and Hotel - product owner, same day: "the cancellation
policy is required to ALL services: Ticket, ClosedTour and Hotels too. But most likely
ClosedTours and Hotels have a more strict policy."

Transfer and Hotel have NO structured cancellationRanges field at all (confirmed - Hotel via
real Swagger, schemas.ContractHotelVO's own docstring; Transfer via a live before/after GET
diff, see claude/transfer-cancellation-no-structured-field-2026-09-11.md), so their only record
of the current policy is free voucher text. builder.parse_cancellation_tiers_from_voucher_text
reads that text back into (days, refund_pct) tiers ONLY when it matches a shape this app's own
_cancellation_voucher_text synthesizer could have written - anything else parses to None, and
cancellation_bulk.build_proposals flags that row existing_unparseable (left unchecked by
default, but NOT blocked - a human can still include it after reading the text)."""
import builder
import cancellation_bulk as cb


# ----------------------------------------------------------------------
# builder.parse_cancellation_tiers_from_voucher_text
# ----------------------------------------------------------------------

def test_parses_the_flat_default_sentence():
    assert builder.parse_cancellation_tiers_from_voucher_text(
        builder._DEFAULT_CANCELLATION_VOUCHER_TEXT) == [(30, 100.0)]


def test_parses_a_single_free_cancellation_bullet_with_header():
    text = "Cancellation Policy:\n- Free cancellation if cancelled at least 60 days before arrival."
    assert builder.parse_cancellation_tiers_from_voucher_text(text) == [(60, 100.0)]


def test_parses_a_no_refund_bullet():
    text = "Cancellation Policy:\n- No refund if cancelled less than 5 days before arrival."
    assert builder.parse_cancellation_tiers_from_voucher_text(text) == [(5, 0.0)]


def test_parses_a_partial_fee_bullet():
    text = ("Cancellation Policy:\n- 25% cancellation fee if cancelled less than 14 days before "
           "arrival (75% refund).")
    assert builder.parse_cancellation_tiers_from_voucher_text(text) == [(14, 75.0)]


def test_parses_day_of_arrival_bullets():
    no_refund = "Cancellation Policy:\n- No refund for cancellations on the day of arrival or no-shows."
    assert builder.parse_cancellation_tiers_from_voucher_text(no_refund) == [(0, 0.0)]
    partial = "Cancellation Policy:\n- 50% cancellation fee on the day of arrival or for no-shows (50% refund)."
    assert builder.parse_cancellation_tiers_from_voucher_text(partial) == [(0, 50.0)]


def test_parses_multiple_bullets_as_multiple_tiers():
    text = ("Cancellation Policy:\n"
           "- Free cancellation if cancelled at least 60 days before arrival.\n"
           "- 25% cancellation fee if cancelled less than 60 days before arrival (75% refund).\n"
           "- No refund if cancelled less than 14 days before arrival.")
    assert builder.parse_cancellation_tiers_from_voucher_text(text) == [
        (60, 100.0), (60, 75.0), (14, 0.0)]


def test_a_single_unrecognized_line_fails_the_whole_parse():
    text = ("Cancellation Policy:\n"
           "- Free cancellation if cancelled at least 60 days before arrival.\n"
           "- Please contact us for special circumstances.")
    assert builder.parse_cancellation_tiers_from_voucher_text(text) is None


def test_a_suppliers_own_freeform_wording_is_not_parsed():
    text = "Cancellation within 24 hours of the excursion incurs a 100% charge."
    assert builder.parse_cancellation_tiers_from_voucher_text(text) is None


def test_empty_or_none_text_returns_none():
    assert builder.parse_cancellation_tiers_from_voucher_text("") is None
    assert builder.parse_cancellation_tiers_from_voucher_text(None) is None


def test_round_trips_through_the_real_synthesizer():
    for tiers in ([(30, 100.0)], [(90, 100.0), (30, 50.0), (0, 0.0)], [(14, 25.0)]):
        text = builder._cancellation_voucher_text(None, tiers)
        assert builder.parse_cancellation_tiers_from_voucher_text(text) == tiers


# ----------------------------------------------------------------------
# cancellation_bulk.py integration - Hotel and Transfer
# ----------------------------------------------------------------------

class _FakeHotelClient:
    def __init__(self, records):
        self._records = records
        self.update_calls = []

    def get_hotels(self, supplier_id):
        return {"hotel": [{"providerCode": r["providerCode"]} for r in self._records]}

    def get_hotel(self, supplier_id, provider_code):
        return next((r for r in self._records if r.get("providerCode") == provider_code), {"error": 404})

    def update_hotel(self, supplier_id, payload):
        self.update_calls.append((supplier_id, payload))
        return {"providerCode": payload.get("providerCode"), "code": 200}


def _hotel_record(provider_code="H1", text="Book direct.\n\nFree cancellation up to 30 days before arrival."):
    return {
        "providerCode": provider_code, "hotelname": "Test Hotel",
        "voucherRemarks": [{"language": "EN", "description": text}],
    }


def test_hotel_existing_stricter_when_current_text_parses_as_stricter():
    text = ("Book direct.\n\n"
           "Cancellation Policy:\n- Free cancellation if cancelled at least 60 days before arrival.")
    client = _FakeHotelClient([_hotel_record(text=text)])
    rows, _ = cb.load_supplier_services_for_cancellation(client, "SUP-X", "Hotel")
    proposals = cb.build_proposals(rows, [{"days": 30, "fee_percentage": 0.0}], "Hotel")
    assert proposals[0]["existing_stricter"] is True
    assert proposals[0]["existing_unparseable"] is False


def test_hotel_not_flagged_stricter_when_current_text_parses_as_less_strict():
    text = ("Book direct.\n\n"
           "Cancellation Policy:\n- Free cancellation if cancelled at least 5 days before arrival.")
    client = _FakeHotelClient([_hotel_record(text=text)])
    rows, _ = cb.load_supplier_services_for_cancellation(client, "SUP-X", "Hotel")
    proposals = cb.build_proposals(rows, [{"days": 30, "fee_percentage": 0.0}], "Hotel")
    assert proposals[0]["existing_stricter"] is False
    assert proposals[0]["existing_unparseable"] is False


def test_hotel_flags_unparseable_when_current_text_is_freeform():
    text = "Book direct.\n\nCancellation within 24 hours incurs a full charge."
    client = _FakeHotelClient([_hotel_record(text=text)])
    rows, _ = cb.load_supplier_services_for_cancellation(client, "SUP-X", "Hotel")
    proposals = cb.build_proposals(rows, [{"days": 30, "fee_percentage": 0.0}], "Hotel")
    assert proposals[0]["existing_unparseable"] is True
    assert proposals[0]["existing_stricter"] is False


def test_hotel_no_existing_cancellation_text_is_neither_stricter_nor_unparseable():
    # No cancellation-mentioning block at all -> current_cancellation_snippet is None -> this is
    # "never configured", same treatment as an empty structured cancellationRanges - always
    # eligible for the new policy, never flagged unparseable (nothing to fail to parse).
    client = _FakeHotelClient([_hotel_record(text="Book direct at reception.")])
    rows, _ = cb.load_supplier_services_for_cancellation(client, "SUP-X", "Hotel")
    proposals = cb.build_proposals(rows, [{"days": 30, "fee_percentage": 0.0}], "Hotel")
    assert proposals[0]["current_cancellation_snippet"] is None
    assert proposals[0]["existing_stricter"] is False
    assert proposals[0]["existing_unparseable"] is False


def test_hotel_apply_skips_existing_stricter_without_writing():
    text = ("Book direct.\n\n"
           "Cancellation Policy:\n- Free cancellation if cancelled at least 60 days before arrival.")
    client = _FakeHotelClient([_hotel_record(text=text)])
    rows, _ = cb.load_supplier_services_for_cancellation(client, "SUP-X", "Hotel")
    proposals = cb.build_proposals(rows, [{"days": 30, "fee_percentage": 0.0}], "Hotel")
    results = cb.apply_proposals(client, "SUP-X", "Hotel", proposals)
    assert results[0]["ok"] is True
    assert results[0]["skipped"] is True
    assert client.update_calls == []


def test_hotel_apply_still_writes_an_unparseable_row_if_the_caller_chose_to_include_it():
    # apply_proposals never filters on existing_unparseable itself - only existing_stricter is
    # a hard block. The UI defaults an unparseable row's checkbox to unchecked, but a human who
    # reads the text and includes it anyway must have that write go through.
    text = "Book direct.\n\nCancellation within 24 hours incurs a full charge."
    client = _FakeHotelClient([_hotel_record(text=text)])
    rows, _ = cb.load_supplier_services_for_cancellation(client, "SUP-X", "Hotel")
    proposals = cb.build_proposals(rows, [{"days": 30, "fee_percentage": 0.0}], "Hotel")
    assert proposals[0]["existing_unparseable"] is True
    results = cb.apply_proposals(client, "SUP-X", "Hotel", proposals)
    assert results[0]["ok"] is True
    assert not results[0].get("skipped")
    assert len(client.update_calls) == 1


class _FakeTransferClient:
    def __init__(self, records):
        self._records = records
        self.update_calls = []

    def get_transfers(self, supplier_id):
        return {"transfer": self._records}

    def update_transfer(self, supplier_id, payload):
        self.update_calls.append((supplier_id, payload))
        return {"id": payload.get("id"), "code": 200}


def test_transfer_gets_the_same_text_parsing_strictness_check_as_hotel():
    text = ("Cancellation Policy:\n"
           "- Free cancellation if cancelled at least 90 days before arrival.")
    record = {"id": "TR1", "datasheets": {"EN": {"name": "Test Transfer", "voucherRemarks": text}}}
    client = _FakeTransferClient([record])
    rows, _ = cb.load_supplier_services_for_cancellation(client, "SUP-X", "Transfer")
    proposals = cb.build_proposals(rows, [{"days": 30, "fee_percentage": 0.0}], "Transfer")
    assert proposals[0]["existing_stricter"] is True
