"""
Widening the bulk actions to every product type the API supports (2026-11-15).

PRODUCT-OWNER REQUEST, verbatim: "every option that we have for the bulk actions, we shall
check by the API if we can widen it or extend it to other products if not happened so far."

The audit of docs/tc_api_reference.md found two gaps, both closed here:

  Bulk images    covered Transfer + Transport, but an image field exists on all five:
                 ContractTicketVO.imageUrls, ContractClosedTourVO.images,
                 ContractHotelDetailedVO.images. Added via product_image_bulk.py.

  Inventory audit covered Transport + Ticket + Transfer. ClosedTour price lists and Hotel
                 rate seasons both carry date ranges that can expire or overlap, so both
                 are auditable. Added via audit_closed_tour / audit_hotel.

The other three were already at the API's limit and are pinned here so a later "why isn't
Transport in this one?" does not get answered by adding a field that does not exist:
stopSales and supplements exist on exactly four types each (never Transport), and
cancellationRanges exists on only three (Transfer and Hotel have no structured field, so
the cancellation flow changes voucher text for those two instead).
"""
import re

import pytest

import product_image_bulk as pib
from flows.inventory_audit import audit_closed_tour, audit_hotel, find_overlaps

TODAY = "2026-11-15"
SOON = "2027-01-14"


# ── the capability matrix this work was derived from ─────────────────────────

def _fields_of(vo_name):
    txt = open("docs/tc_api_reference.md").read()
    m = re.search(r"\n### `" + vo_name + r"`.*?(?=\n### )", txt, re.S)
    assert m, f"{vo_name} not found in the API reference"
    return set(re.findall(r"^\| `([A-Za-z]+)`", m.group(0), re.M))


@pytest.mark.parametrize("vo,field", [
    ("ContractTicketVO", "imageUrls"),
    ("ContractClosedTourVO", "images"),
    ("ContractHotelDetailedVO", "images"),
])
def test_image_field_exists_for_each_newly_added_type(vo, field):
    """The premise of extending bulk images to these three."""
    assert field in _fields_of(vo)


@pytest.mark.parametrize("vo", ["ContractTransportVO", "ContractTransportOptionVO"])
def test_transport_still_has_no_stopsales_or_supplements(vo):
    """Pins why stop sales and supplements stop at four types rather than five."""
    fields = _fields_of(vo)
    assert "stopSales" not in fields
    assert "supplements" not in fields


def test_image_specs_cover_the_three_types_added():
    assert set(pib.PRODUCT_SPECS) == {"Ticket", "ClosedTour", "Hotel"}
    assert pib.PRODUCT_SPECS["Ticket"]["image_field"] == "imageUrls"
    assert pib.PRODUCT_SPECS["ClosedTour"]["image_field"] == "images"
    assert pib.PRODUCT_SPECS["Hotel"]["image_field"] == "images"
    # Hotels are keyed on providerCode, the others on code — getting this wrong sends the
    # PUT to the wrong record.
    assert pib.PRODUCT_SPECS["Hotel"]["id_field"] == "providerCode"
    assert pib.PRODUCT_SPECS["Ticket"]["id_field"] == "code"


# ── overlap detection ────────────────────────────────────────────────────────

def test_adjacent_ranges_do_not_overlap():
    assert find_overlaps([("2027-01-01", "2027-03-31", "A"),
                          ("2027-04-01", "2027-06-30", "B")]) == []


def test_ranges_sharing_a_single_day_overlap():
    """Both endpoints are inclusive, so a shared boundary day is priced twice."""
    assert find_overlaps([("2027-01-01", "2027-04-01", "A"),
                          ("2027-04-01", "2027-06-30", "B")]) == [("A", "B")]


def test_fully_contained_range_overlaps():
    assert find_overlaps([("2027-01-01", "2027-12-31", "Year"),
                          ("2027-06-01", "2027-06-30", "June")]) == [("Year", "June")]


def test_ranges_missing_a_bound_are_ignored_not_crashed():
    assert find_overlaps([("2027-01-01", "", "A"), ("", "2027-06-30", "B")]) == []


# ── ClosedTour audit ─────────────────────────────────────────────────────────

def _opt(code="STD", price_list=None):
    return {"code": code, "priceList": price_list if price_list is not None else []}


def _pl(start, end, name=None):
    d = {"startDate": start, "endDate": end}
    if name:
        d["name"] = name
    return d


