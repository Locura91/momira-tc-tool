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

Fix: right after extraction, if a master-data seed is active and has images, they're folded
straight into hp_data["images"] (the field that already drives the publish payload and the
editable "Image URLs" table) - not just left as candidates. They still ALSO appear in the "Images
found" picker (harmless - a human can still add page/document images the same way), but no longer
NEED a second manual step to actually be included.

app.py can't be imported in a test process (no Streamlit runtime) - same established
source-shape-check pattern as test_2026_09_10_bulk_price_validity_code.py's own app.py wiring
tests.
"""
import os

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_masterdata_seed_images_are_folded_into_hp_data_images_after_extraction():
    src = _read_app_py()
    assert 'st.session_state.hp_data = extract_hotel_data(raw_text, hotel_hint=hotel_hint, human_hint=hp_hint)' in src
    # The auto-fold must happen AFTER hp_data is assigned (so it starts from whatever
    # extract_hotel_data actually returned) and must extend rather than overwrite, in case
    # extraction itself ever populates "images".
    after_extraction = src.split(
        'st.session_state.hp_data = extract_hotel_data(raw_text, hotel_hint=hotel_hint, human_hint=hp_hint)'
    )[1].split("st.session_state.hp_phase = \"reviewing\"")[0]
    assert 'if _hp_md_seed and _hp_md_seed.get("image_urls"):' in after_extraction
    assert 'st.session_state.hp_data["images"] = list(dict.fromkeys(' in after_extraction
    assert '(st.session_state.hp_data.get("images") or []) + _hp_md_seed["image_urls"]' in after_extraction


def test_masterdata_seed_is_read_before_the_auto_fold_uses_it():
    # _hp_md_seed must be defined (from st.session_state.get("hp_masterdata_seed")) before the
    # auto-fold block references it - a plain source-order check, since a NameError here would
    # only surface the first time someone actually created a hotel from master data.
    src = _read_app_py()
    seed_read_pos = src.index('_hp_md_seed = st.session_state.get("hp_masterdata_seed")')
    auto_fold_pos = src.index('if _hp_md_seed and _hp_md_seed.get("image_urls"):')
    assert seed_read_pos < auto_fold_pos
