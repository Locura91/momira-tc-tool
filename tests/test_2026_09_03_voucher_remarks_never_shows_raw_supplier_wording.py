"""Regression test for a real customer-facing bug (product owner, 2026-09-03):

    "why is following information seen in the condition for the client: Voucher Remarks (shown to
    the customer, includes what to bring) More than 24 hours before the excursion: no
    cancellation fee. Less than 24 hours before the excursion: 100% of the excursion price
    charged. Note: cancellations due to sea sickness are not refunded and are charged at the full
    excursion price. --> in this case we must write more like nothing regarding the conditions,
    because our rules with 30 days or prior are better for our company"

Root cause: extract_ticket_data/extract_ticket_main_info used to seed voucher_remarks (the
customer-facing field) directly from the source's raw cancellation_policy_text, verbatim -
including any extra raw clauses the supplier attached (like a "no refund for sea sickness" note).
Because builder.build_ticket_payloads' ticket-voucher composition uses voucher_remarks AS-IS
whenever it's non-empty (voucher_remarks "wins as the BASE text if set"), that raw copy completely
bypassed builder._cancellation_voucher_text's own (already-fixed, same day - see
test_2026_09_03_cancellation_voucher_text_uses_floored_tiers.py) floored-tiers-first logic. Same
underlying bug as that earlier fix, reached through the seeding path instead of the fallback path.

Fix: seed voucher_remarks through that SAME shared, already-correct function, so a document's raw,
more lenient supplier wording is never shown to the customer - only the floored tiers (matching
what's actually enforced), or nothing (default_text="") if no tiers exist, leaving builder's own
publish-time fallback (Momira's standing 30-day/100%-refund default) to apply.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_extractor as ax

MODULE_BUILD = "2026-09-03-modality-code-slash-sanitize-not-reject"

RAW_SUPPLIER_TEXT = (
    "More than 24 hours before the excursion: no cancellation fee. Less than 24 hours before the "
    "excursion: 100% of the excursion price charged. Note: cancellations due to sea sickness are "
    "not refunded and are charged at the full excursion price."
)
# {"days": D, "fee_percentage": F} means "cancel at least D days before arrival -> (100-F)%
# refund" (see builder._cancellation_ranges_from_tiers). This is the ALREADY-FLOORED shape Momira
# actually enforces: 30+ days out -> fee_percentage 0 (100% refund); under 30 days -> fee_percentage
# 100 (0% refund/full charge) - i.e. the "30 days or prior for 100%" house rule.
FLOORED_TIERS = [{"days": 30, "fee_percentage": 0.0}, {"days": 0, "fee_percentage": 100.0}]


def _fake_extraction_result(**overrides):
    result = {
        "cancellation_policy_text": RAW_SUPPLIER_TEXT,
        "cancellation_policy_tiers": FLOORED_TIERS,
        "voucher_remarks": "",
    }
    result.update(overrides)
    return result


# ======================================================================
# extract_ticket_main_info - the batch/primary Ticket creation flow (the exact screen the
# report's screenshot came from: "Reviewing ticket X of Y")
# ======================================================================
def test_main_info_never_seeds_voucher_remarks_with_the_raw_sea_sickness_clause(monkeypatch):
    monkeypatch.setattr(ax, "_call_claude", lambda *a, **k: _fake_extraction_result())
    data = ax.extract_ticket_main_info("some raw document text")
    assert "sea sickness" not in data["voucher_remarks"]
    assert "24 hour" not in data["voucher_remarks"]


def test_main_info_seeds_voucher_remarks_from_the_floored_tiers_instead(monkeypatch):
    monkeypatch.setattr(ax, "_call_claude", lambda *a, **k: _fake_extraction_result())
    data = ax.extract_ticket_main_info("some raw document text")
    assert "30 days" in data["voucher_remarks"]


def test_main_info_never_overwrites_a_human_or_ai_supplied_voucher_remarks(monkeypatch):
    monkeypatch.setattr(
        ax, "_call_claude",
        lambda *a, **k: _fake_extraction_result(voucher_remarks="Please arrive 15 minutes early."))
    data = ax.extract_ticket_main_info("some raw document text")
    assert data["voucher_remarks"] == "Please arrive 15 minutes early."


def test_main_info_leaves_voucher_remarks_blank_when_nothing_at_all_is_stated(monkeypatch):
    monkeypatch.setattr(
        ax, "_call_claude",
        lambda *a, **k: _fake_extraction_result(cancellation_policy_text="", cancellation_policy_tiers=[]))
    data = ax.extract_ticket_main_info("some raw document text")
    assert data["voucher_remarks"] == ""


# ======================================================================
# extract_ticket_data - the legacy/combined single-ticket extraction path
# ======================================================================
def test_extract_ticket_data_never_seeds_voucher_remarks_with_the_raw_sea_sickness_clause(monkeypatch):
    monkeypatch.setattr(ax, "_call_claude", lambda *a, **k: _fake_extraction_result())
    data = ax.extract_ticket_data("some raw document text")
    assert "sea sickness" not in data["voucher_remarks"]
    assert "24 hour" not in data["voucher_remarks"]


def test_extract_ticket_data_seeds_voucher_remarks_from_the_floored_tiers_instead(monkeypatch):
    monkeypatch.setattr(ax, "_call_claude", lambda *a, **k: _fake_extraction_result())
    data = ax.extract_ticket_data("some raw document text")
    assert "30 days" in data["voucher_remarks"]
