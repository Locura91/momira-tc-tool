"""Regression tests for the 2026-08-24 audit fixes.

Each test names the real defect it locks down. See `claude/audit-2026-08-24.md` in the project
for the full findings; the short version is in each test's docstring.
"""
import datetime
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import builder
import price_refresh
from test_builder_ticket import make_pre_config, minimal_ticket_data


class _FakeAPI:
    def __getattr__(self, name):
        return lambda *a, **k: {}


# ---------------------------------------------------------------------------
# A-1: Hotel crashed outright on cancellation tiers without summary text
# ---------------------------------------------------------------------------

def test_hotel_cancellation_tiers_do_not_crash_the_voucher_helper():
    """CONFIRMED CRASH: Hotel passed the extractor's RAW tier shape into
    _cancellation_voucher_text, which expects the converted (days, refund_pct) pairs. Iterating a
    dict yields its KEYS, so it computed 100.0 - "fee_percentage" -> TypeError. The call sits
    outside the builder's try/except and app.py's call site is unguarded, so a hotel with stated
    tiers but no prose summary crashed the app and could never be published."""
    tiers = builder._cancellation_ranges_from_tiers([{"days": 30, "fee_percentage": 25.0}])
    text = builder._cancellation_voucher_text("", tiers)
    assert text  # produced something rather than raising
    # The fee->refund inversion Hotel was silently skipping: 25% fee == 75% refund.
    assert "75" in text


def test_raw_tier_shape_would_still_be_wrong_so_conversion_is_required():
    """Guards the FIX rather than the symptom: if someone later reverts to passing raw tiers,
    this fails loudly instead of the app crashing in front of the operator."""
    with pytest.raises(TypeError):
        builder._cancellation_voucher_text("", [{"days": 30, "fee_percentage": 25.0}])


# ---------------------------------------------------------------------------
# B-2: expired documents produced an inverted, unbookable window
# ---------------------------------------------------------------------------

def test_fully_expired_document_is_blocked():
    """CONFIRMED: start_date_or_today floors a past START to today, but nothing floored or checked
    the END, so a 2025 rate sheet built startDate=today with endDate=2025-12-31 - inverted,
    permanently unbookable, published with no error. Product owner chose: block, tell the
    operator."""
    reason = builder.expired_validity_window("2025-01-01", "2025-12-31")
    assert reason and "2025-12-31" in reason


def test_inverted_window_is_blocked_even_when_not_expired():
    future = (datetime.date.today() + datetime.timedelta(days=400)).isoformat()
    nearer = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
    reason = builder.expired_validity_window(future, nearer)
    assert reason and "before it starts" in reason


def test_current_and_open_ended_documents_are_not_blocked():
    future = (datetime.date.today() + datetime.timedelta(days=365)).isoformat()
    assert builder.expired_validity_window(datetime.date.today().isoformat(), future) is None
    # Open-ended validity is normal and must not be mistaken for expired.
    assert builder.expired_validity_window("2020-01-01", "") is None
    assert builder.expired_validity_window("", "") is None


def test_unparseable_dates_are_not_guessed_at():
    """to_iso_date passes unrecognised text through unchanged. Such a value must be treated as
    'not stated' rather than compared as a string, which would block on arbitrary text."""
    assert builder.expired_validity_window("", "sometime in spring") is None


def test_expired_document_surfaces_on_the_built_ticket():
    data = minimal_ticket_data()
    data.update(start_date="2025-01-01", end_date="2025-12-31")
    result = builder.build_ticket_payloads(make_pre_config(), data, _FakeAPI())
    assert result["expired_validity_error"]


# ---------------------------------------------------------------------------
# B-3: one ticket published two contradictory versions of its own remarks
# ---------------------------------------------------------------------------

def test_datasheet_voucher_remarks_keep_packing_list_and_manual_notes():
    """CONFIRMED: the datasheet short-circuited as `voucher_remarks or <composed>`, so whenever
    voucher_remarks was set the packing list and standing notes were dropped. That was the COMMON
    case - extraction copies cancellation_policy_text into voucher_remarks for every ticket whose
    document states a policy - so the record published a full modality remark and a truncated
    customer-facing one."""
    data = minimal_ticket_data()
    data.update(cancellation_policy_text="Cancel: 50% within 14 days.",
                voucher_remarks="Cancel: 50% within 14 days.",
                what_to_bring="- Passport",
                manual_notes="NOTE: new terminal.")
    result = builder.build_ticket_payloads(make_pre_config(), data, _FakeAPI())
    remarks = result["main_ticket_payload"]["datasheets"]["EN"]["voucherRemarks"]
    assert "Cancel: 50% within 14 days." in remarks
    assert "Passport" in remarks           # was dropped
    assert "new terminal" in remarks       # was dropped


