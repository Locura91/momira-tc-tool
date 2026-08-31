"""Tests for the "images never even shown" fix (2026-08-31).

CONFIRMED REAL BUG (reported): "as for now, I still have the issue with the claudfare [sic] and
the image hosting. Now the App never even shows me available images, even though the document
and/or the URL has some images included." Follow-up answer: no images section appears AT ALL -
not "0 found", not broken thumbnails, nothing.

2026-08-30 already fixed ONE failure point in this pipeline (a failed R2 upload being swallowed
silently - see test_2026_08_30_page_image_validation.py). Investigating this new report found
THREE MORE failure points, all one step EARLIER than that fix (at discovery/extraction, before
R2 is ever involved), all with the exact same "looks identical to a genuine zero-images result"
shape:

  1. web_extractor.get_page_image_bytes(): a page whose <img> tags all failed to download (site-
     wide hotlink/bot protection - a real, already-confirmed failure mode, see
     _looks_like_real_image's docstring) produced the exact same [] as a page with zero <img>
     tags at all. Now takes an optional errors= list and reports the difference.
  2. ui_components._add_page_images_to_doc_pool(): a page-fetch exception (network down, blocked,
     timeout, bad SSL, 403/404) was caught in a bare `except Exception: page_images_bytes = []`.
     Now returns the real reason instead.
  3. document_reader.extract_images(): a genuine extraction failure (corrupted file, a
     PyMuPDF/python-docx/openpyxl internal error) only reached a print() statement - invisible on
     Streamlit Cloud, indistinguishable from "this document has no images". Now takes optional
     errors=/label= parameters.

app.py's four extract_images() call sites (ClosedTour single-tour, and the two multi-tour/ticket
flows that still used the OLDER silent `upload_images_r2` + bare `except: pass` pattern for
document-embedded images, never having received the 2026-08-30 fix at all) are also updated to
thread all of this through to a visible st.warning via the now-generalized
_warn_page_image_upload_errors - checked here via source text, not by importing app.py (a full
Streamlit script; unsafe to import in a test process - see
test_2026_08_31_closedtour_child_discount_visibility.py for the same reasoning).
"""
import os
from unittest.mock import patch

import requests

import document_reader
import web_extractor
import ui_components


_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


# ----------------------------------------------------------------------
# web_extractor.get_page_image_bytes - candidates found but all blocked
# ----------------------------------------------------------------------

def test_get_page_image_bytes_stays_silent_when_the_page_genuinely_has_no_img_tags():
    """Not an error - a page can legitimately have zero images. Must not report anything."""
    with patch.object(web_extractor, "get_page_images", return_value=[]):
        errors = []
        out = web_extractor.get_page_image_bytes("https://supplier.example.com/page", errors=errors)
    assert out == []
    assert errors == []


def test_get_page_image_bytes_reports_when_candidates_exist_but_all_downloads_fail():
    """The exact scenario this was built for: real <img> tags found, but every single request
    blocked (site-wide hotlink/bot protection) - must be distinguishable from 'no images'."""
    with patch.object(web_extractor, "get_page_images",
                      return_value=["https://supplier.example.com/a.jpg",
                                    "https://supplier.example.com/b.jpg"]):
        with patch.object(web_extractor.requests, "get",
                          side_effect=requests.exceptions.HTTPError("403 Forbidden")):
            errors = []
            out = web_extractor.get_page_image_bytes("https://supplier.example.com/page", errors=errors)
    assert out == []
    assert len(errors) == 1
    assert "2" in errors[0]  # mentions how many candidates were found
    assert "403" in errors[0]


def test_get_page_image_bytes_no_errors_param_still_works_silently():
    """Every pre-existing caller doesn't pass errors= at all - must not raise, must not require
    the parameter."""
    with patch.object(web_extractor, "get_page_images", return_value=["https://x.example.com/a.jpg"]):
        with patch.object(web_extractor.requests, "get", side_effect=requests.exceptions.HTTPError("blocked")):
            out = web_extractor.get_page_image_bytes("https://supplier.example.com/page")
    assert out == []


