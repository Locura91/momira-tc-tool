"""Tests for the Transport cancellation-policy TEXT repair tool + the "going forward" change to
cancellation_bulk_transport.py (product owner, 2026-09-11):

    "first missleading: It is not a new supplement/addintional information; I just want to move
    thise phrase 'Cancellation Policy: - Free cancellation if cancelled at least 30 days before
    arrival.' from Description to Voucher remark from Supplier MOMIRA_EG_FT and MOMIRA_TEST"

Followed by an explicit "Both — one-off cleanup AND change it going forward (recommended)"
answer, confirming this is TWO changes:

  1. A one-off repair tool (bulk_notes._plan_transport_cancellation_text_repair /
     _apply_transport_cancellation_text_repair), mirroring the existing
     _plan_transport_voucher_code_repair pattern (see
     test_2026_09_10_transport_voucher_field_fix_and_repair.py), to move the SENTENCE (not a
     date code) for Transports that already have it stuck in Description.
  2. cancellation_bulk_transport.py's ongoing bulk cancellation-policy tool now writes the new
     policy sentence into Voucher remarks going forward (see that module's own docstring for the
     "CORRECTED 2026-09-11" reasoning), with a self-migrating side effect: any leftover
     cancellation paragraph still in Description is removed the next time a supplier's policy is
     bulk-updated there, even for records the one-off repair above hasn't reached yet.

NOTE: builder.py's own build_transport_payloads (the core AI-extraction create/update pipeline
used by every regular document upload) is NOT touched by this change - it still deliberately
locks cancellation text to `description` per the standing "do not change the name and the
description of transfer and transport" product-owner rule. That is a separate, still-open
question, not something this test file asserts either way.
"""
import copy

import bulk_notes
import cancellation_bulk_transport as cbt


# ---------------------------------------------------------------------------
# cancellation_bulk_transport.py: new voucher-remarks-block helpers
# ---------------------------------------------------------------------------

def test_snippet_in_voucher_remarks_finds_the_cancellation_block():
    vr = "Cancellation Policy:\nFree cancellation if cancelled at least 30 days before arrival."
    assert cbt._current_cancellation_snippet_in_voucher_remarks(vr) == vr


def test_snippet_in_voucher_remarks_ignores_other_blocks():
    vr = "(20270430)\n\nCancellation Policy:\nFree cancellation up to 30 days before arrival.\n\nPlease tip the driver."
    assert cbt._current_cancellation_snippet_in_voucher_remarks(vr) == \
        "Cancellation Policy:\nFree cancellation up to 30 days before arrival."


def test_snippet_in_voucher_remarks_returns_none_when_no_block_matches():
    vr = "(20270430)\n\nPlease tip the driver in cash."
    assert cbt._current_cancellation_snippet_in_voucher_remarks(vr) is None


def test_snippet_in_voucher_remarks_returns_none_for_empty_input():
    assert cbt._current_cancellation_snippet_in_voucher_remarks("") is None
    assert cbt._current_cancellation_snippet_in_voucher_remarks(None) is None


def test_combined_snippet_prefers_voucher_remarks_over_description():
    vr = "Cancellation Policy:\nFree cancellation up to 30 days before arrival."
    description = "<p>Service info</p><p>Cancellation Policy:\nDIFFERENT STALE TEXT.</p>"
    assert cbt._current_cancellation_snippet(vr, description) == vr


def test_combined_snippet_falls_back_to_description_when_voucher_remarks_has_nothing():
    description = "<p>Service info</p><p>Free cancellation up to 30 days before arrival.</p>"
    assert cbt._current_cancellation_snippet("", description) == \
        "Free cancellation up to 30 days before arrival."


def test_combined_snippet_none_when_neither_has_it():
    assert cbt._current_cancellation_snippet("(20270430)", "<p>Service info</p>") is None


