"""Tests for cancellation_links.py - the reusable "linked" cancellation policies feature
(2026-08-28, product-owner request: "can I also update existing Cancellation Fees from
provider?" -> "we might have to add a link for the Cancellation fees, not divided per
Supplier, rather... for a whole product type, like Transfers" -> confirmed decision: BOTH
scopes allowed (supplier+type AND type-wide), supplier-specific wins when both exist, and
the document's own stated terms always win over a link.

Uses the same offline platform_store isolation every other durable-storage test relies on
(see conftest.py: PLATFORM_STORE_PATH is a fresh temp SQLite file, no DATABASE_URL).
"""
import cancellation_links as cl


def _reset(product_type, supplier_id=None):
    """Tests share one SQLite file for the whole run (conftest sets PLATFORM_STORE_PATH
    once) - clear out anything a previous test in this file left behind for the same key,
    so tests don't leak state into each other."""
    cl.set_type_link(product_type, [])
    if supplier_id:
        cl.set_supplier_link(supplier_id, product_type, [])


def test_a_supplier_scoped_link_round_trips():
    _reset("Transfer", "SUP-A")
    assert cl.get_supplier_link("SUP-A", "Transfer") is None
    ok = cl.set_supplier_link("SUP-A", "Transfer", [{"days": 30, "fee_percentage": 0}, {"days": 7, "fee_percentage": 50}])
    assert ok is True
    stored = cl.get_supplier_link("SUP-A", "Transfer")
    assert stored["tiers"] == [{"days": 30, "fee_percentage": 0.0}, {"days": 7, "fee_percentage": 50.0}]


def test_a_type_scoped_link_round_trips():
    _reset("Ticket")
    assert cl.get_type_link("Ticket") is None
    ok = cl.set_type_link("Ticket", [{"days": 14, "fee_percentage": 100}])
    assert ok is True
    stored = cl.get_type_link("Ticket")
    assert stored["tiers"] == [{"days": 14, "fee_percentage": 100.0}]


def test_saving_empty_tiers_clears_an_existing_link_rather_than_storing_an_empty_row():
    _reset("Hotel", "SUP-B")
    cl.set_supplier_link("SUP-B", "Hotel", [{"days": 10, "fee_percentage": 25}])
    assert cl.get_supplier_link("SUP-B", "Hotel") is not None
    cl.set_supplier_link("SUP-B", "Hotel", [])
    assert cl.get_supplier_link("SUP-B", "Hotel") is None


def test_tiers_are_sorted_furthest_out_first_same_as_the_review_screen_editor():
    _reset("Transport", "SUP-C")
    cl.set_supplier_link("SUP-C", "Transport", [{"days": 7, "fee_percentage": 50}, {"days": 60, "fee_percentage": 0}])
    tiers = cl.get_supplier_link("SUP-C", "Transport")["tiers"]
    assert [t["days"] for t in tiers] == [60, 7]


def test_fee_percentage_is_clamped_to_0_100_same_as_the_review_screen_editor():
    _reset("ClosedTour", "SUP-D")
    cl.set_supplier_link("SUP-D", "ClosedTour", [{"days": 30, "fee_percentage": 250}, {"days": 5, "fee_percentage": -10}])
    tiers = cl.get_supplier_link("SUP-D", "ClosedTour")["tiers"]
    by_days = {t["days"]: t["fee_percentage"] for t in tiers}
    assert by_days[30] == 100.0
    assert by_days[5] == 0.0


def test_a_row_with_no_days_value_is_dropped_not_stored_as_a_broken_tier():
    _reset("Transfer", "SUP-E")
    cl.set_supplier_link("SUP-E", "Transfer", [{"days": None, "fee_percentage": 50}, {"days": 30, "fee_percentage": 10}])
    tiers = cl.get_supplier_link("SUP-E", "Transfer")["tiers"]
    assert tiers == [{"days": 30, "fee_percentage": 10.0}]


def test_resolve_returns_none_none_when_no_link_is_saved_at_either_scope():
    _reset("Transfer", "SUP-NONE")
    tiers, scope = cl.resolve_cancellation_link("SUP-NONE", "Transfer")
    assert tiers is None
    assert scope is None


def test_resolve_falls_back_to_the_type_wide_link_when_no_supplier_specific_one_exists():
    _reset("Transfer", "SUP-F")
    cl.set_type_link("Transfer", [{"days": 30, "fee_percentage": 100}])
    tiers, scope = cl.resolve_cancellation_link("SUP-F", "Transfer")
    assert tiers == [{"days": 30, "fee_percentage": 100.0}]
    assert "company-wide" in scope
    assert "Transfer" in scope


