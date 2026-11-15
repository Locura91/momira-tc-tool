"""
The "(YYYYMMDD)" price-validity code, as read by the inventory audit (2026-11-15).

PRODUCT-OWNER EXPLANATION, verbatim: "the code that we will add in the future in the
brackets with the year, month, and date, which looks like a random number for any person
who doesn't know, will be added for the transfers and the transports for sure. Also, might
be for the tickets, but this is not sure. And this is just because a transfer and transport
are in the system until 2049."

WHY THIS MATTERS TO THE AUDIT: a transfer's endDate is deliberately 2049-12-31 and should
stay that way, so endDate can never say when the pricing actually stops being valid. An
audit reading only endDate would report every transfer as healthy right up to the day its
real pricing ran out — exactly the failure the audit exists to prevent.

CORRECTED SAME DAY, by the weekly duplicate audit: the first version of this parsed the
code out of the product NAME with its own regex. Both are wrong. price_validity.py has
owned this code since 2026-09-08 and stores it in VOUCHER REMARKS (Ticket/Transfer) or
DESCRIPTION (Transport) — Transport has no voucherRemarks field, settled by a 2026-09-11
revert after Travel Compositor silently dropped every write to it. Reading the name would
have found nothing on real data while reporting "no problems found". The audit now
delegates to price_validity.extract_price_validity_date, so these tests pin the delegation
and the real record shapes rather than a private regex.
"""
import pytest

from flows.inventory_audit import HOUSE_END_DATE, audit_ticket, audit_transfer, parse_validity_code

TODAY = "2026-11-15"
SOON = "2027-01-14"


def _with_remarks(text, **extra):
    """A Ticket/Transfer-shaped record carrying voucher remarks where the code really lives."""
    rec = {"id": "T1", "code": "T1", "name": "Airport → Hotel", "active": True,
           "datasheet": {"EN": {"voucherRemarks": text}}}
    rec.update(extra)
    return rec


# ── which field the code is read from ────────────────────────────────────────

def test_code_is_read_from_voucher_remarks():
    assert parse_validity_code(
        _with_remarks("Prices confirmed (20271231)"), "Transfer") == "2027-12-31"


def test_code_in_the_name_is_ignored():
    """The regression this file was rewritten for. The name is not where the code lives, so
    a code there must not be mistaken for a real one."""
    rec = {"id": "T1", "name": "Airport → Hotel (20251231)", "active": True}
    assert parse_validity_code(rec, "Transfer") is None


def test_transport_reads_the_code_from_description():
    """Transport has no voucherRemarks field at all (2026-09-11 revert, production
    evidence) — its code lives in the description instead."""
    rec = {"id": "TR1", "datasheet": {"EN": {"description": "Valid (20271231)"}}}
    assert parse_validity_code(rec, "Transport") == "2027-12-31"


def test_flat_voucher_remarks_shape_is_accepted():
    """Some list endpoints return a leaner record with the remarks at top level."""
    assert parse_validity_code(
        {"id": "T1", "voucherRemarks": "(20271231)"}, "Transfer") == "2027-12-31"


@pytest.mark.parametrize("rec", [
    {"id": "T1", "datasheet": {"EN": {"voucherRemarks": "no code here"}}},
    {"id": "T1", "datasheet": {"EN": {"voucherRemarks": ""}}},
    {"id": "T1"},
    {},
    None,
])
def test_no_code_returns_none(rec):
    """Most records carry no code yet, and that is never an error."""
    assert parse_validity_code(rec, "Transfer") is None


def test_implausible_number_in_brackets_is_not_a_code():
    """A parenthetical 8-digit number that is not a plausible date is something else — a
    booking reference, a phone number — and must not be read as a validity date."""
    assert parse_validity_code(
        _with_remarks("Booking ref (12345678)"), "Transfer") is None


def test_last_code_wins_when_a_record_carries_more_than_one():
    """A record hand-edited outside this app can carry a stale code; the most recently
    stated date is the one that counts."""
    assert parse_validity_code(
        _with_remarks("old (20251231) superseded (20281231)"), "Transfer") == "2028-12-31"


# ── transfers: endDate stays 2049, the code carries the truth ────────────────

def test_transfer_with_correct_end_date_and_no_code_is_clean():
    r = audit_transfer(_with_remarks("nothing special", endDate=HOUSE_END_DATE),
                       HOUSE_END_DATE, TODAY, missing_reverse=False, soon_cutoff=SOON)
    assert r["ok"], r["issues"]


def test_transfer_with_future_code_is_clean():
    r = audit_transfer(_with_remarks("(20281231)", endDate=HOUSE_END_DATE),
                       HOUSE_END_DATE, TODAY, missing_reverse=False, soon_cutoff=SOON)
    assert r["ok"], r["issues"]
    assert r["validity_end"] == "2028-12-31"


def test_expired_code_is_flagged_even_though_end_date_is_the_house_date():
    """The case the whole feature exists for: endDate says 2049 and looks perfect, but the
    pricing actually stopped being valid last year."""
    r = audit_transfer(_with_remarks("(20251231)", endDate=HOUSE_END_DATE),
                       HOUSE_END_DATE, TODAY, missing_reverse=False, soon_cutoff=SOON)
    assert not r["ok"]
    assert r["expired"] == [{"end": "2025-12-31"}]
    assert any("EXPIRED" in i for i in r["issues"])
    # The house end date itself is correct, so nothing should be "fixed" about it.
    assert r["end_date_fix"] is None


def test_code_expiring_within_window_is_flagged_as_expiring():
    r = audit_transfer(_with_remarks("(20261201)", endDate=HOUSE_END_DATE),
                       HOUSE_END_DATE, TODAY, missing_reverse=False, soon_cutoff=SOON)
    assert r["expiring"] == [{"end": "2026-12-01"}]
    assert r["expired"] == []


def test_wrong_end_date_and_expired_code_are_reported_separately():
    """Two independent problems; neither should mask the other."""
    r = audit_transfer(_with_remarks("(20251231)", endDate="2030-01-01"),
                       HOUSE_END_DATE, TODAY, missing_reverse=False, soon_cutoff=SOON)
    assert len(r["issues"]) == 2
    assert r["end_date_fix"] is not None
    assert r["expired"]


def test_soon_cutoff_omitted_still_reports_expiry():
    """Callers that pass no window must still catch an already-expired code."""
    r = audit_transfer(_with_remarks("(20251231)", endDate=HOUSE_END_DATE),
                       HOUSE_END_DATE, TODAY, missing_reverse=False)
    assert r["expired"]


# ── tickets: the code is optional and may never arrive ───────────────────────

def _mod(end="2027-06-30"):
    return {"code": "M1", "baseAdultPrice": 50.0, "active": True, "endDate": end}


def test_ticket_without_a_code_is_unaffected():
    r = audit_ticket(_with_remarks("no code"), [_mod()], TODAY, SOON)
    assert r["ok"], r["issues"]


def test_ticket_with_expired_code_is_flagged():
    r = audit_ticket(_with_remarks("(20251231)"), [_mod()], TODAY, SOON)
    assert not r["ok"]
    assert any("EXPIRED" in i for i in r["issues"])


def test_ticket_with_future_code_stays_clean():
    r = audit_ticket(_with_remarks("(20281231)"), [_mod()], TODAY, SOON)
    assert r["ok"], r["issues"]