def test_get_page_image_bytes_no_report_when_at_least_one_image_succeeds():
    """Partial success is not a failure worth flagging - same 'only report when the WHOLE thing
    came back empty' rule already used elsewhere (see build_closed_tour_payloads' error-per-
    field pattern)."""
    good_response = type("R", (), {"status_code": 200, "content": b"\xff\xd8\xff" + b"\x00" * 20,
                                    "raise_for_status": lambda self: None})()
    calls = {"n": 0}

    def _get(url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.HTTPError("403 Forbidden")
        return good_response

    with patch.object(web_extractor, "get_page_images",
                      return_value=["https://x.example.com/a.jpg", "https://x.example.com/b.jpg"]):
        with patch.object(web_extractor.requests, "get", side_effect=_get):
            errors = []
            out = web_extractor.get_page_image_bytes("https://supplier.example.com/page", errors=errors)
    assert len(out) == 1
    assert errors == []


# ----------------------------------------------------------------------
# ui_components._add_page_images_to_doc_pool - the page fetch itself failing
# ----------------------------------------------------------------------

def test_add_page_images_surfaces_a_fetch_exception_instead_of_swallowing_it():
    with patch.object(ui_components, "get_page_image_bytes", side_effect=RuntimeError("network down")):
        doc_raw_images, doc_image_urls = [], []
        errors = ui_components._add_page_images_to_doc_pool(
            "https://supplier.example.com/page", doc_raw_images, doc_image_urls)
    assert len(errors) == 1
    assert "network down" in errors[0]
    assert doc_raw_images == []
    assert doc_image_urls == []


def test_add_page_images_threads_through_get_page_image_bytes_own_errors():
    """When get_page_image_bytes reports 'candidates found but all blocked' via its own errors=
    (rather than raising), that message must reach the caller too."""
    def _fake_get_page_image_bytes(url, errors=None):
        if errors is not None:
            errors.append("Found 3 image(s) on the page, but none could be downloaded - blocked")
        return []

    with patch.object(ui_components, "get_page_image_bytes", side_effect=_fake_get_page_image_bytes):
        doc_raw_images, doc_image_urls = [], []
        errors = ui_components._add_page_images_to_doc_pool(
            "https://supplier.example.com/page", doc_raw_images, doc_image_urls)
    assert errors == ["Found 3 image(s) on the page, but none could be downloaded - blocked"]


# ----------------------------------------------------------------------
# document_reader.extract_images - genuine extraction failures
# ----------------------------------------------------------------------

def test_extract_images_stays_silent_for_an_unsupported_extension():
    """A .txt/.doc/.pptx file simply isn't a format this extracts images from - normal, not an
    error, must not report anything even when errors= is given."""
    errors = []
    out = document_reader.extract_images("/tmp/fake.txt", errors=errors)
    assert out == []
    assert errors == []


def test_extract_images_reports_a_genuine_pdf_extraction_failure():
    with patch.object(document_reader, "extract_images_from_pdf", side_effect=RuntimeError("corrupted PDF structure")):
        errors = []
        out = document_reader.extract_images("/tmp/xyz123.pdf", errors=errors, label="Supplier Contract.pdf")
    assert out == []
    assert len(errors) == 1
    assert "Supplier Contract.pdf" in errors[0]
    assert "corrupted PDF structure" in errors[0]


def test_extract_images_label_defaults_to_the_file_path_basename():
    """Without an explicit label, falls back to the (usually meaningless tempfile) basename
    rather than crashing - still functional, just less readable."""
    with patch.object(document_reader, "extract_images_from_docx", side_effect=RuntimeError("bad zip")):
        errors = []
        out = document_reader.extract_images("/tmp/tmpABC123.docx", errors=errors)
    assert out == []
    assert "tmpABC123.docx" in errors[0]


def test_extract_images_no_errors_param_still_works_silently():
    """Every pre-existing caller (all four call sites in app.py, before this fix) doesn't pass
    errors= at all - must not raise, must not require the parameter, must not print a traceback
    that crashes anything."""
    with patch.object(document_reader, "extract_images_from_xlsx", side_effect=RuntimeError("bad workbook")):
        out = document_reader.extract_images("/tmp/x.xlsx")
    assert out == []


# ----------------------------------------------------------------------
# app.py wiring - all four call sites updated, none still silent
# ----------------------------------------------------------------------

def test_app_py_no_call_site_still_uses_the_silent_upload_images_r2_for_document_images():
    """Regression guard for the exact gap found: THREE of the four extract_images() call sites in
    app.py still used the OLDER upload_images_r2 (silent-skip variant) wrapped in a bare `except
    Exception: pass` for document-embedded images, even after the 2026-08-30 fix supposedly
    covered this - only page-scraped images and one of the four document-image call sites had
    actually been fixed. Every call site must now use the _with_errors variant."""
    source = _read_app_py()
    assert "upload_images_r2_with_errors" in source
    # The old silent pattern must be fully gone, not just partially replaced.
    assert "except Exception:\n                                pass" not in source
    assert "except Exception:\n                            pass" not in source
    assert "except Exception:\n                        pass" not in source


def test_app_py_every_extract_images_call_passes_errors_and_label():
    source = _read_app_py()
    count = source.count("embedded_images = extract_images(tmp_path")
    assert count == 4  # all four call sites still present
    count_with_errors = source.count("errors=_doc_image_errors, label=uploaded.name")
    assert count_with_errors == 4  # and every single one now reports failures


def test_app_py_warn_helper_is_generalized_not_r2_specific():
    """The wrapper used to hardcode an R2 Public Access hint (in its actual st.warning() call,
    not just discussed in its docstring/history) that would actively mislead for a page-fetch or
    document-extraction failure - must no longer assume every message is about R2 uploading."""
    source = _read_app_py()
    func_body = source.split("def _warn_page_image_upload_errors")[1].split("\ndef ")[0]
    assert "st.warning(\"⚠️ \" + errors[0]" in func_body
    # The old hardcoded R2-specific hint text must be gone from the actual warning call.
    assert "check that your R2 bucket" not in func_body
    assert "uploading can succeed even when the resulting URL isn't publicly reachable" not in func_body
