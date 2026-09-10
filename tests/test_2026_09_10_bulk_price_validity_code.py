"""Tests for the bulk price-validity code feature (product owner, 2026-09-10):

    "so now I have to add to all Transport from supplier MOMIRA_EG_FT in the Voucher the code
    (20270430) - where can i do it and if there is already a code, the update has to change
    ONLY this code."

Before this, a "(YYYYMMDD)" price-validity code (price_validity.py) could only be set one
service at a time, as part of the full create/update upload flow. This adds a THIRD mode
(bulk_notes.MODE_PRICE_CODE) alongside the existing append/replace text modes: it does not
append (which would pile up a second code alongside the old one) and does not replace (which
would wipe every other word already in the field) - it strips whatever code is already there
and appends the new one, via the SAME price_validity.with_price_validity_code function the
single-service flows already use, so the surgical strip+append behaviour can never drift
between the single-service and bulk paths.
"""
import copy

import bulk_notes
import price_validity


def _transport_record(description="Fast, comfortable transfer from Cairo airport to the hotel.",
                      voucher_remarks=None):
    sheet = {"description": description}
    if voucher_remarks is not None:
        sheet["voucherRemarks"] = voucher_remarks
    return {
        "id": "TRANSPORT-1", "name": "CAI Airport Transfer",
        "datasheets": {"EN": sheet},
    }


def _transfer_record(voucher_remarks="Please be ready 15 minutes before pickup."):
    return {
        "id": "TRANSFER-1", "name": "Hurghada Airport Transfer",
        "datasheets": {"EN": {"voucherRemarks": voucher_remarks}},
    }


# ---------------------------------------------------------------------------
# combine() - the pure logic
# ---------------------------------------------------------------------------

def test_price_code_mode_appends_a_code_to_text_with_no_existing_code():
    result = bulk_notes.combine("Fast transfer from the airport.", "2027-04-30",
                                bulk_notes.MODE_PRICE_CODE)
    assert result == "Fast transfer from the airport.\n(20270430)"


def test_price_code_mode_replaces_only_the_code_not_the_rest_of_the_text():
    existing = "Fast transfer from the airport.\n(20260101)"
    result = bulk_notes.combine(existing, "2027-04-30", bulk_notes.MODE_PRICE_CODE)
    assert "Fast transfer from the airport." in result
    assert "(20260101)" not in result
    assert "(20270430)" in result
    # Only ONE code after the update - never two.
    assert result.count("(20270430)") == 1


def test_price_code_mode_on_an_already_correct_code_is_a_no_op():
    existing = "Fast transfer from the airport.\n(20270430)"
    result = bulk_notes.combine(existing, "2027-04-30", bulk_notes.MODE_PRICE_CODE)
    assert result == existing


def test_price_code_mode_on_empty_field_writes_just_the_code():
    result = bulk_notes.combine("", "2027-04-30", bulk_notes.MODE_PRICE_CODE)
    assert result == "(20270430)"


# ---------------------------------------------------------------------------
# write_field / plan() - the datasheet-shaped record round trip
# ---------------------------------------------------------------------------

def test_write_field_updates_transport_voucher_remarks_leaving_description_untouched():
    # CORRECTED (2026-09-10): Transport has its own real voucherRemarks field - see this
    # module's own docstring update below. Description is a separate field entirely now and
    # must be left completely alone by a "Voucher remarks" write.
    record = _transport_record(
        description="Fast, comfortable transfer from Cairo airport to the hotel.",
        voucher_remarks="(20260101)")
    updated, changes = bulk_notes.write_field(
        record, "Transport", "Voucher remarks", "2027-04-30", bulk_notes.MODE_PRICE_CODE)
    new_text = updated["datasheets"]["EN"]["voucherRemarks"]
    assert "(20260101)" not in new_text
    assert "(20270430)" in new_text
    assert updated["datasheets"]["EN"]["description"] == \
        "Fast, comfortable transfer from Cairo airport to the hotel."
    assert "EN" in changes


def test_write_field_transport_voucher_remarks_writes_to_its_own_real_field():
    # CORRECTED (2026-09-10, real production evidence): Transport DOES have its own separate
    # voucherRemarks field after all - a real screenshot of Travel Compositor's own Transport
    # edit screen proved the earlier "aliased to description" belief wrong. A real bulk write
    # under that wrong belief landed a code in description for 168 Transports of one supplier -
    # see bulk_notes._plan_transport_voucher_code_repair for the one-off fix.
    assert bulk_notes.TARGETS["Transport"]["Voucher remarks"] == "voucherRemarks"


def test_write_field_unchanged_when_same_code_already_present():
    record = _transport_record(voucher_remarks="Fast transfer.\n(20270430)")
    updated, changes = bulk_notes.write_field(
        record, "Transport", "Voucher remarks", "2027-04-30", bulk_notes.MODE_PRICE_CODE)
    assert changes == {}
    assert updated["datasheets"]["EN"]["voucherRemarks"] == "Fast transfer.\n(20270430)"


def test_write_field_updates_transfer_voucher_remarks_field():
    record = _transfer_record("Please be ready 15 minutes before pickup.\n(20260601)")
    updated, changes = bulk_notes.write_field(
        record, "Transfer", "Voucher remarks", "2027-04-30", bulk_notes.MODE_PRICE_CODE)
    new_text = updated["datasheets"]["EN"]["voucherRemarks"]
    assert "Please be ready 15 minutes before pickup." in new_text
    assert "(20260601)" not in new_text
    assert "(20270430)" in new_text


