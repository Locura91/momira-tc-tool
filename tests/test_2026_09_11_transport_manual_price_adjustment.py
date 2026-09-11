"""Tests for the 2026-09-11 manual %/absolute Transport price adjustment (product owner, right
after confirming the earlier bulk write into TRANSPORT-423137/423138/423142/423134 had actually
persisted correctly - see claude/transport-supplement-write-not-persisting-debug-capture-2026-09-11.md):

"I will add the second modality by hand. So short, We must then update in the future in bulk (or
even) only the pricevehicle or the priceperpax. The modalities are not needed then, only if the
price would be per pax. So we can simply the app and we touch in contract transport only those
fields. Ignore the modalities. that would make the app simplier. Instead, add, just for transport,
a update field so the human can manually add a absolute or percentage number - this percantage or
absolute number shall then be possible to bulk update to the price. Example price vehicle in
travel c is 100, and human adds manually 10% the app must calculate the new price by currentprice
* percentage, in this example 100*1,1=110 Euro. Or if number adds in absolute price and it says
12, in the example the app must calculate 100+12=112 Euro."

Follow-up answers (AskUserQuestion, same day): add as a SECOND mode alongside the existing
document-reading flow (not a replacement); the SAME %/absolute number is applied to every route
in one batch, each against its OWN current price; child/infant base prices keep scaling
proportionally with the adult/vehicle price, same as the existing "vehicle round" behaviour.
"""
import price_refresh


def _route(name="Cairo - Luxor", price_per_pax=True, base_adult=100.0, base_children=80.0,
          base_infant=0.0, vehicle_price=0.0, kind=price_refresh.KIND_TRANSPORT):
    raw = {"id": f"{name}-id", "baseAdultPrice": base_adult, "baseChildrenPrice": base_children,
          "baseInfantPrice": base_infant, "vehiclePrice": vehicle_price}
    return {"id": f"{name}-id", "name": name, "kind": kind, "price_per_pax": price_per_pax,
           "options": [], "raw": raw}


# ----------------------------------------------------------------------
# build_manual_adjustment_proposals - the arithmetic
# ----------------------------------------------------------------------

def test_percentage_matches_the_product_owners_own_worked_example():
    # "human adds manually 10% the app must calculate the new price by currentprice *
    # percentage, in this example 100*1,1=110 Euro."
    route = _route(base_adult=100.0)
    proposals = price_refresh.build_manual_adjustment_proposals([route], "percentage", 10)
    assert proposals[0]["old"] == 100.0
    assert proposals[0]["new"] == 110.0
    assert proposals[0]["accepted"] is True
    assert proposals[0]["blocked"] is None


def test_absolute_matches_the_product_owners_own_worked_example():
    # "if number adds in absolute price and it says 12... 100+12=112 Euro."
    route = _route(base_adult=100.0)
    proposals = price_refresh.build_manual_adjustment_proposals([route], "absolute", 12)
    assert proposals[0]["old"] == 100.0
    assert proposals[0]["new"] == 112.0
    assert proposals[0]["accepted"] is True


def test_negative_percentage_is_a_price_cut_not_rejected():
    route = _route(base_adult=100.0)
    proposals = price_refresh.build_manual_adjustment_proposals([route], "percentage", -10)
    assert proposals[0]["new"] == 90.0
    assert proposals[0]["accepted"] is True


def test_negative_absolute_is_a_price_cut_not_rejected():
    route = _route(base_adult=100.0)
    proposals = price_refresh.build_manual_adjustment_proposals([route], "absolute", -20)
    assert proposals[0]["new"] == 80.0
    assert proposals[0]["accepted"] is True


def test_a_result_at_or_below_zero_is_a_hard_block():
    route = _route(base_adult=10.0)
    proposals = price_refresh.build_manual_adjustment_proposals([route], "absolute", -15)
    assert proposals[0]["new"] == -5.0
    assert proposals[0]["accepted"] is False
    assert "zero or negative" in proposals[0]["blocked"]


def test_per_vehicle_transport_uses_vehicleprice_not_baseadultprice():
    route = _route(price_per_pax=False, vehicle_price=200.0, base_adult=0.0)
    proposals = price_refresh.build_manual_adjustment_proposals([route], "percentage", 5)
    assert proposals[0]["old"] == 200.0
    assert proposals[0]["new"] == 210.0


def test_one_number_applies_to_every_route_in_the_batch_against_its_own_price():
    # CONFIRMED REAL SCOPE (product owner follow-up): "One number for the whole batch" - each
    # route uses its OWN current price, not a shared one.
    routes = [_route(name="A", base_adult=100.0), _route(name="B", base_adult=50.0)]
    proposals = price_refresh.build_manual_adjustment_proposals(routes, "percentage", 10)
    by_name = {p["name"]: p for p in proposals}
    assert by_name["A"]["new"] == 110.0
    assert by_name["B"]["new"] == 55.0


def test_transfer_is_refused_defensively_even_though_the_ui_never_offers_this_mode_for_it():
    route = _route(kind=price_refresh.KIND_TRANSFER, base_adult=100.0)
    proposals = price_refresh.build_manual_adjustment_proposals([route], "percentage", 10)
    assert proposals[0]["blocked"] == "Manual adjustment is only for Transport, not Transfer."
    assert proposals[0]["accepted"] is False


def test_a_zero_value_leaves_the_route_unaccepted_not_a_crash():
    route = _route(base_adult=100.0)
    proposals = price_refresh.build_manual_adjustment_proposals([route], "percentage", 0)
    assert proposals[0]["new"] == 100.0
    assert proposals[0]["accepted"] is False  # nothing actually moves


