"""
The bracketed validity code in product names (2026-11-15).

PRODUCT-OWNER EXPLANATION, verbatim: "the code that we will add in the future in the
brackets with the year, month, and date, which looks like a random number for any person
who doesn't know, will be added for the transfers and the transports for sure. Also, might
be for the tickets, but this is not sure. And this is just because a transfer and transport
are in the system until 2049."

WHY THIS MATTERS TO THE AUDIT: a transfer's endDate is deliberately 2049-12-31 and should
stay that way, so endDate can never tell you when the pricing actually stops being valid.
That date is written into the product name instead. An audit reading only endDate would
report every transfer as healthy right up to the day its real pricing ran out — which is
exactly the failure the audit exists to prevent.

The code is being introduced gradually and may never apply to tickets, so its ABSENCE must
never be reported as a problem. These tests pin both halves: the code is honoured when
present, and ignored when not.
"""
import pytest

from flows.inventory_audit import HOUSE_END_DATE, audit_ticket, audit_transfer, parse_validity_code

TODAY = "2026-11-15"
SOON = "2027-01-14"


# ── parsing ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("Airport → Hotel (20271231)", "2027-12-31"),
    ("Airport → Hotel (2027-12-31)", "2027-12-31"),
    ("Airport → Hotel (2027.12.31)", "2027-12-31"),
    ("Airport → Hotel (2027/12/31)", "2027-12-31"),
    ("(20271231) leading code", "2027-12-31"),
])
def test_validity_code_is_parsed(name, expected):
    assert parse_validity_code(name) == expected


@pytest.mark.parametrize("name", [
    "Airport → Hotel",
    "",
    None,
    "Private Transfer 4 pax",
    "Tour (Deluxe)",
    "Minivan (max 8)",
])
def test_no_code_returns_none(name):
    """Most products have no code yet, and a bracketed word is not a date."""
    assert parse_validity_code(name) is None


@pytest.mark.parametrize("name", [
    "Airport → Hotel (20271345)",   # month 13, day 45
    "Airport → Hotel (20270230)",   # 30 February
])
def test_malformed_code_is_reported_not_guessed(name):
    """A code that is present but impossible is worse than no code — it looks authoritative
    while being wrong, so it is surfaced rather than silently skipped."""
    assert parse_validity_code(name) is False


# ── transfers: endDate stays 2049, the code carries the truth ────────────────

def _transfer(name, end=HOUSE_END_DATE, tid="T1"):
    return {"id": tid, "name": name, "endDate": end, "active": True}


def test_transfer_with_correct_end_date_and_no_code_is_clean():
    r = audit_transfer(_transfer("Airport → Hotel"), HOUSE_END_DATE, TODAY,
                       missing_reverse=False, soon_cutoff=SOON)
    assert r["ok"], r["issues"]


def test_transfer_with_future_code_is_clean():
    r = audit_transfer(_transfer("Airport → Hotel (20281231)"), HOUSE_END_DATE, TODAY,
                       missing_reverse=False, soon_cutoff=SOON)
    assert r["ok"], r["issues"]
    assert r["validity_end"] == "2028-12-31"


def test_expired_code_is_flagged_even_though_end_date_is_the_house_date():
    """The case the whole feature exists for: endDate says 2049 and looks perfect, but the
    pricing actually stopped being valid last year."""
    r = audit_transfer(_transfer("Airport → Hotel (20251231)"), HOUSE_END_DATE, TODAY,
                       missing_reverse=False, soon_cutoff=SOON)
    assert not r["ok"]
    assert r["expired"] == [{"end": "2025-12-31"}]
    assert any("EXPIRED" in i for i in r["issues"])
    # The house end date itself is still correct, so nothing should be "fixed" about it.
    assert r["end_date_fix"] is None


def test_code_expiring_within_window_is_flagged_as_expiring():
    r = audit_transfer(_transfer("Airport → Hotel (20261201)"), HOUSE_END_DATE, TODAY,
                       missing_reverse=False, soon_cutoff=SOON)
    assert r["expiring"] == [{"end": "2026-12-01"}]
    assert r["expired"] == []


def test_malformed_code_on_a_transfer_is_surfaced():
    r = audit_transfer(_transfer("Airport → Hotel (20271345)"), HOUSE_END_DATE, TODAY,
                       missing_reverse=False, soon_cutoff=SOON)
    assert not r["ok"]
    assert any("not a real date" in i for i in r["issues"])


def test_wrong_end_date_and_expired_code_are_reported_separately():
    """Two independent problems; neither should mask the other."""
    r = audit_transfer(_transfer("Airport → Hotel (20251231)", end="2030-01-01"),
                       HOUSE_END_DATE, TODAY, missing_reverse=False, soon_cutoff=SOON)
    assert len(r["issues"]) == 2
    assert r["end_date_fix"] is not None
    assert r["expired"]


def test_soon_cutoff_omitted_still_reports_expiry():
    """Callers that pass no window must still catch an already-expired code."""
    r = audit_transfer(_transfer("Airport → Hotel (20251231)"), HOUSE_END_DATE, TODAY,
                       missing_reverse=False)
    assert r["expired"]


# ── tickets: the code is optional and may never arrive ───────────────────────

def _mod(end="2027-06-30"):
    return {"code": "M1", "baseAdultPrice": 50.0, "active": True, "endDate": end}


def test_ticket_without_a_code_is_unaffected():
    r = audit_ticket({"code": "TK1", "name": "Bali Eco Walk", "active": True},
                     [_mod()], TODAY, SOON)
    assert r["ok"], r["issues"]


def test_ticket_with_expired_code_is_flagged():
    r = audit_ticket({"code": "TK1", "name": "Bali Eco Walk (20251231)", "active": True},
                     [_mod()], TODAY, SOON)
    assert not r["ok"]
    assert any("EXPIRED" in i for i in r["issues"])


def test_ticket_with_future_code_stays_clean():
    r = audit_ticket({"code": "TK1", "name": "Bali Eco Walk (20281231)", "active": True},
                     [_mod()], TODAY, SOON)
    assert r["ok"], r["issues"]
