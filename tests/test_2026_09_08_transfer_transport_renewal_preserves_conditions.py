"""Tests for the Transfer/Transport half of the price-validity RENEWAL workflow (product owner,
2026-09-08 follow-up):

    "does the same way work for Transfer and Transport? For those two services this is the most
    important tool we need."

Confirmed rule (same conversation): "the old Code, and only the old code, must be deleted and the
new Code must be added. All other informations in the Voucher remarks must stay, as long as the
conditions itself has not changed."

Two real bugs this closes, both only visible on an UPDATE (existing_transfer_snapshot /
existing_transport_snapshot present):

  1. Transfer: voucherRemarks used to be rebuilt from scratch every single publish (cancellation
     text + what-to-bring + manual notes + location note, all freshly recomputed from THIS run's
     document) rather than starting from what's already live - so a location note or what-to-bring
     line from a PREVIOUS publish that this run's price-list document doesn't happen to restate
     would silently vanish from the republished Voucher Remarks.

  2. Transport (worse): the ENTIRE description (which is where Transport's price-validity code
     actually lives - it has no separate voucherRemarks field) was locked WHOLE to the existing
     live value on every update (the established "do not change name/description" rule) - which
     meant the price-validity code could never actually change via a normal update AT ALL, since
     the freshly-composed text (new code included) was thrown away in favor of the old live text
     (old code included) every single time.

Both are now fixed the same way: on an update, the EXISTING LIVE text (its own old code stripped)
is the base; genuinely NEW what-to-bring/manual-notes content still lands (idempotent - already-
present text is never duplicated); the new code is applied last, unconditionally.
"""
from schemas import TransferHumanPreConfig, TransportHumanPreConfig
from builder import build_transfer_payload, build_transport_payloads, _append_if_new


# ---------------------------------------------------------------------------
# _append_if_new
# ---------------------------------------------------------------------------

def test_append_if_new_adds_genuinely_new_content():
    assert _append_if_new("Base text.", "New line.") == "Base text.\n\nNew line."


def test_append_if_new_skips_content_already_present():
    assert _append_if_new("Base text.\n\nAlready here.", "Already here.") == "Base text.\n\nAlready here."


def test_append_if_new_is_a_no_op_on_blank_addition():
    assert _append_if_new("Base text.", "") == "Base text."
    assert _append_if_new("Base text.", None) == "Base text."


def test_append_if_new_handles_a_blank_base():
    assert _append_if_new("", "First content.") == "First content."
    assert _append_if_new(None, "First content.") == "First content."


# ---------------------------------------------------------------------------
# Transfer
# ---------------------------------------------------------------------------

def _transfer_extracted(**overrides):
    data = {
        "departure_name": "Hurghada Airport", "arrival_name": "Hurghada Hotel Zone",
        "service_name": "Airport Transfer",
        "manual_departure_latitude": 27.18, "manual_departure_longitude": 33.80,
        "manual_arrival_latitude": 27.25, "manual_arrival_longitude": 33.83,
        "occupancy_price_tiers": [{"occupancy": 1, "price": 20}],
    }
    data.update(overrides)
    return data


def _transfer_snapshot(voucher_remarks, name="Airport Transfer: Hurghada Airport - Hurghada Hotel Zone"):
    return {
        "name": name, "currency": "EUR",
        "datasheets": {"EN": {"name": name, "description": "", "pickupDescription": "Meet at Gate 3.",
                              "voucherRemarks": voucher_remarks}},
    }


def test_transfer_update_preserves_a_location_note_the_new_document_does_not_restate(fake_api_client):
    # The live record already carries a location note from a PREVIOUS publish. This run's
    # document is a bare price list - it says nothing about location_notes at all.
    existing_voucher = ("Cancellation Policy:\n- No refund if cancelled less than 30 days before "
                        "arrival.\n\nHarbor pickups incur a $5 surcharge, ask at the desk.")
    snapshot = _transfer_snapshot(existing_voucher)
    result = build_transfer_payload(
        TransferHumanPreConfig(supplier_id="50696", currency="EUR"),
        _transfer_extracted(),  # no location_notes this run
        fake_api_client, existing_transfer_id="123", existing_transfer_snapshot=snapshot,
    )
    voucher = result["transfer_payload"]["datasheets"]["EN"]["voucherRemarks"]
    assert "Harbor pickups incur a $5 surcharge" in voucher


def test_transfer_update_swaps_only_the_price_validity_code(fake_api_client):
    existing_voucher = "Cancellation Policy:\n- No refund if cancelled less than 30 days before arrival.\n(20250101)"
    snapshot = _transfer_snapshot(existing_voucher)
    result = build_transfer_payload(
        TransferHumanPreConfig(supplier_id="50696", currency="EUR"),
        _transfer_extracted(price_valid_until_date="2027-10-31"),
        fake_api_client, existing_transfer_id="123", existing_transfer_snapshot=snapshot,
    )
    voucher = result["transfer_payload"]["datasheets"]["EN"]["voucherRemarks"]
    assert "(20271031)" in voucher
    assert "(20250101)" not in voucher
    assert "Cancellation Policy:" in voucher
    assert "No refund if cancelled less than 30 days" in voucher


