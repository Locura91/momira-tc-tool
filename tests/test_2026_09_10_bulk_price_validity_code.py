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

REVERTED 2026-09-11 (real production evidence): a brief 2026-09-10 "fix" pointed Transport's
"Voucher remarks" target at a genuine, separately-persisted voucherRemarks field, on the belief
of a UI screenshot alone. The product owner has since pasted Travel Compositor's real Swagger
for PUT /transport/{supplierId}, confirming ContractTransportDataSheetVO has ONLY name and
description - no voucherRemarks anywhere. "Voucher remarks" is no longer offered as a Transport
target at all; every Transport price-code write in these tests now targets "Description
(bottom)" instead, same field the code lived in before 2026-09-10.

EN-ONLY (product owner, 2026-09-11 follow-up): "we need the code only in english, no other
languages needed (not even when translation)." write_field's general rule is to write into
EVERY language a record already has (see its own docstring) - but MODE_PRICE_CODE is a
deliberate exception: the "(YYYYMMDD)" code is this app's own internal price-validity marker,
not customer-facing text, and must never appear in a non-EN datasheet even after Travel
Compositor's translation tooling runs. See the "EN-only" tests below.
"""
import copy

import bulk_notes
import price_validity


def _transport_record(description="Fast, comfortable transfer from Cairo airport to the hotel."):
    return {
        "id": "TRANSPORT-1", "name": "CAI Airport Transfer",
        "datasheets": {"EN": {"description": description}},
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

def test_write_field_updates_transport_description_with_the_code():
    # REVERTED 2026-09-11 (see this module's own header update): Transport has no separate
    # voucherRemarks field - the code lands in description, the only field Transport has.
    record = _transport_record(
        description="Fast, comfortable transfer from Cairo airport to the hotel.\n(20260101)")
    updated, changes = bulk_notes.write_field(
        record, "Transport", "Description (bottom)", "2027-04-30", bulk_notes.MODE_PRICE_CODE)
    new_text = updated["datasheets"]["EN"]["description"]
    assert "(20260101)" not in new_text
    assert "(20270430)" in new_text
    assert "Fast, comfortable transfer from Cairo airport to the hotel." in new_text
    assert "EN" in changes


def test_write_field_transport_voucher_remarks_is_not_a_valid_target():
    # REVERTED 2026-09-11 (real production evidence, Swagger-confirmed): the 2026-09-10 belief
    # that Transport had its own separate voucherRemarks field was wrong.
    assert "Voucher remarks" not in bulk_notes.TARGETS["Transport"]
    assert "Voucher remarks" not in bulk_notes.available_targets("Transport")
    assert "Voucher remarks" in bulk_notes.unavailable_targets("Transport")


def test_write_field_unchanged_when_same_code_already_present():
    record = _transport_record(description="Fast transfer.\n(20270430)")
    updated, changes = bulk_notes.write_field(
        record, "Transport", "Description (bottom)", "2027-04-30", bulk_notes.MODE_PRICE_CODE)
    assert changes == {}
    assert updated["datasheets"]["EN"]["description"] == "Fast transfer.\n(20270430)"


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
    bulk_notes.write_field(record, "Transport", "Description (bottom)", "2027-04-30",
                           bulk_notes.MODE_PRICE_CODE)
    assert record == original_copy


# ---------------------------------------------------------------------------
# EN-only (product owner, 2026-09-11): the price-validity code must never be written into a
# non-EN datasheet, even when the record already has one (e.g. from Travel Compositor's own
# translation tooling) - unlike every other bulk-notes mode, which deliberately writes into
# every language the record has.
# ---------------------------------------------------------------------------

def test_write_field_price_code_only_touches_en_leaving_other_languages_untouched():
    record = {
        "id": "TRANSPORT-1", "name": "CAI Airport Transfer",
        "datasheets": {
            "EN": {"description": "Fast transfer from Cairo airport."},
            "DE": {"description": "Schneller Transfer vom Flughafen Kairo."},
            "FR": {"description": "Transfert rapide depuis l'aeroport du Caire."},
        },
    }
    updated, changes = bulk_notes.write_field(
        record, "Transport", "Description (bottom)", "2027-04-30", bulk_notes.MODE_PRICE_CODE)
    assert list(changes.keys()) == ["EN"]
    assert "(20270430)" in updated["datasheets"]["EN"]["description"]
    assert updated["datasheets"]["DE"]["description"] == "Schneller Transfer vom Flughafen Kairo."
    assert updated["datasheets"]["FR"]["description"] == "Transfert rapide depuis l'aeroport du Caire."
    assert "(20270430)" not in updated["datasheets"]["DE"]["description"]
    assert "(20270430)" not in updated["datasheets"]["FR"]["description"]


def test_write_field_price_code_removes_a_stray_old_code_from_en_only_not_other_languages():
    # A leftover code in a non-EN datasheet (e.g. from before this fix, or a stray translation)
    # is left exactly as it is - this mode only ever touches EN, it never goes hunting through
    # other languages to clean them up either.
    record = {
        "id": "TRANSPORT-1", "name": "CAI Airport Transfer",
        "datasheets": {
            "EN": {"description": "Fast transfer.\n(20260101)"},
            "DE": {"description": "Schneller Transfer.\n(20260101)"},
        },
    }
    updated, changes = bulk_notes.write_field(
        record, "Transport", "Description (bottom)", "2027-04-30", bulk_notes.MODE_PRICE_CODE)
    assert list(changes.keys()) == ["EN"]
    assert "(20260101)" not in updated["datasheets"]["EN"]["description"]
    assert "(20270430)" in updated["datasheets"]["EN"]["description"]
    assert updated["datasheets"]["DE"]["description"] == "Schneller Transfer.\n(20260101)"


def test_write_field_regular_text_mode_still_writes_every_language():
    # Confirms the EN-only carve-out is specific to MODE_PRICE_CODE - a normal note (append/
    # replace) still reaches every language, exactly as write_field's own docstring says.
    record = {
        "id": "TRANSPORT-1", "name": "CAI Airport Transfer",
        "datasheets": {
            "EN": {"description": "Fast transfer."},
            "DE": {"description": "Schneller Transfer."},
        },
    }
    updated, changes = bulk_notes.write_field(
        record, "Transport", "Description (bottom)", "New pickup point.", bulk_notes.MODE_APPEND)
    assert set(changes.keys()) == {"EN", "DE"}
    assert "New pickup point." in updated["datasheets"]["EN"]["description"]
    assert "New pickup point." in updated["datasheets"]["DE"]["description"]


# ---------------------------------------------------------------------------
# _unchanged_reason - clearer message than the generic text-mode one
# ---------------------------------------------------------------------------

def test_unchanged_reason_names_the_price_validity_code_specifically():
    record = _transport_record("Fast transfer.\n(20270430)")
    reason = bulk_notes._unchanged_reason(record, "Transport", "Description (bottom)", "2027-04-30",
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


def test_app_py_price_code_branch_wires_the_new_mode_and_transport_aware_target():
    # REVERTED 2026-09-11: the target is no longer a single fixed string - Transport gets
    # "Description (bottom)" (no working voucherRemarks field), every other product type still
    # gets "Voucher remarks".
    src = _read_app_py()
    assert 'elif add_price_code:' in src
    branch = src.split("elif add_price_code:")[1].split("\n    else:")[0]
    assert 'target = "Description (bottom)" if product_type == "Transport" else "Voucher remarks"' in branch
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
    updated, _ = bulk_notes.write_field(record, "Transport", "Description (bottom)", "2027-04-30",
                                        bulk_notes.MODE_PRICE_CODE)
    assert updated["airlineCode"] == ""


def test_write_field_fills_in_a_null_airline_code_for_transport():
    record = {"id": "TRANSPORT-1", "name": "Cairo - Alexandria", "airlineCode": None,
             "datasheets": {"EN": {"description": "Private transfer."}}}
    updated, _ = bulk_notes.write_field(record, "Transport", "Description (bottom)", "2027-04-30",
                                        bulk_notes.MODE_PRICE_CODE)
    assert updated["airlineCode"] == ""


def test_write_field_never_overwrites_a_real_airline_code_already_present():
    record = {"id": "TRANSPORT-1", "name": "Cairo - Hurghada", "airlineCode": "MS",
             "datasheets": {"EN": {"description": "Private transfer."}}}
    updated, _ = bulk_notes.write_field(record, "Transport", "Description (bottom)", "2027-04-30",
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
