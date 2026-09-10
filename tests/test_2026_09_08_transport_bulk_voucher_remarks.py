"""Regression test for a real production gap (product owner, 2026-09-08): "In adding manual
information within transport, the field Voucher remarks not included - we must include that for
bulk upload."

Root cause (original, 2026-09-08): bulk_notes.py's TARGETS/UNAVAILABLE_REASON explicitly excluded
"Voucher remarks" as a selectable field for Transport bulk notes, even though every other product
type offers it. At the time it was aliased to "description", on the belief that Transport had no
separate voucherRemarks field in Travel Compositor.

CORRECTED (2026-09-10, real production evidence): that belief was wrong - a real screenshot of
Travel Compositor's own Transport edit screen shows a genuine, separate Voucher remarks input.
A real bulk price-validity-code run against 168 Transports landed the code in Description because
of this wrong alias, before it was caught - see bulk_notes._plan_transport_voucher_code_repair for
the one-off fix, and schemas.py's TransportDataSheetVO.voucherRemarks for the corrected schema.
"Voucher remarks" now points at the real "voucherRemarks" field, same as every other product
type; "Cancellation update" is unaffected (still "description" - see bulk_notes.TARGETS' own
comment for why that one stays as-is).
"""
import bulk_notes


def test_voucher_remarks_is_now_a_selectable_bulk_target_for_transport():
    assert "Voucher remarks" in bulk_notes.available_targets("Transport")


def test_transport_voucher_remarks_writes_to_its_own_real_field():
    assert bulk_notes.TARGETS["Transport"]["Voucher remarks"] == "voucherRemarks"


def test_voucher_remarks_is_no_longer_listed_as_unavailable_for_transport():
    assert "Voucher remarks" not in bulk_notes.unavailable_targets("Transport")


def test_other_product_types_are_unaffected():
    for product_type in ("Ticket", "Transfer", "ClosedTour", "Hotel"):
        assert "Voucher remarks" in bulk_notes.available_targets(product_type)
        assert bulk_notes.TARGETS[product_type]["Voucher remarks"] == "voucherRemarks"
