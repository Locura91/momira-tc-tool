"""Regression test for a real product-owner request (2026-09-11), reported right after
successfully finding a hotel in Travel Compositor's master data via the (now-fixed)
Destination/GIATA search:

    "I now found a correct hotel with the correct information from Travel C, I selected it and
    it came with many pictures. We need to use the provided images from the masterdata
    automatically. Please make sure that all images are automatically selected when creating a
    hotel from masterdata"

Before this, a master-data match's images (masterdata_matcher.datasheet_to_masterdata_seed's own
"image_urls") only ever landed in `hp_hosted_image_candidates` - the same "found images" picker
every other image source (page scrape, uploaded document) feeds - requiring a SECOND manual pick
(open the "Images found" expander, tick each thumbnail, click "Add selected") even though a human
had already confirmed the exact hotel one step earlier, in Step 3's own candidate-confirmation
list. Skipping that second pick was exactly what produced the "No image has been added yet"
publish-blocking warning the product owner ran into.

FOLLOW-UP FIX (same day): the first version folded master-data images into hp_data["images"]
directly, but ALSO left them sitting in hp_hosted_image_candidates - so the "Images found"
picker still showed all of them as unchecked checkboxes (a screenshot confirmed this), reading
as "these aren't selected" even though they already were. Now master-data images are excluded
from that generic candidate list entirely (they don't need a manual pick - they're already in
hp_data["images"] and visible in the editable "Image URLs" table), and the Step 4 info banner
says explicitly how many were already added.

app.py can't be imported in a test process (no Streamlit runtime) - same established
source-shape-check pattern as test_2026_09_10_bulk_price_validity_code.py's own app.py wiring
tests.
"""
import os

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def _extraction_block():
    src = _read_app_py()
    return src.split(
        'st.session_state.hp_data = extract_hotel_data(raw_text, hotel_hint=hotel_hint, human_hint=hp_hint)'
    )[1].split('st.session_state.hp_phase = "reviewing"')[0]


def test_masterdata_seed_images_are_folded_into_hp_data_images_after_extraction():
    src = _read_app_py()
    assert 'st.session_state.hp_data = extract_hotel_data(raw_text, hotel_hint=hotel_hint, human_hint=hp_hint)' in src
    # The auto-fold must happen AFTER hp_data is assigned (so it starts from whatever
    # extract_hotel_data actually returned) and must extend rather than overwrite, in case
    # extraction itself ever populates "images".
    after_extraction = _extraction_block()
    assert 'if _hp_md_image_urls_list:' in after_extraction
    assert 'st.session_state.hp_data["images"] = list(dict.fromkeys(' in after_extraction
    assert '(st.session_state.hp_data.get("images") or []) + _hp_md_image_urls_list' in after_extraction


def test_masterdata_images_are_excluded_from_the_generic_found_images_picker():
    # The literal follow-up ask: master-data images must not sit unchecked in "Images found"
    # (they'd read as "not selected" even though they're already in hp_data["images"]).
    after_extraction = _extraction_block()
    assert 'st.session_state.hp_hosted_image_candidates = [' in after_extraction
    assert 'if u not in _hp_md_image_urls' in after_extraction


def test_masterdata_seed_is_read_before_the_auto_fold_uses_it():
    # _hp_md_seed must be defined (from st.session_state.get("hp_masterdata_seed")) before the
    # auto-fold block references it - a plain source-order check, since a NameError here would
    # only surface the first time someone actually created a hotel from master data.
    src = _read_app_py()
    seed_read_pos = src.index('_hp_md_seed = st.session_state.get("hp_masterdata_seed")')
    auto_fold_pos = src.index('if _hp_md_image_urls_list:')
    assert seed_read_pos < auto_fold_pos


def test_step4_banner_reports_how_many_masterdata_images_were_already_added():
    src = _read_app_py()
    assert '_hp_md_img_count = len(_hp_md_seed_used.get("image_urls") or [])' in src
    assert 'already added' in src
