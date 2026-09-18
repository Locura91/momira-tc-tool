"""Regression tests for the "a supplement can never be 0 Euro" house rule (product owner,
2026-09-18, verbatim): "also, a supplement can never be 0 Euro. If so, than there is a mistake.
In short, supplement with 0 Euro cosyts does not exist and does not need to be included."

Confirmed via follow-up (AskUserQuestion) to:
  - apply across EVERY product type that has a Supplement concept: Hotel, ClosedTour, Ticket,
    Transfer ("All of them (Recommended)");
  - REPLACE the earlier "free supplement" concept entirely - 0 Euro always means a mistake, even
    when the source document calls it "free"/"complimentary" ("No more free supplements
    (Recommended)");
  - be handled as a non-blocking drop with a visible note, not a silent drop and not a hard
    publish block ("Drop it + show a note (Recommended)").

This covers the drop+note behavior at the builder level for all four product types, plus the
shared app_helpers.render_supplement_zero_price_notes display helper. builder.py's own
test_builder_supplements.py covers the ClosedTour case (build_supplement_vos) in more detail;
this file is the cross-product-type regression suite.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from builder import (
    build_supplement_vos,
    build_ticket_supplement_vos,
    build_transfer_supplement_vos,
    build_hotel_supplement_payloads,
)


# ---------------------------------------------------------------------------------------------
# ClosedTour (build_supplement_vos)
# ---------------------------------------------------------------------------------------------

def test_closedtour_zero_price_supplement_is_dropped_with_a_note():
    notes = []
    vos = build_supplement_vos([{"name": "Abu Simbel Excursion", "price": 0}], notes=notes)
    assert vos == []
    assert len(notes) == 1
    assert "Abu Simbel Excursion" in notes[0]


def test_closedtour_paid_supplement_still_publishes_alongside_a_dropped_one():
    notes = []
    vos = build_supplement_vos([
        {"name": "Free water bottle", "price": 0},
        {"name": "Balloon Ride", "price": 120},
    ], notes=notes)
    assert len(vos) == 1
    assert vos[0].translations["EN"].name == "Balloon Ride"
    assert len(notes) == 1 and "Free water bottle" in notes[0]


def test_closedtour_notes_defaults_to_none_and_does_not_raise():
    # Existing callers that don't pass `notes` must keep working unchanged.
    assert build_supplement_vos([{"name": "Free water bottle", "price": 0}]) == []


# ---------------------------------------------------------------------------------------------
# Ticket (build_ticket_supplement_vos)
# ---------------------------------------------------------------------------------------------

def test_ticket_zero_price_supplement_is_dropped_with_a_note():
    notes = []
    vos = build_ticket_supplement_vos([{
        "name": "Holiday surcharge", "adult_price_supplement": 0,
        "children_price_supplement": 0, "infant_price_supplement": 0,
    }], modality_start="2026-01-01", modality_end="2026-12-31", notes=notes)
    assert vos == []
    assert len(notes) == 1 and "Holiday surcharge" in notes[0]


def test_ticket_paid_supplement_still_publishes():
    vos = build_ticket_supplement_vos([{
        "name": "Peak season", "adult_price_supplement": 15,
    }], modality_start="2026-01-01", modality_end="2026-12-31")
    assert len(vos) == 1
    assert vos[0].adultPriceSupplement == 15.0


def test_ticket_zero_price_check_ignores_no_default_date_window():
    # Confirms the 0-Euro check runs BEFORE the date-window clipping logic, so a supplement
    # with no dates of its own is still dropped cleanly rather than raising.
    notes = []
    vos = build_ticket_supplement_vos([{"name": "Mystery fee"}], notes=notes)
    assert vos == []
    assert len(notes) == 1


# ---------------------------------------------------------------------------------------------
# Transfer (build_transfer_supplement_vos)
# ---------------------------------------------------------------------------------------------

def test_transfer_zero_price_supplement_is_dropped_with_a_note():
    notes = []
    out = build_transfer_supplement_vos([
        {"name": "Night surcharge", "amount": 0, "type": "PERCENT"},
    ], notes=notes)
    assert out == []
    assert len(notes) == 1 and "Night surcharge" in notes[0]


def test_transfer_paid_supplement_still_publishes():
    out = build_transfer_supplement_vos([
        {"name": "Night surcharge", "amount": 50, "type": "PERCENT"},
    ])
    assert len(out) == 1
    assert out[0].amount == 50.0


def test_transfer_unnamed_zero_amount_is_still_a_silent_drop_no_note():
    # Pre-existing behavior: an entry with no name at all was already silently skipped before
    # this rule existed (it's not a real supplement row) - the new note is only for a NAMED
    # supplement that comes out to 0 Euro, so this must not start appending a note for it.
    notes = []
    out = build_transfer_supplement_vos([{"name": "", "amount": 0}], notes=notes)
    assert out == []
    assert notes == []


# ---------------------------------------------------------------------------------------------
# Hotel (build_hotel_supplement_payloads)
# ---------------------------------------------------------------------------------------------

def test_hotel_zero_price_supplement_is_skipped_with_a_reason():
    supp_data = {
        "name": "Late checkout", "value": 0, "apply": "PER_STAY",
        "room_names": [], "meal_plans": [],
        "travel_windows": [{"start": "2026-01-01", "end": "2026-12-31"}],
    }
    results = build_hotel_supplement_payloads(
        [supp_data], room_name_to_provider_code={"Deluxe Room": "AUTO123"},
        hotel_meal_plan_types=["ROOM_ONLY"], hotel_provider_code="HRG-H1",
    )
    assert len(results) == 1
    assert results[0]["action"] == "skipped_zero_price"
    assert results[0]["supplement_payload"] is None
    assert results[0]["supplement_error"] is None
    assert "Late checkout" in results[0]["skip_reason"]


def test_hotel_paid_supplement_still_publishes():
    supp_data = {
        "name": "Airport transfer add-on", "value": 15, "apply": "PER_STAY",
        "room_names": [], "meal_plans": [],
        "travel_windows": [{"start": "2026-01-01", "end": "2026-12-31"}],
    }
    results = build_hotel_supplement_payloads(
        [supp_data], room_name_to_provider_code={"Deluxe Room": "AUTO123"},
        hotel_meal_plan_types=["ROOM_ONLY"], hotel_provider_code="HRG-H1",
    )
    assert results[0]["action"] == "create"
    assert results[0]["supplement_payload"] is not None


def test_hotel_zero_price_check_beats_the_no_charging_basis_check():
    # A supplement that is BOTH 0 Euro AND missing a charging basis must report the 0-Euro
    # skip, not the "no charging basis" error - the zero-price check runs first (see
    # build_hotel_supplement_payloads).
    supp_data = {
        "name": "Mystery add-on", "value": 0,
        "room_names": [], "meal_plans": [],
        "travel_windows": [{"start": "2026-01-01", "end": "2026-12-31"}],
    }
    results = build_hotel_supplement_payloads(
        [supp_data], room_name_to_provider_code={"Deluxe Room": "AUTO123"},
        hotel_meal_plan_types=["ROOM_ONLY"], hotel_provider_code="HRG-H1",
    )
    assert results[0]["action"] == "skipped_zero_price"


# ---------------------------------------------------------------------------------------------
# app_helpers.render_supplement_zero_price_notes
# ---------------------------------------------------------------------------------------------

def test_render_supplement_zero_price_notes_is_exported_from_app_helpers():
    from app_helpers import render_supplement_zero_price_notes
    assert callable(render_supplement_zero_price_notes)


def test_render_supplement_zero_price_notes_uses_a_custom_key(monkeypatch):
    import app_helpers
    warnings = []
    monkeypatch.setattr(app_helpers.st, "warning", lambda msg: warnings.append(msg))
    app_helpers.render_supplement_zero_price_notes(
        {"supplement_occupancy_notes": ["'Gala Dinner' had no price (0 Euro)..."]},
        key="supplement_occupancy_notes",
    )
    assert len(warnings) == 1
    assert "Gala Dinner" in warnings[0]


def test_render_supplement_zero_price_notes_default_key_and_empty_payloads(monkeypatch):
    import app_helpers
    warnings = []
    monkeypatch.setattr(app_helpers.st, "warning", lambda msg: warnings.append(msg))
    app_helpers.render_supplement_zero_price_notes(None)
    app_helpers.render_supplement_zero_price_notes({})
    assert warnings == []
