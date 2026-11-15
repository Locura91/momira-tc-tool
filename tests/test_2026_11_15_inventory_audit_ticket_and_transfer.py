"""
Inventory audit extended to Tickets and Transfers (2026-11-15).

The audit deliberately applies DIFFERENT rules per product type, so the main thing these
tests pin is that the rules do not leak into each other — above all that transport's
"must end 2049-12-31" rule is NOT applied to ticket modalities, whose seasonal end dates
are correct by design. Applying it there would flag every ticket in the account, which is
precisely why the product owner was asked rather than assumed.

Product-owner answers (2026-11-15), which these tests encode:
  Ticket   → expired / expiring-soon pricing, and missing prices or inactive modalities.
  Transfer → endDate not matching the expected house date, and missing reverse direction.
  Expected end date is CONFIGURABLE, not hardcoded: "we will in the future add the code
  for the 'real' price range ... the same as Transport 20271231 for example if the product
  finished 31 of dec of 2027".
"""
import pytest

from flows.inventory_audit import HOUSE_END_DATE, audit_ticket, audit_transfer

TODAY = "2026-11-15"
SOON = "2027-01-14"  # 60 days out


def _mod(code="M1", end=None, adult=50.0, active=True, occ=None):
    m = {"code": code, "baseAdultPrice": adult, "active": active}
    if end is not None:
        m["endDate"] = end
    if occ is not None:
        m["occupancyPrices"] = occ
    return m


def _ticket(code="TK1", name="Bali Eco Walk", active=True):
    return {"code": code, "name": name, "active": active}


# ── Ticket: seasonal end dates are NOT an error ──────────────────────────────

def test_future_seasonal_end_date_is_not_flagged():
    """The whole reason tickets get their own rules: a modality ending well in the future
    is a normal season, not a wrong 'house date'."""
    r = audit_ticket(_ticket(), [_mod(end="2027-09-30")], TODAY, SOON)
    assert r["ok"], r["issues"]
    assert r["expired"] == []
    assert r["expiring"] == []


def test_ticket_end_date_other_than_house_date_is_not_flagged():
    """Explicit guard against someone 'unifying' the rules later: a ticket modality that
    does not end on 2049-12-31 is correct, unlike a transport inventory."""
    r = audit_ticket(_ticket(), [_mod(end="2027-06-30")], TODAY, SOON)
    assert r["ok"]
    assert not any(HOUSE_END_DATE in i for i in r["issues"])


def test_open_ended_modality_with_no_end_date_is_not_flagged():
    r = audit_ticket(_ticket(), [_mod(end=None)], TODAY, SOON)
    assert r["ok"], r["issues"]


# ── Ticket: expiry ───────────────────────────────────────────────────────────

def test_expired_modality_is_flagged_as_expired():
    r = audit_ticket(_ticket(), [_mod(end="2026-10-01")], TODAY, SOON)
    assert not r["ok"]
    assert r["expired"] == [{"modality_code": "M1", "end": "2026-10-01"}]
    assert r["expiring"] == []
    assert any("EXPIRED" in i for i in r["issues"])


def test_modality_expiring_within_window_is_flagged_as_expiring_not_expired():
    r = audit_ticket(_ticket(), [_mod(end="2026-12-01")], TODAY, SOON)
    assert not r["ok"]
    assert r["expiring"] == [{"modality_code": "M1", "end": "2026-12-01"}]
    assert r["expired"] == []


def test_modality_ending_exactly_on_cutoff_counts_as_expiring():
    r = audit_ticket(_ticket(), [_mod(end=SOON)], TODAY, SOON)
    assert r["expiring"] and not r["expired"]


def test_modality_ending_just_past_cutoff_is_clean():
    r = audit_ticket(_ticket(), [_mod(end="2027-01-15")], TODAY, SOON)
    assert r["ok"], r["issues"]


# ── Ticket: prices and inactive modalities ───────────────────────────────────

