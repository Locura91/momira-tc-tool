"""Regression test for a real production bug (product owner, 2026-09-16): "I can add a google
maps Link, but the coordinates are not filled to the geolocation, even not after i click 'use
this links coordinates'."

Root cause: a real, modern Google Maps "Share" link - especially from the mobile app, for a
business/POI that has its own Place ID - very often resolves to a URL that carries NO embedded
coordinates in any of the four shapes parse_google_maps_url already recognized (the "!3d/!4d"
pin, "?q=", "?ll=", or the "@lat,lng" viewport) - just a place-ID hash Google uses to look the
place up on its own servers, e.g.
".../maps/place/Some+Hotel/data=!4m2!3m1!1s0x1450abc...?utm_source=mstt_1&entry=gps". No
client-side URL parsing can recover a coordinate that was never in the link, so every one of
these came back as a flat "couldn't find coordinates" failure.

Fix: parse_google_maps_url now falls back to geocoding the place NAME that IS always present in
a .../maps/place/<name>/... URL, through the same free Nominatim/Photon pipeline geocode()
already uses everywhere else in this app, when none of the four coordinate patterns match.

Also: the product owner asked to remove manual lat/long number entry entirely now that the link
(with this fallback) covers it - "if the google maps link works, the manual adding is not needed
any more" - covered by the updated tests in test_2026_09_03_google_maps_url_coordinates.py,
test_2026_09_01_high_batch3_closedtour_ticket_flows.py and test_2026_09_06_hotel_manual_geolocation.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import geocoding_client


def test_place_page_with_no_coordinates_falls_back_to_geocoding_the_place_name(monkeypatch):
    captured_query = {}

    def fake_geocode(query):
        captured_query["value"] = query
        return {"latitude": 27.3949, "longitude": 33.6784, "display_name": "El Gouna, Egypt",
                "valid": True, "provider": "nominatim"}

    monkeypatch.setattr(geocoding_client, "geocode", fake_geocode)
    url = ("https://www.google.com/maps/place/Steigenberger+Golf+Resort+El+Gouna/"
           "data=!4m2!3m1!1s0x1450abc123:0x456?utm_source=mstt_1&entry=gps")
    result = geocoding_client.parse_google_maps_url(url)

    assert result["valid"] is True
    assert result["latitude"] == 27.3949
    assert result["longitude"] == 33.6784
    assert result["source"] == "geocoded from link"
    assert "Steigenberger Golf Resort El Gouna" in captured_query["value"]


def test_place_page_with_real_pin_never_triggers_the_fallback(monkeypatch):
    def fake_geocode(query):
        raise AssertionError("should not geocode when the link already has coordinates")

    monkeypatch.setattr(geocoding_client, "geocode", fake_geocode)
    url = ("https://www.google.com/maps/place/Vallee+de+Mai/@27.39,33.67,17z/"
           "data=!4m6!3m5!1s0x0:0x0!8m2!3d27.3949!4d33.6784!16s%2Fg%2F1")
    result = geocoding_client.parse_google_maps_url(url)

    assert result["valid"] is True
    assert result["source"] == "link"
    assert result["latitude"] == 27.3949
    assert result["longitude"] == 33.6784


def test_place_page_fallback_reports_not_found_when_geocoding_also_fails(monkeypatch):
    monkeypatch.setattr(geocoding_client, "geocode", lambda query: {
        "latitude": None, "longitude": None, "display_name": None, "valid": False, "provider": None})
    url = "https://www.google.com/maps/place/Somewhere+That+Does+Not+Geocode/data=!4m2!3m1!1s0x0"
    result = geocoding_client.parse_google_maps_url(url)

    assert result["valid"] is False
    assert result["error"]


def test_a_link_with_no_place_segment_and_no_coordinates_still_fails_cleanly():
    # A bare "current view" link with no /place/ segment and no coordinate params at all.
    result = geocoding_client.parse_google_maps_url("https://www.google.com/maps")
    assert result["valid"] is False
    assert result["latitude"] is None
    assert result["longitude"] is None
