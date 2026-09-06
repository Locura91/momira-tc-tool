"""Regression tests for a real customer-facing bug (product owner, 2026-09-06, HRG-H1):

    Even after the in-tool 500x400 minimum-size check (image_dimensions.py) dropped two
    undersized images, publishing HRG-H1 was STILL rejected outright by Travel Compositor for a
    THIRD, different image:
        "Not valid image. Image:
         'https://images.pexels.com/photos/4226146/pexels-photo-4226146.jpeg?...'"

That image measured fine locally (it wasn't in the "skipped for size" list), so a client-side
pixel-dimension check can never fully predict every way Travel Compositor's own server-side
fetch/validation can reject a picked image. Rather than try to guess every possible rule in
advance, the Hotel publish button now reads the specific rejected image out of BOTH known error
shapes ("Minimum size ... found. Image: '<url>'" and "Not valid image. Image: '<url>'" - both end
in the same `Image: '<url>'` shape) and retries with just that one image removed, up to once per
image in the list so a handful of bad picks can't loop forever.

app.py can't be imported in a test process (heavy top-level Streamlit/API-client setup), so -
matching this suite's established pattern - the retry loop's shape is verified by reading app.py's
own source text, and the URL-extraction helper (_extract_rejected_image_url) is tested directly
since it has no Streamlit dependency.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MODULE_BUILD = "2026-09-06-hotel-image-reject-retry"

_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


# ======================================================================
# _extract_rejected_image_url - no Streamlit dependency, importable directly
# ======================================================================
def _import_app_helper(name):
    """Executes just the named function's source (plus its own small helper dependency) in
    isolation, the same pragmatic approach this suite already uses for other app.py-only pure
    helpers that don't need the rest of the module imported."""
    src = _read_app_py()
    ns = {"json": json}
    exec("import re", ns)
    re_idx = src.index('_REJECTED_IMAGE_URL_RE = re.compile(')
    re_end = src.index("\n", re_idx)
    exec(src[re_idx:re_end], ns)
    for fn_name in ("_extract_error_message_detail", "_extract_rejected_image_url"):
        start = src.index(f"def {fn_name}(")
        end = src.index("\n\n\n", start)
        exec(src[start:end], ns)
    return ns[name]


def test_extracts_url_from_the_not_valid_image_error():
    fn = _import_app_helper("_extract_rejected_image_url")
    bad_url = "https://images.pexels.com/photos/4226146/pexels-photo-4226146.jpeg?auto=compress&cs=tinysrgb&h=650&w=940"
    raw_error = {"error": 400, "message": json.dumps({"error": [f"Not valid image. Image: '{bad_url}'"]})}
    assert fn(raw_error) == bad_url


def test_extracts_url_from_the_minimum_size_error():
    fn = _import_app_helper("_extract_rejected_image_url")
    bad_url = "https://images.pexels.com/photos/20401343/pexels-photo-20401343.jpeg?auto=compress&cs=tinysrgb&h=650&w=940"
    raw_error = {"error": 400,
                 "message": json.dumps({"error": [f"Minimum size of 500x400 required, 433x650 found. Image: '{bad_url}'"]})}
    assert fn(raw_error) == bad_url


def test_returns_none_for_an_unrelated_error():
    fn = _import_app_helper("_extract_rejected_image_url")
    raw_error = {"error": 400, "message": json.dumps({"error": ["Room null already exists for contract HRG-H1"]})}
    assert fn(raw_error) is None


# ======================================================================
# app.py wiring - the retry loop itself
# ======================================================================
def _hotel_publish_block():
    src = _read_app_py()
    idx = src.index('f"🚀 Publish — {\'UPDATE\' if existing_snapshot else \'CREATE\'} hotel {provider_code}"')
    end = src.index("progress.success(\"✅ Phase 1", idx)
    return src[idx:end]


def test_hotel_publish_retries_with_the_rejected_image_removed():
    window = _hotel_publish_block()
    assert "_extract_rejected_image_url(hotel_response)" in window
    assert "_hp_image_retries_left" in window
    assert "u for u in _hp_current_images if u != _hp_bad_image" in window
    assert "continue" in window


def test_hotel_publish_retry_falls_back_to_placeholder_if_it_was_the_last_image():
    window = _hotel_publish_block()
    assert "] or [FALLBACK_IMAGE]" in window


def test_hotel_publish_retry_gives_up_and_shows_the_real_error_when_not_an_image_problem():
    window = _hotel_publish_block()
    idx = window.index("_hp_bad_image in _hp_current_images")
    tail = window[idx:]
    assert 'show_publish_error(f"publish hotel **{provider_code}**", hotel_response)' in tail
    assert "return" in tail


def test_hotel_publish_retry_is_bounded_so_it_cannot_loop_forever():
    window = _hotel_publish_block()
    # Bounded by the number of images in the payload at the start, decremented every retry.
    assert "_hp_image_retries_left = len(phase1_payload.get(\"images\") or [])" in window
    assert "_hp_image_retries_left -= 1" in window
    assert "_hp_image_retries_left > 0" in window
