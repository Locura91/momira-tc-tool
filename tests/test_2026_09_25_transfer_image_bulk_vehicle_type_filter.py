"""Tests for transfer_image_bulk.py's Vehicle Type filter (2026-09-25).

CONFIRMED PRODUCT-OWNER REQUEST (verbatim): "we must enhance the selection: Car and Boat, Van
and Boat or Minivan or Boat as a transfer, we will have another transfer image. Could we also
include this selection in the app" - a third optional filter, combinable with supplier and
ServiceType, scoped by Transfer's `vehicleType` field (a separate field from `serviceType`).

VEHICLE_TYPES itself was confirmed from a screenshot of Travel Compositor's own Transfer edit
screen's "Vehicle Type" dropdown (product owner, 2026-09-25) - this test only checks the three
values the product owner explicitly named are present with the exact dropdown spelling, not the
full confirmed list (see transfer_image_bulk.py's own docstring for the rest).

Fake client/helper mirror test_2026_09_25_transfer_image_bulk_upload.py's own, extended with a
vehicle_type kwarg on _transfer.
"""
import transfer_image_bulk as tib


class _FakeClient:
    def __init__(self, records_by_supplier, update_error_for=None, get_error_for_supplier=None):
        self._records_by_supplier = records_by_supplier
        self._update_error_for = update_error_for or {}
        self._get_error_for_supplier = get_error_for_supplier or {}
        self.update_calls = []

    def get_transfers(self, supplier_id):
        if supplier_id in self._get_error_for_supplier:
            return {"error": 500, "message": self._get_error_for_supplier[supplier_id]}
        return {"transfer": self._records_by_supplier.get(supplier_id, [])}

    def update_transfer(self, supplier_id, payload):
        self.update_calls.append((supplier_id, payload))
        if payload.get("id") in self._update_error_for:
            return {"error": 400, "message": self._update_error_for[payload["id"]]}
        return {"id": payload.get("id"), "code": 200}


def _transfer(id_, service_type="PRIVATE", vehicle_type="CAR", images=None,
              dep="Airport", arr="Hotel Sunrise"):
    return {
        "id": id_, "serviceType": service_type, "vehicleType": vehicle_type, "images": images or [],
        "departure": {"name": dep}, "arrival": {"name": arr},
    }


def test_the_three_named_boat_combos_are_in_vehicle_types_with_exact_dropdown_spelling():
    assert "CAR_AND_BOAT" in tib.VEHICLE_TYPES
    assert "VAN_AND_BOAT" in tib.VEHICLE_TYPES
    assert "MINIVAN_OR_BOAT" in tib.VEHICLE_TYPES
    assert tib.VEHICLE_TYPE_LABELS["CAR_AND_BOAT"] == "Car and Boat"
    assert tib.VEHICLE_TYPE_LABELS["VAN_AND_BOAT"] == "Van and Boat"
    assert tib.VEHICLE_TYPE_LABELS["MINIVAN_OR_BOAT"] == "Minivan or Boat"


def test_every_vehicle_type_has_a_label():
    for v in tib.VEHICLE_TYPES:
        assert v in tib.VEHICLE_TYPE_LABELS


def test_plan_filters_by_vehicle_type():
    client = _FakeClient({"SUP-1": [
        _transfer("TRANSFER-1", vehicle_type="CAR"),
        _transfer("TRANSFER-2", vehicle_type="CAR_AND_BOAT"),
        _transfer("TRANSFER-3", vehicle_type="VAN_AND_BOAT"),
    ]})
    planned = tib.plan(client, [{"id": "SUP-1", "name": "Momira_Test"}], None,
                       "https://new.example.com/b.jpg", vehicle_type="CAR_AND_BOAT")
    ids = {i["id"] for i in planned["items"]}
    assert ids == {"TRANSFER-2"}


def test_plan_vehicle_type_filter_is_case_insensitive():
    client = _FakeClient({"SUP-1": [_transfer("TRANSFER-1", vehicle_type="car_and_boat")]})
    planned = tib.plan(client, [{"id": "SUP-1", "name": "Momira_Test"}], None,
                       "https://new.example.com/b.jpg", vehicle_type="CAR_AND_BOAT")
    assert len(planned["items"]) == 1


def test_plan_no_vehicle_type_filter_matches_every_vehicle_type():
    client = _FakeClient({"SUP-1": [
        _transfer("TRANSFER-1", vehicle_type="CAR"),
        _transfer("TRANSFER-2", vehicle_type="MINIVAN_OR_BOAT"),
    ]})
    planned = tib.plan(client, [{"id": "SUP-1", "name": "Momira_Test"}], None,
                       "https://new.example.com/b.jpg")
    assert len(planned["items"]) == 2


def test_plan_service_type_and_vehicle_type_combine_as_and_not_or():
    client = _FakeClient({"SUP-1": [
        _transfer("TRANSFER-1", service_type="PRIVATE", vehicle_type="CAR_AND_BOAT"),
        _transfer("TRANSFER-2", service_type="SHUTTLE", vehicle_type="CAR_AND_BOAT"),
        _transfer("TRANSFER-3", service_type="PRIVATE", vehicle_type="VAN"),
    ]})
    planned = tib.plan(client, [{"id": "SUP-1", "name": "Momira_Test"}], "PRIVATE",
                       "https://new.example.com/b.jpg", vehicle_type="CAR_AND_BOAT")
    ids = {i["id"] for i in planned["items"]}
    assert ids == {"TRANSFER-1"}


def test_plan_items_carry_vehicle_type_for_display():
    client = _FakeClient({"SUP-1": [_transfer("TRANSFER-1", vehicle_type="VAN_AND_BOAT")]})
    planned = tib.plan(client, [{"id": "SUP-1", "name": "Momira_Test"}], None,
                       "https://new.example.com/b.jpg")
    assert planned["items"][0]["vehicle_type"] == "VAN_AND_BOAT"


def test_plan_result_records_the_vehicle_type_filter_used():
    client = _FakeClient({"SUP-1": [_transfer("TRANSFER-1", vehicle_type="CAR_AND_BOAT")]})
    planned = tib.plan(client, [{"id": "SUP-1", "name": "Momira_Test"}], None,
                       "https://new.example.com/b.jpg", vehicle_type="CAR_AND_BOAT")
    assert planned["vehicle_type"] == "CAR_AND_BOAT"


def test_apply_still_sends_the_whole_record_including_vehicle_type_unchanged():
    client = _FakeClient({"SUP-1": [_transfer("TRANSFER-1", vehicle_type="CAR_AND_BOAT", images=[])]})
    planned = tib.plan(client, [{"id": "SUP-1", "name": "Momira_Test"}], None,
                       "https://new.example.com/b.jpg", vehicle_type="CAR_AND_BOAT")
    tib.apply(client, planned)
    sent = client.update_calls[0][1]
    assert sent["vehicleType"] == "CAR_AND_BOAT"
    assert sent["images"] == ["https://new.example.com/b.jpg"]


# Backward compatibility: every pre-existing positional call site (plan(client, suppliers,
# service_type, new_image_url)) must keep working unchanged - vehicle_type is a new keyword-only
# addition, not an inserted positional argument.
def test_plan_still_works_without_vehicle_type_argument_at_all():
    client = _FakeClient({"SUP-1": [_transfer("TRANSFER-1")]})
    planned = tib.plan(client, [{"id": "SUP-1", "name": "Momira_Test"}], None,
                       "https://new.example.com/b.jpg")
    assert planned["error"] is None
