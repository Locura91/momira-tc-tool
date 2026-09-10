"""Tests for the Transport permanent price increase feature (product owner, 2026-09-10):

    "can we already say automatically: Update exsiting price by percentage? Example current
    price is 60 USD and we want to just add 10% to the existing price, all the prices for the
    transport must be automatically calculated and the new price must be added to travel
    compositor."

Clarified scope: Transport only. The human decides PER RUN whether to raise the base price,
the currently-active price supplement, or both ("Just one of each or both. Human must decide")
- so increase_base/increase_supplement are two independent booleans, never mutually exclusive.
New mode in the existing bulk supplement screen (STRUCTURED_TARGETS["Transport"]).

This is deliberately the OPPOSITE philosophy from transport_supplement (2026-09-09): that one
only ever APPENDS a new dated price entry and never touches existing ones; this one MUTATES the
base*Price fields and/or the currently-active dated entry IN PLACE, and never appends anything.
Both share _active_transport_price_entry for "what's currently in effect today".

The 2026-09-10 airlineCode fix (_normalize_for_put) must also apply here, since base-price
writes go through the same whole-record PUT (update_transport) that triggered the original bug.
"""
import bulk_notes


class _FakeClient:
    def __init__(self, transports=None, transport_options=None):
        self._transports = transports or []
        self._options = transport_options or {}  # (transport_id, code) -> option dict
        self.updated_transports = []
        self.updated_options = []

    def get_transports(self, supplier_id):
        return {"transport": self._transports}

    def get_transport(self, supplier_id, transport_id):
        return next(t for t in self._transports if t["id"] == transport_id)

    def get_transport_option(self, supplier_id, transport_id, code):
        return self._options[(transport_id, code)]

    def update_transport(self, supplier_id, payload):
        self.updated_transports.append(payload)
        return payload

    def update_transport_option(self, supplier_id, transport_id, payload):
        self.updated_options.append((transport_id, payload))
        return payload


def _transport(t_id="TRANSPORT-1", name="Airport Transfer CAI", codes=("OPT1",),
               base_adult=100.0, base_children=50.0, base_infant=0.0, airline_code=None):
    t = {
        "id": t_id, "name": name, "optionCodes": list(codes),
        "baseAdultPrice": base_adult, "baseChildrenPrice": base_children,
        "baseInfantPrice": base_infant, "currency": "EUR",
    }
    if airline_code is not None:
        t["airlineCode"] = airline_code
    return t


def _option(code="OPT1", min_p=1, max_p=4, prices=None):
    return {"code": code, "minPassengers": min_p, "maxPassengers": max_p,
            "prices": prices if prices is not None else []}


_ACTIVE_ENTRY = {"name": "Standing rate", "startDate": "2020-01-01", "endDate": "2049-12-31",
                 "adultPriceSupplement": 20.0, "childrenPriceSupplement": 10.0,
                 "infantPriceSupplement": 0.0}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_requires_at_least_one_of_base_or_supplement():
    client = _FakeClient()
    result = bulk_notes._plan_transport_price_increase(client, "SUP1", 10.0, False, False)
    assert result["error"]


def test_requires_a_positive_percent():
    client = _FakeClient(transports=[_transport()])
    result = bulk_notes._plan_transport_price_increase(client, "SUP1", 0.0, True, False)
    assert result["error"]


# ---------------------------------------------------------------------------
# Base price only
# ---------------------------------------------------------------------------

def test_base_only_increases_every_nonzero_base_price_field_by_the_percent():
    t = _transport(base_adult=100.0, base_children=50.0, base_infant=0.0)
    client = _FakeClient(transports=[t])
    result = bulk_notes._plan_transport_price_increase(client, "SUP1", 10.0, True, False)
    assert result["will_change"] == 1
    item = result["items"][0]
    assert item["record"]["baseAdultPrice"] == 110.0
    assert item["record"]["baseChildrenPrice"] == 55.0
    # Zero-value fields are skipped, not turned into a nonzero number from nothing.
    assert item["record"]["baseInfantPrice"] == 0.0


def test_base_only_does_not_touch_supplement_brackets():
    t = _transport()
    opt = _option(prices=[dict(_ACTIVE_ENTRY)])
    client = _FakeClient(transports=[t], transport_options={("TRANSPORT-1", "OPT1"): opt})
    result = bulk_notes._plan_transport_price_increase(client, "SUP1", 10.0, True, False)
    assert result["will_change"] == 1
    assert len(client.updated_options) == 0  # nothing planned/applied at option level


def test_base_with_no_price_set_is_unchanged_not_failed():
    t = _transport(base_adult=0.0, base_children=0.0, base_infant=0.0)
    client = _FakeClient(transports=[t])
    result = bulk_notes._plan_transport_price_increase(client, "SUP1", 10.0, True, False)
    assert result["will_change"] == 0
    assert result["unchanged"] == 1
    assert result["failed"] == 0


