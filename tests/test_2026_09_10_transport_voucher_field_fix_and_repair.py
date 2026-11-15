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





# ---------------------------------------------------------------------------
# bulk_notes.TARGETS fix
# ---------------------------------------------------------------------------



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












# ---------------------------------------------------------------------------
# Repair tool: apply() + dispatch wiring
# ---------------------------------------------------------------------------







# ---------------------------------------------------------------------------
# app.py wiring - source-shape check only (app.py can't be imported in a test process)
# ---------------------------------------------------------------------------
import os

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    """Returns app.py's source concatenated with every module under flows/ - Phase 1
    (2026-09-15) started splitting render_*_flow functions out of app.py into flows/*.py,
    verbatim/zero-behaviour-change, so source-text assertions that used to find their
    target inside app.py alone now need to see the split-out modules too. Reading app.py
    first means any offset/index a test computes for genuinely-still-in-app.py content is
    unaffected; content that moved is simply found further along in the string.
    """
    with open(_APP_PY, "r", encoding="utf-8") as f:
        src = f.read()
    app_helpers_path = os.path.join(os.path.dirname(_APP_PY), "app_helpers.py")
    if os.path.isfile(app_helpers_path):
        with open(app_helpers_path, "r", encoding="utf-8") as f:
            src += chr(10) + f.read()
    flows_dir = os.path.join(os.path.dirname(_APP_PY), "flows")
    if os.path.isdir(flows_dir):
        for _name in sorted(os.listdir(flows_dir)):
            if _name.endswith(".py") and _name != "__init__.py":
                with open(os.path.join(flows_dir, _name), "r", encoding="utf-8") as f:
                    src += chr(10) + f.read()
    return src





def test_app_py_price_code_caption_no_longer_special_cases_transport():
    src = _read_app_py()
    branch = src.split("elif add_price_code:")[1].split("\n    else:")[0]
    assert "Transport has no separate Voucher remarks field" not in branch
