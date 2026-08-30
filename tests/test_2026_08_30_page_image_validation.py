"""Tests for the page-image pipeline fixes made 2026-08-30 (reported: "the images found in your
document/URL page is not working at all" - 12 "Use this image" candidates on a real Ticket
extraction, none usable, example URL https://masonstravel.com/packages/reef-safari/).

Two real gaps were found and fixed:

  1. web_extractor.get_page_image_bytes() downloaded each candidate image and accepted anything
     that came back with a 2xx status and non-empty body - but a 200 response is not proof the
     body is actually a photo. A site's bot/hotlink-protection layer commonly serves an HTML
     challenge page (or a tiny tracking pixel) with a 200 status exactly where a real image was
     expected, and res.raise_for_status() can't catch that - it only reacts to HTTP error codes.
     Fixed with _looks_like_real_image(), a real image-file-signature check (magic bytes), so a
     non-image response is skipped rather than silently accepted and later shown as an unusable
     "found image."

  2. ui_components._add_page_images_to_doc_pool() called the SILENT-SKIP r2_client.upload_images
     inside a bare `except Exception: pass` - so a genuine R2 problem (most commonly the bucket's
     Public Access setting never being enabled in Cloudflare, a separate manual step from the
     write credentials - see r2_client.py's own setup docs) looked EXACTLY like a normal "found N
     images" success, with no error anywhere explaining why none of them actually work. Fixed by
     switching to upload_images_with_errors and returning the per-image failure reasons so
     app.py's call sites (all 8 of them - Ticket single/batch, ClosedTour single/batch) can show
     an actual warning instead of a silently-broken "success."

Neither function had any test coverage before this file.
"""
from unittest.mock import patch

import requests

import web_extractor
import ui_components


# ----------------------------------------------------------------------
# _looks_like_real_image
# ----------------------------------------------------------------------

def test_looks_like_real_image_accepts_jpeg():
    assert web_extractor._looks_like_real_image(b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 20) is True


def test_looks_like_real_image_accepts_png():
    assert web_extractor._looks_like_real_image(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20) is True


def test_looks_like_real_image_accepts_gif():
    assert web_extractor._looks_like_real_image(b"GIF89a" + b"\x00" * 20) is True
    assert web_extractor._looks_like_real_image(b"GIF87a" + b"\x00" * 20) is True


def test_looks_like_real_image_accepts_bmp():
    assert web_extractor._looks_like_real_image(b"BM" + b"\x00" * 20) is True


def test_looks_like_real_image_accepts_webp():
    assert web_extractor._looks_like_real_image(b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 20) is True


def test_looks_like_real_image_rejects_html_challenge_page():
    # CONFIRMED FIX regression test: exactly the case this fix guards against - a bot/hotlink-
    # protection response is still HTTP 200 with real bytes, but it's an HTML page, not a photo.
    html = b"<html><head><title>Access Denied</title></head><body>Blocked</body></html>"
    assert web_extractor._looks_like_real_image(html) is False


def test_looks_like_real_image_rejects_empty_and_garbage():
    assert web_extractor._looks_like_real_image(b"") is False
    assert web_extractor._looks_like_real_image(None) is False
    assert web_extractor._looks_like_real_image(b"not an image, just text") is False


# ----------------------------------------------------------------------
# get_page_image_bytes - filters out non-image responses
# ----------------------------------------------------------------------

class _FakeImgResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error")


def test_get_page_image_bytes_skips_a_200_response_that_isnt_a_real_image():
    real_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 30
    challenge_page = b"<html>Please verify you are human</html>"

    with patch.object(web_extractor, "get_page_images",
                      return_value=["https://example.com/real.jpg", "https://example.com/blocked.jpg"]):
        responses = {
            "https://example.com/real.jpg": _FakeImgResponse(real_jpeg),
            "https://example.com/blocked.jpg": _FakeImgResponse(challenge_page),
        }
        with patch.object(web_extractor.requests, "get", side_effect=lambda url, **kw: responses[url]):
            results = web_extractor.get_page_image_bytes("https://example.com/page")

    assert len(results) == 1
    filename, content = results[0]
    assert content == real_jpeg
    assert filename.endswith(".jpg")


def test_get_page_image_bytes_keeps_every_genuine_image():
    jpeg1 = b"\xff\xd8\xff" + b"\x00" * 30
    png1 = b"\x89PNG\r\n\x1a\n" + b"\x00" * 30

    with patch.object(web_extractor, "get_page_images",
                      return_value=["https://example.com/a.jpg", "https://example.com/b.png"]):
        responses = {
            "https://example.com/a.jpg": _FakeImgResponse(jpeg1),
            "https://example.com/b.png": _FakeImgResponse(png1),
        }
        with patch.object(web_extractor.requests, "get", side_effect=lambda url, **kw: responses[url]):
            results = web_extractor.get_page_image_bytes("https://example.com/page")

    assert len(results) == 2