def test_base_price_write_fills_in_missing_airline_code_via_normalize_for_put():
    # The 2026-09-10 production bug (168 Transport services failed
    # "updateTransport.transport.airlineCode: must not be null") applies to ANY whole-record
    # Transport PUT, including this new base-price write - must not regress.
    t = _transport()
    assert "airlineCode" not in t
    client = _FakeClient(transports=[t])
    result = bulk_notes._plan_transport_price_increase(client, "SUP1", 10.0, True, False)
    assert result["items"][0]["record"]["airlineCode"] == ""


def test_base_price_write_never_overwrites_a_real_airline_code():
    t = _transport(airline_code="MS")
    client = _FakeClient(transports=[t])
    result = bulk_notes._plan_transport_price_increase(client, "SUP1", 10.0, True, False)
    assert result["items"][0]["record"]["airlineCode"] == "MS"


# ---------------------------------------------------------------------------
# Active supplement only
# ---------------------------------------------------------------------------

def test_supplement_only_increases_the_active_entrys_amounts_in_place():
    t = _transport()
    opt = _option(prices=[dict(_ACTIVE_ENTRY)])
    client = _FakeClient(transports=[t], transport_options={("TRANSPORT-1", "OPT1"): opt})
    result = bulk_notes._plan_transport_price_increase(client, "SUP1", 10.0, False, True)
    assert result["will_change"] == 1
    item = result["items"][0]
    assert item["write_kind"] == "transport_option"
    updated_entry = item["record"]["prices"][-1]
    assert updated_entry["adultPriceSupplement"] == 22.0
    assert updated_entry["childrenPriceSupplement"] == 11.0
    # In place - still exactly one entry, never a second appended one.
    assert len(item["record"]["prices"]) == 1


def test_supplement_only_does_not_touch_base_price_fields():
    t = _transport()
    opt = _option(prices=[dict(_ACTIVE_ENTRY)])
    client = _FakeClient(transports=[t], transport_options={("TRANSPORT-1", "OPT1"): opt})
    result = bulk_notes._plan_transport_price_increase(client, "SUP1", 10.0, False, True)
    assert len(client.updated_transports) == 0


def test_supplement_only_with_no_active_entry_is_unchanged():
    t = _transport()
    opt = _option(prices=[])  # nothing active today
    client = _FakeClient(transports=[t], transport_options={("TRANSPORT-1", "OPT1"): opt})
    result = bulk_notes._plan_transport_price_increase(client, "SUP1", 10.0, False, True)
    assert result["will_change"] == 0
    assert result["unchanged"] == 1


# ---------------------------------------------------------------------------
# Both together
# ---------------------------------------------------------------------------

def test_both_base_and_supplement_produce_two_separate_planned_items():
    t = _transport(base_adult=100.0)
    opt = _option(prices=[dict(_ACTIVE_ENTRY)])
    client = _FakeClient(transports=[t], transport_options={("TRANSPORT-1", "OPT1"): opt})
    result = bulk_notes._plan_transport_price_increase(client, "SUP1", 10.0, True, True)
    assert result["will_change"] == 2
    kinds = sorted(item["write_kind"] for item in result["items"])
    assert kinds == ["transport", "transport_option"]


# ---------------------------------------------------------------------------
# apply() dispatch
# ---------------------------------------------------------------------------

def test_apply_dispatches_transport_price_increase_kind_via_plan_structured():
    t = _transport(base_adult=100.0)
    opt = _option(prices=[dict(_ACTIVE_ENTRY)])
    client = _FakeClient(transports=[t], transport_options={("TRANSPORT-1", "OPT1"): opt})
    plan = bulk_notes.plan_structured(
        client, "SUP1", "Transport", "transport_price_increase",
        {"percent": 10.0, "increase_base": True, "increase_supplement": True})
    result = bulk_notes.apply(client, "SUP1", plan)
    assert len(result["updated"]) == 2
    assert len(client.updated_transports) == 1
    assert client.updated_transports[0]["baseAdultPrice"] == 110.0
    assert len(client.updated_options) == 1
    assert client.updated_options[0][0] == "TRANSPORT-1"
    assert client.updated_options[0][1]["prices"][-1]["adultPriceSupplement"] == 22.0


def test_apply_base_write_goes_through_update_transport_not_update_transport_option():
    t = _transport(base_adult=100.0)
    client = _FakeClient(transports=[t])
    plan = bulk_notes._plan_transport_price_increase(client, "SUP1", 10.0, True, False)
    bulk_notes.apply(client, "SUP1", plan)
    assert len(client.updated_transports) == 1
    assert len(client.updated_options) == 0


