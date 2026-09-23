"""Regression tests for a real product-owner report (2026-09-23, verbatim):

    "still not working, that if there is an image in the url or in a document, I canot
    automatically use the images. at least the images are being detected at the moment and i
    can download them, but i canot automatically iuse them for my closedtours and neither for
    my tickets"

Root cause: every URL that lands in `hosted_image_candidates` (ClosedTour/Ticket single-record
flows) or its `mct_`/`mt_`/`mtu_` equivalents (the batch flows) is already a fully verified,
R2-hosted image - uploaded AND public-url-verified inside
ui_components._add_page_images_to_doc_pool / r2_client.upload_images_with_errors /
r2_client.verify_public_url (see claude/r2-public-url-verification-2026-09-18.md) - before it
ever reaches that list. There is nothing left for a human to confirm. Despite that, every one of
these six call sites (ClosedTour single x2, Ticket single x2, ClosedTour batch x1, Ticket batch
x2 [create + update]) still required a manual tick-each-thumbnail-then-click-"Add selected" step
(render_url_image_picker) before a detected/downloaded image was actually used in image_urls -
matching exactly what was reported: detected and downloadable, but not automatically usable.

Fix: every one of these call sites now folds the already-hosted candidate URLs straight into
`image_urls` (or `data["image_urls"]`) at extraction time, and the "Images found" section that
used to gate on a manual pick is now a plain confirmation caption instead. The genuinely-failed-
to-auto-host document images (doc_raw_images / tk_doc_raw_images / mct_doc_raw_images / etc -
these DID fail an R2 upload attempt) still go through their own manual "Upload & Add"/download
picker, since those still need a human decision (upload isn't possible automatically) - this fix
is scoped to the ALREADY-hosted candidates only.

app.py/flows/*.py can't be imported directly in this test process (app.py touches Streamlit at
import time; the flows/*.py modules are pulled in by it) - matching this suite's established
convention (see test_2026_09_01_medium_batch1_app_py.py's own docstring), these are all
source-text checks.
"""
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(_REPO_ROOT, *parts), "r", encoding="utf-8") as f:
        return f.read()


# ======================================================================
# ClosedTour single-record flow (app.py)
# ======================================================================
def test_closedtour_direct_extraction_auto_folds_hosted_candidates_into_image_urls():
    src = _read("app.py")
    marker = 'st.session_state.hosted_image_candidates = list(dict.fromkeys(doc_image_urls))'
    assert marker in src
    idx = src.index(marker)
    window = src[idx:idx + 1500]
    assert "auto_images = list(dict.fromkeys(" in window
    assert "+ st.session_state.hosted_image_candidates))" in window
    assert 'data["image_urls"] = auto_images or [FALLBACK_IMAGE]' in window


def test_closedtour_variant_resolution_also_auto_folds_hosted_candidates():
    src = _read("app.py")
    marker = 'st.session_state.hosted_image_candidates = list(dict.fromkeys(pending_doc_image_urls))'
    assert marker in src
    idx = src.index(marker)
    window = src[idx:idx + 700]
    assert "auto_images = list(dict.fromkeys(" in window
    assert 'data["image_urls"] = auto_images or [FALLBACK_IMAGE]' in window


def test_closedtour_images_found_section_is_a_confirmation_not_a_picker():
    src = _read("app.py")
    # The old manual picker function/call must be gone entirely.
    assert "_ct_add_url_images" not in src
    # A plain confirmation caption replaces it.
    assert "were added automatically above" in src


# ======================================================================
# Ticket single-record flow (flows/ticket.py)
# ======================================================================
def test_ticket_direct_extraction_auto_folds_hosted_candidates_into_image_urls():
    src = _read("flows", "ticket.py")
    marker = 'st.session_state.tk_hosted_image_candidates = list(dict.fromkeys(doc_image_urls))'
    assert marker in src
    idx = src.index(marker)
    window = src[idx:idx + 1400]
    assert "auto_images = list(dict.fromkeys(" in window
    assert "+ st.session_state.tk_hosted_image_candidates))" in window
    assert 'data["image_urls"] = auto_images or [FALLBACK_IMAGE]' in window


