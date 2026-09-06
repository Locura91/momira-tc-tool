"""Regression tests for a real customer-facing bug (product owner, 2026-09-06):

    First-ever Hotel publish attempt (HRG-H1, Steigenberger Golf Resort El Gouna) failed with:
        "The hotel is no located inside any destination, please check the coordinates"
    User's explicit instruction: "same issue as with tickets, human must select the coordinates."

Root cause (confirmed via a pre-existing code comment in builder.py, since removed): unlike
Ticket/Transfer/ClosedTour, build_hotel_contract_payload never resolved a geolocation at all - it
only ever read whatever the extractor happened to find in the document (often None/None, since
hotel documents don't reliably state coordinates) or an existing live snapshot's own lat/long.
Travel Compositor rejects a hotel whose coordinates don't fall inside any of its own known
destination boundaries, and there was no way for a human to override a wrong/missing pair from
inside the tool.

Fix (2026-09-06): Hotel now gets the same manual-override capability Ticket already has
(build_ticket_payloads) - build_hotel_contract_payload resolves geolocation with this priority:
  1. manual_latitude/manual_longitude (human override) - always wins
  2. the document's own extracted latitude/longitude
  3. an existing live snapshot's latitude/longitude
  4. a free OpenStreetMap geocode of the hotel's own address (location name + country)
...and returns a "geolocation" dict ({latitude, longitude, valid, source}) so app.py's new Hotel
Step 4 "Geolocation" section (mirroring Ticket's search/paste-a-maps-link/manual-entry UI) can
show it and require a human "I've checked this" confirmation before the Publish button unlocks.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas import HumanPreConfig
from builder import build_hotel_contract_payload

MODULE_BUILD = "2026-09-06-hotel-geo-checkbox-key-fix"


def make_pre_config(**overrides):
    defaults = dict(
        supplier_id="51216", provider_code="HRG-H1", min_pax=1, max_pax=4,
        currency="EUR", modality_code="STANDARD",
    )
    defaults.update(overrides)
    return HumanPreConfig(**defaults)


def _room(name="Deluxe Room"):
    return {"name": name, "distributions": [{"adults": 2, "children": 0}]}


# ======================================================================
# Priority order
# ======================================================================
def test_manual_override_wins_over_document_and_existing_snapshot():
    existing_snapshot = {"rooms": [], "latitude": 30.0444, "longitude": 31.2357}
    extracted = {
        "hotelname": "Test Hotel", "rooms": [_room()],
        "latitude": 27.0, "longitude": 33.0,
        "manual_latitude": 27.394900, "manual_longitude": 33.678400,
    }
    result = build_hotel_contract_payload(make_pre_config(), extracted, existing_hotel_snapshot=existing_snapshot)
    assert result["hotel_payload"]["latitude"] == 27.394900
    assert result["hotel_payload"]["longitude"] == 33.678400
    assert result["geolocation"]["valid"] is True
    assert result["geolocation"]["source"] == "manual override"


def test_document_coordinates_win_over_existing_snapshot_when_no_manual_override():
    existing_snapshot = {"rooms": [], "latitude": 30.0444, "longitude": 31.2357}
    extracted = {"hotelname": "Test Hotel", "rooms": [_room()], "latitude": 27.0, "longitude": 33.0}
    result = build_hotel_contract_payload(make_pre_config(), extracted, existing_hotel_snapshot=existing_snapshot)
    assert result["hotel_payload"]["latitude"] == 27.0
    assert result["hotel_payload"]["longitude"] == 33.0
    assert result["geolocation"]["source"] == "document"
    assert result["geolocation"]["valid"] is True


def test_existing_snapshot_coordinates_used_when_document_and_manual_are_both_absent():
    existing_snapshot = {"rooms": [], "latitude": 30.0444, "longitude": 31.2357}
    extracted = {"hotelname": "Test Hotel", "rooms": [_room()], "latitude": None, "longitude": None}
    result = build_hotel_contract_payload(make_pre_config(), extracted, existing_hotel_snapshot=existing_snapshot)
    assert result["hotel_payload"]["latitude"] == 30.0444
    assert result["hotel_payload"]["longitude"] == 31.2357
    assert result["geolocation"]["source"] == "existing hotel record"
    assert result["geolocation"]["valid"] is True


# ======================================================================
# Geocode fallback - THE actual real-world case (brand-new hotel, no coordinates anywhere,
# no manual override yet) that produced the "not located inside any destination" error.
# ======================================================================
def test_falls_back_to_geocoding_the_address_when_nothing_else_is_available(monkeypatch):
    import builder

    captured_query = {}

    def fake_geocode(query):
        captured_query["value"] = query
        return {"latitude": 27.3949, "longitude": 33.6784, "valid": True, "provider": "nominatim",
                "display_name": "El Gouna, Egypt"}

    monkeypatch.setattr(builder, "geocode", fake_geocode)
    extracted = {
        "hotelname": "Test Hotel", "rooms": [_room()], "latitude": None, "longitude": None,
        "address": {"location_name": "El Gouna, Hurghada, Red Sea", "country": "EG"},
    }
    result = build_hotel_contract_payload(make_pre_config(), extracted, existing_hotel_snapshot=None)
    assert result["hotel_payload"]["latitude"] == 27.3949
    assert result["hotel_payload"]["longitude"] == 33.6784
    assert result["geolocation"]["valid"] is True
    assert result["geolocation"]["source"] == "OpenStreetMap/Nominatim"
    assert "El Gouna" in captured_query["value"]


def test_geolocation_marked_invalid_when_geocoding_finds_nothing_and_nothing_else_is_available(monkeypatch):
    import builder

    monkeypatch.setattr(builder, "geocode", lambda query: {"latitude": None, "longitude": None, "valid": False, "provider": None})
    extracted = {
        "hotelname": "Test Hotel", "rooms": [_room()], "latitude": None, "longitude": None,
        "address": {"location_name": "Nowhere Really", "country": "XX"},
    }
    result = build_hotel_contract_payload(make_pre_config(), extracted, existing_hotel_snapshot=None)
    # CONFIRMED REAL BUG this reproduces: an unresolved geolocation used to silently reach
    # Travel Compositor as NULL/NULL and get rejected there instead of being caught in-tool.
    assert result["hotel_payload"]["latitude"] is None
    assert result["hotel_payload"]["longitude"] is None
    assert result["geolocation"]["valid"] is False
    assert result["geolocation"]["source"] == "not_found"


def test_geolocation_invalid_and_source_not_found_when_there_is_no_address_at_all():
    extracted = {"hotelname": "Test Hotel", "rooms": [_room()], "latitude": None, "longitude": None}
    result = build_hotel_contract_payload(make_pre_config(), extracted, existing_hotel_snapshot=None)
    assert result["geolocation"]["valid"] is False
    assert result["geolocation"]["source"] == "not_found"


# ======================================================================
# app.py wiring - Geolocation section + publish-button gate (source-text regression, same
# pattern this suite already uses for other UI wiring - see test_2026_09_01_medium_batch1_app_py.py)
# ======================================================================
def _read_app_py():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_hotel_publish_button_is_gated_on_geolocation_confirmation():
    src = _read_app_py()
    idx = src.index('key="hp_publish", disabled=not rooms_ok or not priced_rooms or not images_ok or not geo_ok):')
    assert idx > 0


def test_hotel_geo_ok_requires_both_valid_and_confirmed():
    src = _read_app_py()
    idx = src.index("geo_ok = bool(hp_geo.get(\"valid\")) and hp_geo_confirmed")
    assert idx > 0


def test_hotel_geolocation_section_offers_manual_entry_search_and_maps_link():
    src = _read_app_py()
    idx = src.index("#### Geolocation")
    window = src[idx:idx + 4000]
    assert 'st.number_input("Latitude"' in window
    assert 'st.number_input("Longitude"' in window
    assert "geocode_search" in window
    assert "parse_google_maps_url" in window
    assert 'hp_geo_confirmed' in window


def test_hotel_geolocation_section_appears_before_the_publish_section():
    src = _read_app_py()
    geo_idx = src.index("#### Geolocation")
    # There are earlier, unrelated "#### Publish" markers for other product flows (Transfer,
    # Transport) - find the one that actually follows the Hotel Geolocation section.
    publish_idx = src.index("#### Publish", geo_idx)
    assert geo_idx < publish_idx
    assert publish_idx - geo_idx < 6000
