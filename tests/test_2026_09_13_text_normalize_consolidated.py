"""Regression tests for consolidating name-normalization into text_normalize.py (2026-09-13,
product owner: "is there a chance we could merge some files... maybe we can find some double
written codes that could be combined").

Before this change, the same NFKC + whitespace-collapse + casefold normalization existed as
THREE independently-written copies:
  - hotel_matcher.py's `_norm()` (the original, hardened in the 2026-08-30 audit)
  - masterdata_matcher.py's `_norm()` (a byte-for-byte copy, whose own docstring said "deliberately
    consistent" - true only as long as nobody touched one without touching the other)
  - transfer_matcher.py's and transport_matcher.py's `_route_key()` nested `norm()` (a THIRD,
    weaker copy that never received the NFKC-Unicode fix at all - a smart quote, full-width
    character, or stray tab in a departure/arrival name could silently miss the app's own
    remembered route mapping, falling back to an unnecessary human-confirmation step)

These tests prove: (1) every module that used to define its own copy now shares
text_normalize.normalize_name, and (2) transfer_matcher.py/transport_matcher.py's route-key
normalization now actually has the NFKC fix, not just a shared file layout.
"""
import hotel_matcher
import masterdata_matcher
import text_normalize
import transfer_matcher
import transport_matcher


# ======================================================================
# 1. every module now points at the ONE shared implementation
# ======================================================================
def test_hotel_matcher_norm_is_the_shared_implementation():
    assert hotel_matcher._norm is text_normalize.normalize_name


def test_masterdata_matcher_norm_is_the_shared_implementation():
    assert masterdata_matcher._norm is text_normalize.normalize_name


# ======================================================================
# 2. the actual bug: transfer/transport _route_key used to use a WEAKER,
#    independently-written normalization with no NFKC Unicode handling.
# ======================================================================
def test_transfer_route_key_now_treats_a_full_width_character_variant_as_the_same_route():
    """Before this fix, transfer_matcher._route_key had its own local norm() with no NFKC
    normalization - a full-width 'Ａ' (U+FF21) vs a regular 'A' would produce two DIFFERENT
    keys for what a human would recognize as the same airport name, silently missing the
    remembered route mapping and forcing an unnecessary re-confirmation."""
    key_ascii = transfer_matcher._route_key("Cairo Airport", "Hurghada Airport")
    key_fullwidth = transfer_matcher._route_key("Cairo Airport", "Hurghada Airport")
    assert key_ascii == key_fullwidth  # sanity: identical input is always identical
    key_with_fullwidth_a = transfer_matcher._route_key("Ｃairo Airport", "Hurghada Airport")
    assert key_with_fullwidth_a == key_ascii


def test_transfer_route_key_collapses_internal_whitespace_like_hotel_matcher_does():
    key_clean = transfer_matcher._route_key("Cairo Airport", "Hurghada Airport")
    key_double_space = transfer_matcher._route_key("Cairo  Airport", "Hurghada Airport")
    key_tab = transfer_matcher._route_key("Cairo\tAirport", "Hurghada Airport")
    assert key_clean == key_double_space == key_tab


def test_transport_route_key_now_treats_a_full_width_character_variant_as_the_same_route():
    key_ascii = transport_matcher._route_key("Cairo Airport", "Hurghada Airport")
    key_with_fullwidth_a = transport_matcher._route_key("Ｃairo Airport", "Hurghada Airport")
    assert key_with_fullwidth_a == key_ascii


def test_transport_route_key_collapses_internal_whitespace():
    key_clean = transport_matcher._route_key("Cairo Airport", "Hurghada Airport")
    key_tab = transport_matcher._route_key("Cairo\tAirport", "Hurghada Airport")
    assert key_clean == key_tab


def test_transfer_and_transport_route_keys_still_differ_for_genuinely_different_routes():
    """The fix must not make _route_key overly permissive - two different routes still get
    different keys."""
    assert transfer_matcher._route_key("Cairo Airport", "Hurghada Airport") != \
        transfer_matcher._route_key("Cairo Airport", "Luxor Airport")


# ======================================================================
# 3. build stamps - transfer_matcher.py and transport_matcher.py never had one before
# ======================================================================
def test_transfer_matcher_now_carries_a_module_build_stamp():
    assert hasattr(transfer_matcher, "MODULE_BUILD")
    assert isinstance(transfer_matcher.MODULE_BUILD, str) and transfer_matcher.MODULE_BUILD


def test_transport_matcher_now_carries_a_module_build_stamp():
    assert hasattr(transport_matcher, "MODULE_BUILD")
    assert isinstance(transport_matcher.MODULE_BUILD, str) and transport_matcher.MODULE_BUILD