def test_swap_in_voucher_remarks_replaces_the_existing_block_leaving_others_untouched():
    vr = "(20270430)\n\nCancellation Policy:\nOld wording.\n\nPlease tip the driver."
    new_vr, found = cbt._swap_cancellation_text_in_voucher_remarks(vr, "Cancellation Policy:\nNew wording.")
    assert found is True
    assert new_vr == "(20270430)\n\nCancellation Policy:\nNew wording.\n\nPlease tip the driver."


def test_swap_in_voucher_remarks_appends_when_no_block_found():
    vr = "(20270430)\n\nPlease tip the driver."
    new_vr, found = cbt._swap_cancellation_text_in_voucher_remarks(vr, "Cancellation Policy:\nNew wording.")
    assert found is False
    assert "Cancellation Policy:\nNew wording." in new_vr
    assert "(20270430)" in new_vr
    assert "Please tip the driver." in new_vr


def test_swap_in_voucher_remarks_handles_a_completely_empty_field():
    new_vr, found = cbt._swap_cancellation_text_in_voucher_remarks("", "Cancellation Policy:\nNew wording.")
    assert found is False
    assert new_vr == "Cancellation Policy:\nNew wording."
    new_vr2, found2 = cbt._swap_cancellation_text_in_voucher_remarks(None, "Cancellation Policy:\nNew wording.")
    assert found2 is False
    assert new_vr2 == "Cancellation Policy:\nNew wording."


def test_swap_in_voucher_remarks_is_idempotent():
    vr = "Cancellation Policy:\nSame wording."
    new_vr, found = cbt._swap_cancellation_text_in_voucher_remarks(vr, "Cancellation Policy:\nSame wording.")
    assert found is True
    assert new_vr == vr


# ---------------------------------------------------------------------------
# cancellation_bulk_transport.py: _remove_cancellation_paragraph (description-side cleanup)
# ---------------------------------------------------------------------------

def test_remove_cancellation_paragraph_deletes_the_matched_block_entirely():
    html = ("<p>Private transfer from the airport.</p>"
           "<p>Free cancellation up to 30 days before arrival.</p>"
           "<p>What to bring:\nPassport</p>")
    new_html, found = cbt._remove_cancellation_paragraph(html)
    assert found is True
    assert new_html == ("<p>Private transfer from the airport.</p>"
                        "<p>What to bring:\nPassport</p>")
    assert "cancellation" not in new_html.lower()


def test_remove_cancellation_paragraph_no_op_when_nothing_matches():
    html = "<p>Private transfer from the airport.</p>"
    new_html, found = cbt._remove_cancellation_paragraph(html)
    assert found is False
    assert new_html == html


def test_remove_cancellation_paragraph_handles_empty_input():
    new_html, found = cbt._remove_cancellation_paragraph("")
    assert found is False
    assert new_html == ""
    new_html2, found2 = cbt._remove_cancellation_paragraph(None)
    assert found2 is False
    assert new_html2 == ""


def test_remove_cancellation_paragraph_leaves_a_later_paragraph_that_merely_mentions_it():
    # Same "first match only" rule _swap_cancellation_paragraph documents - a manual note that
    # merely refers to cancellation later must not also be swept up.
    html = ("<p>Desc.</p><p>Free cancellation up to 30 days before arrival.</p>"
           "<p>This supplier's cancellation terms changed in March.</p>")
    new_html, found = cbt._remove_cancellation_paragraph(html)
    assert found is True
    assert new_html == ("<p>Desc.</p>"
                        "<p>This supplier's cancellation terms changed in March.</p>")


# ---------------------------------------------------------------------------
# bulk_notes.py: the one-off repair tool
# ---------------------------------------------------------------------------

class _FakeClient:
    def __init__(self, transports=None):
        self._transports = transports or []
        self.updated_transports = []

    def get_transports(self, supplier_id):
        return {"transport": self._transports}

    def update_transport(self, supplier_id, payload):
        self.updated_transports.append(payload)
        return payload