def test_a_humans_own_voucher_text_still_wins_as_the_base():
    """The composition must not trample a human's explicit text - it replaces the CANCELLATION
    default, and the appended blocks still follow it."""
    data = minimal_ticket_data()
    data.update(cancellation_policy_text="Auto-extracted policy.",
                voucher_remarks="HUMAN WROTE THIS.",
                what_to_bring="- Hat")
    result = builder.build_ticket_payloads(make_pre_config(), data, _FakeAPI())
    remarks = result["main_ticket_payload"]["datasheets"]["EN"]["voucherRemarks"]
    assert remarks.startswith("HUMAN WROTE THIS.")
    assert "Auto-extracted policy." not in remarks
    assert "Hat" in remarks


# ---------------------------------------------------------------------------
# B-8 / B-9: Ticket escaped the pax cap and published free inventory
# ---------------------------------------------------------------------------

def test_ticket_max_passengers_is_capped_at_the_platform_limit():
    """CONFIRMED: the 9-pax cap 'applies for all services' and every other product enforced it.
    Ticket published maxPassengers straight from a dropdown offering up to 20, while its price rows
    were capped at 9 - advertising a group size with no rate behind it."""
    result = builder.build_ticket_payloads(
        make_pre_config(max_passengers=15), minimal_ticket_data(), _FakeAPI())
    assert result["ticket_option_payload"]["maxPassengers"] == builder._MAX_OCCUPANCY_PAX


def test_zero_priced_occupancies_are_named_for_the_publish_gate():
    """CONFIRMED: the pricing editor materializes rows 1..cap defaulting to 0, so a document
    pricing only 1-4 pax left 5-9 bookable at 0.00. Hotel already hard-blocks this."""
    data = minimal_ticket_data(
        price_type="OCCUPANCY",
        occupancy_prices=[{"occupancy": 1, "amount": 100},
                          {"occupancy": 2, "amount": 0},
                          {"occupancy": 3, "amount": 0}])
    result = builder.build_ticket_payloads(make_pre_config(), data, _FakeAPI())
    assert result["zero_priced_occupancies"] == [2, 3]


def test_fully_priced_ticket_reports_no_zero_occupancies():
    data = minimal_ticket_data(
        price_type="OCCUPANCY",
        occupancy_prices=[{"occupancy": 1, "amount": 100}, {"occupancy": 2, "amount": 80}])
    result = builder.build_ticket_payloads(make_pre_config(), data, _FakeAPI())
    assert result["zero_priced_occupancies"] == []


# ---------------------------------------------------------------------------
# B-1: one failed GET silently repriced every other modality
# ---------------------------------------------------------------------------

def _route_with_a_failed_option():
    return {
        "kind": "transport", "id": "T1", "name": "Aswan - Hurghada",
        "raw": {"baseAdultPrice": 100.0, "baseChildrenPrice": 50.0},
        "options": [
            {"code": "WIDE", "min_pax": 2, "max_pax": 9, "unit_price": 100.0, "raw": {"prices": []}},
            {"code": "SOLO", "fetch_failed": True},
        ],
    }


def test_rebuild_refuses_a_route_whose_options_could_not_all_be_read():
    """CONFIRMED: rebuild_prices derives ONE base from the WIDEST bracket and expresses every other
    option as a supplement against it. Computing that base from the SURVIVING options meant a
    single transient GET failure (GETs are never retried) left the unread option's old supplement
    against a NEW base - a price nobody chose - and, if the failed option WAS the widest, repriced
    the whole transport from a narrower bracket. Child/infant prices scale by the same ratio."""
    out = price_refresh.rebuild_prices(_route_with_a_failed_option(), {"WIDE": 120.0})
    assert out["transport"] is None
    assert out.get("blocked")


def test_a_partially_read_route_is_flagged_and_never_pre_accepted():
    route = _route_with_a_failed_option()
    proposals = price_refresh.build_proposals(
        [route], {0: {"found": True, "brackets": [], "confidence": "high",
                      "note": "", "matched_row": "", "minimum_pax": 1, "currency": ""}})
    assert proposals[0]["status"] == "blocked_unreadable"
    assert proposals[0]["accepted"] is False
    assert "SOLO" in proposals[0]["unreadable_options"]


def test_a_fully_readable_route_still_rebuilds_normally():
    """The guard must not block the normal path."""
    route = _route_with_a_failed_option()
    route["options"] = [o for o in route["options"] if not o.get("fetch_failed")]
    out = price_refresh.rebuild_prices(route, {"WIDE": 120.0})
    assert out["transport"] is not None
    assert out["transport"]["baseAdultPrice"] == 120.0
    # Child price moves with the adult price rather than silently changing the discount.
    assert out["transport"]["baseChildrenPrice"] == 60.0
