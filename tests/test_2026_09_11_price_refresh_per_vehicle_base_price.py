"""Regression tests for the 2026-09-11 fix to price_refresh.py's Transport base-price handling
for PER-VEHICLE transports (pricePerPax=False).

Found while investigating the product owner's question "could the app understand that Sedan is
for base modality and Hiace is for Price supplement calculated?" for the FTS rate-matrix price
refresh flow (see tests/test_2026_09_11_price_refresh_fts_matrix_bypass.py) - every FTS-created
Transport is per-vehicle (pricePerPax=False, confirmed via
fts_transfer_matrix.fts_route_to_extracted_transport_data's charge_unit="per_service" and
builder.build_transport_payloads' own `vehiclePrice=0.0 if price_per_pax else base_price,
baseAdultPrice=base_price if price_per_pax else 0.0`), so this flow's Transport handling was
about to be run against exactly the product type it had never been verified against.

price_refresh.py read/wrote baseAdultPrice UNCONDITIONALLY - for a per-vehicle transport that
field is genuinely 0 (the real price lives in vehiclePrice instead), so:
  * load_supplier_transports reported every option's "current price" as just its own supplement
    (base read as 0), making every refresh look like a huge change even when nothing moved;
  * rebuild_prices wrote the new base into baseAdultPrice - which the live record ignores when
    pricePerPax=False - leaving the real vehiclePrice field stale forever, so the price shown
    to a booker would never actually change.

This is the SAME root cause already found and fixed once in bulk_notes.py's dated-supplement
flow (see claude/transport-supplement-per-vehicle-price-bug-2026-09-10.md, real
TRANSPORT-415965 test) - mirrored here exactly rather than re-derived, so both fixes agree:
vehiclePrice is the base for a per-vehicle transport, only adultPriceSupplement carries a
per-vehicle bracket's delta, and child/infant base amounts don't apply.
"""
import price_refresh


class _FakeTransportClient:
    def __init__(self, record, options):
        self._record = record
        self._options = {o["code"]: o for o in options}

    def get_transports(self, supplier_id):
        return {"transport": [self._record]}

    def get_transport_option(self, supplier_id, transport_id, code):
        return self._options[code]


def _per_vehicle_record(vehicle_price=100.0, base_adult=0.0):
    return {
        "id": "TRANSPORT-1", "name": "Cairo - Luxor", "currency": "USD",
        "pricePerPax": False,
        "vehiclePrice": vehicle_price,
        "baseAdultPrice": base_adult, "baseChildrenPrice": 0.0, "baseInfantPrice": 0.0,
        "optionCodes": ["Sedan", "Hiace"],
        "segments": [{"departureLocationCode": "meet_CAI", "arrivalLocationCode": "meet_LXR"}],
    }


def _option(code, min_pax, max_pax, supplement=0.0):
    return {
        "code": code, "minPassengers": min_pax, "maxPassengers": max_pax,
        "prices": [{"adultPriceSupplement": supplement}] if supplement else [],
        "translations": {"EN": {"name": code}},
    }


# ----------------------------------------------------------------------
# load_supplier_transports - reading the CURRENT price correctly
# ----------------------------------------------------------------------

def test_per_vehicle_transport_reads_its_base_from_vehicle_price_not_base_adult_price():
    # Sedan (base) = vehiclePrice = 100; Hiace = vehiclePrice + its own 40 supplement = 140.
    # baseAdultPrice is 0 (as it genuinely is on a real per-vehicle record) - reading it would
    # have reported both options at just their bare supplement (0 and 40).
    record = _per_vehicle_record(vehicle_price=100.0, base_adult=0.0)
    options = [_option("Sedan", 1, 3, supplement=0.0), _option("Hiace", 1, 8, supplement=40.0)]
    client = _FakeTransportClient(record, options)
    routes, err = price_refresh.load_supplier_transports(client, "SUP-X")
    assert err is None
    route = routes[0]
    assert route["price_per_pax"] is False
    by_code = {o["code"]: o["unit_price"] for o in route["options"]}
    assert by_code["Sedan"] == 100.0
    assert by_code["Hiace"] == 140.0


def test_per_pax_transport_is_unaffected_reads_base_adult_price_as_before():
    record = {
        "id": "TRANSPORT-2", "name": "Aswan - Hurghada", "currency": "EUR",
        "pricePerPax": True,
        "baseAdultPrice": 50.0, "baseChildrenPrice": 25.0, "baseInfantPrice": 0.0,
        "optionCodes": ["A"],
        "segments": [{}],
    }
    client = _FakeTransportClient(record, [_option("A", 2, 9, supplement=10.0)])
    routes, err = price_refresh.load_supplier_transports(client, "SUP-X")
    assert err is None
    assert routes[0]["price_per_pax"] is True
    assert routes[0]["options"][0]["unit_price"] == 60.0  # 50 + 10, exactly as before the fix