def test_transfer_update_does_not_duplicate_the_cancellation_text_every_republish(fake_api_client):
    # The live text already has the (deterministic) house-standard cancellation text baked in
    # from a prior publish - republishing must not pile up a second copy.
    existing_voucher = "Cancellation Policy:\n- No refund if cancelled less than 30 days before arrival."
    snapshot = _transfer_snapshot(existing_voucher)
    result = build_transfer_payload(
        TransferHumanPreConfig(supplier_id="50696", currency="EUR"),
        _transfer_extracted(), fake_api_client,
        existing_transfer_id="123", existing_transfer_snapshot=snapshot,
    )
    voucher = result["transfer_payload"]["datasheets"]["EN"]["voucherRemarks"]
    assert voucher.count("Cancellation Policy:") == 1


def test_transfer_update_still_lands_a_genuinely_new_location_note(fake_api_client):
    existing_voucher = "Cancellation Policy:\n- No refund if cancelled less than 30 days before arrival."
    snapshot = _transfer_snapshot(existing_voucher)
    result = build_transfer_payload(
        TransferHumanPreConfig(supplier_id="50696", currency="EUR"),
        _transfer_extracted(location_notes="New for this season: harbor pickups add $5."),
        fake_api_client, existing_transfer_id="123", existing_transfer_snapshot=snapshot,
    )
    voucher = result["transfer_payload"]["datasheets"]["EN"]["voucherRemarks"]
    assert "New for this season: harbor pickups add $5." in voucher
    assert "Cancellation Policy:" in voucher


def test_transfer_create_with_no_existing_snapshot_is_unaffected(fake_api_client):
    result = build_transfer_payload(
        TransferHumanPreConfig(supplier_id="50696", currency="EUR"),
        _transfer_extracted(what_to_bring="- Passport\n- Sun cream"),
        fake_api_client,
    )
    voucher = result["transfer_payload"]["datasheets"]["EN"]["voucherRemarks"]
    assert "Cancellation Policy" in voucher or "30 days" in voucher
    assert "What to bring:" in voucher


def test_transfer_update_preserves_pickup_information_not_restated_this_run(fake_api_client):
    snapshot = _transfer_snapshot("Cancellation Policy:\n- No refund if cancelled less than 30 days before arrival.")
    result = build_transfer_payload(
        TransferHumanPreConfig(supplier_id="50696", currency="EUR"),
        _transfer_extracted(),  # no pickup_information this run
        fake_api_client, existing_transfer_id="123", existing_transfer_snapshot=snapshot,
    )
    assert result["transfer_payload"]["datasheets"]["EN"]["pickupDescription"] == "Meet at Gate 3."


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

def _transport_extracted(**overrides):
    data = {
        "departure_name": "Aswan", "arrival_name": "Luxor",
        "service_name": "Private Transport",
        "occupancy_brackets": [{"min_occupancy": 1, "max_occupancy": 4, "price": 100}],
    }
    data.update(overrides)
    return data


def _transport_snapshot(description, name="Aswan - Luxor"):
    return {
        "name": name, "currency": "EUR",
        "datasheets": {"EN": {"name": name, "description": description}},
    }


def test_transport_update_swaps_only_the_price_validity_code(fake_api_client):
    # CONFIRMED REAL BUG this closes: before this fix, the code could never actually change on
    # a Transport update at all - the whole description (code included) was locked to the OLD
    # live value regardless of what this run computed.
    existing_description = "<p>Private Transport from Aswan to Luxor.</p><p>(20250101)</p>"
    snapshot = _transport_snapshot(existing_description)
    result = build_transport_payloads(
        TransportHumanPreConfig(supplier_id="50696", currency="EUR"),
        _transport_extracted(price_valid_until_date="2027-10-31"),
        fake_api_client, existing_transport_id="123", existing_transport_snapshot=snapshot,
    )
    description = result["transport_payload"]["datasheets"]["EN"]["description"]
    assert "(20271031)" in description
    assert "(20250101)" not in description
    assert "Private Transport from Aswan to Luxor." in description


def test_transport_update_preserves_the_locked_description_text_untouched(fake_api_client):
    existing_description = "<p>A custom, human-edited description that must survive.</p>"
    snapshot = _transport_snapshot(existing_description)
    result = build_transport_payloads(
        TransportHumanPreConfig(supplier_id="50696", currency="EUR"),
        # This run's document describes the route completely differently - must be ignored,
        # same as the pre-existing name/description lock rule.
        _transport_extracted(description="A totally different AI-guessed description.",
                             description_is_custom=True),
        fake_api_client, existing_transport_id="123", existing_transport_snapshot=snapshot,
    )
    description = result["transport_payload"]["datasheets"]["EN"]["description"]
    assert "A custom, human-edited description that must survive." in description
    assert "totally different AI-guessed description" not in description


def test_transport_update_still_lands_new_what_to_bring_content(fake_api_client):
    existing_description = "<p>Private Transport from Aswan to Luxor.</p>"
    snapshot = _transport_snapshot(existing_description)
    result = build_transport_payloads(
        TransportHumanPreConfig(supplier_id="50696", currency="EUR"),
        _transport_extracted(what_to_bring="- Passport\n- Sun cream"),
        fake_api_client, existing_transport_id="123", existing_transport_snapshot=snapshot,
    )
    description = result["transport_payload"]["datasheets"]["EN"]["description"]
    assert "What to bring:" in description
    assert "Sun cream" in description


def test_transport_create_with_no_existing_snapshot_is_unaffected(fake_api_client):
    result = build_transport_payloads(
        TransportHumanPreConfig(supplier_id="50696", currency="EUR"),
        _transport_extracted(price_valid_until_date="2027-10-31"),
        fake_api_client,
    )
    description = result["transport_payload"]["datasheets"]["EN"]["description"]
    assert "(20271031)" in description
    assert "Private Transport" in description or "Aswan" in description
