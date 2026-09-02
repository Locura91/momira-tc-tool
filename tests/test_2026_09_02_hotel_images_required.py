"""Tests for the reported bug (2026-09-02): "Couldn't publish hotel SEY-H1:
createHotel.contract.images: Size must be between 1 and 2147483647 ([])" - Travel Compositor
requires at least one image per hotel, but the Hotel flow's Input Source step (Step 3) never
looked for images anywhere (not in an uploaded document, not on the hotel's page URL - it fetched
the page's TEXT for extraction but never its images), and Step 4's review screen was a bare
manual "paste a URL" table with nothing to paste FROM. So a real hotel contract with a real
website full of property photos still reached Publish with an empty images list, and Travel
Compositor rejected it with a raw technical error instead of a clear, actionable one.

Fixed with three changes, all in app.py's Hotel flow:
  1. Step 3 (extraction) now runs the same doc_raw_images/doc_image_urls pipeline every other
     product-type flow already has: extract_images() pulls images embedded in an uploaded
     document, _add_page_images_to_doc_pool() downloads images found on the hotel page URL
     server-side (same as Ticket/ClosedTour/Transfer/Transport already do).
  2. Step 4 (review) gained the same picker sections those other flows have - stock photo search
     (Pexels/Pixabay), a picker for images found on the page/document, and a picker for images
     extracted from an uploaded document - all writing into data["images"] (Hotel's own field
     name; other flows call theirs "image_urls").
  3. A new hard publish gate (images_ok), matching the existing priced_rooms gate immediately
     above it: if contract_result["hotel_payload"]["images"] is empty, Publish is disabled and a
     clear message is shown - the raw createHotel error can no longer reach the operator's screen
     since the tool now catches the exact same condition Travel Compositor's own validator checks,
     before ever calling the API.

app.py can't be imported in a test process (heavy top-level Streamlit/API-client setup), so -
matching this suite's established pattern - these are verified by reading app.py's own source
text and confirming the specific code shapes exist in the right places, in the right order.
"""
import os

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP_PY = os.path.join(_REPO_DIR, "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def _hotel_step3_block(content):
    start = content.index('st.header("Hotel — Step 3: Input Source")')
    end = content.index('# PHASE 2: review everything, then publish', start)
    return content[start:end]


def _hotel_step4_block(content):
    start = content.index('# PHASE 2: review everything, then publish')
    end = content.index('# ---- Rooms ----', start)
    return content[start:end]


# ======================================================================
# 1. Step 3 - image discovery wired in (embedded doc images + page-URL images)
# ======================================================================
def test_step3_initializes_image_pools():
    block = _hotel_step3_block(_read_app_py())
    assert "doc_raw_images = []" in block
    assert "doc_image_urls = []" in block
    assert "seen_image_hashes = set()" in block


def test_step3_extracts_embedded_document_images():
    block = _hotel_step3_block(_read_app_py())
    assert "extract_images(tmp_path" in block
    assert "upload_images_r2_with_errors(embedded_images)" in block


def test_step3_scrapes_images_from_the_page_url():
    block = _hotel_step3_block(_read_app_py())
    assert "_add_page_images_to_doc_pool(hp_url, doc_raw_images, doc_image_urls)" in block


def test_step3_stores_candidates_into_session_state_for_step4_pickers():
    block = _hotel_step3_block(_read_app_py())
    assert "st.session_state.hp_doc_raw_images = doc_raw_images" in block
    assert "st.session_state.hp_hosted_image_candidates = list(dict.fromkeys(doc_image_urls))" in block


def test_step3_image_scrape_happens_before_extraction_is_stored():
    # so a failure fetching images can't accidentally skip storing hp_data
    block = _hotel_step3_block(_read_app_py())
    scrape_idx = block.index("_add_page_images_to_doc_pool(hp_url")
    store_idx = block.index("st.session_state.hp_data = extract_hotel_data(")
    assert scrape_idx < store_idx


# ======================================================================
# 2. Step 4 - picker sections wired in, writing to data["images"]
# ======================================================================
def test_step4_has_stock_photo_pickers():
    block = _hotel_step4_block(_read_app_py())
    assert '_hp_add_pexels' in block
    assert '_hp_add_pixabay' in block
    assert 'render_stock_photo_picker("Pexels"' in block
    assert 'render_stock_photo_picker("Pixabay"' in block


def test_step4_has_page_document_image_pickers():
    block = _hotel_step4_block(_read_app_py())
    assert "render_url_image_picker(st.session_state.get(\"hp_hosted_image_candidates\")" in block
    assert "render_doc_image_picker(st.session_state.get(\"hp_doc_raw_images\")" in block


def test_step4_pickers_all_write_into_data_images():
    block = _hotel_step4_block(_read_app_py())
    for fn in ("_hp_add_pexels", "_hp_add_pixabay", "_hp_add_url_images", "_hp_add_doc_image"):
        fn_idx = block.index(f"def {fn}(")
        fn_block = block[fn_idx: fn_idx + 400]
        assert 'data["images"]' in fn_block, f"{fn} doesn't write into data['images']"


# ======================================================================
# 3. Publish gate - images_ok, matching the existing priced_rooms gate
# ======================================================================
def test_images_ok_computed_from_the_actual_payload_sent_to_travel_compositor():
    content = _read_app_py()
    assert 'images_ok = bool(contract_result["hotel_payload"].get("images"))' in content


def test_publish_button_disabled_when_no_images():
    content = _read_app_py()
    idx = content.index('key="hp_publish", disabled=')
    line = content[idx: idx + 200]
    assert "not images_ok" in line


def test_images_ok_gate_shows_a_clear_actionable_error_not_a_raw_api_error():
    content = _read_app_py()
    idx = content.index("if not images_ok:")
    block = content[idx: idx + 400]
    assert "Travel Compositor requires at least one image" in block
    assert "contract.images" not in block  # never leak the raw API field name to the operator


def test_ready_to_publish_caption_now_shows_image_count():
    content = _read_app_py()
    idx = content.index("Ready to publish: **")
    block = content[idx: idx + 600]
    assert "image(s)" in block
