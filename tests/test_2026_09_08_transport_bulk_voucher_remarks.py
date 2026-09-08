"""Regression test for a real production gap (product owner, 2026-09-08): "In adding manual
information within transport, the field Voucher remarks not included - we must include that for
bulk upload."

Root cause: bulk_notes.py's TARGETS/UNAVAILABLE_REASON explicitly excluded "Voucher remarks" as a
selectable field for Transport bulk notes, even though every other product type offers it -
Transport genuinely has no separate voucherRemarks field in Travel Compositor (confirmed - only
name + description), but "Cancellation update" already aliased to "description" for exactly this
reason, so "Voucher remarks" should too, under the label a human looking for it will actually
find (matching how ClosedTour/Ticket/Transfer already alias BOTH "Voucher remarks" and
"Cancellation update" to the same underlying "voucherRemarks" field).
"""
import bulk_notes


def test_voucher_remarks_is_now_a_selectable_bulk_target_for_transport():
    assert "Voucher remarks" in bulk_notes.available_targets("Transport")


def test_transport_voucher_remarks_writes_to_the_description_field():
    assert bulk_notes.TARGETS["Transport"]["Voucher remarks"] == "description"


def test_voucher_remarks_is_no_longer_listed_as_unavailable_for_transport():
    assert "Voucher remarks" not in bulk_notes.unavailable_targets("Transport")


def test_other_product_types_are_unaffected():
    for product_type in ("Ticket", "Transfer", "ClosedTour", "Hotel"):
        assert "Voucher remarks" in bulk_notes.available_targets(product_type)
        assert bulk_notes.TARGETS[product_type]["Voucher remarks"] == "voucherRemarks"
