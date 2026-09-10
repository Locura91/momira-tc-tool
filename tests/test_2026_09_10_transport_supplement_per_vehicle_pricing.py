"""Regression test for TRANSPORT-415965 (product owner, 2026-09-10, real test after the previous
supplement-date fix went live): "the most important is missing, the supplement price!" -
screenshot showed every row in Travel Compositor's own Prices tab with Supplement 0,00 EUR,
even the newly-added peak-period row, on a Transport with "Price Per Pax" UNCHECKED (a single
"Vehicle" base price of 100.00, no separate Adult/Children/Infant price fields shown at all).

ROOT CAUSE: `_plan_transport_supplement` always computed each bracket's new supplement against
`baseAdultPrice`/`baseChildrenPrice`/`baseInfantPrice` - which are genuinely 0.0 for a
per-vehicle transport (pricePerPax=False); the real base price for that pricing mode lives in
the separate `vehiclePrice` field (confirmed ContractTransportVO field - the same one
build_transport_payload writes to for a per-vehicle transport, per its own long-flagged
"UNCONFIRMED ASSUMPTION" docstring note). So a Percent-type supplement compounded onto 0 and
always came out 0, no matter what percentage was entered - exactly what the screenshot showed.

FIX: when `pricePerPax` is False, use `vehiclePrice` as the base for the (only) adult field, and
only write `adultPriceSupplement` - mirroring build_transport_payload's own confirmed create-flow
behavior (its adult_delta/children_delta/infant_delta split, where a per-vehicle bracket's whole
delta goes into adultPriceSupplement alone; there's nothing to key a per-headcount children/
infant surcharge off when the whole vehicle is one lump price).
"""
import bulk_notes


class _FakeClient:
    def __init__(self, transport, options):
        self._transport = transport
        self._options = options
        self.updated_options = []

    def get_transports(self, supplier_id):
        return {"transport": [self._transport]}

    def get_transport(self, supplier_id, transport_id):
        return self._transport

    def get_transport_option(self, supplier_id, transport_id, code):
        return self._options[code]

    def update_transport_option(self, supplier_id, transport_id, payload):
        self.updated_options.append((transport_id, payload))
        return payload


def _per_vehicle_client(vehicle_price=100.0):
    transport = {
        "id": "TRANSPORT-415965", "name": "Private Transfer", "optionCodes": ["Private18"],
        "pricePerPax": False, "vehiclePrice": vehicle_price,
        "baseAdultPrice": 0.0, "baseChildrenPrice": 0.0, "baseInfantPrice": 0.0,
    }
    option = {"code": "Private18", "minPassengers": 1, "maxPassengers": 8, "prices": []}
    return _FakeClient(transport, {"Private18": option})


def test_percent_supplement_on_a_per_vehicle_transport_compounds_onto_vehicle_price_not_zero():
    client = _per_vehicle_client(vehicle_price=100.0)
    item_data = {"name": "High Season", "amount": 15, "is_percent": True,
                "start_date": "2026-10-01", "end_date": "2027-04-30"}
    planned = bulk_notes.plan_structured(
        client, "S1", "Transport", "transport_supplement", item_data)
    assert planned["will_change"] == 1
    entry = planned["items"][0]["record"]["prices"][0]
    assert entry["adultPriceSupplement"] == 15.0  # 15% of vehiclePrice=100, NOT 0
    assert entry["childrenPriceSupplement"] == 0.0
    assert entry["infantPriceSupplement"] == 0.0


def test_absolute_supplement_on_a_per_vehicle_transport_also_only_writes_adult_field():
    client = _per_vehicle_client(vehicle_price=100.0)
    item_data = {"name": "High Season", "amount": 25, "is_percent": False,
                "start_date": "2026-10-01", "end_date": "2027-04-30"}
    planned = bulk_notes.plan_structured(
        client, "S1", "Transport", "transport_supplement", item_data)
    entry = planned["items"][0]["record"]["prices"][0]
    assert entry["adultPriceSupplement"] == 25.0
    assert entry["childrenPriceSupplement"] == 0.0
    assert entry["infantPriceSupplement"] == 0.0


def test_per_pax_transport_is_unaffected_and_still_uses_the_three_base_price_fields():
    transport = {
        "id": "TRANSPORT-1", "name": "Airport Run", "optionCodes": ["OPT1"],
        "pricePerPax": True, "vehiclePrice": 0.0,
        "baseAdultPrice": 50.0, "baseChildrenPrice": 30.0, "baseInfantPrice": 0.0,
    }
    option = {"code": "OPT1", "minPassengers": 1, "maxPassengers": 4, "prices": []}
    client = _FakeClient(transport, {"OPT1": option})
    item_data = {"name": "High Season", "amount": 10, "is_percent": True,
                "start_date": "2026-10-01", "end_date": "2027-04-30"}
    planned = bulk_notes.plan_structured(
        client, "S1", "Transport", "transport_supplement", item_data)
    entry = planned["items"][0]["record"]["prices"][0]
    assert entry["adultPriceSupplement"] == 5.0     # 10% of 50
    assert entry["childrenPriceSupplement"] == 3.0  # 10% of 30
    assert entry["infantPriceSupplement"] == 0.0    # 10% of 0


def test_missing_price_per_pax_field_defaults_to_the_old_per_pax_behavior():
    """A transport fetched from a real supplier before this fix might not even have the field
    if it predates pricePerPax being read at all - default True (the confirmed common case, per
    build_transport_payload's own "every real example seen has pricePerPax=true" note) rather
    than silently treating it as per-vehicle and zeroing children/infant unexpectedly."""
    transport = {
        "id": "TRANSPORT-2", "name": "No Flag", "optionCodes": ["OPT1"],
        "baseAdultPrice": 40.0, "baseChildrenPrice": 20.0, "baseInfantPrice": 0.0,
    }
    option = {"code": "OPT1", "minPassengers": 1, "maxPassengers": 4, "prices": []}
    client = _FakeClient(transport, {"OPT1": option})
    item_data = {"name": "High Season", "amount": 10, "is_percent": False,
                "start_date": "2026-10-01", "end_date": "2027-04-30"}
    planned = bulk_notes.plan_structured(
        client, "S1", "Transport", "transport_supplement", item_data)
    entry = planned["items"][0]["record"]["prices"][0]
    assert entry["adultPriceSupplement"] == 10.0
    assert entry["childrenPriceSupplement"] == 10.0
    assert entry["infantPriceSupplement"] == 10.0