def test_a_transport_missing_the_price_per_pax_key_defaults_to_per_pax_not_per_vehicle():
    # CONFIRMED bulk_notes.py rule: "a transport missing the pricePerPax field entirely still
    # defaults to the old per-pax behavior (the confirmed common case)." The old
    # `bool(record.get("pricePerPax"))` here defaulted a missing key to False (per-vehicle) -
    # the opposite of that rule.
    record = {
        "id": "TRANSPORT-3", "name": "X - Y", "currency": "EUR",
        "baseAdultPrice": 20.0, "baseChildrenPrice": 0.0, "baseInfantPrice": 0.0,
        "optionCodes": ["A"], "segments": [{}],
        # no "pricePerPax" key at all
    }
    client = _FakeTransportClient(record, [_option("A", 1, 4)])
    routes, err = price_refresh.load_supplier_transports(client, "SUP-X")
    assert err is None
    assert routes[0]["price_per_pax"] is True
    assert routes[0]["options"][0]["unit_price"] == 20.0


# ----------------------------------------------------------------------
# rebuild_prices - writing the new price to the correct field
# ----------------------------------------------------------------------

def _per_vehicle_route(min_max=((1, 3), (1, 8))):
    return {
        "id": "TRANSPORT-1", "name": "Cairo - Luxor", "kind": None,
        "price_per_pax": False,
        "options": [
            {"code": "Sedan", "min_pax": min_max[0][0], "max_pax": min_max[0][1],
             "unit_price": 100.0, "name": "Sedan", "raw": {"code": "Sedan", "prices": []}},
            {"code": "Hiace", "min_pax": min_max[1][0], "max_pax": min_max[1][1],
             "unit_price": 140.0, "name": "Hiace",
             "raw": {"code": "Hiace", "prices": [{"adultPriceSupplement": 40.0}]}},
        ],
        "raw": {"id": "TRANSPORT-1", "pricePerPax": False, "vehiclePrice": 100.0,
               "baseAdultPrice": 0.0, "baseChildrenPrice": 0.0, "baseInfantPrice": 0.0},
    }


def test_per_vehicle_rebuild_writes_the_new_base_into_vehicle_price_not_base_adult_price():
    route = _per_vehicle_route()
    payloads = price_refresh.rebuild_prices(route, {"Sedan": 110.0, "Hiace": 150.0})
    parent = payloads["transport"]
    # Widest bracket (Hiace, 1-8) is chosen as base, same widest-bracket rule as always - only
    # WHICH FIELD it's written into changes for a per-vehicle transport.
    assert parent["vehiclePrice"] == 150.0
    assert parent["baseAdultPrice"] == 0.0  # must stay 0, never repurposed


def test_per_vehicle_rebuild_still_nets_out_to_the_correct_per_option_price():
    # Base+supplement must still reproduce the exact requested price for EVERY option,
    # regardless of which one was chosen as base - this is what makes the base-field choice
    # safe to change without touching the actual arithmetic.
    route = _per_vehicle_route()
    payloads = price_refresh.rebuild_prices(route, {"Sedan": 110.0, "Hiace": 150.0})
    by_code = {o["code"]: o for o in payloads["options"]}
    assert by_code["Hiace"]["unit_price"] == 150.0
    assert by_code["Sedan"]["unit_price"] == 110.0
    sedan_supplement = by_code["Sedan"]["payload"]["prices"][0]["adultPriceSupplement"]
    assert round(payloads["transport"]["vehiclePrice"] + sedan_supplement, 2) == 110.0


def test_per_vehicle_rebuild_does_not_touch_child_or_infant_base_fields():
    route = _per_vehicle_route()
    route["raw"]["baseChildrenPrice"] = 5.0  # should never legitimately be nonzero on a
    route["raw"]["baseInfantPrice"] = 3.0    # per-vehicle record, but must be left alone if it is
    payloads = price_refresh.rebuild_prices(route, {"Sedan": 110.0, "Hiace": 150.0})
    assert payloads["transport"]["baseChildrenPrice"] == 5.0
    assert payloads["transport"]["baseInfantPrice"] == 3.0


def test_per_pax_rebuild_is_unaffected_still_writes_base_adult_price():
    route = {
        "id": "TRANSPORT-2", "name": "Aswan - Hurghada", "kind": None,
        "price_per_pax": True,
        "options": [
            {"code": "A", "min_pax": 1, "max_pax": 1, "unit_price": 30.0, "name": "A",
             "raw": {"code": "A", "prices": []}},
            {"code": "B", "min_pax": 2, "max_pax": 9, "unit_price": 50.0, "name": "B",
             "raw": {"code": "B", "prices": [{"adultPriceSupplement": 20.0}]}},
        ],
        "raw": {"id": "TRANSPORT-2", "pricePerPax": True, "baseAdultPrice": 30.0,
               "baseChildrenPrice": 15.0, "baseInfantPrice": 0.0},
    }
    payloads = price_refresh.rebuild_prices(route, {"A": 40.0, "B": 60.0})
    parent = payloads["transport"]
    assert parent["baseAdultPrice"] == 60.0  # widest bracket (B) still wins, as before
    assert "vehiclePrice" not in parent
    # Child base scales with the adult base ratio, exactly as before this fix.
    assert parent["baseChildrenPrice"] == round(15.0 * (60.0 / 30.0), 2)
