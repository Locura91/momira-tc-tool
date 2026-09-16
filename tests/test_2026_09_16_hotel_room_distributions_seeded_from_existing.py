"""Regression test for a real publish failure (product owner, 2026-09-16, HRG-H1 "Winter 26-27"):

    "Steigenberger Golf Resort Winter 26-27": {"error":["java.lang.IllegalArgumentException:
    Room price missing for distributions: 2 Ad. + 0 Ch., 1 Ad. + 0 Ch."],"status":"BAD_REQUEST"}

Root cause: flows/hotel.py built room_name_to_distributions - the map that feeds builder.py's
missing-distribution-price safety net (_fill_missing_distribution_prices, 2026-09-11 HRG-H1 fix)
- ONLY from data.get("rooms"), this DOCUMENT's own freshly extracted rooms. That was fine while
every document restated its rooms, but the 2026-09-16 "only focusing on price, supplement, meal
type, offer, stop sale" simplification means a rate-only follow-up document for an ALREADY-
EXISTING hotel legitimately never mentions rooms at all - their occupancy is already correct in
Travel Compositor. With room_name_to_distributions coming back empty for any room this document
doesn't name, the safety net had nothing to check a season's prices against, so a genuinely
missing distribution price sailed straight through to Travel Compositor's own raw exception
instead of being caught beforehand with an editable message.

Fix: room_name_to_distributions is now seeded from the EXISTING hotel's own live rooms first,
then this document's own rooms override per name - a document that DOES restate a room's
occupancy still wins for that room, same "document overrides, existing carries forward"
philosophy already used throughout this codebase for meal plans/offers/supplements/seasons.

This is a source-text check (same established pattern every other flows/hotel.py wiring test in
this suite uses) - the underlying fill/detect logic itself (_fill_missing_distribution_prices) is
a pure function already covered by its own tests elsewhere; what needed covering here is that its
INPUT is no longer blind to a room the fresh document doesn't restate.
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_HOTEL_FLOW_PY = os.path.join(os.path.dirname(_HERE), "flows", "hotel.py")


def _read_hotel_flow():
    with open(_HOTEL_FLOW_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_room_name_to_distributions_is_seeded_from_the_existing_snapshot_first():
    src = _read_hotel_flow()
    build_idx = src.index("room_name_to_distributions = {")
    window = src[build_idx:build_idx + 400]
    assert '(existing_snapshot or {}).get("rooms")' in window


def test_room_name_to_distributions_then_lets_this_documents_own_rooms_override():
    src = _read_hotel_flow()
    build_idx = src.index("room_name_to_distributions = {")
    # the existing-snapshot dict comprehension must come first, then an .update(...) call with
    # this document's OWN rooms (data.get("rooms")) - so a document that DOES restate a room
    # still wins for that room, rather than the existing snapshot always taking priority.
    update_idx = src.index("room_name_to_distributions.update({", build_idx)
    assert build_idx < update_idx
    window = src[update_idx:update_idx + 200]
    assert 'data.get("rooms")' in window


def test_room_name_to_distributions_feeds_build_hotel_rate_payloads_as_before():
    src = _read_hotel_flow()
    update_idx = src.index("room_name_to_distributions.update({")
    call_idx = src.index("build_hotel_rate_payloads(", update_idx)
    window = src[update_idx:call_idx + 400]
    assert "room_name_to_distributions=room_name_to_distributions" in window


# ---------------------------------------------------------------------------------------------
# End to end (builder.py is a pure function, no Streamlit needed): reproduces the real scenario -
# a rate-only document for an existing room the document itself never redeclares - and proves the
# safety net now catches a genuinely missing price instead of letting it reach Travel Compositor
# raw, once room_name_to_distributions is built the way flows/hotel.py now builds it (existing
# snapshot's rooms first, this document's own rooms override).
# ---------------------------------------------------------------------------------------------

def test_missing_price_is_caught_when_the_room_is_only_known_from_the_existing_snapshot():
    from builder import build_hotel_rate_payloads

    existing_snapshot = {
        "rooms": [{
            "name": "Deluxe Room", "providerCode": "AUTO123",
            # This room allows two distributions - 2 Ad + 0 Ch and 1 Ad + 0 Ch - exactly the
            # real reported combos.
            "distributions": [{"adults": 2, "children": 0}, {"adults": 1, "children": 0}],
        }],
        "rates": [],
    }
    # The exact bug shape: room_name_to_distributions built the OLD way (document rooms only,
    # this document has none) would be empty - simulating that directly.
    room_name_to_distributions_old_way = {
        (r or {}).get("name"): (r or {}).get("distributions") or []
        for r in [] if (r or {}).get("name")  # data.get("rooms") is empty for a rate-only doc
    }
    rate_data = [{
        "name": "Winter 26-27",
        "seasons": [{
            "name": "Winter Season",
            "date_ranges": [{"start": "2027-12-01", "end": "2028-02-28"}],
            "room_prices": [{
                # Only ONE of the two allowed combos is priced - the real gap.
                "room_name": "Deluxe Room",
                "distribution_prices": [{"amount": 200.0, "adults": 2, "children": 0}],
                "base_price": 200.0, "adult_prices": [], "child_prices": [],
            }],
            "meal_plans": [],
        }],
        "offer_names": [], "supplement_names": [], "stop_sales": [],
    }]
    room_map = {"Deluxe Room": "AUTO123"}

    # OLD behavior: nothing to check against, so the gap slips through uncaught.
    old_results = build_hotel_rate_payloads(
        rate_data, room_map, {}, {}, existing_hotel_snapshot=existing_snapshot,
        room_name_to_distributions=room_name_to_distributions_old_way,
    )
    assert old_results[0]["rate_error"] is None  # the bug: nothing caught it

    # NEW behavior: seeded from the existing snapshot's own rooms first (same merge flows/
    # hotel.py now does) - the gap is caught with an editable message instead of reaching
    # Travel Compositor raw.
    room_name_to_distributions_new_way = {
        (r or {}).get("name"): (r or {}).get("distributions") or []
        for r in existing_snapshot.get("rooms") or [] if (r or {}).get("name")
    }
    new_results = build_hotel_rate_payloads(
        rate_data, room_map, {}, {}, existing_hotel_snapshot=existing_snapshot,
        room_name_to_distributions=room_name_to_distributions_new_way,
    )
    assert new_results[0]["rate_error"] is not None
    assert "1 Ad. + 0 Ch." in new_results[0]["rate_error"]
