"""Tests for transfer_image_bulk.py - the mass Transfer-image-replace tool (2026-09-25).

CONFIRMED PRODUCT-OWNER REQUEST (verbatim): "i need to create a mass image upload for
Transfers. The goal is that human can select the supplier if he wants or the human selects
ServiceType by Transfer(Private; Shuttle or Shared) and the existing image will be removed and
the new image will be added ... either per supplier or per transfertype or a mixture."

Fake client mirrors conftest.py's FakeTravelCompositorAPI philosophy (hand-built, not Mock()),
same shape as tests/test_2026_09_10_bulk_cancellation_policy_all_types.py's _FakeClient for
get_transfers/update_transfer, extended to serve different records per supplier_id (needed for
the cross-supplier ServiceType-only scope).
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


def _transfer(id_, service_type="PRIVATE", images=None, dep="Airport", arr="Hotel Sunrise"):
    return {
        "id": id_, "serviceType": service_type, "images": images or [],
        "departure": {"name": dep}, "arrival": {"name": arr},
    }


# ----------------------------------------------------------------------
# plan()
# ----------------------------------------------------------------------

def test_plan_replaces_existing_image_marks_will_change():
    client = _FakeClient({"SUP-1": [_transfer("TRANSFER-1", images=["https://old.example.com/a.jpg"])]})
    planned = tib.plan(client, [{"id": "SUP-1", "name": "Momira_Test"}], None,
                       "https://new.example.com/b.jpg")
    assert planned["error"] is None
    assert planned["will_change"] == 1
    assert planned["unchanged"] == 0
    item = planned["items"][0]
    assert item["current_images"] == ["https://old.example.com/a.jpg"]
    assert item["new_images"] == ["https://new.example.com/b.jpg"]
    assert item["record"]["images"] == ["https://new.example.com/b.jpg"]
    assert item["status"] == "will_change"


def test_plan_no_existing_image_still_will_change():
    client = _FakeClient({"SUP-1": [_transfer("TRANSFER-1", images=[])]})
    planned = tib.plan(client, [{"id": "SUP-1", "name": "Momira_Test"}], None,
                       "https://new.example.com/b.jpg")
    assert planned["items"][0]["status"] == "will_change"
    assert planned["items"][0]["current_images"] == []


def test_plan_already_matching_image_is_unchanged():
    client = _FakeClient({"SUP-1": [_transfer("TRANSFER-1", images=["https://new.example.com/b.jpg"])]})
    planned = tib.plan(client, [{"id": "SUP-1", "name": "Momira_Test"}], None,
                       "https://new.example.com/b.jpg")
    assert planned["will_change"] == 0
    assert planned["unchanged"] == 1
    assert planned["items"][0]["status"] == "unchanged"


def test_plan_filters_by_service_type():
    client = _FakeClient({"SUP-1": [
        _transfer("TRANSFER-1", service_type="PRIVATE"),
        _transfer("TRANSFER-2", service_type="SHUTTLE"),
        _transfer("TRANSFER-3", service_type="SHARED"),
    ]})
    planned = tib.plan(client, [{"id": "SUP-1", "name": "Momira_Test"}], "SHUTTLE",
                       "https://new.example.com/b.jpg")
    ids = {i["id"] for i in planned["items"]}
    assert ids == {"TRANSFER-2"}


def test_plan_service_type_filter_is_case_insensitive():
    client = _FakeClient({"SUP-1": [_transfer("TRANSFER-1", service_type="private")]})
    planned = tib.plan(client, [{"id": "SUP-1", "name": "Momira_Test"}], "PRIVATE",
                       "https://new.example.com/b.jpg")
    assert len(planned["items"]) == 1


def test_plan_no_service_type_filter_matches_every_type():
    client = _FakeClient({"SUP-1": [
        _transfer("TRANSFER-1", service_type="PRIVATE"),
        _transfer("TRANSFER-2", service_type="SHUTTLE"),
    ]})
    planned = tib.plan(client, [{"id": "SUP-1", "name": "Momira_Test"}], None,
                       "https://new.example.com/b.jpg")
    assert len(planned["items"]) == 2


def test_plan_scans_multiple_suppliers_for_cross_supplier_scope():
    client = _FakeClient({
        "SUP-1": [_transfer("TRANSFER-1", service_type="SHUTTLE")],
        "SUP-2": [_transfer("TRANSFER-2", service_type="SHUTTLE"),
                 _transfer("TRANSFER-3", service_type="PRIVATE")],
    })
    planned = tib.plan(client,
                       [{"id": "SUP-1", "name": "Momira_A"}, {"id": "SUP-2", "name": "Momira_B"}],
                       "SHUTTLE", "https://new.example.com/b.jpg")
    ids = {i["id"] for i in planned["items"]}
    assert ids == {"TRANSFER-1", "TRANSFER-2"}
    suppliers_touched = {i["supplier_id"] for i in planned["items"]}
    assert suppliers_touched == {"SUP-1", "SUP-2"}


def test_plan_no_image_url_errors_without_scanning():
    client = _FakeClient({"SUP-1": [_transfer("TRANSFER-1")]})
    planned = tib.plan(client, [{"id": "SUP-1", "name": "Momira_Test"}], None, "")
    assert planned["error"] == "No image to apply."
    assert planned["items"] == []


def test_plan_no_suppliers_errors():
    client = _FakeClient({})
    planned = tib.plan(client, [], None, "https://new.example.com/b.jpg")
    assert planned["error"] == "No supplier(s) to scan."


def test_plan_partial_supplier_failure_still_returns_other_suppliers_items():
    client = _FakeClient(
        {"SUP-2": [_transfer("TRANSFER-2")]},
        get_error_for_supplier={"SUP-1": "boom"},
    )
    planned = tib.plan(client,
                       [{"id": "SUP-1", "name": "Momira_A"}, {"id": "SUP-2", "name": "Momira_B"}],
                       None, "https://new.example.com/b.jpg")
    assert planned["error"] and "boom" in planned["error"]
    assert len(planned["items"]) == 1
    assert planned["items"][0]["id"] == "TRANSFER-2"


def test_plan_label_uses_route_names():
    client = _FakeClient({"SUP-1": [_transfer("TRANSFER-1", dep="Hurghada Airport", arr="Steigenberger Hotel")]})
    planned = tib.plan(client, [{"id": "SUP-1", "name": "Momira_EG"}], None,
                       "https://new.example.com/b.jpg")
    assert planned["items"][0]["name"] == "Hurghada Airport → Steigenberger Hotel · Momira_EG"


def test_plan_does_not_mutate_the_original_record():
    original_images = ["https://old.example.com/a.jpg"]
    record = _transfer("TRANSFER-1", images=original_images)
    client = _FakeClient({"SUP-1": [record]})
    tib.plan(client, [{"id": "SUP-1", "name": "Momira_Test"}], None, "https://new.example.com/b.jpg")
    assert record["images"] == original_images


# ----------------------------------------------------------------------
# apply()
# ----------------------------------------------------------------------

def _planned_from(client, supplier_id="SUP-1", service_type=None, url="https://new.example.com/b.jpg"):
    return tib.plan(client, [{"id": supplier_id, "name": "Momira_Test"}], service_type, url)


def test_apply_only_sends_will_change_items():
    client = _FakeClient({"SUP-1": [
        _transfer("TRANSFER-1", images=["https://old.example.com/a.jpg"]),
        _transfer("TRANSFER-2", images=["https://new.example.com/b.jpg"]),  # already matches
    ]})
    planned = _planned_from(client)
    results = tib.apply(client, planned)
    assert len(client.update_calls) == 1
    assert client.update_calls[0][1]["id"] == "TRANSFER-1"
    assert results["skipped"] == 0  # unchanged items were never "pending", not "skipped"
    assert [u["id"] for u in results["updated"]] == ["TRANSFER-1"]


def test_apply_respects_selected_ids_deselecting_leaves_it_skipped():
    client = _FakeClient({"SUP-1": [
        _transfer("TRANSFER-1", images=[]),
        _transfer("TRANSFER-2", images=[]),
    ]})
    planned = _planned_from(client)
    results = tib.apply(client, planned, selected_ids={"TRANSFER-1"})
    assert len(client.update_calls) == 1
    assert client.update_calls[0][1]["id"] == "TRANSFER-1"
    assert results["skipped"] == 1


def test_apply_sends_the_whole_record_not_a_partial_payload():
    client = _FakeClient({"SUP-1": [_transfer("TRANSFER-1", service_type="SHUTTLE", images=[])]})
    planned = _planned_from(client)
    tib.apply(client, planned)
    sent = client.update_calls[0][1]
    assert sent["serviceType"] == "SHUTTLE"
    assert sent["departure"] == {"name": "Airport"}
    assert sent["images"] == ["https://new.example.com/b.jpg"]


def test_apply_collects_failures_without_aborting_the_rest():
    client = _FakeClient(
        {"SUP-1": [_transfer("TRANSFER-1", images=[]), _transfer("TRANSFER-2", images=[])]},
        update_error_for={"TRANSFER-1": "rejected"},
    )
    planned = _planned_from(client)
    results = tib.apply(client, planned)
    assert len(results["updated"]) == 1
    assert results["updated"][0]["id"] == "TRANSFER-2"
    assert len(results["failed"]) == 1
    assert results["failed"][0]["id"] == "TRANSFER-1"
    assert results["failed"][0]["detail"] == "rejected"


def test_apply_routes_each_item_to_its_own_supplier_across_a_cross_supplier_plan():
    client = _FakeClient({
        "SUP-1": [_transfer("TRANSFER-1", service_type="SHUTTLE", images=[])],
        "SUP-2": [_transfer("TRANSFER-2", service_type="SHUTTLE", images=[])],
    })
    planned = tib.plan(client,
                       [{"id": "SUP-1", "name": "Momira_A"}, {"id": "SUP-2", "name": "Momira_B"}],
                       "SHUTTLE", "https://new.example.com/b.jpg")
    tib.apply(client, planned)
    called_suppliers = {c[0] for c in client.update_calls}
    assert called_suppliers == {"SUP-1", "SUP-2"}


def test_apply_progress_callback_invoked_per_pending_item():
    client = _FakeClient({"SUP-1": [_transfer("TRANSFER-1", images=[]), _transfer("TRANSFER-2", images=[])]})
    planned = _planned_from(client)
    calls = []
    tib.apply(client, planned, progress=lambda done, total, label: calls.append((done, total, label)))
    assert calls == [(1, 2, "Airport → Hotel Sunrise · Momira_Test"),
                     (2, 2, "Airport → Hotel Sunrise · Momira_Test")]
