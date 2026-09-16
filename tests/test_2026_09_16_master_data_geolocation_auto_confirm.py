"""Regression tests for auto-confirmed geolocation when a hotel is seeded from confirmed Travel
Compositor master data (2026-09-16). Product owner, after watching a screen-share confirming the
geolocation-confirmation gate exists because of a real geocoder-accuracy bug:

    "I am not even sure, when creating a new hotel, why humans must confirm the geolocation. this
    information is coming from the hotel information already"

Correct specifically for a hotel seeded from a human-CONFIRMED master-data record picked via the
destination-confirmed search (app_helpers.py): those coordinates are Travel Compositor's own data
about that exact property, not a geocoder's guess from an address string, so the manual "I've
checked this location on the map" gate is no longer needed for that case - it stays needed for the
old geocoded-from-address path, which is what the gate was built for in the first place
(test_2026_09_06_hotel_manual_geolocation.py).

Covers: builder.build_hotel_contract_payload's new master_latitude/master_longitude priority tier
(between document and existing-snapshot - see builder.py's own "MASTER-DATA COORDINATES" comment),
and flows/hotel.py's wiring: the master-data seed's own geolocation feeding into hp_data, and the
checkbox auto-confirming exactly once for that source without overriding a human's own uncheck.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas import HumanPreConfig
from builder import build_hotel_contract_payload, GEOLOCATION_SOURCE_CONFIRMED_MASTER


def make_pre_config(**overrides):
    defaults = dict(
        supplier_id="51216", provider_code="HRG-H1", min_pax=1, max_pax=4,
        currency="EUR", modality_code="STANDARD",
    )
    defaults.update(overrides)
    return HumanPreConfig(**defaults)


def _room(name="Deluxe Room"):
    return {"name": name, "distributions": [{"adults": 2, "children": 0}]}


# ---------------------------------------------------------------------------------------------
# builder.py priority order
# ---------------------------------------------------------------------------------------------

def test_master_data_coordinates_used_when_no_manual_or_document_coordinates():
    extracted = {
        "hotelname": "Test Hotel", "rooms": [_room()],
        "latitude": None, "longitude": None,
        "master_latitude": 27.394900, "master_longitude": 33.678400,
    }
    result = build_hotel_contract_payload(make_pre_config(), extracted)
    assert result["hotel_payload"]["latitude"] == 27.394900
    assert result["hotel_payload"]["longitude"] == 33.678400
    assert result["geolocation"]["valid"] is True
    assert result["geolocation"]["source"] == GEOLOCATION_SOURCE_CONFIRMED_MASTER


def test_document_coordinates_still_win_over_master_data():
    extracted = {
        "hotelname": "Test Hotel", "rooms": [_room()],
        "latitude": 27.0, "longitude": 33.0,
        "master_latitude": 27.394900, "master_longitude": 33.678400,
    }
    result = build_hotel_contract_payload(make_pre_config(), extracted)
    assert result["hotel_payload"]["latitude"] == 27.0
    assert result["hotel_payload"]["longitude"] == 33.0
    assert result["geolocation"]["source"] == "document"


def test_manual_override_still_wins_over_master_data():
    extracted = {
        "hotelname": "Test Hotel", "rooms": [_room()],
        "latitude": None, "longitude": None,
        "master_latitude": 27.394900, "master_longitude": 33.678400,
        "manual_latitude": 30.0, "manual_longitude": 31.0,
    }
    result = build_hotel_contract_payload(make_pre_config(), extracted)
    assert result["hotel_payload"]["latitude"] == 30.0
    assert result["hotel_payload"]["longitude"] == 31.0
    assert result["geolocation"]["source"] == "manual override"


def test_existing_snapshot_wins_over_master_data_on_update():
    # SUPERSEDES the original version of this test ("master data wins over existing"). CONFIRMED
    # PRODUCT-OWNER DECISION (2026-09-16, same day, follow-up): "the information already provided
    # by Travel C is great and no rewrite needed." An already-existing hotel's own record now
    # outranks even a confirmed master-data seed - in real usage this exact combination can't
    # actually happen (the master-data search step is skipped entirely once a hotel already
    # exists - see app_helpers.py), but the priority order is still locked down here defensively.
    existing_snapshot = {"rooms": [], "latitude": 30.0444, "longitude": 31.2357}
    extracted = {
        "hotelname": "Test Hotel", "rooms": [_room()],
        "latitude": None, "longitude": None,
        "master_latitude": 27.394900, "master_longitude": 33.678400,
    }
    result = build_hotel_contract_payload(make_pre_config(), extracted, existing_hotel_snapshot=existing_snapshot)
    assert result["hotel_payload"]["latitude"] == 30.0444
    assert result["hotel_payload"]["longitude"] == 31.2357
    assert result["geolocation"]["source"] == "existing hotel record"


def test_no_master_data_falls_through_unaffected():
    extracted = {"hotelname": "Test Hotel", "rooms": [_room()], "latitude": None, "longitude": None}
    result = build_hotel_contract_payload(make_pre_config(), extracted)
    assert result["geolocation"]["source"] != GEOLOCATION_SOURCE_CONFIRMED_MASTER


# ---------------------------------------------------------------------------------------------
# flows/hotel.py wiring
# ---------------------------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_HOTEL_FLOW_PY = os.path.join(os.path.dirname(_HERE), "flows", "hotel.py")


def _read_hotel_flow():
    with open(_HOTEL_FLOW_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_master_seed_geolocation_is_stashed_as_master_latitude_longitude_not_manual():
    src = _read_hotel_flow()
    assert 'st.session_state.hp_data["master_latitude"] = _hp_md_geo["latitude"]' in src
    assert 'st.session_state.hp_data["master_longitude"] = _hp_md_geo["longitude"]' in src
    # Must not be folded into manual_latitude - that would make an explicit document coordinate
    # lose to it (manual_latitude outranks document in builder.py's priority order).
    assert 'st.session_state.hp_data["manual_latitude"] = _hp_md_geo' not in src


def test_checkbox_auto_confirms_exactly_once_for_the_master_data_source():
    src = _read_hotel_flow()
    assert "hp_geo_auto_confirmed_for" in src
    auto_confirm_idx = src.index("hp_geo.get(\"source\") == GEOLOCATION_SOURCE_CONFIRMED_MASTER")
    # 2026-09-16 "keep the app simple" simplification nested the whole search/checkbox UI one
    # level deeper inside an `else:` (see test_2026_09_16_hotel_simplified_update_screen.py), so
    # this no longer sits at a fixed 8-space indent - match on the checkbox call's own text
    # instead of the exact indentation of its first argument.
    checkbox_idx = src.index("I've checked this location", auto_confirm_idx)
    # the auto-confirm block must run BEFORE the checkbox widget renders, so its pre-tick value
    # is what the widget actually reads on this render.
    assert auto_confirm_idx < checkbox_idx


def test_auto_confirm_does_not_override_a_human_uncheck_on_the_next_rerun():
    src = _read_hotel_flow()
    # The guard must gate re-setting True on whether we already auto-confirmed THIS source, not
    # unconditionally set hp_geo_confirmed = True every render.
    # This exact "if" line appears twice (once for the info caption above, once for the
    # auto-confirm gate right before the checkbox) - the auto-confirm one is the later of the two.
    block_start = src.rindex('if hp_geo.get("source") == GEOLOCATION_SOURCE_CONFIRMED_MASTER:')
    block = src[block_start:block_start + 500]
    assert 'if st.session_state.get("hp_geo_auto_confirmed_for") != GEOLOCATION_SOURCE_CONFIRMED_MASTER:' in block


def test_master_data_source_is_excluded_from_the_openstreetmap_attribution_caption():
    src = _read_hotel_flow()
    exclusion_idx = src.index('if hp_geo.get("source") not in (')
    exclusion_block = src[exclusion_idx:exclusion_idx + 250]
    assert "GEOLOCATION_SOURCE_CONFIRMED_MASTER" in exclusion_block


def test_builder_import_includes_the_shared_geolocation_source_constant():
    src = _read_hotel_flow()
    assert "GEOLOCATION_SOURCE_CONFIRMED_MASTER" in src
    assert "from builder import (" in src
