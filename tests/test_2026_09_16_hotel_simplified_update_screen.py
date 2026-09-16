"""Regression tests for "keep the app simple" (product owner, 2026-09-16, screenshot with red
crossouts over the Hotel review screen):

    "this information is useless now as we map the hotels differently. The hint can be deleted.
    In addition: When we use the masterdata from Travel C, no need to add additional images, no
    need to review the Description - we are only focusing on price, supplement, meal type,
    offer, stop sale. No need to include 'Standing note - applies to EVERY Hotel from this
    supplier'. Why I still need to confirm the geolocation, this is already confirmed with
    creating the hotel with master data. We must keep the app simple"

This follows the "create the hotel shell in Travel Compositor first, then only use this app to
add rooms/rates/meal plans/offers/supplements" workflow (see
test_2026_09_16_existing_basic_info_wins_on_update.py) - once a hotel already has an
existing_snapshot, its name/address/category/chain/images/description/geolocation are already
correct in Travel Compositor and don't need a human to re-review them here. A brand-new hotel
(no existing_snapshot yet) still needs the full Property/Images/Geolocation UI, since there's no
existing record yet to trust.

Covers:
  - the home-screen automap banner/button removal (see
    test_2026_09_13_hotel_automap_master_link.py's test_the_home_screen_banner_and_button_have_
    been_removed for the app.py half of this same request)
  - flows/hotel.py: Property (name/address/category/chain/description) and Images sections
    collapsed to a one-line caption when existing_snapshot is present, full UI otherwise
  - flows/hotel.py: Geolocation section collapsed to a one-line "already confirmed" caption when
    existing_snapshot has valid coordinates, full search/checkbox UI otherwise
  - service_notes.py: the standing-note editor is no longer rendered a second time on Hotel's
    per-service review screen (it's already shown once at Step 2, right after picking the
    supplier)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import service_notes


def _read_hotel_flow():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "flows", "hotel.py")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------------------------
# Property / Images / Description - collapsed to a one-line note on an update
# ---------------------------------------------------------------------------------------------

def test_property_section_is_conditional_on_not_having_an_existing_snapshot():
    src = _read_hotel_flow()
    idx = src.index('st.markdown("#### Property")')
    preceding = src[max(0, idx - 200):idx]
    assert "if not existing_snapshot:" in preceding


def test_images_section_is_conditional_on_not_having_an_existing_snapshot():
    src = _read_hotel_flow()
    idx = src.index('st.markdown("#### Images")')
    preceding = src[max(0, idx - 200):idx]
    assert "if not existing_snapshot:" in preceding


def test_existing_hotel_gets_a_one_line_caption_instead_of_the_full_property_review():
    src = _read_hotel_flow()
    assert "existing name, address, category, chain, description and images" in src
    idx = src.index("existing name, address, category, chain, description and images")
    preceding = src[max(0, idx - 400):idx]
    assert "else:" in preceding


# ---------------------------------------------------------------------------------------------
# Geolocation - auto-confirmed one-liner when an existing snapshot already has valid coordinates
# ---------------------------------------------------------------------------------------------

def test_geolocation_section_shows_a_one_line_confirmation_for_an_existing_hotel():
    src = _read_hotel_flow()
    idx = src.index('if existing_snapshot and hp_geo.get("valid"):')
    window = src[idx:idx + 600]
    assert "st.session_state.hp_geo_confirmed = True" in window
    assert "already confirmed when this" in window


def test_geolocation_falls_back_to_the_full_ui_when_existing_snapshot_lacks_coordinates():
    # The full search/checkbox UI (guarded by st.markdown("#### Geolocation")) must still be
    # reachable - either there's no existing_snapshot at all (brand-new hotel), or there is one
    # but it has no valid lat/long yet (a gap the document/master-data/manual override can fill).
    src = _read_hotel_flow()
    idx = src.index('if existing_snapshot and hp_geo.get("valid"):')
    window = src[idx:idx + 1500]
    assert 'else:' in window
    assert 'st.markdown("#### Geolocation")' in window


def test_geolocation_confirmed_flag_is_read_after_the_if_else():
    # hp_geo_confirmed must be computed after both branches so it reflects whichever one ran.
    src = _read_hotel_flow()
    idx = src.index('if existing_snapshot and hp_geo.get("valid"):')
    # must appear later in the file (after both branches), not before - .index with a start
    # position raises ValueError if it's missing entirely, which is the failure mode we want.
    later_idx = src.index('hp_geo_confirmed = st.session_state.get("hp_geo_confirmed", False)', idx)
    assert later_idx > idx


# ---------------------------------------------------------------------------------------------
# Standing note - no longer duplicated on the per-service review screen
# ---------------------------------------------------------------------------------------------

def test_hotel_review_screen_passes_show_standing_note_false():
    src = _read_hotel_flow()
    assert 'service_notes.render_notes_editor(supplier_id, "Hotel", data, show_standing_note=False)' in src


def test_render_notes_editor_supports_hiding_the_standing_note_editor():
    import inspect
    sig = inspect.signature(service_notes.render_notes_editor)
    assert "show_standing_note" in sig.parameters
    assert sig.parameters["show_standing_note"].default is True


def test_compose_manual_notes_still_includes_the_standing_note_even_when_editor_is_hidden():
    # show_standing_note=False only hides the second copy of the EDITOR UI - a supplier's
    # already-saved standing note must still be composed into the voucher either way.
    import inspect
    src = inspect.getsource(service_notes.render_notes_editor)
    assert "compose_manual_notes(supplier_id, product_type, one_off)" in src
    assert "data[\"manual_notes\"] = composed" in src
