"""Regression tests for the worked-example caption on the bulk Transport price-refresh review
screen (product owner, 2026-09-11, in the same message that asked for the base-modality
confirmation question): "if a price is matching to a modality which will be added to the price
supplement, the app shall give one example and show the human what the app would calculate and
add to the product." Their own worked example, reproduced directly here: "Sedan Modality would be
base price and if I would update the Hiace the price difference must be calculated and the price
must then be added to the price supplement" - i.e. new Hiace 90 = base (Sedan) 40 + supplement 50.
"""
import price_refresh


def _route(sedan_price=40.0, hiace_price=40.0, base_override=None, extra_option=None):
    options = [
        {"code": "Hiace", "min_pax": 1, "max_pax": 8, "unit_price": hiace_price, "name": "Hiace",
         "raw": {"code": "Hiace", "prices": []}},
        {"code": "Sedan", "min_pax": 1, "max_pax": 3, "unit_price": sedan_price, "name": "Sedan",
         "raw": {"code": "Sedan", "prices": []}},
    ]
    if extra_option:
        options.append(extra_option)
    route = {
        "id": "TRANSPORT-1", "name": "Test Route", "kind": None, "price_per_pax": False,
        "currency": "USD", "options": options,
        "raw": {"id": "TRANSPORT-1", "pricePerPax": False, "vehiclePrice": sedan_price,
                "baseAdultPrice": 0.0, "baseChildrenPrice": 0.0, "baseInfantPrice": 0.0},
    }
    if base_override is not None:
        route["base_bracket_override"] = base_override
    return route


def test_the_products_own_worked_example_sedan_base_hiace_supplement():
    route = _route(sedan_price=40.0, hiace_price=40.0, base_override=(1, 3))
    changes = [{"code": "Hiace", "min_pax": 1, "max_pax": 8, "old": 40.0, "new": 90.0,
               "name": "Hiace"}]
    out = price_refresh.supplement_calculation_examples(route, changes)
    assert len(out) == 1
    ex = out[0]
    assert ex["code"] == "Hiace"
    assert ex["base_code"] == "Sedan"
    assert ex["base_price"] == 40.0
    assert ex["new_price"] == 90.0
    assert ex["supplement"] == 50.0


def test_changing_the_base_modality_itself_produces_no_example():
    # Sedan IS the base - its own new price is written directly, no supplement arithmetic.
    route = _route(sedan_price=40.0, hiace_price=40.0, base_override=(1, 3))
    changes = [{"code": "Sedan", "min_pax": 1, "max_pax": 3, "old": 40.0, "new": 60.0,
               "name": "Sedan"}]
    assert price_refresh.supplement_calculation_examples(route, changes) == []


def test_both_modalities_changing_uses_the_bases_own_new_price():
    # If Sedan is also moving this round, Hiace's supplement must be computed against Sedan's
    # NEW price, not its old one - matching rebuild_prices' own "resolved" arithmetic exactly.
    route = _route(sedan_price=40.0, hiace_price=40.0, base_override=(1, 3))
    changes = [
        {"code": "Sedan", "min_pax": 1, "max_pax": 3, "old": 40.0, "new": 55.0, "name": "Sedan"},
        {"code": "Hiace", "min_pax": 1, "max_pax": 8, "old": 40.0, "new": 90.0, "name": "Hiace"},
    ]
    out = price_refresh.supplement_calculation_examples(route, changes)
    assert len(out) == 1
    ex = out[0]
    assert ex["base_price"] == 55.0
    assert ex["supplement"] == 35.0  # 90 - 55


def test_a_negative_supplement_is_reported_as_is_not_clamped():
    route = _route(sedan_price=40.0, hiace_price=40.0, base_override=(1, 3))
    changes = [{"code": "Hiace", "min_pax": 1, "max_pax": 8, "old": 40.0, "new": 25.0,
               "name": "Hiace"}]
    ex = price_refresh.supplement_calculation_examples(route, changes)[0]
    assert ex["supplement"] == -15.0


def test_no_changes_produces_no_examples():
    route = _route()
    assert price_refresh.supplement_calculation_examples(route, []) == []


def test_a_single_modality_route_never_produces_an_example():
    route = _route()
    route["options"] = [route["options"][0]]
    changes = [{"code": "Hiace", "min_pax": 1, "max_pax": 8, "old": 40.0, "new": 90.0,
               "name": "Hiace"}]
    assert price_refresh.supplement_calculation_examples(route, changes) == []


def test_no_forced_base_falls_back_to_the_live_auto_detect_read():
    # Sedan carries a real live supplement of -30 (i.e. Sedan is NOT base); Hiace has none, so
    # auto-detect picks Hiace as base, matching _current_base_option's own default behaviour.
    hiace_opt = {"code": "Hiace", "min_pax": 1, "max_pax": 8, "unit_price": 200.0, "name": "Hiace",
                "raw": {"code": "Hiace", "prices": []}}
    sedan_opt = {"code": "Sedan", "min_pax": 1, "max_pax": 3, "unit_price": 170.0, "name": "Sedan",
                "raw": {"code": "Sedan",
                        "prices": [{"adultPriceSupplement": -30.0}]}}
    route = {"id": "T", "name": "R", "kind": None, "price_per_pax": False, "currency": "USD",
            "options": [hiace_opt, sedan_opt],
            "raw": {"vehiclePrice": 170.0, "baseAdultPrice": 0.0, "pricePerPax": False}}
    changes = [{"code": "Sedan", "min_pax": 1, "max_pax": 3, "old": 170.0, "new": 95.0,
               "name": "Sedan"}]
    ex = price_refresh.supplement_calculation_examples(route, changes)[0]
    assert ex["base_code"] == "Hiace"
    assert ex["base_price"] == 200.0
    assert ex["supplement"] == -105.0  # 95 - 200


def test_transfer_kind_never_produces_examples():
    route = _route()
    route["kind"] = price_refresh.KIND_TRANSFER
    changes = [{"code": "Hiace", "min_pax": 1, "max_pax": 8, "old": 40.0, "new": 90.0,
               "name": "Hiace"}]
    assert price_refresh.supplement_calculation_examples(route, changes) == []


# ----------------------------------------------------------------------
# build_proposals wiring
# ----------------------------------------------------------------------

def _finding(price):
    return {"found": True, "minimum_pax": 1, "currency": "USD", "confidence": "high", "note": "",
            "matched_row": "row", "brackets": [{"min_pax": 1, "max_pax": 8, "price": price,
                                                 "child_price": None, "infant_price": None}]}


def test_build_proposals_attaches_the_supplement_examples():
    route = _route(sedan_price=40.0, hiace_price=40.0, base_override=(1, 3))
    proposal = price_refresh.build_proposals([route], {0: _finding(90.0)})[0]
    assert proposal["status"] == "changed"
    assert len(proposal["supplement_examples"]) == 1
    assert proposal["supplement_examples"][0]["code"] == "Hiace"
    assert proposal["supplement_examples"][0]["supplement"] == 50.0
