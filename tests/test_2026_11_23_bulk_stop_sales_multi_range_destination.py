"""Bulk Stop Sales: multiple ranges, destination filter, and yearly recurrence.

CONFIRMED REAL REQUESTS (operator, 2026-11-23):
- Multiple stop-sale ranges in one apply (e.g. Nyepi + Christmas in one trip).
- Destination filter after supplier + product type: 'Koh Lipe' matches Pier,
  Harbour, Airport all at once (substring, case-insensitive).
- Yearly repeat: a seasonal route (closed May-Oct) needs the same block for the
  next 10 years, and a public holiday (Nyepi) needs the same single day for N
  years. One input, N stop-sale records.

Pins the pure-logic helpers so a Streamlit-less test run can exercise them."""

from datetime import date

from flows.bulk_stop_sales import (
    _expand_yearly,
    _extract_cities_from_name,
    _product_matches_destinations,
)


# ── destination filter ─────────────────────────────────────────────────────────

def test_extracts_cities_from_hyphen_separated_name():
    """A 'from CITY A - CITY B' name yields both endpoints."""
    name = "One-way transfer from Hat Yai Airport (HDY) - Koh Lipe Harbour"
    cities = _extract_cities_from_name(name)
    assert "Hat Yai Airport" in cities
    assert "Koh Lipe Harbour" in cities


def test_extracts_cities_strips_transport_prefix():
    """The 'One-way transfer from' prefix must not become part of a city name.
    Real TC product names use ' - ' as the city separator, not 'to'."""
    name = "One-way transfer from Bangkok - Phuket"
    cities = _extract_cities_from_name(name)
    assert "Bangkok" in cities
    assert "Phuket" in cities
    assert not any(c.lower().startswith("one-way") for c in cities)
    assert not any(c.lower().startswith("transfer") for c in cities)


def test_koh_lipe_matches_pier_harbour_airport():
    """The operator's own real test case: picking 'Koh Lipe' must hit every variant."""
    products = [
        {"name": "One-way transfer from Hat Yai Airport (HDY) - Koh Lipe Harbour"},
        {"name": "One-way transfer from Pak Bara Pier - Koh Lipe Pier"},
        {"name": "One-way transfer from Krabi Airport - Koh Lipe Pier"},
        {"name": "One-way transfer from Phuket Airport - Phuket Hotel"},  # unrelated
    ]
    selected = ["Koh Lipe"]
    hits = [p for p in products if _product_matches_destinations(p, selected)]
    assert len(hits) == 3, f"expected 3 Koh Lipe matches, got {[p['name'] for p in hits]}"


def test_empty_filter_matches_everything():
    """No destination selected = show all products (not filter them all out)."""
    products = [{"name": "foo"}, {"name": "bar"}]
    assert all(_product_matches_destinations(p, []) for p in products)


def test_destination_match_is_case_insensitive():
    products = [{"name": "Transfer from Bangkok to PATTAYA"}]
    assert _product_matches_destinations(products[0], ["pattaya"])
    assert _product_matches_destinations(products[0], ["bangkok"])


# ── yearly recurrence ──────────────────────────────────────────────────────────

def test_single_year_returns_one_range():
    """years=1 is a no-op: just the original range, one entry."""
    out = _expand_yearly(date(2027, 5, 1), date(2027, 10, 31), 1)
    assert out == [{"start": "2027-05-01", "end": "2027-10-31"}]


def test_ten_years_returns_ten_consecutive_ranges():
    """CONFIRMED (operator): Phuket-Koh Lipe closed May-Oct for next 10 years."""
    out = _expand_yearly(date(2027, 5, 1), date(2027, 10, 31), 10)
    assert len(out) == 10
    assert out[0] == {"start": "2027-05-01", "end": "2027-10-31"}
    assert out[9] == {"start": "2036-05-01", "end": "2036-10-31"}


def test_single_day_holiday_repeats_yearly():
    """Nyepi in Indonesia: one day per year, all stop sales."""
    out = _expand_yearly(date(2027, 3, 17), date(2027, 3, 17), 5)
    assert len(out) == 5
    assert out[0]["start"] == out[0]["end"] == "2027-03-17"
    assert out[4]["start"] == out[4]["end"] == "2031-03-17"


def test_feb_29_clamps_to_feb_28_on_non_leap_year():
    """Feb 29 can only exist on leap years - shift forward by 1 year lands on Feb 28
    rather than raising or silently skipping the year, so the 10-year block stays
    intact for a leap-day stop sale."""
    out = _expand_yearly(date(2028, 2, 29), date(2028, 2, 29), 3)  # 2028 is a leap year
    assert len(out) == 3
    assert out[0] == {"start": "2028-02-29", "end": "2028-02-29"}
    # 2029 is NOT a leap year - Feb 29 clamps to Feb 28
    assert out[1] == {"start": "2029-02-28", "end": "2029-02-28"}
    assert out[2] == {"start": "2030-02-28", "end": "2030-02-28"}


def test_zero_or_negative_years_falls_back_to_one():
    """Defensive: a 0 or negative years argument must not produce an empty list."""
    assert len(_expand_yearly(date(2027, 1, 1), date(2027, 1, 2), 0)) == 1
    assert len(_expand_yearly(date(2027, 1, 1), date(2027, 1, 2), -5)) == 1
