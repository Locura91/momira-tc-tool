"""Unit tests for builder._with_what_to_bring and its end-to-end effect on voucher remarks.

CONFIRMED HOUSE RULE (product owner, 2026-08-24): "If the document or the URL states something
like: Please remember to bring: Passports, Sun Cream, pocket-torch, Tissue, Hat - we should also
mention this at the voucher remarks, as this information is great information for the customer."

Applied via a single shared helper used by every product builder, the same rollout pattern as
_cancellation_voucher_text, so it can't be wired into some products and forgotten on others.
"""
import pytest

from builder import _with_what_to_bring, build_closed_tour_payloads

# fake_api_client is a shared fixture from tests/conftest.py - no import needed.
from test_builder_closed_tour import make_pre_config, minimal_extracted_data


PACKING_LIST = "- Passports\n- Sun cream\n- Pocket torch\n- Tissues\n- Hat"


def test_nothing_stated_leaves_the_voucher_text_untouched():
    assert _with_what_to_bring("Cancellation: 30 days.", {}) == "Cancellation: 30 days."
    assert _with_what_to_bring("Cancellation: 30 days.", {"what_to_bring": ""}) == "Cancellation: 30 days."
    assert _with_what_to_bring("Cancellation: 30 days.", {"what_to_bring": "   "}) == "Cancellation: 30 days."
    assert _with_what_to_bring("Cancellation: 30 days.", None) == "Cancellation: 30 days."


def test_a_stated_list_is_appended_under_a_heading():
    out = _with_what_to_bring("Cancellation: 30 days.", {"what_to_bring": PACKING_LIST})
    assert out.startswith("Cancellation: 30 days.")
    assert "What to bring:" in out
    assert "- Passports" in out
    assert "- Hat" in out


def test_it_appends_rather_than_replacing_the_cancellation_text():
    """The packing list is EXTRA customer information, never a substitute for the cancellation
    policy - losing a document-stated cancellation policy would be silent, customer-visible data
    loss (the exact failure _cancellation_voucher_text exists to prevent)."""
    out = _with_what_to_bring("Cancellation: 30 days before arrival, 100% fee.",
                              {"what_to_bring": PACKING_LIST})
    assert "Cancellation: 30 days before arrival, 100% fee." in out
    assert "- Sun cream" in out


def test_it_works_when_there_is_no_cancellation_text_at_all():
    assert _with_what_to_bring("", {"what_to_bring": PACKING_LIST}) == f"What to bring:\n{PACKING_LIST}"
    assert _with_what_to_bring(None, {"what_to_bring": PACKING_LIST}) == f"What to bring:\n{PACKING_LIST}"


def test_closed_tour_voucher_remarks_carry_the_packing_list(fake_api_client):
    """End-to-end: a document's packing list must actually reach the published voucher field."""
    result = build_closed_tour_payloads(
        make_pre_config(),
        minimal_extracted_data(what_to_bring=PACKING_LIST),
        fake_api_client)
    voucher = result["main_tour_payload"]["datasheets"]["EN"]["voucherRemarks"]
    assert "What to bring:" in voucher
    assert "- Passports" in voucher
    assert "- Hat" in voucher


def test_closed_tour_voucher_keeps_cancellation_and_packing_list_together(fake_api_client):
    result = build_closed_tour_payloads(
        make_pre_config(),
        minimal_extracted_data(
            what_to_bring=PACKING_LIST,
            cancellation_policy_text="Cancellation Policy:\n- 45 days to check-in: 100% fee"),
        fake_api_client)
    voucher = result["main_tour_payload"]["datasheets"]["EN"]["voucherRemarks"]
    assert "100% fee" in voucher
    assert "What to bring:" in voucher
    # Cancellation first, packing list after - see _with_what_to_bring's docstring on ordering.
    assert voucher.index("100% fee") < voucher.index("What to bring:")


def test_manual_notes_stay_last_after_the_packing_list(fake_api_client):
    """_with_manual_notes' own rule: a human's note is the most recent and most specific
    information, so it stays at the very end even once a packing list is in between."""
    result = build_closed_tour_payloads(
        make_pre_config(),
        minimal_extracted_data(what_to_bring=PACKING_LIST,
                               manual_notes="Pickup moved to the new terminal."),
        fake_api_client)
    voucher = result["main_tour_payload"]["datasheets"]["EN"]["voucherRemarks"]
    assert voucher.index("What to bring:") < voucher.index("Pickup moved to the new terminal.")


def test_no_packing_list_produces_no_stray_heading(fake_api_client):
    result = build_closed_tour_payloads(make_pre_config(), minimal_extracted_data(), fake_api_client)
    voucher = result["main_tour_payload"]["datasheets"]["EN"]["voucherRemarks"]
    assert "What to bring:" not in voucher
