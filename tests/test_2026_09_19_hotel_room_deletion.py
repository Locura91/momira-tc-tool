"""Regression tests for a real product-owner request (2026-09-19, verbatim):

    "we must make it possible to delete a complete room and not only occupancy"

Before this, build_hotel_contract_payload ALWAYS carried an existing room forward unchanged
whenever the fresh document didn't mention it by name - by design, so an unrelated update (a new
price period, a fix to a different room) could never accidentally drop a room nobody meant to
touch (see test_2026_08_30_hotel_matcher_and_room_merge.py's
test_existing_room_not_in_document_is_carried_forward_unchanged). That's still the right default,
but it meant there was NO way at all to actually delete a room that's genuinely meant to go -
simply leaving it out of the document's own "rooms" list was indistinguishable from "this
document doesn't happen to restate it".

Fix: a new `rooms_to_delete` parameter (a list of room names the human explicitly, deliberately
picked - see flows/hotel.py's new "Delete an existing room entirely" expander) is checked before
the carry-forward loop. A name in that list is matched by the same hotel_matcher.match_room_by_name
normalization used everywhere else, and that existing room is simply left out of the rebuilt
rooms[] array - the same "PUT replaces the whole array" mechanism that already adds/updates rooms
is what makes the deletion actually take effect, there is no separate delete endpoint.
"""
from schemas import HumanPreConfig
from builder import build_hotel_contract_payload


def make_pre_config(**overrides):
    defaults = dict(
        supplier_id="48940", provider_code="CAI-H1", min_pax=1, max_pax=4,
        currency="EUR", modality_code="STANDARD",
    )
    defaults.update(overrides)
    return HumanPreConfig(**defaults)


def _room(name, adults=2, children=0):
    return {"name": name, "distributions": [{"adults": adults, "children": children}]}


def _existing_snapshot(*rooms):
    return {"rooms": [
        {"name": name, "providerCode": code, "distributions": [{"adults": 2, "children": 0}]}
        for name, code in rooms
    ]}


def test_room_marked_for_deletion_is_left_out_of_the_carry_forward():
    existing = _existing_snapshot(("Deluxe Room", "AUTO_1"), ("Suite", "AUTO_2"))
    # This document doesn't mention either room - a plain price-only update.
    extracted = {"hotelname": "Test Hotel", "rooms": []}
    result = build_hotel_contract_payload(
        make_pre_config(), extracted, existing_hotel_snapshot=existing, rooms_to_delete=["Suite"])
    names = sorted(r["name"] for r in result["hotel_payload"]["rooms"])
    assert names == ["Deluxe Room"]  # Suite is gone; Deluxe Room still carried forward as before


def test_without_rooms_to_delete_both_existing_rooms_are_still_carried_forward():
    # Same setup as above, minus the deletion - confirms the new parameter is additive and the
    # existing carry-forward behavior is completely unchanged when it's not used.
    existing = _existing_snapshot(("Deluxe Room", "AUTO_1"), ("Suite", "AUTO_2"))
    extracted = {"hotelname": "Test Hotel", "rooms": []}
    result = build_hotel_contract_payload(make_pre_config(), extracted, existing_hotel_snapshot=existing)
    names = sorted(r["name"] for r in result["hotel_payload"]["rooms"])
    assert names == ["Deluxe Room", "Suite"]


def test_rooms_to_delete_is_name_normalized_like_every_other_room_match():
    # Existing name has a double space (as genuinely stored, per the matcher's own test suite);
    # the human picks it from a dropdown built off the existing snapshot's own name, so it
    # matches exactly here, but the matcher's tolerant normalization must still apply.
    existing = _existing_snapshot(("Deluxe  Room", "AUTO_1"))
    extracted = {"hotelname": "Test Hotel", "rooms": []}
    result = build_hotel_contract_payload(
        make_pre_config(), extracted, existing_hotel_snapshot=existing,
        rooms_to_delete=["Deluxe Room"])  # single space
    assert result["hotel_payload"]["rooms"] == []


def test_deleting_a_room_that_does_not_exist_is_a_safe_no_op():
    existing = _existing_snapshot(("Deluxe Room", "AUTO_1"))
    extracted = {"hotelname": "Test Hotel", "rooms": []}
    result = build_hotel_contract_payload(
        make_pre_config(), extracted, existing_hotel_snapshot=existing,
        rooms_to_delete=["Room That Never Existed"])
    names = [r["name"] for r in result["hotel_payload"]["rooms"]]
    assert names == ["Deluxe Room"]


def test_rooms_to_delete_defaults_to_none_and_does_nothing():
    existing = _existing_snapshot(("Deluxe Room", "AUTO_1"))
    extracted = {"hotelname": "Test Hotel", "rooms": []}
    result = build_hotel_contract_payload(make_pre_config(), extracted, existing_hotel_snapshot=existing)
    assert len(result["hotel_payload"]["rooms"]) == 1


def test_deleting_one_room_does_not_affect_another_room_still_matched_and_updated_by_the_document():
    existing = _existing_snapshot(("Deluxe Room", "AUTO_1"), ("Suite", "AUTO_2"))
    # The document DOES restate Deluxe Room (e.g. widening its distributions) while Suite is
    # explicitly deleted.
    extracted = {"hotelname": "Test Hotel", "rooms": [_room("Deluxe Room", adults=3, children=1)]}
    result = build_hotel_contract_payload(
        make_pre_config(), extracted, existing_hotel_snapshot=existing, rooms_to_delete=["Suite"])
    rooms = result["hotel_payload"]["rooms"]
    assert len(rooms) == 1
    assert rooms[0]["name"] == "Deluxe Room"
    assert rooms[0]["providerCode"] == "AUTO_1"  # still correctly matched/updated, not touched by the delete
    assert rooms[0]["distributions"][0]["adults"] == 3


def test_deleting_a_brand_new_hotel_room_list_is_a_no_op_since_there_is_nothing_existing_to_delete():
    # existing_hotel_snapshot=None (a brand-new hotel, CREATE not UPDATE) - rooms_to_delete has
    # nothing to match against, so it must not error or affect the freshly-created rooms.
    extracted = {"hotelname": "Test Hotel", "rooms": [_room("Deluxe Room")]}
    result = build_hotel_contract_payload(
        make_pre_config(), extracted, existing_hotel_snapshot=None, rooms_to_delete=["Deluxe Room"])
    assert result["hotel_error"] is None
    assert len(result["hotel_payload"]["rooms"]) == 1
    assert result["hotel_payload"]["rooms"][0]["name"] == "Deluxe Room"
