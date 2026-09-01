"""Tests for _extend_tiers_for_multi_vehicle_pricing (Transfer's per-vehicle-rate synthesis).

CONFIRMED BUG (full-app audit, 2026-09-01, CRITICAL #1): a flat per-vehicle rate stated as a
range ("1-7 pax: EUR 80") is extracted by ai_extractor.py with "occupancy" set to the bracket's
LOWER bound (occupancy=1 - see ai_extractor.py's own extraction rule, "use the bracket's LOWER
bound as the occupancy number"). That's correct for per-pax defaulting, but the old code also
used it as the vehicle's real capacity when synthesizing multi-vehicle coverage, so a 7-pax
booking in one EUR-80 minivan was priced as ceil(7/1) = 7 separate vehicles = EUR 560. The fix
threads the document's own separately-extracted max_occupancy field through as the real vehicle
capacity (vehicle_capacity=), which wins whenever it's larger than the tier's own occupancy
value. No test existed for this function before this fix - the audit's own words: "No test
exists for this function at all."
"""
from builder import _extend_tiers_for_multi_vehicle_pricing


def test_flat_range_bracket_uses_real_capacity_not_lower_bound():
    # Document said "1-7 pax: EUR 80" - extraction stores occupancy=1 (the lower bound) for
    # this single tier, but the document's own max_occupancy field says 7 (the real capacity).
    # Occupancies 2-7 aren't given their own explicit entry (TC's basePrice-default semantics -
    # see the function's own docstring - already cover them at the same EUR 80 rate); only
    # occupancies ABOVE the real capacity need a synthesized multi-vehicle entry.
    tiers = [{"occupancy": 1, "price": 80.0}]
    result = _extend_tiers_for_multi_vehicle_pricing(tiers, price_by_pax=False, vehicle_capacity=7)
    by_occ = {t["occupancy"]: t["price"] for t in result}
    assert by_occ[1] == 80.0
    assert 7 not in by_occ  # covered implicitly by basePrice=80, not synthesized here
    # 8 pax needs a second vehicle: ceil(8/7) = 2 * 80 = 160, not ceil(8/1) = 8 * 80 = 640
    # (the bug: dividing by the lower-bound occupancy=1 instead of the real capacity=7).
    assert by_occ[8] == 160.0
    assert by_occ[9] == 160.0


def test_without_vehicle_capacity_falls_back_to_old_behavior():
    # No vehicle_capacity given (e.g. a caller that hasn't been updated) - falls back to the
    # tier's own occupancy value, same as before the fix, so nothing else regresses.
    tiers = [{"occupancy": 4, "price": 100.0}]
    result = _extend_tiers_for_multi_vehicle_pricing(tiers, price_by_pax=False)
    by_occ = {t["occupancy"]: t["price"] for t in result}
    assert by_occ[4] == 100.0
    assert by_occ[5] == 200.0  # ceil(5/4) = 2 vehicles


def test_vehicle_capacity_smaller_than_tier_occupancy_is_ignored():
    # vehicle_capacity should never SHRINK the capacity below what the tier itself already
    # states - only correct the case where the tier's occupancy undersells the real capacity.
    tiers = [{"occupancy": 6, "price": 120.0}]
    result = _extend_tiers_for_multi_vehicle_pricing(tiers, price_by_pax=False, vehicle_capacity=2)
    by_occ = {t["occupancy"]: t["price"] for t in result}
    assert by_occ[6] == 120.0
    assert by_occ[7] == 240.0  # ceil(7/6) = 2, not ceil(7/2) = 4


def test_child_and_infant_prices_still_scale_by_vehicle_count_with_real_capacity():
    tiers = [{"occupancy": 1, "price": 80.0, "child_price": 40.0, "infant_price": 0.0}]
    result = _extend_tiers_for_multi_vehicle_pricing(tiers, price_by_pax=False, vehicle_capacity=7)
    eight_pax = next(t for t in result if t["occupancy"] == 8)
    assert eight_pax["price"] == 160.0
    assert eight_pax["child_price"] == 80.0  # 40 * 2 vehicles
    assert eight_pax["infant_price"] == 0.0


def test_capacity_at_or_above_system_cap_returns_tiers_unchanged():
    tiers = [{"occupancy": 1, "price": 80.0}]
    result = _extend_tiers_for_multi_vehicle_pricing(tiers, price_by_pax=False, vehicle_capacity=9)
    assert result == tiers


def test_per_pax_pricing_is_never_extended_regardless_of_vehicle_capacity():
    tiers = [{"occupancy": 1, "price": 30.0}]
    result = _extend_tiers_for_multi_vehicle_pricing(tiers, price_by_pax=True, vehicle_capacity=7)
    assert result == tiers


def test_empty_tiers_returns_empty():
    assert _extend_tiers_for_multi_vehicle_pricing([], price_by_pax=False, vehicle_capacity=7) == []