def test_ticket_variant_resolution_also_auto_folds_hosted_candidates():
    src = _read("flows", "ticket.py")
    marker = 'st.session_state.tk_hosted_image_candidates = list(dict.fromkeys(pending_doc_image_urls))'
    assert marker in src
    idx = src.index(marker)
    window = src[idx:idx + 900]
    assert "auto_images = list(dict.fromkeys(" in window
    assert 'data["image_urls"] = auto_images or [FALLBACK_IMAGE]' in window


def test_ticket_images_found_section_is_a_confirmation_not_a_picker():
    src = _read("flows", "ticket.py")
    assert "_tk_add_url_images" not in src
    assert "were added automatically above" in src


# ======================================================================
# ClosedTour batch flow (flows/multi_tour.py)
# ======================================================================
def test_multi_tour_batch_auto_folds_hosted_candidates_into_image_urls():
    src = _read("flows", "multi_tour.py")
    marker = 'auto_images = list(dict.fromkeys(st.session_state.get("mct_hosted_image_candidates") or []))'
    assert marker in src
    idx = src.index(marker)
    window = src[idx:idx + 200]
    assert 'tour["main_data"]["image_urls"] = auto_images or [FALLBACK_IMAGE]' in window


def test_multi_tour_images_found_section_is_a_confirmation_not_a_picker():
    src = _read("flows", "multi_tour.py")
    assert "_mct_add_url_images" not in src
    assert "were added automatically." in src


# ======================================================================
# Ticket batch flows (flows/multi_ticket.py) - create (mt_) and update (mtu_)
# ======================================================================
def test_multi_ticket_create_batch_auto_folds_hosted_candidates():
    src = _read("flows", "multi_ticket.py")
    marker = 'auto_images = list(dict.fromkeys(st.session_state.get("mt_hosted_image_candidates") or []))'
    assert marker in src
    idx = src.index(marker)
    window = src[idx:idx + 200]
    assert 'current["data"]["image_urls"] = auto_images or [FALLBACK_IMAGE]' in window


def test_multi_ticket_update_batch_auto_folds_hosted_candidates():
    src = _read("flows", "multi_ticket.py")
    marker = '+ (st.session_state.get("mtu_hosted_image_candidates") or [])))'
    assert marker in src
    idx = src.index(marker)
    window = src[idx:idx + 200]
    assert 'current["data"]["image_urls"] = auto_images or [FALLBACK_IMAGE]' in window


def test_multi_ticket_images_found_sections_are_confirmations_not_pickers():
    src = _read("flows", "multi_ticket.py")
    assert "_mt_add_url_images" not in src
    assert "_mtu_add_url_images" not in src
    assert "in your document/page were added automatically." in src  # mt_ (create)
    assert "plus any found in your document/page," in src  # mtu_ (update)
    assert 'f"added automatically).")' in src


# ======================================================================
# The genuinely-failed-to-auto-host document pickers must still exist - those still need a
# human, since the automatic R2 upload attempt already failed for them.
# ======================================================================
def test_failed_to_host_document_pickers_are_untouched():
    ct_src = _read("app.py")
    assert "_ct_add_doc_image" in ct_src
    assert "render_doc_image_picker(st.session_state.doc_raw_images" in ct_src

    tk_src = _read("flows", "ticket.py")
    assert "_tk_add_doc_image" in tk_src
    assert "render_doc_image_picker(st.session_state.tk_doc_raw_images" in tk_src

    mct_src = _read("flows", "multi_tour.py")
    assert "_mct_add_doc_image" in mct_src

    mt_src = _read("flows", "multi_ticket.py")
    assert "mt_doc_raw_images" in mt_src
    assert "mtu_doc_raw_images" in mt_src
