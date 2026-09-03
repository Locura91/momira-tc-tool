"""Unit tests for builder._with_what_to_bring/format_what_to_bring_line and their end-to-end
effect on voucher remarks.

CONFIRMED HOUSE RULE (product owner, 2026-08-24): "If the document or the URL states something
like: Please remember to bring: Passports, Sun Cream, pocket-torch, Tissue, Hat - we should also
mention this at the voucher remarks, as this information is great information for the customer."

Applied via a single shared helper used by every product builder, the same rollout pattern as
_cancellation_voucher_text, so it can't be wired into some products and forgotten on others.

UPDATED (product owner, 2026-09-03): "if mentioned What to bring and we include them in the
Voucher remark, just write it like this: 'What to bring: Example 1, Example 2 etc'" - replacing
the previous multi-line "What to bring:\n- Item\n- Item" block with one comma-separated line.
format_what_to_bring_line is the single shared formatter behind this (used by both
_with_what_to_bring here and ui_components.merge_what_to_bring_into_voucher_remarks).
"""
import pytest

from builder import _with_what_to_bring, format_what_to_bring_line, build_closed_tour_payloads

# fake_api_client is a shared fixture from tests/conftest.py - no import needed.
from test_builder_closed_tour import make_pre_config, minimal_extracted_data


PACKING_LIST = "- Passports\n- Sun cream\n- Pocket torch\n- Tissues\n- Hat"
PACKING_LINE = "What to bring: Passports, Sun cream, Pocket torch, Tissues, Hat"


# ======================================================================
# format_what_to_bring_line
# ======================================================================
def test_empty_or_blank_input_returns_empty_string():
    assert format_what_to_bring_line("") == ""
    assert format_what_to_bring_line("   ") == ""
    assert format_what_to_bring_line(None) == ""


def test_dashed_newline_list_becomes_one_comma_separated_line():
    assert format_what_to_bring_line(PACKING_LIST) == PACKING_LINE


def test_free_text_with_no_bullets_passes_through_on_one_line():
    assert format_what_to_bring_line("Passports and sun cream") == \
        "What to bring: Passports and sun cream"


def test_blank_lines_between_items_are_dropped():
    assert format_what_to_bring_line("- Passports\n\n- Hat\n") == "What to bring: Passports, Hat"


# ======================================================================
# _with_what_to_bring
# ======================================================================
def test_nothing_stated_leaves_the_voucher_text_untouched():
    assert _with_what_to_bring("Cancellation: 30 days.", {}) == "Cancellation: 30 days."
    assert _with_what_to_bring("Cancellation: 30 days.", {"what_to_bring": ""}) == "Cancellation: 30 days."
    assert _with_what_to_bring("Cancellation: 30 days.", {"what_to_bring": "   "}) == "Cancellation: 30 days."
    assert _with_what_to_bring("Cancellation: 30 days.", None) == "Cancellation: 30 days."


def test_a_stated_list_is_appended_as_one_comma_separated_line():
    out = _with_what_to_bring("Cancellation: 30 days.", {"what_to_bring": PACKING_LIST})
    assert out.startswith("Cancellation: 30 days.")
    assert PACKING_LINE in out
    # Never the old multi-line bulleted form.
    assert "- Passports" not in out
    assert "\n- " not in out


def test_it_appends_rather_than_replacing_the_cancellation_text():
    """The packing list is EXTRA customer information, never a substitute for the cancellation
    policy - losing a document-stated cancellation policy would be silent, customer-visible data
    loss (the exact failure _cancellation_voucher_text exists to prevent)."""
    out = _with_what_to_bring("Cancellation: 30 days before arrival, 100% fee.",
                              {"what_to_bring": PACKING_LIST})
    assert "Cancellation: 30 days before arrival, 100% fee." in out
    assert "Sun cream" in out


def test_it_works_when_there_is_no_cancellation_text_at_all():
    assert _with_what_to_bring("", {"what_to_bring": PACKING_LIST}) == PACKING_LINE
    assert _with_what_to_bring(None, {"what_to_bring": PACKING_LIST}) == PACKING_LINE


def test_closed_tour_voucher_remarks_carry_the_packing_list(fake_api_client):
    """End-to-end: a document's packing list must actually reach the published voucher field."""
    result = build_closed_tour_payloads(
        make_pre_config(),
        minimal_extracted_data(what_to_bring=PACKING_LIST),
        fake_api_client)
    voucher = result["main_tour_payload"]["datasheets"]["EN"]["voucherRemarks"]
    assert PACKING_LINE in voucher


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
