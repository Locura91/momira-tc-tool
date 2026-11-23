"""Transfer review table: one row per occupancy, in numeric order.

CONFIRMED REAL COMPLAINT (operator, 2026-11-23, with screenshot): "the order shall be
always 1 packs, 2 packs, and then 1 to 6 packs if you do it like this way. But it will be
better [to show] 3 packs, 4 packs, 5 packs, 6 packs, 7 packs, and 9 packs, 8 packs also to
add it in one line."

The screenshot showed the old ordering, which sorted raw brackets by (min_pax, max_pax):

    1-1 pax | 7330.0 THB
    1-6 pax | 3240.0 THB      <- the catch-all default, wedged in the middle
    2-2 pax | 4150.0 THB

The default range sorts between the two single-occupancy overrides, which reads as
nonsense and hides that occupancies 3-6 are priced at all.
"""
from flows.price_refresh import _transfer_occupancy_rows


def _route():
    """The exact shape from the operator's screenshot: a 1-6 default at 3240, with
    single-occupancy overrides at 1 pax (7330) and 2 pax (4150)."""
    return {
        "kind": "Transfer",
        "currency": "THB",
        "options": [
            {"code": "default", "min_pax": 1, "max_pax": 6, "unit_price": 3240.0,
             "unit_price_child": 0.0, "unit_price_infant": 0.0, "name": "default rate"},
            {"code": "occ1", "min_pax": 1, "max_pax": 1, "unit_price": 7330.0,
             "unit_price_child": 0.0, "unit_price_infant": 0.0, "name": "1 pax"},
            {"code": "occ2", "min_pax": 2, "max_pax": 2, "unit_price": 4150.0,
             "unit_price_child": 0.0, "unit_price_infant": 0.0, "name": "2 pax"},
        ],
        "raw": {"maxOccupancy": 6, "basePrice": 3240.0},
    }


def test_one_row_per_occupancy_in_numeric_order():
    rows = _transfer_occupancy_rows(_route(), {})
    assert [r["occ"] for r in rows] == [1, 2, 3, 4, 5, 6], \
        "Occupancies must read 1,2,3,4,5,6 - never 1-1, 1-6, 2-2 as the screenshot showed"


def test_the_catch_all_range_never_appears_between_single_occupancies():
    """The specific thing the operator photographed: a '1-6 pax' row sitting between
    '1-1 pax' and '2-2 pax'. No row may span a range at all now."""
    rows = _transfer_occupancy_rows(_route(), {})
    for r in rows:
        assert isinstance(r["occ"], int)
    assert len({r["occ"] for r in rows}) == len(rows), "no duplicated occupancy rows"


def test_overrides_win_and_the_rest_inherit_the_default_rate():
    rows = {r["occ"]: r for r in _transfer_occupancy_rows(_route(), {})}
    assert rows[1]["adult"] == 7330.0
    assert rows[2]["adult"] == 4150.0
    for occ in (3, 4, 5, 6):
        assert rows[occ]["adult"] == 3240.0, f"occ {occ} should inherit the 3240 default"


def test_inherited_rows_say_so_in_the_modality_cell():
    """An operator must be able to tell an occupancy priced in its own right from one
    falling through to the default, without cross-referencing anything."""
    rows = {r["occ"]: r for r in _transfer_occupancy_rows(_route(), {})}
    assert "default" in rows[4]["modality"].lower()
    assert "default" not in rows[1]["modality"].lower()


def test_a_pending_change_is_reflected_and_marked_changed():
    changes = {"occ1": {"code": "occ1", "old": 7330.0, "new": 5450.0,
                        "child_new": 2500.0, "infant_new": 0.0}}
    rows = {r["occ"]: r for r in _transfer_occupancy_rows(_route(), changes)}
    assert rows[1]["adult"] == 5450.0
    assert rows[1]["child"] == 2500.0
    assert rows[1]["changed"] is True
    assert rows[2]["changed"] is False


def test_occupancy_count_never_exceeds_the_tc_maximum_of_nine():
    r = _route()
    r["raw"]["maxOccupancy"] = 50
    rows = _transfer_occupancy_rows(r, {})
    assert [x["occ"] for x in rows] == [1, 2, 3, 4, 5, 6, 7, 8, 9]


def test_max_occupancy_falls_back_to_the_widest_bracket_when_absent():
    r = _route()
    r["raw"].pop("maxOccupancy")
    rows = _transfer_occupancy_rows(r, {})
    assert [x["occ"] for x in rows] == [1, 2, 3, 4, 5, 6]
