"""Tests for the "minimum party size" solo-traveller price synthesis rule.

CONFIRMED REAL RULE (product owner): "when the document says min. 2 Pax, we can offer this
for 1 Pax by simply increasing the cost - meaning, 1 pax pays the price what 2 pax would pay
together." Already built and live for Transport (_add_minimum_charge_bracket); this test file
also covers Transfer's own version (_add_transfer_minimum_charge_tier), added because the rule
was confirmed missing there despite being requested for both product types.

These test the pure helper functions directly (no network/API calls needed) rather than the
full build_transfer_payload/build_transport_payloads pipelines, which require geolocation
resolution this test suite doesn't need to exercise here.
"""
from builder import _add_minimum_charge_bracket, _add_transfer_minimum_charge_tier


# ----------------------------------------------------------------------
# Transport (pre-existing rule, brackets keyed by min_occupancy/max_occupancy)
# ----------------------------------------------------------------------

def test_transport_synthesizes_solo_bracket_at_minimum_times_unit_price():
    brackets = [{"min_occupancy": 2, "max_occupancy": 9, "price": 90.0,
                "child_price": 45.0, "infant_price": None}]
    result = _add_minimum_charge_bracket(brackets, price_per_pax=True, min_billable_pax=2)
    assert result[0]["min_occupancy"] == 1
    assert result[0]["max_occupancy"] == 1
    assert result[0]["price"] == 180.0  # 90 * 2
    assert result[0]["child_price"] == 90.0  # 45 * 2
    assert result[0]["synthesized_minimum_charge"] is True
    assert result[1]["min_occupancy"] == 2  # original bracket untouched, still present


def test_transport_no_synthesis_when_already_bookable_from_one():
    brackets = [{"min_occupancy": 1, "max_occupancy": 9, "price": 90.0}]
    result = _add_minimum_charge_bracket(brackets, price_per_pax=True, min_billable_pax=1)
    assert result == brackets


def test_transport_no_synthesis_for_per_vehicle_pricing():
    brackets = [{"min_occupancy": 2, "max_occupancy": 9, "price": 90.0}]
    result = _add_minimum_charge_bracket(brackets, price_per_pax=False, min_billable_pax=2)
    assert result == brackets  # a per-vehicle rate costs the same regardless of headcount


def test_transport_no_synthesis_above_system_cap():
    brackets = [{"min_occupancy": 10, "max_occupancy": 14, "price": 90.0}]
    result = _add_minimum_charge_bracket(brackets, price_per_pax=True, min_billable_pax=10,
                                         max_cap=9)
    assert result == brackets


# ----------------------------------------------------------------------
# Transfer (the newly-added counterpart, tiers are single-occupancy rows)
# ----------------------------------------------------------------------

def test_transfer_synthesizes_solo_tier_at_minimum_times_unit_price():
    tiers = [{"occupancy": 2, "price": 30.0, "child_price": 15.0, "infant_price": None}]
    result = _add_transfer_minimum_charge_tier(tiers, price_per_pax=True, min_billable_pax=2)
    assert result[0]["occupancy"] == 1
    assert result[0]["price"] == 60.0  # 30 * 2 - the two-person total, product owner's example
    assert result[0]["child_price"] == 30.0
    assert result[0]["synthesized_minimum_charge"] is True
    assert result[1]["occupancy"] == 2  # original tier untouched, still present


def test_transfer_no_synthesis_when_document_already_prices_solo():
    tiers = [{"occupancy": 1, "price": 60.0}, {"occupancy": 2, "price": 30.0}]
    result = _add_transfer_minimum_charge_tier(tiers, price_per_pax=True, min_billable_pax=2)
    assert result == tiers  # nothing to synthesize - the supplier already prices 1 pax


def test_transfer_no_synthesis_when_no_minimum_stated():
    tiers = [{"occupancy": 1, "price": 30.0}]
    result = _add_transfer_minimum_charge_tier(tiers, price_per_pax=True, min_billable_pax=1)
    assert result == tiers


def test_transfer_no_synthesis_for_per_service_pricing():
    tiers = [{"occupancy": 2, "price": 30.0}]
    result = _add_transfer_minimum_charge_tier(tiers, price_per_pax=False, min_billable_pax=2)
    assert result == tiers


def test_transfer_no_synthesis_on_empty_tiers():
    assert _add_transfer_minimum_charge_tier([], price_per_pax=True, min_billable_pax=2) == []


def test_transfer_falls_back_to_lowest_tier_when_minimum_not_explicitly_priced():
    # min_billable_pax says 3, but the document's lowest actual tier is occupancy=2 - the
    # helper should still use SOME real document price as its base rather than crashing.
    tiers = [{"occupancy": 2, "price": 20.0}, {"occupancy": 4, "price": 15.0}]
    result = _add_transfer_minimum_charge_tier(tiers, price_per_pax=True, min_billable_pax=3)
    assert result[0]["occupancy"] == 1
    assert result[0]["price"] == 60.0  # falls back to tiers_sorted[0] (occupancy=2, price=20) * 3