def _tour(code="CT1", name="Bali 5D"):
    return {"code": code, "name": name}


def test_non_overlapping_future_seasons_are_clean():
    opt = _opt(price_list=[_pl("2027-01-01", "2027-03-31", "Low"),
                           _pl("2027-04-01", "2027-09-30", "High")])
    r = audit_closed_tour(_tour(), [opt], TODAY, SOON)
    assert r["ok"], r["issues"]


def test_overlapping_seasons_are_flagged():
    """CLAUDE.md: 'Date ranges within one modality must NEVER overlap.' Nothing in the API
    enforces this, so an overlap can sit in a live tour until a booking prices wrong."""
    opt = _opt(price_list=[_pl("2027-01-01", "2027-06-30", "Low"),
                           _pl("2027-05-01", "2027-09-30", "High")])
    r = audit_closed_tour(_tour(), [opt], TODAY, SOON)
    assert not r["ok"]
    assert any("OVERLAP" in i for i in r["issues"])


def test_closed_tour_with_all_pricing_in_the_past_is_expired():
    opt = _opt(price_list=[_pl("2025-01-01", "2025-12-31")])
    r = audit_closed_tour(_tour(), [opt], TODAY, SOON)
    assert r["expired"] and not r["expiring"]


def test_closed_tour_pricing_running_out_soon_is_expiring():
    opt = _opt(price_list=[_pl("2026-01-01", "2026-12-01")])
    r = audit_closed_tour(_tour(), [opt], TODAY, SOON)
    assert r["expiring"] and not r["expired"]


def test_modality_with_empty_price_list_is_flagged():
    r = audit_closed_tour(_tour(), [_opt(price_list=[])], TODAY, SOON)
    assert not r["ok"]
    assert any("no price list" in i for i in r["issues"])


def test_closed_tour_with_no_modalities_is_flagged():
    r = audit_closed_tour(_tour(), [], TODAY, SOON)
    assert not r["ok"]
    assert any("no modalities" in i for i in r["issues"])


# ── Hotel audit ──────────────────────────────────────────────────────────────

def _season(name, ranges):
    return {"name": name, "dateRanges": [{"start": s, "end": e} for s, e in ranges]}


def _hotel(rates, code="CAI-H1", name="Nile View"):
    return {"providerCode": code, "name": name, "rates": rates}


def test_hotel_with_clean_future_seasons_is_ok():
    h = _hotel([{"name": "Standard",
                 "seasons": [_season("Low", [("2027-01-01", "2027-03-31")]),
                             _season("High", [("2027-04-01", "2027-09-30")])]}])
    r = audit_hotel(h, TODAY, SOON)
    assert r["ok"], r["issues"]


def test_split_season_with_two_ranges_is_not_an_overlap_with_itself():
    """A single season legitimately covers e.g. spring and autumn as two ranges. Comparing
    those two against each other must not report the season as overlapping itself."""
    h = _hotel([{"name": "Standard",
                 "seasons": [_season("Shoulder", [("2027-03-01", "2027-04-30"),
                                                  ("2027-09-01", "2027-10-31")])]}])
    r = audit_hotel(h, TODAY, SOON)
    assert r["ok"], r["issues"]


def test_two_different_seasons_overlapping_is_flagged():
    h = _hotel([{"name": "Standard",
                 "seasons": [_season("Low", [("2027-01-01", "2027-06-30")]),
                             _season("High", [("2027-05-01", "2027-09-30")])]}])
    r = audit_hotel(h, TODAY, SOON)
    assert not r["ok"]
    assert any("OVERLAP" in i for i in r["issues"])


def test_hotel_with_all_seasons_past_is_expired():
    h = _hotel([{"name": "Standard",
                 "seasons": [_season("Old", [("2025-01-01", "2025-12-31")])]}])
    r = audit_hotel(h, TODAY, SOON)
    assert r["expired"]


def test_hotel_with_no_rates_is_flagged():
    r = audit_hotel(_hotel([]), TODAY, SOON)
    assert not r["ok"]
    assert any("no rates" in i for i in r["issues"])


def test_rate_with_no_seasons_is_flagged():
    r = audit_hotel(_hotel([{"name": "Standard", "seasons": []}]), TODAY, SOON)
    assert not r["ok"]
    assert any("no seasons" in i for i in r["issues"])