def test_resolve_prefers_the_supplier_specific_link_over_the_type_wide_one():
    """CONFIRMED RULE (product owner, 2026-08-28): when both scopes are set for the same
    supplier + product type, the more specific supplier link wins."""
    _reset("Transfer", "SUP-G")
    cl.set_type_link("Transfer", [{"days": 30, "fee_percentage": 100}])
    cl.set_supplier_link("SUP-G", "Transfer", [{"days": 14, "fee_percentage": 50}])
    tiers, scope = cl.resolve_cancellation_link("SUP-G", "Transfer")
    assert tiers == [{"days": 14, "fee_percentage": 50.0}]
    assert "this supplier" in scope


def test_resolve_does_not_leak_across_different_suppliers_or_product_types():
    _reset("Transfer", "SUP-H")
    _reset("Transport", "SUP-H")
    cl.set_supplier_link("SUP-H", "Transfer", [{"days": 30, "fee_percentage": 100}])
    # Same supplier, different product type - must not see the Transfer link.
    tiers, scope = cl.resolve_cancellation_link("SUP-H", "Transport")
    assert tiers is None
    # Different supplier, same product type - must not see SUP-H's link either.
    tiers2, scope2 = cl.resolve_cancellation_link("SUP-OTHER", "Transfer")
    assert tiers2 is None


def test_apply_default_fills_in_empty_tiers_from_a_saved_link():
    _reset("Ticket", "SUP-I")
    cl.set_supplier_link("SUP-I", "Ticket", [{"days": 21, "fee_percentage": 75}])
    data = {"cancellation_policy_tiers": []}
    scope = cl.apply_cancellation_link_default(data, "SUP-I", "Ticket")
    assert data["cancellation_policy_tiers"] == [{"days": 21, "fee_percentage": 75.0}]
    assert scope is not None
    assert data["_cancellation_link_scope"] == scope


def test_apply_default_never_overwrites_terms_the_document_already_stated():
    """CONFIRMED RULE (product owner, 2026-08-28): 'Document wins when present' - this is
    the core safety property of the whole feature. A link is a fallback for silence, never
    an override of what the supplier's own document actually says."""
    _reset("Ticket", "SUP-J")
    cl.set_supplier_link("SUP-J", "Ticket", [{"days": 21, "fee_percentage": 75}])
    data = {"cancellation_policy_tiers": [{"days": 45, "fee_percentage": 10}]}
    scope = cl.apply_cancellation_link_default(data, "SUP-J", "Ticket")
    assert scope is None
    assert data["cancellation_policy_tiers"] == [{"days": 45, "fee_percentage": 10}]


def test_apply_default_does_nothing_when_no_link_is_saved():
    _reset("Hotel", "SUP-K")
    data = {"cancellation_policy_tiers": []}
    scope = cl.apply_cancellation_link_default(data, "SUP-K", "Hotel")
    assert scope is None
    assert data.get("cancellation_policy_tiers") == []
    assert "_cancellation_link_scope" not in data


def test_apply_default_treats_a_missing_key_the_same_as_an_empty_list():
    """Baseline dicts built from a live GET response (_map_fetched_ticket_to_data /
    _map_fetched_tour_to_data) never set this key at all - must be treated as empty, not
    skipped because the key is absent."""
    _reset("Transfer", "SUP-L")
    cl.set_type_link("Transfer", [{"days": 30, "fee_percentage": 100}])
    data = {"ticket_name": "Some transfer"}
    scope = cl.apply_cancellation_link_default(data, "SUP-L", "Transfer")
    assert scope is not None
    assert data["cancellation_policy_tiers"] == [{"days": 30, "fee_percentage": 100.0}]


def test_apply_default_returns_a_fresh_copy_not_a_reference_to_the_stored_tiers():
    """A human editing the review screen's table must not silently mutate the saved link -
    each application should hand back its own copy."""
    _reset("Hotel", "SUP-M")
    cl.set_supplier_link("SUP-M", "Hotel", [{"days": 30, "fee_percentage": 100}])
    data = {"cancellation_policy_tiers": []}
    cl.apply_cancellation_link_default(data, "SUP-M", "Hotel")
    data["cancellation_policy_tiers"][0]["fee_percentage"] = 0
    stored = cl.get_supplier_link("SUP-M", "Hotel")
    assert stored["tiers"][0]["fee_percentage"] == 100.0


def test_list_links_reports_both_scopes_with_correct_labels():
    _reset("Transfer", "SUP-N")
    cl.set_supplier_link("SUP-N", "Transfer", [{"days": 30, "fee_percentage": 100}])
    cl.set_type_link("Transfer", [{"days": 14, "fee_percentage": 50}])
    rows = cl.list_links()
    transfer_rows = [r for r in rows if r["product_type"] == "Transfer"]
    scopes = {r["scope"] for r in transfer_rows}
    assert "Supplier" in scopes
    assert "All suppliers (product type)" in scopes
    supplier_row = next(r for r in transfer_rows if r["scope"] == "Supplier" and r["supplier_id"] == "SUP-N")
    assert supplier_row["tiers"] == [{"days": 30, "fee_percentage": 100.0}]