def _transport(t_id="TRANSPORT-1", name="Luxor - Hurghada",
               description="<p>Private car with driver.</p>"
                          "<p>Cancellation Policy: - Free cancellation if cancelled at least "
                          "30 days before arrival.</p>",
               voucher_remarks=None):
    sheet = {"name": name, "description": description}
    if voucher_remarks is not None:
        sheet["voucherRemarks"] = voucher_remarks
    return {"id": t_id, "name": name, "datasheets": {"EN": sheet}}


def test_repair_moves_the_cancellation_sentence_from_description_to_voucher_remarks():
    t = _transport()
    client = _FakeClient(transports=[t])
    plan = bulk_notes._plan_transport_cancellation_text_repair(client, "SUP1")
    assert plan["will_change"] == 1
    assert plan["unchanged"] == 0
    new_sheet = plan["items"][0]["record"]["datasheets"]["EN"]
    assert "Cancellation Policy" not in new_sheet["description"]
    assert new_sheet["description"] == "<p>Private car with driver.</p>"
    assert "Free cancellation if cancelled at least 30 days before arrival." in new_sheet["voucherRemarks"]


def test_repair_preserves_existing_voucher_remarks_text_when_moving_the_sentence():
    t = _transport(voucher_remarks="(20270430)")
    client = _FakeClient(transports=[t])
    plan = bulk_notes._plan_transport_cancellation_text_repair(client, "SUP1")
    new_sheet = plan["items"][0]["record"]["datasheets"]["EN"]
    assert "(20270430)" in new_sheet["voucherRemarks"]
    assert "Free cancellation" in new_sheet["voucherRemarks"]


def test_repair_skips_a_transport_with_no_cancellation_paragraph_in_description():
    t = _transport(description="<p>Private car with driver.</p>")
    client = _FakeClient(transports=[t])
    plan = bulk_notes._plan_transport_cancellation_text_repair(client, "SUP1")
    assert plan["will_change"] == 0
    assert plan["unchanged"] == 1


def test_repair_is_idempotent_running_it_twice_finds_nothing_the_second_time():
    t = _transport()
    client = _FakeClient(transports=[t])
    plan = bulk_notes._plan_transport_cancellation_text_repair(client, "SUP1")
    fixed_record = plan["items"][0]["record"]
    client2 = _FakeClient(transports=[fixed_record])
    plan2 = bulk_notes._plan_transport_cancellation_text_repair(client2, "SUP1")
    assert plan2["will_change"] == 0
    assert plan2["unchanged"] == 1


def test_repair_fills_in_missing_airline_code_via_normalize_for_put():
    t = _transport()
    assert "airlineCode" not in t
    client = _FakeClient(transports=[t])
    plan = bulk_notes._plan_transport_cancellation_text_repair(client, "SUP1")
    assert plan["items"][0]["record"]["airlineCode"] == ""


def test_original_record_never_mutated_by_the_repair_plan():
    t = _transport()
    original_copy = copy.deepcopy(t)
    client = _FakeClient(transports=[t])
    bulk_notes._plan_transport_cancellation_text_repair(client, "SUP1")
    assert t == original_copy


def test_apply_dispatches_the_repair_kind_via_plan_structured():
    t = _transport()
    client = _FakeClient(transports=[t])
    plan = bulk_notes.plan_structured(
        client, "SUP1", "Transport", "transport_cancellation_text_repair", {})
    result = bulk_notes.apply(client, "SUP1", plan)
    assert len(result["updated"]) == 1
    assert len(client.updated_transports) == 1
    written = client.updated_transports[0]["datasheets"]["EN"]
    assert "Free cancellation if cancelled at least 30 days before arrival." in written["voucherRemarks"]
    assert "Cancellation Policy" not in written["description"]


def test_structured_targets_lists_the_repair_option_for_transport():
    assert "transport_cancellation_text_repair" in bulk_notes.STRUCTURED_TARGETS["Transport"].values()
    assert bulk_notes.available_structured_targets("Transport") == \
        list(bulk_notes.STRUCTURED_TARGETS["Transport"].keys())


