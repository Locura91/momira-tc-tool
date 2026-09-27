"""Regression tests for a real bug report (product owner, 2026-09-27), from a screenshot of a
ClosedTour Supplements grid showing "Required Holiday Dinner (24 or 31 December)" priced at 0 in
every column, sitting right above the existing "a supplement can never be 0 Euro" warning -
verbatim: "The required holiday dinner must be added in the voucher remark, as this supplement is
with no cost but it is important to know for the client. So if supplement has no costs, we must
add it to the voucher remark."

Before this fix, a genuinely free-but-important supplement was dropped at publish time with only
an INTERNAL review note (builder.build_supplement_vos' `notes` param, surfaced as
`supplement_occupancy_notes` on the review screen) - the client never saw it at all. This adds a
SECOND, customer-facing note (`voucher_notes`) that gets folded into the ClosedTour's own
voucherRemarks, mirroring the already-shipped park_fee_notes/Transfer location_notes pattern:
an informational note instead of a price.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from builder import build_supplement_vos, build_closed_tour_payloads
from schemas import HumanPreConfig


# ======================================================================
# build_supplement_vos - voucher_notes param
# ======================================================================
def test_zero_price_supplement_adds_a_customer_facing_voucher_note():
    voucher_notes = []
    vos = build_supplement_vos(
        [{"name": "Required Holiday Dinner (24 or 31 December)", "price": 0}],
        voucher_notes=voucher_notes)
    assert vos == []
    assert len(voucher_notes) == 1
    assert "Required Holiday Dinner (24 or 31 December)" in voucher_notes[0]
    assert "no extra charge" in voucher_notes[0]


def test_unnamed_zero_price_row_adds_no_voucher_note():
    voucher_notes = []
    build_supplement_vos([{"name": "", "price": 0}], voucher_notes=voucher_notes)
    assert voucher_notes == []


def test_paid_supplement_adds_no_voucher_note():
    voucher_notes = []
    vos = build_supplement_vos([{"name": "Balloon Ride", "price": 120}], voucher_notes=voucher_notes)
    assert len(vos) == 1
    assert voucher_notes == []


def test_voucher_notes_defaults_to_none_and_does_not_raise():
    # Existing callers that don't pass voucher_notes must keep working unchanged.
    assert build_supplement_vos([{"name": "Free water bottle", "price": 0}]) == []


def test_internal_notes_and_voucher_notes_are_independent():
    notes = []
    voucher_notes = []
    build_supplement_vos([{"name": "Free water bottle", "price": 0}],
                          notes=notes, voucher_notes=voucher_notes)
    assert len(notes) == 1 and "can never be 0 Euro" in notes[0]
    assert len(voucher_notes) == 1 and "no extra charge" in voucher_notes[0]


# ======================================================================
# build_closed_tour_payloads - free supplement -> voucherRemarks wiring
# ======================================================================
def make_pre_config(**overrides):
    defaults = dict(
        supplier_id="48940", provider_code="ASW-1", min_pax=1, max_pax=4,
        currency="THB", modality_code="STANDARD_CABIN", on_request=True,
    )
    defaults.update(overrides)
    return HumanPreConfig(**defaults)


def minimal_extracted_data(**overrides):
    data = {
        "tour_name": "Our Jungle Resorts",
        "tour_code": "TOUR-OJR-1",
        "description": "A lovely test resort stay.",
        "itinerary_destinations": ["Phuket", "Khao Sok"],
        "price_list": [{
            "startDate": "2026-11-01", "endDate": "2027-10-31",
            "price": {"singlePrice": {"amount": 22390}, "doublePrice": {"amount": 14520}},
        }],
        "supplements": [],
        "nights": 2,
    }
    data.update(overrides)
    return data


def test_free_supplement_note_lands_in_voucher_remarks(fake_api_client):
    result = build_closed_tour_payloads(
        make_pre_config(),
        minimal_extracted_data(supplements=[
            {"name": "Required Holiday Dinner (24 or 31 December)", "price": 0,
             "mandatory": True},
        ]),
        fake_api_client)
    voucher = result["main_tour_payload"]["datasheets"]["EN"]["voucherRemarks"]
    assert "Required Holiday Dinner (24 or 31 December)" in voucher
    assert "no extra charge" in voucher


def test_free_supplement_is_not_published_as_a_priced_supplement(fake_api_client):
    result = build_closed_tour_payloads(
        make_pre_config(),
        minimal_extracted_data(supplements=[
            {"name": "Required Holiday Dinner (24 or 31 December)", "price": 0,
             "mandatory": True},
        ]),
        fake_api_client)
    supplement_names = [
        s["translations"]["EN"]["name"]
        for s in result["main_tour_payload"].get("supplements", [])
    ]
    assert "Required Holiday Dinner (24 or 31 December)" not in supplement_names


def test_a_real_paid_supplement_alongside_a_free_one_still_publishes(fake_api_client):
    result = build_closed_tour_payloads(
        make_pre_config(),
        minimal_extracted_data(supplements=[
            {"name": "Required Holiday Dinner (24 or 31 December)", "price": 0, "mandatory": True},
            {"name": "Sunset Cruise", "price": 45},
        ]),
        fake_api_client)
    supplement_names = [
        s["translations"]["EN"]["name"]
        for s in result["main_tour_payload"].get("supplements", [])
    ]
    assert supplement_names == ["Sunset Cruise"]
    voucher = result["main_tour_payload"]["datasheets"]["EN"]["voucherRemarks"]
    assert "Required Holiday Dinner (24 or 31 December)" in voucher


def test_no_free_supplements_means_voucher_text_unaffected(fake_api_client):
    baseline = build_closed_tour_payloads(
        make_pre_config(), minimal_extracted_data(), fake_api_client)
    with_paid_only = build_closed_tour_payloads(
        make_pre_config(),
        minimal_extracted_data(supplements=[{"name": "Sunset Cruise", "price": 45}]),
        fake_api_client)
    baseline_voucher = baseline["main_tour_payload"]["datasheets"]["EN"]["voucherRemarks"]
    paid_voucher = with_paid_only["main_tour_payload"]["datasheets"]["EN"]["voucherRemarks"]
    assert baseline_voucher == paid_voucher