# ----------------------------------------------------------------------
# ui_components._add_page_images_to_doc_pool - surfaces R2 upload errors
# ----------------------------------------------------------------------

def test_add_page_images_returns_empty_list_when_everything_uploads_cleanly():
    page_images = [("page_img1.jpg", b"bytes1"), ("page_img2.jpg", b"bytes2")]
    with patch.object(ui_components, "get_page_image_bytes", return_value=page_images):
        with patch.object(ui_components, "upload_images_r2_with_errors",
                          return_value=(["https://r2.example.com/1.jpg", "https://r2.example.com/2.jpg"], [])):
            doc_raw_images, doc_image_urls = [], []
            errors = ui_components._add_page_images_to_doc_pool(
                "https://supplier.example.com/page", doc_raw_images, doc_image_urls)

    assert errors == []
    assert doc_raw_images == page_images
    assert doc_image_urls == ["https://r2.example.com/1.jpg", "https://r2.example.com/2.jpg"]


def test_add_page_images_surfaces_upload_errors_instead_of_swallowing_them():
    # CONFIRMED FIX regression test: the exact reported scenario - images are found and
    # downloaded (so doc_raw_images IS populated), but R2 upload fails for all of them (e.g. R2
    # Public Access not enabled) - the caller must be told WHY, not just see an empty result.
    page_images = [("page_img1.jpg", b"bytes1"), ("page_img2.jpg", b"bytes2")]
    with patch.object(ui_components, "get_page_image_bytes", return_value=page_images):
        with patch.object(ui_components, "upload_images_r2_with_errors",
                          return_value=([], ["page_img1.jpg: R2 upload failed: 403 Forbidden",
                                             "page_img2.jpg: R2 upload failed: 403 Forbidden"])):
            doc_raw_images, doc_image_urls = [], []
            errors = ui_components._add_page_images_to_doc_pool(
                "https://supplier.example.com/page", doc_raw_images, doc_image_urls)

    assert len(errors) == 2
    assert "403 Forbidden" in errors[0]
    # The raw bytes are still kept (they render fine via st.image(bytes) regardless of R2) -
    # only the R2-hosted URL list is missing entries, exactly what the caller's own
    # len(doc_image_urls) >= len(doc_raw_images) fallback-section logic depends on.
    assert doc_raw_images == page_images
    assert doc_image_urls == []


def test_add_page_images_partial_upload_failure_reports_only_the_failed_ones():
    page_images = [("page_img1.jpg", b"bytes1"), ("page_img2.jpg", b"bytes2")]
    with patch.object(ui_components, "get_page_image_bytes", return_value=page_images):
        with patch.object(ui_components, "upload_images_r2_with_errors",
                          return_value=(["https://r2.example.com/1.jpg"],
                                       ["page_img2.jpg: R2 upload failed: timeout"])):
            doc_raw_images, doc_image_urls = [], []
            errors = ui_components._add_page_images_to_doc_pool(
                "https://supplier.example.com/page", doc_raw_images, doc_image_urls)

    assert errors == ["page_img2.jpg: R2 upload failed: timeout"]
    assert doc_image_urls == ["https://r2.example.com/1.jpg"]


def test_add_page_images_returns_empty_list_for_a_blank_url():
    doc_raw_images, doc_image_urls = [], []
    errors = ui_components._add_page_images_to_doc_pool("", doc_raw_images, doc_image_urls)
    assert errors == []
    assert doc_raw_images == []
    assert doc_image_urls == []


def test_add_page_images_returns_empty_list_when_no_images_found():
    with patch.object(ui_components, "get_page_image_bytes", return_value=[]):
        doc_raw_images, doc_image_urls = [], []
        errors = ui_components._add_page_images_to_doc_pool(
            "https://supplier.example.com/page", doc_raw_images, doc_image_urls)
    assert errors == []
    assert doc_raw_images == []
    assert doc_image_urls == []


def test_add_page_images_handles_get_page_image_bytes_raising():
    with patch.object(ui_components, "get_page_image_bytes", side_effect=RuntimeError("network down")):
        doc_raw_images, doc_image_urls = [], []
        errors = ui_components._add_page_images_to_doc_pool(
            "https://supplier.example.com/page", doc_raw_images, doc_image_urls)
    assert errors == []
    assert doc_raw_images == []
    assert doc_image_urls == []
