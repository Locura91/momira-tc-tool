"""Regression test for a real production gap (product owner, 2026-09-08): "In adding manual
information within transport, the field Voucher remarks not included - we must include that for
bulk upload."

THE ANSWER TURNED OUT TO BE "IT CANNOT BE" — and that is what this file now pins.

History, because the back-and-forth is the whole point of these tests:
  2026-09-08  "Voucher remarks" was not offered for Transport. Reported as a gap.
  2026-09-10  Believed to be a real omission, on the strength of a screenshot of Travel
              Compositor's own Transport edit screen showing a Voucher remarks input.
              "Voucher remarks" was pointed at a "voucherRemarks" field for Transport.
  2026-09-11  REVERTED against real production evidence. Travel Compositor's own Swagger for
              PUT /transport/{supplierId} shows ContractTransportDataSheetVO carries ONLY
              name and description — there is no voucherRemarks field on Transport at all.
              Every write sent to it had been silently dropped, and a live screenshot showed
              the field still empty afterwards. The admin UI shows an input that its own API
              will not persist.

So Transport genuinely has nowhere to put voucher remarks, and bulk_notes correctly refuses
to offer the target rather than writing into a void. These tests previously asserted the
2026-09-10 belief and had been failing ever since the revert; they now assert the revert,
which is the behaviour the code has actually had since.

The cost of getting this wrong is not theoretical: a bulk price-validity-code run against
168 Transports landed its code in the wrong field because of the earlier wrong alias.
"""
import bulk_notes


def test_voucher_remarks_is_not_offered_as_a_bulk_target_for_transport():
    """Transport has no voucherRemarks field, so offering the target would mean writing
    text Travel Compositor silently discards."""
    assert "Voucher remarks" not in bulk_notes.available_targets("Transport")


def test_transport_offers_only_the_fields_that_really_persist():
    assert set(bulk_notes.TARGETS["Transport"]) == {"Description (bottom)",
                                                    "Cancellation update"}
    assert bulk_notes.TARGETS["Transport"]["Description (bottom)"] == "description"


def test_voucher_remarks_is_listed_as_unavailable_for_transport_with_a_reason():
    """Listed as explicitly unavailable rather than quietly absent, so the human is told
    why and pointed at Description instead."""
    unavailable = bulk_notes.unavailable_targets("Transport")
    assert "Voucher remarks" in unavailable
    assert "Description" in unavailable["Voucher remarks"]


def test_transport_voucher_remarks_is_never_aliased_onto_another_field():
    """The 2026-09-08 state aliased it to description, which is how the 168-Transport run
    put the code in the wrong place. An alias is worse than no target at all: it looks
    like it worked."""
    assert "Voucher remarks" not in bulk_notes.TARGETS["Transport"]


def test_cancellation_update_still_points_at_description_for_transport():
    """Unaffected by any of the above — it was always description for Transport."""
    assert bulk_notes.TARGETS["Transport"]["Cancellation update"] == "description"


def test_other_product_types_are_unaffected():
    """Every type that DOES have the field still offers it — the restriction is Transport's
    alone, not a general retreat."""
    for product_type in ("Ticket", "Transfer", "ClosedTour", "Hotel"):
        assert "Voucher remarks" in bulk_notes.available_targets(product_type)
        assert bulk_notes.TARGETS[product_type]["Voucher remarks"] == "voucherRemarks"
