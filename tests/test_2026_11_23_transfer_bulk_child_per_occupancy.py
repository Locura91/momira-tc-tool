"""Transfer bulk update: child/infant prices per occupancy bracket.

Verifies that the Transfer proposal builder (build_proposals) includes
child_old/child_new/infant_new per bracket when the document provides them,
and that rebuild_prices passes them through to _rebuild_transfer_prices."""

from price_refresh import build_proposals, rebuild_prices, KIND_TRANSFER


def _route(options):
    return {
        "kind": KIND_TRANSFER,
        "id": "12345",
        "name": "Test Transfer",
        "currency": "THB",
        "price_per_pax": True,
        "base_adult": 3350.0,
        "base_child": 0.0,
        "base_infant": 0.0,
        "options": options,
        "raw": {
            "id": "12345",
            "basePrice": 3350.0,
            "currency": "THB",
            "priceByPax": True,
            "minOccupancy": 1,
            "maxOccupancy": 6,
            "pricesByOccupancy": [
                {"occupancy": 1, "basePrice": {"amount": 8750.0, "currency": "THB"}},
                {"occupancy": 2, "basePrice": {"amount": 4650.0, "currency": "THB"}},
            ],
        },
    }


def test_transfer_proposal_includes_child_from_document():
    options = [
        {"code": "default", "min_pax": 1, "max_pax": 6, "unit_price": 3350.0,
         "unit_price_child": 0.0, "unit_price_infant": 0.0,
         "name": "default rate", "raw": None},
        {"code": "occ1", "min_pax": 1, "max_pax": 1, "unit_price": 8750.0,
         "unit_price_child": 0.0, "unit_price_infant": 0.0,
         "name": "1 pax", "raw": {"occupancy": 1, "basePrice": {"amount": 8750.0, "currency": "THB"}}},
    ]
    route = _route(options)
    finding = {
        "found": True,
        "brackets": [
            {"min_pax": 1, "max_pax": 1, "price": 5450.0, "child_price": 2500.0, "infant_price": None},
            {"min_pax": 2, "max_pax": 6, "price": 3350.0, "child_price": 2500.0, "infant_price": None},
        ],
        "confidence": "high",
        "matched_row": "test",
        "minimum_pax": 1,
        "currency": "THB",
    }
    proposals = build_proposals([route], {0: finding})
    assert len(proposals) == 1
    p = proposals[0]
    assert p["status"] == "changed"
    occ1_change = next((c for c in p["changes"] if c["code"] == "occ1"), None)
    assert occ1_change is not None
    assert occ1_change["new"] == 5450.0
    assert occ1_change["child_new"] == 2500.0
    assert occ1_change["child_source"] == "document"


def test_transfer_proposal_scales_child_proportionally():
    options = [
        {"code": "default", "min_pax": 1, "max_pax": 6, "unit_price": 3350.0,
         "unit_price_child": 2500.0, "unit_price_infant": 0.0,
         "name": "default rate", "raw": None},
    ]
    route = _route(options)
    finding = {
        "found": True,
        "brackets": [
            {"min_pax": 1, "max_pax": 6, "price": 6700.0, "child_price": None, "infant_price": None},
        ],
        "confidence": "high",
        "matched_row": "test",
        "minimum_pax": 1,
        "currency": "THB",
    }
    proposals = build_proposals([route], {0: finding})
    p = proposals[0]
    assert p["status"] == "changed"
    c = p["changes"][0]
    assert c["new"] == 6700.0
    assert c["child_new"] == 5000.0
    assert c["child_source"] == "scaled"


def test_rebuild_transfer_writes_child_per_occupancy():
    options = [
        {"code": "default", "min_pax": 1, "max_pax": 6, "unit_price": 3350.0,
         "unit_price_child": 0.0, "unit_price_infant": 0.0,
         "name": "default rate", "raw": None},
        {"code": "occ1", "min_pax": 1, "max_pax": 1, "unit_price": 8750.0,
         "unit_price_child": 0.0, "unit_price_infant": 0.0,
         "name": "1 pax", "raw": {"occupancy": 1, "basePrice": {"amount": 8750.0, "currency": "THB"}}},
        {"code": "occ2", "min_pax": 2, "max_pax": 2, "unit_price": 4650.0,
         "unit_price_child": 0.0, "unit_price_infant": 0.0,
         "name": "2 pax", "raw": {"occupancy": 2, "basePrice": {"amount": 4650.0, "currency": "THB"}}},
    ]
    route = _route(options)
    changes = [
        {"code": "occ1", "min_pax": 1, "max_pax": 1, "old": 8750.0, "new": 5450.0,
         "name": "1 pax", "child_old": 0.0, "child_new": 2500.0, "child_source": "document",
         "infant_old": 0.0, "infant_new": 0.0},
        {"code": "occ2", "min_pax": 2, "max_pax": 2, "old": 4650.0, "new": 3150.0,
         "name": "2 pax", "child_old": 0.0, "child_new": 2500.0, "child_source": "document",
         "infant_old": 0.0, "infant_new": 0.0},
    ]
    result = rebuild_prices(route, changes)
    payload = result["transport"]
    assert payload is not None
    occ_entries = payload.get("pricesByOccupancy", [])
    occ1 = next((e for e in occ_entries if e["occupancy"] == 1), None)
    assert occ1 is not None
    assert occ1["basePrice"]["amount"] == 5450.0
    assert occ1["childPrice"]["amount"] == 2500.0
    occ2 = next((e for e in occ_entries if e["occupancy"] == 2), None)
    assert occ2 is not None
    assert occ2["childPrice"]["amount"] == 2500.0