def test_original_record_is_never_mutated_in_place():
    record = _transport_record("Fast transfer.\n(20260101)")
    original_copy = copy.deepcopy(record)
    bulk_notes.write_field(record, "Transport", "Voucher remarks", "2027-04-30",
                           bulk_notes.MODE_PRICE_CODE)
    assert record == original_copy


# ---------------------------------------------------------------------------
# _unchanged_reason - clearer message than the generic text-mode one
# ---------------------------------------------------------------------------

def test_unchanged_reason_names_the_price_validity_code_specifically():
    record = _transport_record("Fast transfer.\n(20270430)")
    reason = bulk_notes._unchanged_reason(record, "Transport", "Voucher remarks", "2027-04-30",
                                          bulk_notes.MODE_PRICE_CODE)
    assert "price-validity code" in reason


# ---------------------------------------------------------------------------
# End-to-end sanity: the same primitive the single-service flow already uses
# ---------------------------------------------------------------------------

def test_bulk_mode_and_single_service_helper_produce_identical_output():
    # bulk_notes.combine's price-code branch must be a thin wrapper around
    # price_validity.with_price_validity_code, never a parallel re-implementation that could
    # quietly drift from the single-service upload flow's own behaviour.
    existing = "Fast transfer.\n(20260101)"
    via_bulk = bulk_notes.combine(existing, "2027-04-30", bulk_notes.MODE_PRICE_CODE)
    via_single = price_validity.with_price_validity_code(
        existing, {"price_valid_until_date": "2027-04-30"})
    assert via_bulk == via_single


# ---------------------------------------------------------------------------
# app.py wiring - source-shape check only (app.py can't be imported in a test
# process, same established pattern as test_2026_09_08_ticket_batch_update_flow.py)
# ---------------------------------------------------------------------------
import os

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_app_py_offers_the_price_validity_code_bulk_option():
    src = _read_app_py()
    assert '"A price-validity code (YYYYMMDD)"' in src
    assert "price_code_available = product_type in price_validity.PRODUCT_TYPES" in src


def test_app_py_price_code_branch_wires_the_new_mode_and_fixed_target():
    src = _read_app_py()
    assert 'elif add_price_code:' in src
    branch = src.split("elif add_price_code:")[1].split("\n    else:")[0]
    assert 'target = "Voucher remarks"' in branch
    assert "mode = bulk_notes.MODE_PRICE_CODE" in branch
    assert 'pv_date.isoformat()' in branch


# ---------------------------------------------------------------------------
# Real production bug (product owner, 2026-09-10): a real run against every Transport of
# supplier MOMIRA_EG_FT failed ALL 168 services with "updateTransport.transport.airlineCode:
# must not be null" - Travel Compositor's PUT requires airlineCode even though a real GET
# routinely omits/nulls it for a non-flight transport (confirmed in schemas.py's own note on
# ContractTransportVO.airlineCode). write_field PUTs the record back exactly as fetched, so a
# record whose GET never carried this field failed validation on the way back in. Because ALL
# 168 failed with BAD_REQUEST before any write happened, nothing was actually changed on any of
# them - this is purely a "the retry will now succeed" fix, not a data-repair one.
# ---------------------------------------------------------------------------

def test_write_field_fills_in_a_missing_airline_code_for_transport():
    record = {"id": "TRANSPORT-1", "name": "Luxor - Hurghada",
             "datasheets": {"EN": {"description": "Private transfer."}}}
    assert "airlineCode" not in record
    updated, _ = bulk_notes.write_field(record, "Transport", "Voucher remarks", "2027-04-30",
                                        bulk_notes.MODE_PRICE_CODE)
    assert updated["airlineCode"] == ""


def test_write_field_fills_in_a_null_airline_code_for_transport():
    record = {"id": "TRANSPORT-1", "name": "Cairo - Alexandria", "airlineCode": None,
             "datasheets": {"EN": {"description": "Private transfer."}}}
    updated, _ = bulk_notes.write_field(record, "Transport", "Voucher remarks", "2027-04-30",
                                        bulk_notes.MODE_PRICE_CODE)
    assert updated["airlineCode"] == ""


def test_write_field_never_overwrites_a_real_airline_code_already_present():
    record = {"id": "TRANSPORT-1", "name": "Cairo - Hurghada", "airlineCode": "MS",
             "datasheets": {"EN": {"description": "Private transfer."}}}
    updated, _ = bulk_notes.write_field(record, "Transport", "Voucher remarks", "2027-04-30",
                                        bulk_notes.MODE_PRICE_CODE)
    assert updated["airlineCode"] == "MS"


def test_write_field_does_not_touch_airline_code_for_other_product_types():
    # The fix is scoped to Transport (the only product type confirmed to hit this) - a Transfer
    # record must not gain a field it never had.
    record = {"id": "TRANSFER-1", "name": "Hurghada Airport Transfer",
             "datasheets": {"EN": {"voucherRemarks": "Please be ready 15 minutes before pickup."}}}
    updated, _ = bulk_notes.write_field(record, "Transfer", "Voucher remarks", "2027-04-30",
                                        bulk_notes.MODE_PRICE_CODE)
    assert "airlineCode" not in updated
