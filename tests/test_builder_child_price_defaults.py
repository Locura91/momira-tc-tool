"""Tests for builder.transfer_tier_child_price and builder.transport_base_child_price
(full-app audit HIGH, 2026-09-01).

CONFIRMED HOUSE RULE: child_price=None means "the document didn't state a separate child rate
for this occupancy/bracket" (as opposed to 0, which means "the document says children are
free here") - already correctly implemented for Tickets. This used to collapse into
TransferMoneyVO's/baseChildrenPrice's own 0.0 default, publishing every undocumented Transfer/
Transport child rate as free instead of the adult rate. Infant pricing is deliberately NOT
covered by this rule - an unstated infant price staying free is correct, by convention.

Tests the pure helpers directly - no geolocation/network needed, same approach as
test_2026_08_28_transfer_transport_images.py.
"""
from builder import transfer_tier_child_price, transport_base_child_price


# --- Transfer: transfer_tier_child_price ---

def test_transfer_stated_child_price_is_used_even_when_zero():
    # 0 is a real, explicit "children are free" statement - must be honored, not overridden.
    assert transfer_tier_child_price({"price": 50.0, "child_price": 0}) == 0


def test_transfer_stated_nonzero_child_price_is_used():
    assert transfer_tier_child_price({"price": 50.0, "child_price": 35.0}) == 35.0


def test_transfer_unstated_child_price_defaults_to_the_adult_rate_not_free():
    assert transfer_tier_child_price({"price": 50.0}) == 50.0


def test_transfer_unstated_child_price_and_missing_adult_price_defaults_to_zero():
    assert transfer_tier_child_price({}) == 0


# --- Transport: transport_base_child_price ---

def test_transport_stated_child_price_is_used_even_when_zero():
    assert transport_base_child_price({"child_price": 0}, base_price=100.0) == 0


def test_transport_stated_nonzero_child_price_is_used():
    assert transport_base_child_price({"child_price": 60.0}, base_price=100.0) == 60.0


def test_transport_unstated_child_price_defaults_to_the_adult_base_price_not_free():
    assert transport_base_child_price({}, base_price=100.0) == 100.0


def test_transport_no_base_bracket_defaults_to_the_adult_base_price():
    assert transport_base_child_price(None, base_price=100.0) == 100.0