# ----------------------------------------------------------------------
# rebuild_manual_adjustment - the payload, and that it touches ONLY the base field
# ----------------------------------------------------------------------

def test_rebuild_writes_only_the_base_field_for_a_per_vehicle_transport_no_child_infant_scaling():
    route = _route(price_per_pax=False, vehicle_price=100.0, base_adult=0.0, base_children=0.0)
    payload = price_refresh.rebuild_manual_adjustment(route, 110.0)
    assert payload["vehiclePrice"] == 110.0
    # Per-vehicle transports have no separate child/infant base to scale - untouched at 0.
    assert payload["baseChildrenPrice"] == 0.0


def test_rebuild_scales_child_and_infant_proportionally_for_a_per_pax_transport():
    # CONFIRMED (AskUserQuestion, 2026-09-11): "Yes, keep scaling child/infant proportionally...
    # e.g. a +10% adult adjustment also moves child/infant base prices by +10%."
    route = _route(price_per_pax=True, base_adult=100.0, base_children=80.0, base_infant=40.0)
    payload = price_refresh.rebuild_manual_adjustment(route, 110.0)  # +10%
    assert payload["baseAdultPrice"] == 110.0
    assert payload["baseChildrenPrice"] == 88.0  # 80 * 1.10
    assert payload["baseInfantPrice"] == 44.0    # 40 * 1.10


def test_rebuild_never_touches_options_or_prices_arrays_at_all():
    route = _route(base_adult=100.0)
    route["options"] = [{"code": "SOMECODE", "unit_price": 100.0, "raw": {"prices": [
        {"name": "should never move", "adultPriceSupplement": 999.0}]}}]
    payload = price_refresh.rebuild_manual_adjustment(route, 110.0)
    # rebuild_manual_adjustment returns ONLY the parent payload - no options key at all, unlike
    # rebuild_prices' {"transport", "options", "write_kind"} shape.
    assert "options" not in payload
    assert "prices" not in payload  # the parent record itself never carries an option's prices


# ----------------------------------------------------------------------
# apply_manual_adjustments - end to end against a fake client, including debug capture
# ----------------------------------------------------------------------

class _FakeClient:
    def __init__(self, responses=None, raise_on=None):
        self.calls = []
        self._responses = responses or {}
        self._raise_on = raise_on or set()

    def update_transport(self, supplier_id, payload):
        self.calls.append(("update_transport", supplier_id, payload))
        if payload.get("id") in self._raise_on:
            raise RuntimeError("boom")
        return self._responses.get(payload.get("id"), {"id": payload.get("id"), "ok": True})


def test_apply_only_calls_update_transport_never_update_transport_option():
    route = _route(name="A", base_adult=100.0)
    proposals = price_refresh.build_manual_adjustment_proposals([route], "percentage", 10)
    client = _FakeClient()
    out = price_refresh.apply_manual_adjustments(client, "999", proposals)
    assert len(out["updated"]) == 1
    assert out["updated"][0]["old"] == 100.0
    assert out["updated"][0]["new"] == 110.0
    assert [c[0] for c in client.calls] == ["update_transport"]  # never the option updater


def test_apply_captures_raw_request_and_response_for_debugging():
    route = _route(name="A", base_adult=100.0)
    proposals = price_refresh.build_manual_adjustment_proposals([route], "percentage", 10)
    client = _FakeClient(responses={"A-id": {"id": "A-id", "baseAdultPrice": 110.0}})
    out = price_refresh.apply_manual_adjustments(client, "999", proposals)
    debug = out["updated"][0]["debug"]
    assert debug["transport_request"]["baseAdultPrice"] == 110.0
    assert debug["transport_response"] == {"id": "A-id", "baseAdultPrice": 110.0}


def test_apply_reports_an_api_error_response_as_failed_with_debug():
    route = _route(name="A", base_adult=100.0)
    proposals = price_refresh.build_manual_adjustment_proposals([route], "percentage", 10)
    client = _FakeClient(responses={"A-id": {"error": 422, "message": "nope"}})
    out = price_refresh.apply_manual_adjustments(client, "999", proposals)
    assert out["updated"] == []
    assert len(out["failed"]) == 1
    assert "nope" in out["failed"][0]["detail"]
    assert out["failed"][0]["debug"]["transport_response"] == {"error": 422, "message": "nope"}


def test_apply_reports_an_exception_as_failed_with_debug():
    route = _route(name="A", base_adult=100.0)
    proposals = price_refresh.build_manual_adjustment_proposals([route], "percentage", 10)
    client = _FakeClient(raise_on={"A-id"})
    out = price_refresh.apply_manual_adjustments(client, "999", proposals)
    assert out["updated"] == []
    assert len(out["failed"]) == 1
    assert out["failed"][0]["debug"]["transport_request"]["id"] == "A-id"


def test_apply_skips_blocked_proposals_entirely():
    routes = [_route(name="A", base_adult=100.0), _route(name="B", base_adult=10.0)]
    proposals = price_refresh.build_manual_adjustment_proposals(routes, "absolute", -50)
    # A: 100 - 50 = 50 (fine). B: 10 - 50 = -40 (blocked).
    client = _FakeClient()
    out = price_refresh.apply_manual_adjustments(client, "999", proposals)
    assert len(out["updated"]) == 1
    assert out["updated"][0]["name"] == "A"
    assert out["skipped"] == 1
