"""Tests for pasting a Google Maps link to fill in a Ticket's coordinates (product owner,
2026-09-03): "If I have to manually change the coordinates, I would like to just add the URL with
the correct place from google. Could the app then get the correct data like longitude and
latitude?"

geocoding_client.parse_google_maps_url() extracts {"latitude", "longitude"} straight out of a
pasted Google Maps URL, so the human doesn't have to read coordinates off the page and retype them
(a real source of transposition errors). It handles the shapes actually produced by Google Maps'
own address bar / Share button:
  - a place page's own precise pin (the "!3d..!4d.." pair embedded in "data=...", preferred over
    the "@lat,lng" viewport-center the map happened to be panned to)
  - a bare coordinate link ("?q=lat,lng" or "?ll=lat,lng")
  - a plain "current view" link ("/@lat,lng,zoom")
  - a shortened share-link (maps.app.goo.gl/..., goo.gl/maps/...) - resolved via one HTTP redirect
    hop before the same parsing runs on the expanded URL

Wired into all three places app.py lets a human replace a Ticket's coordinates by hand - the
multi-ticket batch flow's combined search/manual expander, and the single-ticket flow's two
"fix the location" spots (resolved-but-wrong, and not-resolved-at-all) - each gets a "paste a
Google Maps link" box next to the existing manual lat/lng entry, using the same
manual_latitude/manual_longitude fields so it's a drop-in alternative to typing numbers, not a
separate code path.

app.py can't be imported in a test process (heavy top-level Streamlit/API-client setup), so its
three call sites are verified by reading its own source text, per this suite's established
pattern. geocoding_client.py has no such import-time side effects, so parse_google_maps_url()
itself is tested directly, with requests.get/head monkeypatched for the short-link cases so no
real network call is made.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import geocoding_client

MODULE_BUILD = "2026-09-03-time-window-fix-what-to-bring-duration-unit"

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def _function_source(src, def_line):
    start = src.index(def_line)
    end = src.index("\ndef ", start + len(def_line))
    return src[start:end]


# ======================================================================
# parse_google_maps_url - the four recognized coordinate shapes
# ======================================================================
def test_place_page_prefers_the_precise_pin_over_the_viewport_center():
    url = ("https://www.google.com/maps/place/Vallee+de+Mai/@27.39,33.67,17z/"
           "data=!4m6!3m5!1s0x0:0x0!8m2!3d27.3949!4d33.6784!16s%2Fg%2F1")
    result = geocoding_client.parse_google_maps_url(url)
    assert result["valid"] is True
    assert result["latitude"] == 27.3949
    assert result["longitude"] == 33.6784


def test_bare_q_coordinate_link():
    result = geocoding_client.parse_google_maps_url("https://www.google.com/maps?q=27.394900,33.678400")
    assert result == {"latitude": 27.3949, "longitude": 33.6784, "valid": True, "error": None}


def test_ll_coordinate_link():
    result = geocoding_client.parse_google_maps_url("https://www.google.com/maps?ll=-4.328900,55.737800&z=15")
    assert result["valid"] is True
    assert result["latitude"] == -4.3289
    assert result["longitude"] == 55.7378


def test_plain_viewport_link():
    result = geocoding_client.parse_google_maps_url("https://www.google.com/maps/@27.394900,33.678400,15z")
    assert result["valid"] is True
    assert result["latitude"] == 27.3949
    assert result["longitude"] == 33.6784


def test_url_without_scheme_is_accepted():
    result = geocoding_client.parse_google_maps_url("www.google.com/maps?q=27.3949,33.6784")
    assert result["valid"] is True


# ======================================================================
# parse_google_maps_url - short links (mocked redirect resolution)
# ======================================================================
def test_short_link_is_resolved_before_parsing(monkeypatch):
    class _FakeResponse:
        def __init__(self, url):
            self.url = url

    def _fake_head(url, allow_redirects=True, timeout=10, headers=None):
        assert "maps.app.goo.gl" in url
        return _FakeResponse("https://www.google.com/maps?q=27.394900,33.678400")

    monkeypatch.setattr(geocoding_client.requests, "head", _fake_head)
    result = geocoding_client.parse_google_maps_url("https://maps.app.goo.gl/AbCd1234")
    assert result["valid"] is True
    assert result["latitude"] == 27.3949
    assert result["longitude"] == 33.6784


def test_short_link_head_failure_falls_back_to_get(monkeypatch):
    class _FakeResponse:
        def __init__(self, url):
            self.url = url

    def _fake_head(*a, **k):
        raise ConnectionError("HEAD not supported")

    def _fake_get(url, allow_redirects=True, timeout=10, headers=None, stream=True):
        return _FakeResponse("https://www.google.com/maps?q=1.234500,5.678900")

    monkeypatch.setattr(geocoding_client.requests, "head", _fake_head)
    monkeypatch.setattr(geocoding_client.requests, "get", _fake_get)
    result = geocoding_client.parse_google_maps_url("https://goo.gl/maps/AbCd1234")
    assert result["valid"] is True
    assert result["latitude"] == 1.2345
    assert result["longitude"] == 5.6789


# ======================================================================
# parse_google_maps_url - rejects garbage without crashing
# ======================================================================
def test_empty_input_is_rejected_with_a_friendly_error():
    result = geocoding_client.parse_google_maps_url("")
    assert result["valid"] is False
    assert result["error"]


def test_non_google_url_is_rejected():
    result = geocoding_client.parse_google_maps_url("https://example.com/maps?q=1,2")
    assert result["valid"] is False
    assert "Google Maps" in result["error"]


def test_plain_text_is_rejected_not_crashed_on():
    result = geocoding_client.parse_google_maps_url("just some random text, not a link")
    assert result["valid"] is False
    assert result["latitude"] is None and result["longitude"] is None


def test_google_url_with_no_coordinates_in_it_is_rejected():
    result = geocoding_client.parse_google_maps_url("https://www.google.com/maps/search/restaurants+near+me")
    assert result["valid"] is False
    assert "coordinates" in result["error"].lower()


def test_out_of_range_numbers_are_rejected():
    # A "q=" value that happens to look like two numbers but isn't a valid lat/lng pair (e.g. a
    # place ID or zoom-style string) must not be accepted as real coordinates.
    result = geocoding_client.parse_google_maps_url("https://www.google.com/maps?q=999.000000,999.000000")
    assert result["valid"] is False


# ======================================================================
# app.py wiring - import + all three UI call sites
# ======================================================================
def test_parse_google_maps_url_is_imported():
    src = _read_app_py()
    assert "from geocoding_client import geocode_search, geocode, parse_google_maps_url" in src


def test_multi_ticket_flow_has_paste_url_option_before_manual_entry():
    src = _read_app_py()
    window = _function_source(
        src,
        'def render_multi_ticket_flow(client, supplier_id, currency, on_request, release_days, '
        'tk_url, tk_files, min_passengers=1, max_passengers=9, default_ticket_code=""):')
    assert "mt_geo_maps_url_btn_" in window
    assert "parse_google_maps_url(mt_maps_url)" in window
    paste_idx = window.index("mt_geo_maps_url_btn_")
    manual_idx = window.index('mt_geo_manual_btn_')
    assert paste_idx < manual_idx
    # A successful parse must feed the SAME manual_latitude/manual_longitude fields the number-
    # entry path uses, not a separate/parallel field.
    paste_block = window[paste_idx:manual_idx]
    assert 'data["manual_latitude"] = mt_url_geo["latitude"]' in paste_block
    assert 'data["manual_longitude"] = mt_url_geo["longitude"]' in paste_block


def test_single_ticket_flow_resolved_branch_has_paste_url_option():
    src = _read_app_py()
    window = _function_source(src, "def render_ticket_flow(client):")
    assert 'key="tk_geo_maps_url_btn"' in window
    idx = window.index('key="tk_geo_maps_url_btn"')
    block = window[idx:idx + 900]
    assert 'parse_google_maps_url(tk_maps_url)' in block
    assert 'data["manual_latitude"] = tk_url_geo["latitude"]' in block
    assert 'data["manual_longitude"] = tk_url_geo["longitude"]' in block


def test_single_ticket_flow_not_resolved_branch_has_paste_url_option_before_manual_entry():
    src = _read_app_py()
    window = _function_source(src, "def render_ticket_flow(client):")
    assert 'key="tk_geo_maps_url_btn2"' in window
    paste_idx = window.index('key="tk_geo_maps_url_btn2"')
    manual_idx = window.index('key="tk_use_manual_geo"')
    assert paste_idx < manual_idx
    block = window[paste_idx:manual_idx]
    assert 'parse_google_maps_url(tk_maps_url2)' in block
    assert 'data["manual_latitude"] = tk_url_geo2["latitude"]' in block
    assert 'data["manual_longitude"] = tk_url_geo2["longitude"]' in block


def test_all_three_paste_url_sites_show_an_error_on_failed_parse():
    src = _read_app_py()
    window = _function_source(
        src,
        'def render_multi_ticket_flow(client, supplier_id, currency, on_request, release_days, '
        'tk_url, tk_files, min_passengers=1, max_passengers=9, default_ticket_code=""):')
    assert 'st.error(mt_url_geo["error"])' in window

    window2 = _function_source(src, "def render_ticket_flow(client):")
    assert 'st.error(tk_url_geo["error"])' in window2
    assert 'st.error(tk_url_geo2["error"])' in window2
