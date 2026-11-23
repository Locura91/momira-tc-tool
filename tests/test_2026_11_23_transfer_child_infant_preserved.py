"""Transfer price refresh must preserve childPrice and infantPrice on pricesByOccupancy entries.

CONFIRMED REAL BUG (audit 2026-11-23): _transfer_brackets read only basePrice, so when
_rebuild_transfer_prices wrote the record back it had no way to carry child/infant prices
forward - they silently disappeared on every refresh. This test pins the fix: an operator
sets a child price for occupancy 2 in TC, a price refresh touches only the adult number,
and the child price stays live.

Also covers force_explicit_all=True (used by the CSV bulk importer) which keeps every
occupancy entry even when its adult amount matches the default - the only way to attach a
child price to an occupancy that would otherwise fall through to the (child-less) default
basePrice."""

from price_refresh import _transfer_brackets, _rebuild_transfer_prices, DEFAULT_BRACKET_CODE


def _sample_record():
    """A transfer with a default base of 2550 and two overrides (occ 1 solo surcharge,
    occ 2 with a child rate) - the shape the TRANSFER-454324 screenshot shows."""
    return {
        "id": "TRANSFER-454324",
        "basePrice": 2550.0,
        "currency": "THB",
        "minOccupancy": 1,
        "maxOccupancy": 6,
        "pricesByOccupancy": [
            {"occupancy": 1,
             "basePrice": {"amount": 4950.0, "currency": "THB"},
             "childPrice": {"amount": 0.0, "currency": "THB"},
             "infantPrice": {"amount": 0.0, "currency": "THB"}},
            {"occupancy": 2,
             "basePrice": {"amount": 3150.0, "currency": "THB"},
             "childPrice": {"amount": 2550.0, "currency": "THB"},
             "infantPrice": {"amount": 0.0, "currency": "THB"}},
        ],
    }


def _route(record):
    return {"id": record["id"], "raw": record,
            "options": _transfer_brackets(record), "currency": "THB"}


def test_transfer_brackets_reads_child_and_infant_prices():
    """_transfer_brackets must pull childPrice and infantPrice amounts, not just basePrice."""
    brackets = _transfer_brackets(_sample_record())
    occ2 = next(b for b in brackets if b["code"] == "occ2")
    assert occ2["unit_price"] == 3150.0
    assert occ2["unit_price_child"] == 2550.0
    assert occ2["unit_price_infant"] == 0.0


def test_rebuild_preserves_child_when_only_adult_changes():
    """An adult-price refresh must leave a live child rate untouched (the original bug)."""
    record = _sample_record()
    route = _route(record)
    # Operator bumps occ 2 adult from 3150 to 3250; child/infant left alone.
    new_adult = {"__default__": 2550.0, "occ1": 4950.0, "occ2": 3250.0}
    result = _rebuild_transfer_prices(route, new_adult)

    entries = result["transport"]["pricesByOccupancy"]
    occ2 = next(e for e in entries if e["occupancy"] == 2)
    assert occ2["basePrice"]["amount"] == 3250.0
    assert occ2["childPrice"]["amount"] == 2550.0, (
        "Pre-fix: child price was silently dropped. This assertion pins the fix.")


def test_rebuild_writes_new_child_price_when_supplied():
    """Caller supplying new_child_prices updates the child rate on that bracket."""
    record = _sample_record()
    route = _route(record)
    new_adult = {"__default__": 2550.0, "occ1": 4950.0, "occ2": 3150.0}
    new_child = {"occ2": 2800.0}
    result = _rebuild_transfer_prices(route, new_adult, new_child_prices=new_child)

    entries = result["transport"]["pricesByOccupancy"]
    occ2 = next(e for e in entries if e["occupancy"] == 2)
    assert occ2["childPrice"]["amount"] == 2800.0


def test_force_explicit_all_writes_every_occupancy_even_when_matching_default():
    """force_explicit_all keeps every occupancy row - needed so occupancies 3..maxOccupancy
    can carry their own child/infant price even though their adult amount equals the default
    base (and would otherwise be skipped as redundant)."""
    record = _sample_record()
    # Simulate the Thailand CSV bulk importer: explicit brackets for all of occ 1..6.
    brackets = [
        {"code": DEFAULT_BRACKET_CODE, "min_pax": 1, "max_pax": 6,
         "unit_price": 2550.0, "unit_price_child": 0.0, "unit_price_infant": 0.0,
         "name": "default", "raw": None},
        {"code": "occ1", "min_pax": 1, "max_pax": 1,
         "unit_price": 4950.0, "unit_price_child": 0.0, "unit_price_infant": 0.0,
         "name": "1 pax", "raw": {"occupancy": 1}},
        {"code": "occ2", "min_pax": 2, "max_pax": 2,
         "unit_price": 3150.0, "unit_price_child": 2500.0, "unit_price_infant": 0.0,
         "name": "2 pax", "raw": {"occupancy": 2}},
    ]
    # Occ 3..6 all have adult == base (2550) and the same child price (2500).
    for occ in (3, 4, 5, 6):
        brackets.append({
            "code": f"occ{occ}", "min_pax": occ, "max_pax": occ,
            "unit_price": 2550.0, "unit_price_child": 2500.0, "unit_price_infant": 0.0,
            "name": f"{occ} pax", "raw": {"occupancy": occ},
        })
    route = {"id": record["id"], "raw": record, "options": brackets, "currency": "THB"}
    new_adult = {b["code"]: b["unit_price"] for b in brackets}

    result = _rebuild_transfer_prices(route, new_adult, force_explicit_all=True)
    entries = result["transport"]["pricesByOccupancy"]

    # Every occupancy 1..6 should be present.
    written_occs = {e["occupancy"] for e in entries}
    assert written_occs == {1, 2, 3, 4, 5, 6}
    # And occ 5 (adult == default, non-zero child) must carry its child price.
    occ5 = next(e for e in entries if e["occupancy"] == 5)
    assert occ5["childPrice"]["amount"] == 2500.0


def test_rebuild_still_skips_pure_duplicates_without_force():
    """Without force_explicit_all, a bracket whose adult matches the default AND has no
    child/infant override is skipped as redundant - the long-standing behaviour."""
    record = _sample_record()
    # Add an occ 3 row that matches the base exactly, with no child price.
    brackets = _transfer_brackets(record)
    brackets.append({"code": "occ3", "min_pax": 3, "max_pax": 3,
                     "unit_price": 2550.0, "unit_price_child": 0.0,
                     "unit_price_infant": 0.0, "name": "3 pax",
                     "raw": {"occupancy": 3}})
    route = {"id": record["id"], "raw": record, "options": brackets, "currency": "THB"}
    new_adult = {b["code"]: b["unit_price"] for b in brackets}

    result = _rebuild_transfer_prices(route, new_adult)
    entries = result["transport"]["pricesByOccupancy"]
    occupancies = {e["occupancy"] for e in entries}
    assert 3 not in occupancies, "A redundant adult-only entry should still be dropped"
    assert 2 in occupancies  # the real override with a child rate stays
