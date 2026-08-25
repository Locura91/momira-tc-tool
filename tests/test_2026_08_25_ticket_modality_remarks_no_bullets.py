"""Regression tests for a real product-owner request (2026-08-25): "please in the Remarks of
Modality within Ticket, no Bullet points."

ticket_cancellation_voucher_text (build_ticket_payloads, builder.py) is composed from several
pieces that deliberately DO use bullet/list markers - the entrance-fee notice is explicitly meant
to be "displayed as a bullet point" on the customer-facing Voucher Remarks (see
_with_entrance_fee_notice), and a synthesized cancellation policy lists one "- " line per tier
(see _cancellation_voucher_text). Those markers stay exactly as they are on the Voucher Remarks
field (TicketDatasheetEN.voucherRemarks). Only the Ticket Modality's OWN separate Remarks field
(ContractTicketModalityVO.remarks - Travel Compositor's per-modality "Condition"/Remarks screen)
now gets a plain-line version via builder._strip_bullet_points, applied only at that one call site
in build_ticket_payloads.
"""
import builder
from builder import _strip_bullet_points
from test_builder_ticket import make_pre_config, minimal_ticket_data


# ---------------------------------------------------------------------------
# _strip_bullet_points itself
# ---------------------------------------------------------------------------

def test_strips_a_leading_bullet_character():
    assert _strip_bullet_points("• Entrance fees are NOT included in this price.") == (
        "Entrance fees are NOT included in this price.")


def test_strips_a_leading_dash_marker_per_line():
    text = "Cancellation Policy:\n- Free cancellation if cancelled at least 30 days before arrival.\n- No refund for cancellations on the day of arrival or no-shows."
    result = _strip_bullet_points(text)
    assert "\n- " not in ("\n" + result)
    assert "Free cancellation if cancelled at least 30 days before arrival." in result
    assert "No refund for cancellations on the day of arrival or no-shows." in result


def test_strips_a_leading_asterisk_marker():
    assert _strip_bullet_points("* Some note") == "Some note"


def test_does_not_touch_a_dash_in_the_middle_of_a_line():
    """A hyphen that isn't a list marker (a date range, a number range) must survive untouched -
    only a marker at the very start of the (whitespace-trimmed) line, followed by a space, is a
    bullet."""
    assert _strip_bullet_points("Groups of 3-5 people get a discount.") == (
        "Groups of 3-5 people get a discount.")


def test_preserves_leading_whitespace_before_the_stripped_marker():
    assert _strip_bullet_points("  • Indented bullet") == "  Indented bullet"


def test_multiline_text_strips_every_line_independently():
    text = "• Entrance fees are NOT included in this price.\n\nCancellation Policy:\n- Free cancellation."
    result = _strip_bullet_points(text)
    assert result == "Entrance fees are NOT included in this price.\n\nCancellation Policy:\nFree cancellation."


def test_empty_and_none_pass_through_unchanged():
    assert _strip_bullet_points("") == ""
    assert _strip_bullet_points(None) is None


def test_text_with_no_bullets_is_unchanged():
    text = "Plain remarks with no markers at all."
    assert _strip_bullet_points(text) == text


# ---------------------------------------------------------------------------
# build_ticket_payloads: only the Modality's own Remarks field is affected
# ---------------------------------------------------------------------------

def test_modality_remarks_has_no_bullet_even_when_voucher_remarks_does(fake_api_client):
    data = minimal_ticket_data()
    data["entrance_fees_excluded"] = True
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    voucher_remarks = result["main_ticket_payload"]["datasheets"]["EN"]["voucherRemarks"]
    modality_remarks = result["ticket_option_payload"]["remarks"]["EN"]["remarks"]
    assert voucher_remarks.startswith("•")
    assert "•" not in modality_remarks
    assert "\n- " not in ("\n" + modality_remarks)


def test_modality_remarks_strips_synthesized_cancellation_tier_dashes(fake_api_client):
    data = minimal_ticket_data(cancellation_policy_tiers=[{"days": 30, "fee_percentage": 100}])
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    modality_remarks = result["ticket_option_payload"]["remarks"]["EN"]["remarks"]
    assert "\n- " not in ("\n" + modality_remarks)
    assert "No refund" in modality_remarks or "cancellation fee" in modality_remarks.lower()


def test_voucher_remarks_and_modality_remarks_carry_the_same_content_bullets_aside(fake_api_client):
    data = minimal_ticket_data()
    data["entrance_fees_excluded"] = True
    result = builder.build_ticket_payloads(make_pre_config(), data, fake_api_client)
    voucher_remarks = result["main_ticket_payload"]["datasheets"]["EN"]["voucherRemarks"]
    modality_remarks = result["ticket_option_payload"]["remarks"]["EN"]["remarks"]
    # Stripping the leading "• " from voucher_remarks should match modality_remarks exactly -
    # confirms this is the SAME underlying text, only the marker differs.
    assert voucher_remarks[2:] == modality_remarks