# ---------------------------------------------------------------------------
# app.py wiring - source-shape check only (app.py can't be imported in a test process)
# ---------------------------------------------------------------------------
import os

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_app_py_has_the_repair_ui_branch():
    src = _read_app_py()
    assert 'structured_kind == "transport_cancellation_text_repair"' in src


# ---------------------------------------------------------------------------
# cancellation_bulk_transport.py: build_proposals / apply_proposals self-migration
# ---------------------------------------------------------------------------

class _FakeTransportClient:
    def __init__(self, transports=None):
        self._transports = transports or []
        self.update_calls = []

    def get_transports(self, supplier_id):
        return {"transport": self._transports}

    def get_transport(self, supplier_id, transport_id):
        return next((t for t in self._transports if t.get("id") == transport_id), {"error": 404})

    def update_transport(self, supplier_id, payload):
        self.update_calls.append((supplier_id, payload))
        return {"id": payload.get("id"), "code": 200}


def _cbt_sample_record(id_="T1", name="Airport - Hotel X", days=30, refund_pct=100.0,
                       description="<p>Private transfer from the airport.</p>",
                       voucher_remarks=""):
    return {
        "id": id_, "name": name,
        "segments": [{"departureLocationCode": "SSH", "arrivalLocationCode": "HTL-X"}],
        "cancellationRanges": [{"days": days, "percentage": refund_pct, "isBeforeStart": True}],
        "datasheets": {"EN": {"name": name, "description": description, "voucherRemarks": voucher_remarks}},
        "baseAdultPrice": 42.0, "currency": "EUR",
    }


def test_build_proposals_migrates_a_not_yet_repaired_record_out_of_description():
    # A Transport that STILL has the cancellation sentence in description (never went through
    # the one-off repair, and this is its first bulk-policy run since the 2026-09-11 change)
    # must be picked up and migrated, not left alone just because the structured tiers match.
    stale_description = ("<p>Private transfer from the airport.</p>"
                         "<p>Cancellation Policy:\nFree cancellation if cancelled at least "
                         "30 days before arrival.</p>")
    client = _FakeTransportClient(transports=[_cbt_sample_record(
        days=30, refund_pct=100.0, description=stale_description, voucher_remarks="")])
    rows, _ = cbt.load_supplier_transports_for_cancellation(client, "SUP-X")
    proposals = cbt.build_proposals(rows, [{"days": 30, "fee_percentage": 0.0}])
    assert proposals[0]["unchanged"] is False
    assert proposals[0]["existing_paragraph_found"] is True
    assert "Cancellation Policy" not in proposals[0]["new_description_html"]
    assert proposals[0]["new_voucher_remarks"]


def test_apply_proposals_removes_leftover_description_paragraph_on_every_language():
    # days=14/refund_pct=100.0 is LESS strict than the new 30-day policy being applied, so this
    # genuinely goes through apply_proposals' live-write path rather than being skipped by the
    # "never overwrite a stricter existing policy" rule.
    stale_description = "<p>Info</p><p>Free cancellation up to 14 days before arrival.</p>"
    record = _cbt_sample_record(days=14, refund_pct=100.0, description=stale_description, voucher_remarks="")
    client = _FakeTransportClient(transports=[record])
    rows, _ = cbt.load_supplier_transports_for_cancellation(client, "SUP-X")
    proposals = cbt.build_proposals(rows, [{"days": 30, "fee_percentage": 0.0}])
    results = cbt.apply_proposals(client, "SUP-X", proposals)
    assert results[0]["ok"] is True
    _, payload = client.update_calls[0]
    sheet = payload["datasheets"]["EN"]
    assert "cancellation" not in sheet["description"].lower()
    assert "Cancellation Policy" in sheet["voucherRemarks"]
