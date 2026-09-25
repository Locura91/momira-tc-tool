"""Regression tests for flows/transfer_image_bulk.py's Vehicle Type filter UI (2026-09-25).

CONFIRMED PRODUCT-OWNER REQUEST (verbatim): "we must enhance the selection: Car and Boat, Van
and Boat or Minivan or Boat as a transfer, we will have another transfer image. Could we also
include this selection in the app."

flows/*.py files can't be imported outside the real Streamlit app - this reads the module's
source text instead, the same approach every other test on a flows/*.py file in this suite
already uses (see test_2026_09_25_transfer_image_bulk_verifies_public_url.py).
"""
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(_REPO_ROOT, *parts), "r", encoding="utf-8") as f:
        return f.read()


def test_vehicle_type_selectbox_is_offered_alongside_supplier_and_servicetype():
    src = _read("flows", "transfer_image_bulk.py")
    assert 'st.selectbox("Vehicle Type"' in src
    assert 'tib.VEHICLE_TYPES' in src
    assert 'tib.VEHICLE_TYPE_LABELS' in src


def test_the_any_of_three_gate_requires_at_least_one_filter_not_just_two():
    src = _read("flows", "transfer_image_bulk.py")
    gate_idx = src.index("if supplier_choice == _ANY_SUPPLIER and type_choice == _ANY_TYPE")
    gate_line = src[gate_idx:src.index(":", gate_idx)]
    # The gate must also check the vehicle choice - two filters both being "Any" is no longer
    # enough to block scanning if the third (vehicle type) was picked.
    assert "vehicle_choice == _ANY_VEHICLE" in gate_line


def test_vehicle_type_is_passed_into_plan():
    src = _read("flows", "transfer_image_bulk.py")
    plan_call = src[src.index("planned = tib.plan("):src.index("planned = tib.plan(") + 300]
    assert "vehicle_type=vehicle_type" in plan_call