@pytest.mark.parametrize("adult", [0, 0.0, None])
def test_modality_with_no_usable_price_is_flagged(adult):
    r = audit_ticket(_ticket(), [_mod(adult=adult)], TODAY, SOON)
    assert not r["ok"]
    assert any("no usable price" in i for i in r["issues"])


def test_occupancy_priced_modality_counts_as_having_a_price():
    """Priced per occupancy instead of a flat adult price — not a missing price."""
    r = audit_ticket(_ticket(),
                     [_mod(adult=0, occ=[{"amount": 80.0, "occupancy": 2}])],
                     TODAY, SOON)
    assert r["ok"], r["issues"]


def test_zero_amount_occupancy_prices_do_not_count_as_a_price():
    r = audit_ticket(_ticket(),
                     [_mod(adult=0, occ=[{"amount": 0, "occupancy": 2}])],
                     TODAY, SOON)
    assert not r["ok"]
    assert any("no usable price" in i for i in r["issues"])


def test_inactive_modality_under_live_ticket_is_flagged():
    r = audit_ticket(_ticket(active=True), [_mod(active=False)], TODAY, SOON)
    assert not r["ok"]
    assert any("inactive" in i for i in r["issues"])


def test_inactive_modality_under_inactive_ticket_is_not_flagged():
    """A retired ticket's retired modality is consistent, not a hole in availability."""
    r = audit_ticket(_ticket(active=False), [_mod(active=False)], TODAY, SOON)
    assert not any("inactive" in i for i in r["issues"])


def test_ticket_with_no_modalities_is_flagged():
    r = audit_ticket(_ticket(), [], TODAY, SOON)
    assert not r["ok"]
    assert any("no modalities" in i for i in r["issues"])


# ── Transfer: end date ───────────────────────────────────────────────────────

def _transfer(tid="T1", name="Airport → Hotel", end=HOUSE_END_DATE, active=True):
    t = {"id": tid, "name": name, "active": active}
    if end is not None:
        t["endDate"] = end
    return t


def test_transfer_on_house_date_is_clean():
    r = audit_transfer(_transfer(), HOUSE_END_DATE, TODAY, missing_reverse=False)
    assert r["ok"], r["issues"]
    assert r["end_date_fix"] is None


def test_transfer_with_wrong_end_date_is_flagged_and_fixable():
    r = audit_transfer(_transfer(end="2027-12-31"), HOUSE_END_DATE, TODAY,
                       missing_reverse=False)
    assert not r["ok"]
    assert r["end_date_fix"] == {"current_end": "2027-12-31"}


def test_expected_end_date_is_configurable_for_real_contract_end_dates():
    """Product owner: a product that genuinely finishes on 2027-12-31 should audit clean
    against that date. Hardcoding 2049-12-31 would make this impossible."""
    r = audit_transfer(_transfer(end="2027-12-31"), "2027-12-31", TODAY,
                       missing_reverse=False)
    assert r["ok"], r["issues"]


def test_past_end_date_is_reported_as_expired_not_merely_wrong():
    r = audit_transfer(_transfer(end="2026-01-01"), HOUSE_END_DATE, TODAY,
                       missing_reverse=False)
    assert not r["ok"]
    assert any("PAST" in i for i in r["issues"])
    assert r["end_date_fix"] is not None


def test_transfer_with_no_end_date_is_flagged():
    r = audit_transfer(_transfer(end=None), HOUSE_END_DATE, TODAY, missing_reverse=False)
    assert not r["ok"]
    assert any("No end date" in i for i in r["issues"])


# ── Transfer: reverse direction ──────────────────────────────────────────────

def test_missing_reverse_is_flagged_even_when_dates_are_correct():
    r = audit_transfer(_transfer(), HOUSE_END_DATE, TODAY, missing_reverse=True)
    assert not r["ok"]
    assert any("reverse-direction" in i for i in r["issues"])
    # Nothing to fix by changing a date — this one needs a new product created.
    assert r["end_date_fix"] is None


def test_both_problems_are_reported_together():
    r = audit_transfer(_transfer(end="2027-12-31"), HOUSE_END_DATE, TODAY,
                       missing_reverse=True)
    assert len(r["issues"]) == 2