def test_per_vehicle_transport_raises_vehicle_price_not_the_zeroed_base_fields():
    """Regression for a real 2026-09-10 product-owner report: "when i bulk update transport
    price, does the app understand the logic between Sedan and Hiace?" Tracing this base-price
    path found it only ever looked at baseAdultPrice/baseChildrenPrice/baseInfantPrice - all 0
    for a pricePerPax=False (per-vehicle) transport, whose real base price lives in
    vehiclePrice instead (same root cause as _plan_transport_supplement's per-vehicle fix
    earlier the same day). Before this fix, a per-vehicle transport silently produced
    "unchanged - no base price set" no matter what percentage was entered."""
    t = {
        "id": "TRANSPORT-1", "name": "Cairo - Alexandria", "optionCodes": ["Sedan", "Hiace"],
        "pricePerPax": False, "vehiclePrice": 100.0,
        "baseAdultPrice": 0.0, "baseChildrenPrice": 0.0, "baseInfantPrice": 0.0,
    }
    sedan = {"code": "Sedan", "minPassengers": 1, "maxPassengers": 3, "prices": []}
    hiace = {"code": "Hiace", "minPassengers": 1, "maxPassengers": 8, "prices": []}
    client = _FakeClient(transports=[t], transport_options={
        ("TRANSPORT-1", "Sedan"): sedan, ("TRANSPORT-1", "Hiace"): hiace,
    })
    plan = bulk_notes._plan_transport_price_increase(client, "SUP1", 10.0, True, False)
    base_item = next(it for it in plan["items"] if it["id"] == "TRANSPORT-1:base")
    assert base_item["status"] == "will_change"
    assert base_item["record"]["vehiclePrice"] == 110.0
    # The base fields that stay genuinely 0 for a per-vehicle transport must not be touched.
    assert base_item["record"]["baseAdultPrice"] == 0.0


def test_per_pax_transport_is_unaffected_by_the_per_vehicle_branch():
    t = _transport(base_adult=100.0, base_children=50.0, base_infant=0.0)
    client = _FakeClient(transports=[t])
    plan = bulk_notes._plan_transport_price_increase(client, "SUP1", 10.0, True, False)
    base_item = next(it for it in plan["items"] if it["id"] == "TRANSPORT-1:base")
    assert base_item["status"] == "will_change"
    assert base_item["record"]["baseAdultPrice"] == 110.0
    assert base_item["record"]["baseChildrenPrice"] == 55.0
    assert "vehiclePrice" not in base_item["changes"]["EN"][1]


def test_absolute_amount_adds_a_flat_number_to_the_base_price():
    """CONFIRMED REAL NEED (product owner, 2026-09-10, follow-up on the same feature): "adds
    manually amount of percentage or absolute number and this will be added to the already
    existing base price" - is_percent=False must ADD the raw amount, not multiply."""
    t = _transport(base_adult=100.0, base_children=50.0, base_infant=0.0)
    client = _FakeClient(transports=[t])
    plan = bulk_notes._plan_transport_price_increase(
        client, "SUP1", 5.0, True, False, is_percent=False)
    base_item = next(it for it in plan["items"] if it["id"] == "TRANSPORT-1:base")
    assert base_item["status"] == "will_change"
    assert base_item["record"]["baseAdultPrice"] == 105.0
    assert base_item["record"]["baseChildrenPrice"] == 55.0
    # A field genuinely at 0 stays at 0 in absolute mode too - it means "not priced".
    assert base_item["record"]["baseInfantPrice"] == 0.0


def test_absolute_amount_on_a_per_vehicle_transport_adds_to_vehicle_price():
    t = {
        "id": "TRANSPORT-1", "name": "Cairo - Alexandria", "optionCodes": [],
        "pricePerPax": False, "vehiclePrice": 65.0,
        "baseAdultPrice": 0.0, "baseChildrenPrice": 0.0, "baseInfantPrice": 0.0,
    }
    client = _FakeClient(transports=[t])
    plan = bulk_notes._plan_transport_price_increase(
        client, "SUP1", 10.0, True, False, is_percent=False)
    base_item = next(it for it in plan["items"] if it["id"] == "TRANSPORT-1:base")
    assert base_item["record"]["vehiclePrice"] == 75.0


def test_no_amount_given_reports_the_right_error_for_each_mode():
    t = _transport()
    client = _FakeClient(transports=[t])
    percent_plan = bulk_notes._plan_transport_price_increase(client, "SUP1", 0, True, False)
    assert "percentage" in percent_plan["error"]
    absolute_plan = bulk_notes._plan_transport_price_increase(
        client, "SUP1", 0, True, False, is_percent=False)
    assert "amount" in absolute_plan["error"]


def test_structured_targets_lists_the_new_transport_option():
    assert bulk_notes.STRUCTURED_TARGETS["Transport"]["Permanent price increase (%)"] == \
        "transport_price_increase"
    assert "Permanent price increase (%)" in bulk_notes.available_structured_targets("Transport")


# ---------------------------------------------------------------------------
# app.py wiring - source-shape check only (app.py can't be imported in a test process)
# ---------------------------------------------------------------------------
import os

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_app_py_has_the_price_increase_ui_branch():
    src = _read_app_py()
    assert 'structured_kind == "transport_price_increase"' in src
    branch = src.split('structured_kind == "transport_price_increase":')[1].split("\n        elif")[0]
    assert 'item_data["amount"]' in branch
    assert 'item_data["is_percent"]' in branch
    assert 'item_data["increase_base"]' in branch
    assert 'item_data["increase_supplement"]' in branch
