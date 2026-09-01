"""Tests for builder._extend_tiers_for_per_pax_pricing (full-app audit HIGH, 2026-09-01).

CONFIRMED BUG: per-pax Transfer/Transport rate sheets price real group discounts (e.g. 1 pax
pays EUR 20/person, 2-3 pax pay EUR 14/person) but only ever list the occupancies the source
document states. Any occupancy above the largest documented tier had no explicit
pricesByOccupancy entry, so Travel Compositor fell back to the top-level basePrice - set from
the SMALLEST (most expensive) occupancy's per-person rate - instead of the cheaper group rate.
Verified real example: 50% overcharge at 4 pax, 100% overcharge at 8 pax.

Tests the pure helper directly (no network/geolocation needed), same approach as
test_builder_multi_vehicle_pricing.py for its per-vehicle counterpart.
"""
from builder import _extend_tiers_for_per_pax_pricing, _MAX_OCCUPANCY_PAX


def test_per_vehicle_pricing_is_untouched_price_by_pax_false():
    tiers = [{"occupancy": 1, "price": 80.0}]
    assert _extend_tiers_for_per_pax_pricing(tiers, price_by_pax=False) == tiers


def test_no_tiers_returns_empty_unchanged():
    assert _extend_tiers_for_per_pax_pricing([], price_by_pax=True) == []


def test_fills_gap_above_largest_documented_tier_at_that_tiers_rate():
    tiers = [
        {"occupancy": 1, "price": 20.0},
        {"occupancy": 2, "price": 14.0},
    ]
    extended = _extend_tiers_for_per_pax_pricing(tiers, price_by_pax=True)
    by_occ = {t["occupancy"]: t["price"] for t in extended}
    # Original tiers preserved.
    assert by_occ[1] == 20.0
    assert by_occ[2] == 14.0
    # Every occupancy above the largest documented tier (2) up to the 9-pax cap carries the
    # SAME per-person rate as the largest tier (14.0) - never the more expensive solo rate.
    for occ in range(3, _MAX_OCCUPANCY_PAX + 1):
        assert by_occ[occ] == 14.0
    assert len(extended) == _MAX_OCCUPANCY_PAX


def test_verified_real_example_4_pax_no_longer_overcharged_50_percent():
    # Real verified example from the audit: 1 pax=20, 2-3 pax=14/person. A 4-pax booking with
    # no explicit tier used to fall back to basePrice=20 (the 1-pax solo rate) - a 50% overcharge
    # versus the correct group rate of 14.
    tiers = [
        {"occupancy": 1, "price": 20.0},
        {"occupancy": 3, "price": 14.0},
    ]
    extended = _extend_tiers_for_per_pax_pricing(tiers, price_by_pax=True)
    by_occ = {t["occupancy"]: t["price"] for t in extended}
    assert by_occ[4] == 14.0
    assert by_occ[8] == 14.0  # the 100%-overcharge case from the audit is also fixed


def test_synthesized_tiers_are_flagged():
    tiers = [{"occupancy": 2, "price": 14.0}]
    extended = _extend_tiers_for_per_pax_pricing(tiers, price_by_pax=True)
    synthesized = [t for t in extended if t.get("occupancy") != 2]
    assert synthesized  # at least one gap-fill tier was added
    assert all(t.get("synthesized_per_pax_gap_fill") is True for t in synthesized)
    # The original, real, document-stated tier is NOT flagged as synthesized.
    original = next(t for t in extended if t["occupancy"] == 2)
    assert "synthesized_per_pax_gap_fill" not in original


def test_child_and_infant_price_carried_forward_only_when_source_stated_them():
    tiers = [{"occupancy": 2, "price": 14.0, "child_price": 10.0}]
    extended = _extend_tiers_for_per_pax_pricing(tiers, price_by_pax=True)
    gap_filled = [t for t in extended if t["occupancy"] == 3][0]
    assert gap_filled["child_price"] == 10.0
    assert "infant_price" not in gap_filled  # never invented - source never gave one


def test_largest_tier_already_at_or_above_cap_returns_unchanged():
    tiers = [{"occupancy": _MAX_OCCUPANCY_PAX, "price": 5.0}]
    assert _extend_tiers_for_per_pax_pricing(tiers, price_by_pax=True) == tiers


def test_respects_a_custom_max_cap():
    tiers = [{"occupancy": 2, "price": 14.0}]
    extended = _extend_tiers_for_per_pax_pricing(tiers, price_by_pax=True, max_cap=4)
    occupancies = sorted(t["occupancy"] for t in extended)
    assert occupancies == [2, 3, 4]
