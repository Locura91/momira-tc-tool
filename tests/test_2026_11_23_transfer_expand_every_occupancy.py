"""Apply must create a pricesByOccupancy row for EVERY occupancy, 1..maxOccupancy.

CONFIRMED REAL REQUEST (operator, 2026-11-23, side-by-side screenshot of TC's "Transfer
Prices By Occupancy" table next to the tool's review table):

    "it would be better and would be more accurate if we would exactly put just the prices
    we add for each of the occupancy, for each of the max occupancy that we have... that we
    are adding there a single line."
    "each occupancy would get their own occupancy line in this case, so it would be maximum
    9 packs available because this is the maximum we do."

TRANSFER-454499 had Max Occupancy 6 but only TWO rows in TC (occupancy 1 and 2).
Occupancies 3-6 fell through to the adult-only top-level basePrice, so the 2650 THB child
rate did not apply to them at all. The review screen listed all six, which made the
mismatch invisible until TC was opened side by side.

Root cause: _rebuild_transfer_prices looped over the brackets that ALREADY existed and
skipped the default one. With no bracket for occupancies 3-6 there was nothing to iterate,
so no amount of skip-logic tweaking could have produced the missing rows - even though
force_explicit_all's own docstring claimed "every occupancy 1..maxOccupancy ends up with
its own explicit row".
"""
from price_refresh import KIND_TRANSFER, _transfer_brackets, rebuild_prices


def _route(max_occ=6, base=3775.0, rows=((1, 4640.0), (2, 4030.0))):
    """TRANSFER-454499 as the operator's TC screenshot shows it."""
    record = {
        "id": "454499", "basePrice": base, "currency": "THB", "priceByPax": True,
        "minOccupancy": 1, "maxOccupancy": max_occ,
        "pricesByOccupancy": [
            {"occupancy": occ,
             "basePrice": {"amount": amt, "currency": "THB"},
             "childPrice": {"amount": 2650.0, "currency": "THB"}}
            for occ, amt in rows
        ],
    }
    return {
        "kind": KIND_TRANSFER, "id": "454499",
        "name": "Phuket Airport (HKT) - Koh Lipe Harbour",
        "currency": "THB", "price_per_pax": True,
        "options": _transfer_brackets(record), "raw": record,
    }


def _changes(route, child=2650.0):
    return [{
        "code": b["code"], "min_pax": b["min_pax"], "max_pax": b["max_pax"],
        "old": b["unit_price"], "new": b["unit_price"], "name": b["name"],
        "child_old": b.get("unit_price_child", 0.0), "child_new": child,
        "infant_old": 0.0, "infant_new": 0.0,
    } for b in route["options"]]


def _apply(route, child=2650.0):
    payload = rebuild_prices(route, _changes(route, child))["transport"]
    return {e["occupancy"]: e for e in payload["pricesByOccupancy"]}, payload


def test_every_occupancy_up_to_max_gets_its_own_row():
    by_occ, _ = _apply(_route())
    assert sorted(by_occ) == [1, 2, 3, 4, 5, 6], (
        "TC held only occupancies 1 and 2; Apply must now create 3,4,5,6 too"
    )


def test_occupancies_without_an_override_inherit_the_base_adult_price():
    by_occ, _ = _apply(_route())
    assert by_occ[1]["basePrice"]["amount"] == 4640.0
    assert by_occ[2]["basePrice"]["amount"] == 4030.0
    for occ in (3, 4, 5, 6):
        assert by_occ[occ]["basePrice"]["amount"] == 3775.0


def test_the_child_rate_reaches_the_newly_created_rows():
    """The whole point: before this, occupancies 3-6 had no row, so the child price could
    not apply to them - the top-level basePrice is adult-only."""
    by_occ, _ = _apply(_route())
    for occ in range(1, 7):
        assert by_occ[occ]["childPrice"]["amount"] == 2650.0, f"occ {occ} lost its child rate"


def test_currency_is_never_clobbered_on_a_created_row():
    by_occ, _ = _apply(_route())
    for occ in range(1, 7):
        assert by_occ[occ]["basePrice"]["currency"] == "THB"
        assert by_occ[occ]["childPrice"]["currency"] == "THB"


def test_occupancy_numbers_are_distinct_and_not_copied_from_the_default_bracket():
    """A created row starts from an empty dict, so "occupancy" must be set explicitly -
    otherwise every inherited row would carry the default bracket's own min_pax of 1."""
    by_occ, payload = _apply(_route())
    occs = [e["occupancy"] for e in payload["pricesByOccupancy"]]
    assert len(occs) == len(set(occs)), f"duplicate occupancy rows: {occs}"
    assert occs.count(1) == 1


def test_nine_is_the_ceiling_even_when_tc_reports_more():
    r = _route(max_occ=25)
    by_occ, _ = _apply(r)
    assert sorted(by_occ) == [1, 2, 3, 4, 5, 6, 7, 8, 9], "max 9 passengers per booking"


def test_a_smaller_max_occupancy_is_respected():
    r = _route(max_occ=3)
    by_occ, _ = _apply(r)
    assert sorted(by_occ) == [1, 2, 3]


def test_the_top_level_base_price_is_left_alone():
    """Expanding the table must not disturb the record's own default rate."""
    _, payload = _apply(_route())
    assert payload["basePrice"] == 3775.0


def test_zero_child_price_still_produces_every_row():
    """Expansion is unconditional now - it must not depend on a child price existing."""
    by_occ, _ = _apply(_route(), child=0.0)
    assert sorted(by_occ) == [1, 2, 3, 4, 5, 6]
