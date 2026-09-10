"""Tests for the Transport voucherRemarks field fix + repair tool (product owner, 2026-09-10):

    "transfer update with supplement looks good and it worked. Code upload in Bulk for
    Transport worked, but it was uploaded in the Description field - but the goal was to
    upload it within the Voucher remarks of the base information: Now I have full 168
    Transports with the code in the wrong field."

Root cause: bulk_notes.TARGETS["Transport"]["Voucher remarks"] was aliased to "description",
on the (wrong) belief that Transport had no separate voucherRemarks field in Travel Compositor
- confirmed wrong via a real screenshot of Travel Compositor's own Transport edit screen, which
shows a genuine, separate Voucher remarks input. Fixed in three places:

  1. schemas.py: TransportDataSheetVO now models voucherRemarks (Optional, no-op default).
  2. builder.py: the single-service create/update flow now composes the price-validity code
     onto voucherRemarks instead of description (description keeps its existing
     cancellation/conditions text, unrelated to this fix).
  3. bulk_notes.py: TARGETS["Transport"]["Voucher remarks"] now points at "voucherRemarks".

Plus a one-off repair tool (_plan_transport_voucher_code_repair /
_apply_transport_voucher_code_repair) to fix the 168 Transports the bug already wrote to the
wrong field before it was caught.
"""
import copy

import bulk_notes
import schemas


# ---------------------------------------------------------------------------
# Schema fix
# ---------------------------------------------------------------------------

def test_transport_datasheet_vo_now_has_a_voucher_remarks_field():
    sheet = schemas.TransportDataSheetVO(name="Test")
    assert hasattr(sheet, "voucherRemarks")
    assert sheet.voucherRemarks is None  # no-op default - never writes an unwanted null


def test_transport_datasheet_vo_accepts_a_voucher_remarks_value():
    sheet = schemas.TransportDataSheetVO(name="Test", voucherRemarks="(20270430)")
    assert sheet.voucherRemarks == "(20270430)"


# ---------------------------------------------------------------------------
# bulk_notes.TARGETS fix
# ---------------------------------------------------------------------------

def test_transport_voucher_remarks_target_points_at_the_real_field():
    assert bulk_notes.TARGETS["Transport"]["Voucher remarks"] == "voucherRemarks"


def test_transport_cancellation_update_target_is_unaffected_still_description():
    # Deliberately unrelated rule (builder.py's own comment) - must not have been swept up by
    # this fix.
    assert bulk_notes.TARGETS["Transport"]["Cancellation update"] == "description"


def test_transport_description_bottom_target_is_unaffected():
    assert bulk_notes.TARGETS["Transport"]["Description (bottom)"] == "description"


# ---------------------------------------------------------------------------
# Repair tool: fake client
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


def _transport(t_id="TRANSPORT-1", name="Luxor - Hurghada", description="Private car with driver.",
               voucher_remarks=None):
    sheet = {"name": name, "description": description}
    if voucher_remarks is not None:
        sheet["voucherRemarks"] = voucher_remarks
    return {"id": t_id, "name": name, "datasheets": {"EN": sheet}}


def test_repair_moves_a_code_from_description_to_voucher_remarks():
    t = _transport(description="Private car with driver.\n(20270430)")
    client = _FakeClient(transports=[t])
    plan = bulk_notes._plan_transport_voucher_code_repair(client, "SUP1")
    assert plan["will_change"] == 1
    assert plan["unchanged"] == 0
    item = plan["items"][0]
    new_sheet = item["record"]["datasheets"]["EN"]
    assert "(20270430)" not in new_sheet["description"]
    assert new_sheet["description"] == "Private car with driver."
    assert new_sheet["voucherRemarks"] == "(20270430)"


def test_repair_preserves_existing_voucher_remarks_text_when_moving_the_code():
    t = _transport(description="Private car with driver.\n(20270430)",
                   voucher_remarks="Please tip the driver in cash.")
    client = _FakeClient(transports=[t])
    plan = bulk_notes._plan_transport_voucher_code_repair(client, "SUP1")
    new_sheet = plan["items"][0]["record"]["datasheets"]["EN"]
    assert "Please tip the driver in cash." in new_sheet["voucherRemarks"]
    assert "(20270430)" in new_sheet["voucherRemarks"]


def test_repair_skips_a_transport_with_no_code_in_description():
    t = _transport(description="Private car with driver.")  # no code at all
    client = _FakeClient(transports=[t])
    plan = bulk_notes._plan_transport_voucher_code_repair(client, "SUP1")
    assert plan["will_change"] == 0
    assert plan["unchanged"] == 1


def test_repair_is_idempotent_running_it_twice_finds_nothing_the_second_time():
    t = _transport(description="Private car with driver.\n(20270430)")
    client = _FakeClient(transports=[t])
    plan = bulk_notes._plan_transport_voucher_code_repair(client, "SUP1")
    fixed_record = plan["items"][0]["record"]
    client2 = _FakeClient(transports=[fixed_record])
    plan2 = bulk_notes._plan_transport_voucher_code_repair(client2, "SUP1")
    assert plan2["will_change"] == 0
    assert plan2["unchanged"] == 1


def test_repair_fills_in_missing_airline_code_via_normalize_for_put():
    # Same whole-record PUT the original 168-service airlineCode bug hit - must not regress.
    t = _transport(description="Private car with driver.\n(20270430)")
    assert "airlineCode" not in t
    client = _FakeClient(transports=[t])
    plan = bulk_notes._plan_transport_voucher_code_repair(client, "SUP1")
    assert plan["items"][0]["record"]["airlineCode"] == ""


# ---------------------------------------------------------------------------
# Repair tool: apply() + dispatch wiring
# ---------------------------------------------------------------------------

def test_apply_dispatches_the_repair_kind_via_plan_structured():
    t = _transport(description="Private car with driver.\n(20270430)")
    client = _FakeClient(transports=[t])
    plan = bulk_notes.plan_structured(
        client, "SUP1", "Transport", "transport_voucher_code_repair", {})
    result = bulk_notes.apply(client, "SUP1", plan)
    assert len(result["updated"]) == 1
    assert len(client.updated_transports) == 1
    written = client.updated_transports[0]["datasheets"]["EN"]
    assert written["voucherRemarks"] == "(20270430)"
    assert "(20270430)" not in written["description"]


def test_structured_targets_lists_the_repair_option_for_transport():
    assert "transport_voucher_code_repair" in bulk_notes.STRUCTURED_TARGETS["Transport"].values()
    assert bulk_notes.available_structured_targets("Transport") == \
        list(bulk_notes.STRUCTURED_TARGETS["Transport"].keys())


def test_original_record_never_mutated_by_the_repair_plan():
    t = _transport(description="Private car with driver.\n(20270430)")
    original_copy = copy.deepcopy(t)
    client = _FakeClient(transports=[t])
    bulk_notes._plan_transport_voucher_code_repair(client, "SUP1")
    assert t == original_copy


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
    assert 'structured_kind == "transport_voucher_code_repair"' in src


def test_app_py_price_code_caption_no_longer_special_cases_transport():
    src = _read_app_py()
    branch = src.split("elif add_price_code:")[1].split("\n    else:")[0]
    assert "Transport has no separate Voucher remarks field" not in branch
