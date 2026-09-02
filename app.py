"""
Momira Travel Platform - review UI for the DMC -> Travel Compositor pipeline.

Covers all five product types: ClosedTour, Ticket, Transfer, Transport and
Hotel. Step 1 picks the product type; each type then runs its own wizard.

Restructured as a strict sequential wizard so the human always specifies
WHAT they're doing (create/add-option/update-tour/update-option) and WHICH
supplier/tour/modality BEFORE any extraction happens - avoids the mistake
of extracting first and only later realizing the wrong action/tour was set.

Run with:
    streamlit run app.py

Reuses everything already built and tested:
    - api_client.py       (auth, destination resolution, uploads)
    - schemas.py          (validated payload models)
    - builder.py           (combines pre-config + extracted data + destinations)
    - document_reader.py  (PDF/Word/Excel -> raw text)
    - ai_extractor.py     (raw text -> structured English data)
    - web_extractor.py    (URL -> structured data, incl. destination scanning)
"""
import json
import re
import copy
import math
import tempfile
import os
import difflib
import requests
from datetime import datetime
import streamlit as st
import pandas as pd

if hasattr(st, "secrets"):
    for _key in ["TRAVELC_BASE_URL", "TRAVELC_MICROSITE_ID", "TRAVELC_USERNAME",
                 "TRAVELC_PASSWORD", "ANTHROPIC_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY",
                 # R2 (Cloudflare) image hosting - see r2_client.py's module docstring. Replaces
                 # the old FREEIMAGE_API_KEY entry that used to be here (freeimage_client.py is
                 # gone - r2_client.py is the only image-hosting path now). Without these five
                 # listed here, a correctly-filled Streamlit Secrets R2_* value would never
                 # actually reach os.environ - this whitelist is the only thing that copies
                 # st.secrets into os.environ, so a key missing here silently behaves as if it
                 # were never set, even though it's right there in Secrets.
                 "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME",
                 "R2_PUBLIC_BASE_URL",
                 # Translation Sync tool (merged in from momira-translation-sync). Loaded here,
                 # before translation_tool is imported, so its engines see the env they expect.
                 # TRANSLATION_PROVIDER picks gemini (default, cheapest) or claude.
                 "TRANSLATION_PROVIDER", "GEMINI_API_KEY", "GEMINI_MODEL", "ANTHROPIC_MODEL",
                 "TC_TARGET_LANGUAGES", "TRAVELC_SUPPLIER_ID",
                 # Supplier Discovery & Outreach tool (merged in from momira-suppliersearch-mail).
                 # IMPORTANT: this list is a WHITELIST - a secret not named here is never copied
                 # into os.environ, and outreach_discovery/outreach_email read os.getenv() only.
                 # Omitting a key here means the operator sets it in Streamlit secrets and the
                 # tool silently behaves as if it were never configured (mock search / demo
                 # email), with no error to explain why. Any new outreach setting must be added
                 # to this list too.
                 "TAVILY_API_KEY", "SERPAPI_API_KEY",
                 "RESEND_API_KEY", "SMTP_HOST", "SMTP_PORT", "SMTP_SECURE", "SMTP_USER",
                 "SMTP_PASS", "SMTP_FROM", "SMTP_REPLY_TO", "EMAIL_FROM", "SENDER_NAME",
                 "TEST_MODE_RECIPIENTS", "EMAIL_THROTTLE_MS", "PDF_ATTACHMENT_PATH",
                 "MIN_SUPPLIER_RATING", "MAX_SUPPLIER_RESULTS", "MAX_SUPPLIER_CANDIDATES",
                 # Durable storage. Without DATABASE_URL the platform still runs, but every
                 # thing it remembers between runs (what has already been translated, which
                 # routes map to which Travel Compositor id) lives in a local file that
                 # Streamlit Cloud wipes on redeploy - see platform_store.py.
                 "DATABASE_URL"]:
        try:
            if _key in st.secrets and _key not in os.environ:
                # str() because os.environ rejects non-string values - a secret typed as a
                # number or list (easy to do for TC_TARGET_LANGUAGES) would otherwise raise
                # here and be swallowed by the except, leaving the key silently unset.
                os.environ[_key] = str(st.secrets[_key])
        except Exception:
            pass

from api_client import TravelCompositorAPI
from schemas import HumanPreConfig, TicketHumanPreConfig, TransferHumanPreConfig, TransportHumanPreConfig, HotelHumanPreConfig
from builder import (transport_company_name as builder_transport_company_name,
                     transport_description as builder_transport_description,
                     start_date_or_today as builder_start_date_or_today)
from builder import derive_arrival_from_duration, build_closed_tour_payloads, build_ticket_payloads, build_supplement_vos, build_transfer_payload
from builder import build_transport_payloads
from builder import transport_type_is_confirmed_match
from builder import _APPLY_TYPE_VALUES as HOTEL_APPLY_VALUES
from builder import build_ticket_modality_combinations
from builder import LANGUAGE_CODE_NAMES
from builder import coerce_price_list_shape, coerce_ticket_occupancy_prices_shape
from builder import _MAX_OCCUPANCY_PAX as MAX_OCCUPANCY_PAX
# HOUSE RULE (product owner): "always for Date: DD/MM/YYYY". That is what a human reads and
# types; Travel Compositor only accepts YYYY-MM-DD, so every screen converts at the boundary
# and the payload stays ISO throughout. Both helpers accept both forms - see date_format.py.
from date_format import to_iso_date as _iso, to_display_date as _disp, DISPLAY_HINT as _DATE_HINT
# Widget-key generations - the defence against a widget showing a PREVIOUSLY-reviewed item's
# value. See widget_state.py's module docstring for the bug class and why it replaces sweeping.
import widget_state
from builder import (build_hotel_contract_payload, resolve_room_provider_codes, build_hotel_offer_payloads,
                     build_hotel_supplement_payloads, build_hotel_rate_payloads)
from document_reader import extract_raw_text, extract_images
from document_reader import scanned_document_warning as document_reader_scanned_warning
from ai_extractor import extract_structured_data, extract_option_only_data, extract_modality_data, detect_tour_variants, detect_multiple_modalities, apply_clarification, extract_ticket_data, extract_ticket_option_only_data, detect_ticket_variants, friendly_error_message, detect_transfer_products, extract_transfer_data, extract_ticket_main_info, extract_ticket_modality_data, detect_ticket_modalities
from ai_extractor import detect_transport_products, extract_transport_data, detect_hotel_products, extract_hotel_data
import ai_extractor as ai_extractor_module
# Shared Streamlit building blocks used by all five product-type flows (ClosedTour, Ticket,
# Transfer, Transport, Hotel) - see ui_components.py's module docstring.
from ui_components import (
    editable_table, editable_field, merge_what_to_bring_into_voucher_remarks,
    render_stop_sales_editor, render_cancellation_policy_editor,
    render_ticket_modality_supplements_editor, render_ticket_pricing_editor,
    render_seasonal_price_editor, render_currency_check, render_readonly_source, render_optional_time_input,
    render_closable_image_section, render_url_image_picker, render_doc_image_picker,
    render_stock_photo_picker, render_closedtour_supplements, render_child_age_band, render_extra_child_notice,
    render_child_discount_editor,
    _clean_time_table_rows, _safe_cell_str, _safe_float, _safe_int,
    _add_page_images_to_doc_pool,
)
from web_extractor import get_page_text, get_page_image_bytes, short_page_text_warning
from pexels_client import search_images
from pixabay_client import search_images as search_images_pixabay
# CONFIRMED (product owner, 2026-08-22): switched from freeimage_client (free third-party
# public host) to r2_client (private Cloudflare R2 bucket you own) - see r2_client.py's module
# docstring for why and for the one-time setup this requires.
from r2_client import upload_images as upload_images_r2
from r2_client import upload_images_with_errors as upload_images_r2_with_errors
from geocoding_client import geocode_search, geocode
import transfer_matcher
import transport_matcher
import platform_store
import service_notes
import cancellation_links
import cancellation_bulk_transport
import supplier_images
import weekly_review
import extraction_memory
import bulk_notes
import publish_advisor
import price_refresh
# The Translation Sync tool, merged in from the standalone momira-translation-sync
# app. Its own sync engines and API client live in separate modules (translator.py,
# state_store.py, sync_*.py, travelcompositor_api.py) and are untouched - see
# translation_tool.py's docstring for what changed at the UI layer and why.
from translation_tool import render_translation_tool, DEFAULT_TARGET_LANGUAGES
# The Supplier Discovery & Outreach tool, merged in from the standalone
# momira-suppliersearch-mail app (originally React + Express). Its discovery/vetting
# and email engines were ported to Python in outreach_discovery.py and
# outreach_email.py, both differential-tested against the original JavaScript.
from outreach_tool import render_outreach_tool
# PROTOTYPE (2026-08-19): "AI Trip Idea" - a customer's free-text trip idea turned into
# structured search criteria, shown to a human. Not connected to Travel Compositor yet - see
# trip_idea_tool.py's module docstring and the "client-trip-prompt-idea" project note.
from trip_idea_tool import render_trip_idea_tool
# PROTOTYPE (2026-08-19): "Package Rollover" - a human enters one Holiday Package ID, the tool
# looks up its real departure/price/hotel data and calendar from Travel Compositor and
# proposes a replacement departure under the confirmed rules. Read-only (real GET calls, no
# PUT yet) - see package_rollover_tool.py's module docstring and the
# "package-auto-rollover-rules" project note.
from package_rollover_tool import render_package_rollover_tool

FALLBACK_IMAGE = "https://multiwander.com/wp-content/uploads/2026/07/Please-load-images.png"
ALL_WEEKDAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


def _warn_page_image_upload_errors(errors):
    """Shows whatever _add_page_images_to_doc_pool's (or extract_images's) return value reports,
    right after every call site. CONFIRMED FIX (2026-08-30, reported: page images "not working at
    all" - every one of a page's found images unusable, with nothing on screen explaining why): a
    failed R2 upload used to be swallowed silently.

    GENERALIZED (2026-08-31, reported: "the App never even shows me available images...even
    though the document and/or the URL has some images included" - no images section appeared at
    all, not just broken thumbnails): this used to assume every message was specifically an R2
    "failed to upload" error and appended a hardcoded Public Access hint accordingly - actively
    MISLEADING for the two earlier failure points added that same day (the page couldn't be
    fetched at all; a document's embedded images couldn't be read at all), neither of which has
    anything to do with R2. Each message is now written to already be a complete, self-explanatory
    sentence at its source (see get_page_image_bytes, _add_page_images_to_doc_pool, and
    document_reader.extract_images's own docstrings for the three distinct cases), so this is just
    a plain, cause-agnostic wrapper that puts it on screen."""
    if not errors:
        return
    st.warning("⚠️ " + errors[0] + (f" (+{len(errors) - 1} more issue(s))" if len(errors) > 1 else ""))

# Session-state key prefixes used ONLY by the shared editable_field/
# editable_table widget helpers - never by any flow's own phase/queue
# control state (mct_phase, mm_queue, tk_..., etc. never start with any of
# these). Safe to sweep-clear in bulk: see _clear_batch_widget_state's
# docstring for why this is needed (positional-index widget keys getting
# reused by a different queue item after a skip or a fresh batch start).
SHARED_WIDGET_STATE_PREFIXES = ["_editing_", "_widgetval_", "pencil_", "save_", "editor_",
                                # "sn_" = service_notes widgets, also keyed per queue item -
                                # without this a one-off note typed on one service reappears
                                # on the next one and gets published to the wrong voucher.
                                "sn_"]

# Currency dropdown options - EUR/USD first as the ones actually used in
# practice, then the rest of the common ISO codes a DMC document might quote
# in, alphabetically. A dropdown (instead of free-text) prevents typos like
# "EURO" or "Eur" that Travel Compositor's API would otherwise reject or
# silently mishandle.
CURRENCY_OPTIONS = [
    "EUR", "USD", "GBP", "AUD", "CAD", "CHF", "CNY", "IDR", "INR", "JPY",
    "MXN", "NZD", "SEK", "SGD", "THB", "TRY", "VND", "ZAR",
]

TICKET_ACTION_LABELS = {
    "create": "1: Create new Ticket + 1 Modality",
    "add_option": "2: Add new Modality to existing Ticket",
    "update_ticket": "3: Update an existing Ticket",
    "update_option": "4: Update existing Ticket Modality",
}
TICKET_ACTION_FIELDS = {
    # NOTE: "create" deliberately does NOT include "modality_code" - Step 4's
    # queue-based flow (render_multi_ticket_flow) collects the Modality Code
    # per Ticket there instead, right next to the Ticket Code, so it's asked
    # exactly once instead of twice. See "modality_code" in ACTION_FIELDS below
    # for the identical ClosedTour case.
    "create": ["ticket_code", "min_passengers", "max_passengers", "currency", "on_request", "release_days"],
    "add_option": ["existing_ticket_code", "modality_code", "on_request"],
    # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-28): "3" used to always run the full,
    # expensive extraction (name/description/cancellation AND pricing/schedule) even
    # though it only ever published the non-pricing half - the pricing it extracted was
    # silently discarded. Now asks up front (see the "Price only"/"Whole ticket" radio in
    # Step 3) which half to actually do: "Price only" reuses "update_option"'s existing
    # cheap, pricing-only path outright (same extractor, same publish call); "Whole
    # ticket" keeps today's full extraction but NOW ACTUALLY PUBLISHES the pricing it
    # extracts instead of throwing it away. Either way requires knowing which Modality/
    # Option the pricing half applies to, so modality_code is asked unconditionally.
    "update_ticket": ["existing_ticket_code", "modality_code", "release_days"],
    "update_option": ["existing_ticket_code", "modality_code", "on_request"],
}

ACTION_LABELS = {
    "create": "1: Create new ClosedTour + 1 Modality",
    "add_option": "2: Add new Modality to existing ClosedTour",
    "update_tour": "3: Update an existing ClosedTour",
    "update_option": "4: Update existing ClosedTour Modality",
}
ACTION_FIELDS = {
    # NOTE: "create" deliberately does NOT include "modality_code" - Step 4's
    # queue-based flow (render_multi_tour_flow / the "Set up this tour" screen)
    # collects the Modality Code per tour there instead, right next to the Tour
    # Code, so it's asked exactly once instead of twice (previously a human had
    # to type it here in Step 3 AND again in Step 4 - pure double work, since
    # Step 3's value was never even used).
    "create": ["provider_code", "min_pax", "max_pax", "currency", "on_request", "release_days"],
    "add_option": ["existing_tour_code", "modality_code", "on_request"],
    # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-28), identical fix to Ticket's "update_ticket"
    # above: "3" used to always run the full, expensive extraction (name/description/
    # cancellation AND pricing/schedule) even though it only ever published the non-pricing
    # half - the pricing it extracted was silently discarded. Now asks up front (see the
    # "Price only"/"Whole tour" radio in Step 3) which half to actually do: "Price only"
    # reuses "update_option"'s existing cheap, pricing-only path outright (same extractor,
    # same publish call); "Whole tour" keeps today's full extraction but NOW ACTUALLY
    # PUBLISHES the pricing it extracts instead of throwing it away. Either way requires
    # knowing which Modality/Option the pricing half applies to, so modality_code is asked
    # unconditionally.
    "update_tour": ["existing_tour_code", "modality_code", "release_days"],
    # CONFIRMED REAL RULE (product owner): an UPDATE never asks for things the live record
    # already has - the code, the currency, the min/max passengers. "update_option" used to
    # ask for currency, which meant re-picking (and possibly re-denominating) the currency
    # of a tour that already has one. It is inherited from the fetched tour instead.
    "update_option": ["existing_tour_code", "modality_code", "on_request"],
}

def _data_fingerprint(data):
    """
    A stable snapshot of an extracted-data dict, used to detect "this was
    edited after the payload was last built" in the legacy (non-queue)
    single-Tour/single-Ticket flows.

    CONFIRMED REAL BUG (internal audit): in those legacy flows, clicking
    "Check Locations & Continue" builds st.session_state.payloads
    ONCE and caches it - but the price/supplements/stop-sales/itinerary
    tables above that button stay fully editable and visible afterward too.
    Editing one of those tables mutates `data` in place and reruns the
    script (editable_table always reruns on save), but nothing rebuilt the
    cached payload - so the human sees their edit reflected on screen, but
    the STALE pre-edit payload is what actually gets published, silently
    discarding the edit. The newer queue-based flow avoids this entirely by
    rebuilding the payload fresh on every render instead of caching it -
    not adopted wholesale here since that would mean re-resolving
    destinations against Travel Compositor on every single keystroke
    anywhere on the page, which is wasteful. Instead: fingerprint `data`
    right after a successful build, and re-check it on every render -  if
    it no longer matches, the cached payload is stale and must be
    discarded, forcing an explicit rebuild rather than silently publishing
    outdated data.

    Returns None (never matches anything, safest default - always treated
    as "changed") if `data` can't be serialized for some unexpected reason,
    rather than crashing the page over what is just a staleness check.
    """
    try:
        return json.dumps(data, sort_keys=True, default=str)
    except Exception:
        return None


def _fetch_url_text_safe(url_val):
    """
    Fetches a product page URL's text via get_page_text(), but never lets a
    fetch failure abort the whole "gather content" step - the URL field is
    always optional, so a site refusing the request (bot protection, a dead
    link, a timeout) shouldn't block extraction when document(s) were also
    provided.

    CONFIRMED REAL BUG: a supplier site (farahnilecruise.com) rejected the
    fetch with "406 Client Error: Not Acceptable" - this used to bubble up
    through the generic except-block and get shown as "Something went wrong
    while talking to the AI service", which is actively misleading (the AI
    was never involved; the failure was fetching the web page itself) and,
    worse, threw away an uploaded document that had already been provided
    alongside the URL.

    Returns (text, None) on success, or (None, human_readable_error) on
    failure - the error is phrased around "the product page URL" specifically.
    """
    try:
        text = get_page_text(url_val)
        # CONFIRMED BUG FIX (audit 2026-09-01, MEDIUM/LOW batch 2): a fetch that "succeeds" but
        # returns almost no readable text used to have no visible signal at all - joins the same
        # _scanned_doc_warnings list the document-upload path already surfaces on screen, rather
        # than introducing a new return shape that would ripple into every call site.
        _url_warning = short_page_text_warning(url_val, text)
        if _url_warning:
            st.session_state.setdefault("_scanned_doc_warnings", []).append(_url_warning)
        return text, None
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status in (401, 403, 406, 429):
            return None, (f"the website blocked the request (HTTP {status}) - some sites reject automated "
                           f"fetching; try downloading the page as a PDF and uploading it instead")
        return None, f"the website returned an error (HTTP {status})"
    except requests.exceptions.Timeout:
        return None, "the website took too long to respond and timed out"
    except requests.exceptions.RequestException as e:
        return None, f"couldn't reach the website ({str(e)[:150]})"
    except Exception as e:
        return None, f"unexpected error reading the page ({str(e)[:150]})"


# editable_table / editable_field / render_stop_sales_editor / render_cancellation_policy_editor /
# render_seasonal_price_editor / render_readonly_source / render_optional_time_input /
# _clean_time_table_rows / _safe_cell_str / _safe_float / _safe_int / the HTML<->plain-text
# converters - all moved to ui_components.py (imported at the top of this file) so every one of
# the five product-type flows shares exactly one implementation. See ui_components.py's
# module docstring for why.


def _clean_modality_code(raw_code):
    """Shared Modality-Code cleanup for every flow that lets an AI suggest one (the ClosedTour
    create flow's select_modalities phase, and this file's render_multi_modality_flow). CONFIRMED
    FIX (real production failure): a Modality Code is sent straight to Travel Compositor's API -
    "." used to slip through here on some call sites (only / \\ + - were stripped), and an
    AI-suggested code with extra descriptive text (e.g. "Standard English min. 2 people") got
    rejected outright by the real API ("Modality code ... not found in contract modalities")."""
    return "".join(c for c in (raw_code or "") if c not in "/\\+-.")


def _modality_code_suspicious(code):
    """Shared suspicious-code heuristic - see _clean_modality_code's docstring. A Modality Code
    is only ever safe if it's the short category name itself; anything with a stray junk word or
    unusually long text is almost certainly going to be rejected by the real API the same way
    "Standard English min. 2 people" was.

    CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): this check (and _clean_modality_code's
    "." stripping) used to exist only in the single-tour ClosedTour create flow's
    select_modalities phase (as a nested function, `_mct_modality_code_suspicious`) - the sibling
    "add multiple Modalities to an existing ClosedTour" flow (render_multi_modality_flow below)
    built its own candidate codes with none of this hardening, so the exact same real-world
    failure mode (a descriptive AI-suggested code getting rejected by Travel Compositor) could
    still happen there. Promoted to module level so both flows share one implementation."""
    c = (code or "")
    if len(c) > 24:
        return True
    lowered = c.lower()
    return any(junk in lowered for junk in ("people", " pax", "person", " min ", " max ", "min ", "max "))


def render_multi_modality_flow(client, url=None, uploaded_files=None):
    """
    Queue-based flow for adding MULTIPLE modalities from one shared source:
    1. Reuse the URL/document(s) already provided above (no re-entry), auto-detect
       distinct pricing categories, and let the human explicitly SELECT which
       detected ones to actually include (+ add more manually if needed)
    2. Review each SELECTED one individually - its OWN focused AI extraction (via a
       per-item hint), so modalities never get mixed up with each other
    3. Publish all of them SEQUENTIALLY, one real POST call at a time, each
       with its own clear success/failure status (not one opaque batch call)
    """
    if "mm_phase" not in st.session_state:
        st.session_state.mm_phase = "gather"

    supplier_id = st.session_state.cfg_supplier_id
    existing_tour_code = st.session_state.cfg_existing_tour_code
    currency = st.session_state.cfg_currency
    on_request = st.session_state.cfg_on_request

    # ------------------------------------------------------------------
    # PHASE 1: detect modalities from the source already provided above
    # ------------------------------------------------------------------
    if st.session_state.mm_phase == "gather":
        if not (url or uploaded_files):
            st.info("Provide a URL and/or upload document(s) above, then click below.")
        if st.button("🔎 Detect Modalities", disabled=not (url or uploaded_files)):
            with st.spinner("Gathering content and detecting distinct pricing categories..."):
                try:
                    combined_parts = []
                    if url:
                        page_text, page_text_err = _fetch_url_text_safe(url)
                        if page_text is not None:
                            combined_parts.append(f"--- SOURCE: WEB PAGE ({url}) ---\n{page_text}")
                        else:
                            st.warning(f"⚠️ Couldn't fetch the product page URL: {page_text_err}.")
                    for uploaded in (uploaded_files or []):
                        suffix = os.path.splitext(uploaded.name)[1]
                        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                            tmp.write(uploaded.getbuffer())
                            tmp_path = tmp.name
                        _doc_text = extract_raw_text(tmp_path)
                        _scan_warning = document_reader_scanned_warning(tmp_path, _doc_text)
                        if _scan_warning:
                            st.session_state.setdefault("_scanned_doc_warnings", []).append(_scan_warning)
                        combined_parts.append(f"--- SOURCE: UPLOADED DOCUMENT ({uploaded.name}) ---\n{_doc_text}")
                        os.remove(tmp_path)

                    if not combined_parts:
                        st.error("Nothing to extract - the product page URL couldn't be fetched and no document(s) were provided.")
                        st.stop()

                    raw_text = "\n\n".join(combined_parts)
                    detected = detect_multiple_modalities(raw_text)

                    candidates = []
                    for m in detected:
                        raw_code = (m.get("suggested_code") or m.get("label") or "").strip()
                        # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): this flow used
                        # to build its own candidate codes with none of the hardening the
                        # sibling ClosedTour create flow has - see _clean_modality_code's
                        # docstring for the real production failure this closes.
                        clean_code = _clean_modality_code(raw_code)
                        candidates.append({"code": clean_code, "hint": m.get("label", ""), "selected": True})
                    if not candidates:
                        candidates = [{"code": "", "hint": "", "selected": True}]

                    st.session_state.mm_raw_text = raw_text
                    st.session_state.mm_candidates = candidates
                    st.session_state.mm_phase = "prepare_queue"
                    st.rerun()
                except Exception as e:
                    st.error(f"Extraction failed: {friendly_error_message(e)}")
        return

    # ------------------------------------------------------------------
    # PHASE 2: explicitly SELECT which detected modalities to include
    # ------------------------------------------------------------------
    if st.session_state.mm_phase == "prepare_queue":
        st.subheader("Modalities detected - select which ones to include")
        st.caption("Untick any that don't apply - only SELECTED modalities will be reviewed and published. "
                  "Edit codes/hints as needed, or add more rows manually.")

        candidates = st.session_state.mm_candidates
        for i, cand in enumerate(candidates):
            ccol1, ccol2, ccol3 = st.columns([1, 3, 3])
            with ccol1:
                cand["selected"] = st.checkbox("Include", value=cand["selected"], key=f"mm_sel_{i}")
            with ccol2:
                cand["code"] = st.text_input("Modality Code", value=cand["code"], key=f"mm_code_{i}")
            with ccol3:
                cand["hint"] = st.text_input("Focus Hint", value=cand["hint"], key=f"mm_hint_{i}")

        if st.button("➕ Add another Modality manually"):
            candidates.append({"code": "", "hint": "", "selected": True})
            st.rerun()

        # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): see _clean_modality_code's
        # docstring - this flow had neither the suspicious-code warning nor a duplicate-code
        # check its sibling ClosedTour create flow has, so a descriptive AI-suggested code (or
        # two modalities accidentally sharing one code) could sail through to publish and fail
        # at Travel Compositor with no earlier warning.
        suspicious_codes = [c["code"] for c in candidates if c["selected"] and _modality_code_suspicious(c["code"])]
        if suspicious_codes:
            st.warning(
                "🤔 These Modality Codes look unusually long/descriptive for a real code, which has "
                "caused real publish failures before (Travel Compositor rejects anything that isn't "
                "the short category name itself, e.g. 'Standard' not 'Standard English min. 2 people') "
                "- please shorten them to just the core category name: " + ", ".join(f"'{c}'" for c in suspicious_codes)
            )

        new_queue = []
        for cand in candidates:
            if not cand["selected"]:
                continue
            code = cand["code"].strip()
            if not code:
                continue
            new_queue.append({"code": code, "hint": cand["hint"].strip(), "data": None, "confirmed": False})

        dup_codes = {}
        for item in new_queue:
            dup_codes.setdefault(item["code"], []).append(item)
        dup_codes = {code: v for code, v in dup_codes.items() if len(v) > 1}
        if dup_codes:
            st.error(f"🚫 Duplicate Modality Codes: {list(dup_codes.keys())} - each Modality needs its own unique code.")

        st.caption(f"**{len(new_queue)}** modality(ies) selected to review and publish.")

        if st.button("➡️ Start Reviewing", type="primary", disabled=not new_queue or bool(dup_codes)):
            st.session_state.mm_queue = new_queue
            st.session_state.mm_queue_index = 0
            st.session_state.mm_phase = "reviewing"
            st.rerun()
        return

    # ------------------------------------------------------------------
    # PHASE 3: review each modality individually, one at a time
    # ------------------------------------------------------------------
    if st.session_state.mm_phase == "reviewing":
        idx = st.session_state.mm_queue_index
        queue = st.session_state.mm_queue
        current = queue[idx]

        st.subheader(f"Reviewing modality {idx + 1} of {len(queue)}: **{current['code']}**")
        st.progress((idx) / len(queue))

        render_skip_item_button(
            current['code'], queue, idx,
            "mm_queue", "mm_queue_index",
            ["mm_phase", "mm_raw_text", "mm_candidates", "mm_queue", "mm_queue_index"],
            button_key=f"mm_skip_{idx}",
            widget_state_prefixes=["mm_"] + SHARED_WIDGET_STATE_PREFIXES
        )

        if current["data"] is None:
            with st.spinner(f"Extracting pricing/schedule focused on '{current['hint'] or current['code']}'..."):
                current["data"] = extract_option_only_data(st.session_state.mm_raw_text, human_hint=current["hint"])

        data = current["data"]

        if data.get("schedule_notes"):
            st.info(f"🔎 {data['schedule_notes']}")

        data["operational_days"] = st.multiselect(
            "Operational Days", ALL_WEEKDAYS, default=data.get("operational_days", ALL_WEEKDAYS), key=f"mm_days_{idx}"
        )
        render_stop_sales_editor(data, f"mm_{idx}")

        default_price_list = sorted(
            coerce_price_list_shape(data.get("price_list"), currency)[0] or [{"name": "Example row", "startDate": "2027-01-01", "endDate": "2027-12-31",
                                        "price": {"singlePrice": {"amount": 0, "currency": currency},
                                                 "doublePrice": {"amount": 0, "currency": currency}}}],
            key=lambda e: e.get("startDate", "")
        )
        price_df_rows = []
        for entry in default_price_list:
            price = entry.get("price") if isinstance(entry.get("price"), dict) else {}
            def _amt(key, price=price):
                block = price.get(key)
                if isinstance(block, dict):
                    block = block.get("amount")
                try:
                    return float(block) if block not in (None, "") else None
                except (TypeError, ValueError):
                    return None
            price_df_rows.append({"Name": entry.get("name", ""), "Start Date": _disp(entry.get("startDate", "")),
                                  "End Date": _disp(entry.get("endDate", "")), "Single": _amt("singlePrice"),
                                  "Double": _amt("doublePrice"), "Triple": _amt("triplePrice"), "Quadruple": _amt("quadruplePrice")})
        price_df = pd.DataFrame(price_df_rows)

        def _save_mm_price_list(edited_df, data=data, currency=currency):
            def _row_to_entry(row):
                price = {}
                for col, key in [("Single", "singlePrice"), ("Double", "doublePrice"), ("Triple", "triplePrice"), ("Quadruple", "quadruplePrice")]:
                    val = row.get(col)
                    if val is not None and not pd.isna(val):
                        price[key] = {"amount": float(val), "currency": currency}
                entry = {"startDate": _iso(_safe_cell_str(row.get("Start Date"))), "endDate": _iso(_safe_cell_str(row.get("End Date"))), "price": price}
                name = _safe_cell_str(row.get("Name")).strip()
                if name:
                    entry["name"] = name
                return entry
            data["price_list"] = sorted(
                [_row_to_entry(r) for _, r in edited_df.iterrows() if _iso(_safe_cell_str(r.get("Start Date"))) and _iso(_safe_cell_str(r.get("End Date")))],
                key=lambda e: e.get("startDate", "")
            )

        editable_table(f"Pricing - {current['code']}", price_df, f"mm_pricing_{idx}", on_save=_save_mm_price_list)
        render_extra_child_notice(data, f"mm_{idx}")
        render_child_discount_editor(data, f"mm_{idx}", currency)

        st.subheader(f"🤖 Tell AI what to fix - {current['code']}")
        st.caption("Ask a question, or tell it to fix something (e.g. 'the price should be x3 for 3 "
                  "nights, not the per-night rate'). Applies real changes when you ask for them.")
        mm_clarify_q = st.text_input("Your message", key=f"mm_clarify_input_{idx}")
        if render_house_rule_shortcut(mm_clarify_q, "ClosedTour", f"mm_{idx}"):
            pass
        elif not mm_clarify_q.strip():
            st.caption(f"Type a message above first — Send stays disabled until there's something to send. "
                      f"Start with \"{HOUSE_RULE_CODEWORD}\" to save a standing rule for every ClosedTour "
                      f"supplier instead of a one-off fix.")
        if not mm_clarify_q.strip().upper().startswith(HOUSE_RULE_CODEWORD.upper()) and st.button(
                "Send", disabled=not mm_clarify_q.strip(), key=f"mm_clarify_send_{idx}"):
            with st.spinner("Thinking..."):
                result = apply_clarification(st.session_state.mm_raw_text, data, mm_clarify_q)
                st.session_state[f"mm_clarify_result_{idx}"] = result
                remember_clarification(clarify_supplier_id(), "ClosedTour", mm_clarify_q, result)
                if result.get("changes"):
                    apply_clarify_changes(data, result, currency)
                    reset_stale_editable_field_widgets(result["changes"], key_suffix=f"_{idx}")
                    if "price_list" in result["changes"]:
                        st.session_state[f"_editing_table_mm_pricing_{idx}"] = False
                    if "operational_days" in result["changes"]:
                        st.session_state.pop(f"mm_days_{idx}", None)
                    if "stop_sales" in result["changes"]:
                        st.session_state[f"_editing_table_mm_{idx}_stop_sales"] = False
                st.rerun()
        if st.session_state.get(f"mm_clarify_result_{idx}"):
            r = st.session_state[f"mm_clarify_result_{idx}"]
            render_clarify_result(r)
        remember_memory_panel(clarify_supplier_id(), "ClosedTour", "mm")

        is_last = idx == len(queue) - 1
        btn_label = "✅ Confirm this modality & Finish Review" if is_last else "✅ Confirm this modality & Continue →"
        if st.button(btn_label, type="primary", disabled=not data.get("price_list")):
            current["confirmed"] = True
            if is_last:
                st.session_state.mm_phase = "publishing"
            else:
                st.session_state.mm_queue_index += 1
            st.rerun()
        if not data.get("price_list"):
            st.info("Add at least one price row before continuing.")
        return

    # ------------------------------------------------------------------
    # PHASE 4: publish all confirmed modalities, ONE BY ONE
    # ------------------------------------------------------------------
    if st.session_state.mm_phase == "publishing":
        queue = st.session_state.mm_queue
        st.subheader(f"Ready to publish {len(queue)} modalities - one by one")
        for q in queue:
            st.write(f"- **{q['code']}** ({len(q['data'].get('price_list', []))} price row(s))")

        if st.button("🚀 Publish all (one by one)", type="primary"):
            for q in queue:
                with st.spinner(f"Publishing '{q['code']}'..."):
                    try:
                        pre_config = HumanPreConfig(
                            supplier_id=supplier_id,
                            # CONFIRMED BUG FIX (audit CRITICAL #3, 2026-09-01): raw read of
                            # fetched_tour_provider_code, un-validated against which tour it was
                            # actually fetched for - see fetched_tour_matches_code()'s docstring.
                            provider_code=(
                                st.session_state.get("fetched_tour_provider_code")
                                if fetched_tour_matches_code(existing_tour_code) else None
                            ) or "XXX-1",
                            min_pax=1, max_pax=9, currency=currency,
                            modality_code=q["code"], on_request=on_request
                        )
                        payloads = build_closed_tour_payloads(pre_config, q["data"], client)
                        if payloads["tour_option_error"]:
                            show_publish_error(f"prepare **{q['code']}**'s payload", payloads['tour_option_error'])
                            continue
                        result, used_code = try_code_variants(
                            lambda c: client.create_closed_tour_option(supplier_id, c, payloads["tour_option_payload"]),
                            existing_tour_code
                        )
                        if "error" in result:
                            show_publish_error(f"publish **{q['code']}**", result)
                        else:
                            st.success(f"✅ **{q['code']}**: published successfully (code `{used_code}`).")
                    except Exception as e:
                        show_publish_error(f"publish **{q['code']}** (unexpected error - skipped, rest of batch continues)", str(e))
                        continue

        st.write("")
        st.divider()
        if st.button("🆕 Start a new batch"):
            for key in ["mm_phase", "mm_raw_text", "mm_candidates", "mm_queue", "mm_queue_index"]:
                st.session_state.pop(key, None)
            # Also clear per-item widget state (see _clear_batch_widget_state) -
            # otherwise a fresh batch's first item (always idx==0) can inherit
            # leftover edited values from the PREVIOUS batch's idx==0 item.
            # CONFIRMED BUG FIX (full-app audit MEDIUM (plausible), 2026-09-01): this used to
            # sweep only SHARED_WIDGET_STATE_PREFIXES (the generic editing-table widgets shared
            # across every flow), never this flow's OWN "mm_"-prefixed widget keys (Modality
            # Code/hint text inputs, checkboxes) - those could carry over into the next batch's
            # positionally-identical widget.
            _clear_batch_widget_state(["mm_"] + SHARED_WIDGET_STATE_PREFIXES)
            st.rerun()
        return


def _reset_mct_state():
    """Clears all state for the single-ClosedTour create flow, ready to start over."""
    for key in ["mct_phase", "mct_raw_text", "mct_candidates", "mct_doc_raw_images",
               "mct_hosted_image_candidates", "mct_tour",
               # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): the "Just published: X"
               # success panel (and its "Add another Modality to this same ClosedTour" prefill)
               # read these two keys, but they were only ever SET on a successful publish and
               # never cleared here - so starting a genuinely new ClosedTour after a publish
               # kept showing tour A's "just published" panel on tour B's screen, and "Add
               # another Modality" on tour B would prefill tour A's code.
               "just_published_tour_code", "just_published_supplier_id"]:
        st.session_state.pop(key, None)
    # CONFIRMED BUG FIX (full-app audit MEDIUM (plausible), 2026-09-01): see the matching fix in
    # render_multi_modality_flow's "Start a new batch" - this used to sweep only the generic
    # SHARED_WIDGET_STATE_PREFIXES, never this flow's own "mct_"-prefixed widget keys (Modality
    # Code/hint inputs, geo-confirm checkboxes, etc.), so a fresh ClosedTour could inherit
    # leftover typed values from the previous one's positionally-identical widgets.
    _clear_batch_widget_state(["mct_"] + SHARED_WIDGET_STATE_PREFIXES)


def _new_mct_tour(candidate, tour_code):
    """Builds the single-tour state dict once the human has picked (or auto-picked,
    if only one was ever detected) which ClosedTour to create."""
    return {
        "label": candidate.get("label", ""),
        "nights_hint": candidate.get("nights"),
        "is_genuine_variant": candidate.get("is_genuine_variant", False),
        "tour_code": tour_code,
        "main_data": None,
        "modality_candidates": None,
        "modalities": [],
        "modality_index": 0,
    }


def render_multi_tour_flow(client, supplier_id, currency, on_request, release_days, url, uploaded_files,
                          min_pax=1, max_pax=9, default_tour_code="", extraction_hint=None):
    """
    Single-ClosedTour create flow (CONFIRMED REDESIGN - replaces the old
    multi-tour batch/queue flow, which let several AI-detected "variants"
    each become their own separate ClosedTour in one pass - that's exactly
    what caused real confusion in practice: a document describing ONE tour
    with two Modalities ("Standard | English" / "Superior | English", same
    itinerary) got misdetected as "2 variants", and the old flow tried to
    create TWO separate ClosedTours for it.

    New design: only ONE ClosedTour is ever created per run through this
    flow. If the AI detects what might be multiple distinct ClosedTours in
    the source, the human picks exactly ONE to proceed with - Tour Code and
    Modality Code are NOT collected at that stage (Tour Code already came
    from Step 3; Modality Code isn't relevant until Modalities are set up
    later). Step by step:
      1. gather            - reuse URL/document(s) from Step 4, detect distinct ClosedTours
      2. select_tour       - human picks ONE (skipped automatically if only one was found)
      3. reviewing_main    - review the TOUR-level info only (name, description, itinerary,
                              hotels, included/excluded, meeting point, policy, images) - no
                              pricing/supplements/operational days here, those are per-Modality
      4. select_modalities - AI detects distinct Modalities (room/cabin/pricing categories);
                              human confirms which to include (min. 1 required), can add more
      5. reviewing_modality - EACH Modality reviewed individually and sequentially: its own
                              focused extraction (with an editable AI hint), operational days,
                              stop sales, pricing, and supplements - a universal supplement
                              entered on Modality 1 is carried forward as an editable starting
                              point for the rest, instead of being retyped every time
      6. final_review      - recap of the main tour info + every Modality, with "Edit" links
                              back into any earlier step
      7. publishing        - unchanged from before: create the tour + first Modality's option
                              (active), then each remaining Modality's option, then deactivate
                              if the human chose draft/inactive
    """
    if "mct_phase" not in st.session_state:
        st.session_state.mct_phase = "gather"

    # ------------------------------------------------------------------
    # PHASE 1: detect distinct ClosedTours from the source already provided above
    # ------------------------------------------------------------------
    if st.session_state.mct_phase == "gather":
        if not (url or uploaded_files):
            st.info("Provide a URL and/or upload document(s) above, then click below.")
        if st.button("🔎 Detect ClosedTour(s)", disabled=not (url or uploaded_files)):
            with st.spinner("Gathering content and detecting distinct ClosedTours..."):
                try:
                    combined_parts = []
                    doc_raw_images = []
                    doc_image_urls = []
                    seen_image_hashes = set()
                    if url:
                        page_text, page_text_err = _fetch_url_text_safe(url)
                        if page_text is not None:
                            combined_parts.append(f"--- SOURCE: WEB PAGE ({url}) ---\n{page_text}")
                        else:
                            st.warning(f"⚠️ Couldn't fetch the product page URL: {page_text_err}.")
                    for uploaded in (uploaded_files or []):
                        suffix = os.path.splitext(uploaded.name)[1]
                        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                            tmp.write(uploaded.getbuffer())
                            tmp_path = tmp.name
                        _doc_text = extract_raw_text(tmp_path)
                        _scan_warning = document_reader_scanned_warning(tmp_path, _doc_text)
                        if _scan_warning:
                            st.session_state.setdefault("_scanned_doc_warnings", []).append(_scan_warning)
                        combined_parts.append(f"--- SOURCE: UPLOADED DOCUMENT ({uploaded.name}) ---\n{_doc_text}")
                        remaining_budget = 12 - len(doc_raw_images)
                        _doc_image_errors = []
                        embedded_images = extract_images(tmp_path, max_images=remaining_budget, seen_hashes=seen_image_hashes, errors=_doc_image_errors, label=uploaded.name) if remaining_budget > 0 else []
                        if embedded_images:
                            for i, (img_bytes, ext) in enumerate(embedded_images):
                                doc_raw_images.append((f"{os.path.splitext(uploaded.name)[0]}_img{i+1}.{ext or 'jpg'}", img_bytes))
                            try:
                                new_urls, _upload_errors = upload_images_r2_with_errors(embedded_images)
                                doc_image_urls.extend(new_urls)
                                _doc_image_errors.extend(_upload_errors)
                            except Exception as e:
                                _doc_image_errors.append(f"'{uploaded.name}': R2 upload failed entirely - {e}")
                        _warn_page_image_upload_errors(_doc_image_errors)
                        os.remove(tmp_path)

                    if not combined_parts:
                        st.error("Nothing to extract - the product page URL couldn't be fetched and no document(s) were provided.")
                        st.stop()

                    raw_text = "\n\n".join(combined_parts)
                    detected = detect_tour_variants(raw_text)

                    candidates = []
                    for v in detected:
                        candidates.append({
                            "label": v.get("label", ""), "nights": v.get("nights"),
                            # This label came from a REAL AI-detected candidate, so it's safe
                            # to later tell the extraction "only extract this one, ignore the
                            # rest" - see is_genuine_variant usage in PHASE 3 below.
                            "is_genuine_variant": True,
                        })
                    if not candidates:
                        candidates = [{"label": "", "nights": None, "is_genuine_variant": False}]

                    # Fold any images found on the page into the SAME pool as
                    # document-embedded images (downloaded server-side, not
                    # hotlinked) - see _add_page_images_to_doc_pool's docstring.
                    _warn_page_image_upload_errors(_add_page_images_to_doc_pool(url, doc_raw_images, doc_image_urls))

                    # Only offer "needs hosting" for images that DIDN'T get auto-uploaded -
                    # if every image already got a real URL, showing them again in a second
                    # section would just be a confusing, redundant duplicate of "Images found" above.
                    if len(doc_image_urls) >= len(doc_raw_images):
                        doc_raw_images = []

                    st.session_state.mct_raw_text = raw_text
                    st.session_state.mct_candidates = candidates
                    st.session_state.mct_doc_raw_images = doc_raw_images
                    st.session_state.mct_hosted_image_candidates = list(dict.fromkeys(doc_image_urls))
                    st.session_state.mct_phase = "select_tour"
                    st.rerun()
                except Exception as e:
                    st.error(f"Detection failed: {friendly_error_message(e)}")
        return

    # ------------------------------------------------------------------
    # PHASE 2: human picks exactly ONE ClosedTour to create (only shown if
    # more than one was detected - otherwise auto-proceeds with the one found)
    # ------------------------------------------------------------------
    if st.session_state.mct_phase == "select_tour":
        candidates = st.session_state.mct_candidates

        if len(candidates) <= 1:
            chosen = candidates[0] if candidates else {"label": "", "nights": None, "is_genuine_variant": False}
            st.session_state.mct_tour = _new_mct_tour(chosen, default_tour_code)
            st.session_state.mct_phase = "reviewing_main"
            st.rerun()
            return

        st.subheader(f"{len(candidates)} possible ClosedTours detected - choose ONE to create")
        st.caption("This document seems to describe more than one distinct tour product (different "
                  "length/itinerary) - only ONE ClosedTour is created per run through this flow. Pick "
                  "the one you want below; run this again with a different Tour Code (Step 3) for any "
                  "others. Tour Code and Modality Code aren't needed here - Modalities are set up in "
                  "the next steps, after the main tour info is confirmed.")

        # SAFETY NET (confirmed real case): genuine different ClosedTours almost
        # always differ in duration - that's usually the whole point of them being
        # different products. If every candidate here reports the SAME nights,
        # that's a strong signal the AI actually found different Modalities/room-
        # categories (e.g. separate "Standard | English" / "Superior | English"
        # pricing+accommodation blocks for the SAME itinerary), not different tour
        # products - in that case just pick any one below (they're really the same
        # tour) and add the others as Modalities in the next steps.
        distinct_nights = {c.get("nights") for c in candidates if c.get("nights") is not None}
        if len(distinct_nights) <= 1:
            st.warning(
                "🤔 These all report the same length - that often means this is really ONE tour with "
                "different Modalities (e.g. 'Standard' vs 'Superior' pricing/accommodation for the same "
                "itinerary), not genuinely different tour products. If so, just pick any one below - "
                "you'll be able to add the others as Modalities of this same tour in the next steps."
            )

        labels = [
            (c.get("label") or "(unnamed)") + (f" ({c['nights']} nights)" if c.get("nights") else "")
            for c in candidates
        ]
        choice_idx = st.radio("Which ClosedTour do you want to create?", list(range(len(candidates))),
                             format_func=lambda i: labels[i], key="mct_tour_choice")

        if st.button("➡️ Start Reviewing", type="primary"):
            st.session_state.mct_tour = _new_mct_tour(candidates[choice_idx], default_tour_code)
            st.session_state.mct_phase = "reviewing_main"
            st.rerun()

        with st.expander("Not what you wanted?"):
            if st.button("🔙 Start over", key="mct_cancel_select"):
                _reset_mct_state()
                st.rerun()
        return

    # ------------------------------------------------------------------
    # PHASE 3: review the TOUR-LEVEL info only (no pricing/supplements here -
    # those are handled per-Modality in the phases below)
    # ------------------------------------------------------------------
    if st.session_state.mct_phase == "reviewing_main":
        tour = st.session_state.mct_tour

        st.subheader(f"Tour details: {tour['label'] or tour['tour_code'] or '(new tour)'}")
        with st.expander("Not what you wanted?"):
            if st.button("🔙 Start over", key="mct_cancel_main"):
                _reset_mct_state()
                st.rerun()

        if tour["main_data"] is None:
            # CONFIRMED BUG FIX: only pass a variant_hint when this label came from a
            # REAL AI-detected candidate (is_genuine_variant). Passing a human-typed
            # label as a variant_hint told the AI "extract ONLY the variant named X,
            # ignore everything else" - if the source has no variant literally named
            # that, the AI finds no match and returns an empty extraction.
            variant_hint = tour["label"] if tour.get("is_genuine_variant") else None
            with st.spinner(f"Extracting tour details{f' focused on ' + repr(tour['label']) if variant_hint else ''}..."):
                try:
                    tour["main_data"] = extract_structured_data(
                        st.session_state.mct_raw_text, variant_hint=variant_hint,
                        human_hint=with_learned_guidance(supplier_id, "ClosedTour", extraction_hint)
                    )
                    tour["main_data"]["image_urls"] = [FALLBACK_IMAGE]
                    reset_child_age_band_widgets("mct_main")
                    # Only fills in when this document didn't state its own cancellation
                    # terms - see apply_cancellation_link_default's docstring. Runs once,
                    # here at extraction time, not inside the review widgets below.
                    tour["_cancellation_link_scope"] = cancellation_links.apply_cancellation_link_default(
                        tour["main_data"], supplier_id, "ClosedTour")
                except Exception as e:
                    st.error(f"⚠️ Couldn't extract tour details: {friendly_error_message(e)}")
                    if st.button("🔄 Retry extraction", key="mct_retry_main"):
                        st.rerun()
                    return

        data = tour["main_data"]
        if not data.get("meeting_point"):
            data["meeting_point"] = ("Meet your guide in the airport arrival hall or, if you are already in "
                                     "the tour's starting city, in your hotel lobby.")

        tour["tour_code"] = st.text_input(
            "Tour Code", value=tour["tour_code"], key="mct_tour_code",
            help="Your own reference code for this tour, e.g. 'BKK-1' - carried over from Step 3, edit "
                 "here if you want to change it."
        )
        # CONFIRMED REAL COMPLAINT (product owner): "Only because I forgot to change the Code, I
        # have to start all over, there must be a way that either the system first checks if the
        # code is available or the human must be able to change the code even at the last step
        # before publishing." This flow (the batch ClosedTour wizard) had NO code-availability
        # check anywhere, even though check_code_availability() already existed and was already
        # wired into the Ticket batch flow's equivalent code-entry step - just never carried over
        # here. Checking right where the code is typed catches a collision before any of Steps
        # 5/6's review/pricing/image work happens, instead of only at the very last "Publish"
        # click after all of that is done. See the "publishing" phase below for the second half
        # of the fix - an editable Tour Code right on the final screen too, so a code that still
        # turns out to be taken (this check can be inconclusive, see its own docstring) never
        # forces starting over.
        _mct_code_check = check_code_availability(client, "tour", supplier_id, tour["tour_code"])
        if _mct_code_check and _mct_code_check["exists"]:
            st.error(f"🚫 Tour Code `{tour['tour_code']}` is ALREADY TAKEN by an existing tour "
                     f"(\"{_mct_code_check.get('name') or '(unnamed)'}\") - change it above before "
                     f"publishing, or this will fail at the very last step.")

        editable_field("Tour name", data, "tour_name", widget="text_input", key_suffix="_main")
        editable_field("Description", data, "description", widget="html_text_area", height=150, key_suffix="_main")
        editable_field("Hotels", data, "hotels_text", widget="text_area", height=100, key_suffix="_main")
        editable_field("Included", data, "included", widget="html_list_area", height=100, key_suffix="_main")
        editable_field("Excluded", data, "excluded", widget="html_list_area", height=100, key_suffix="_main")
        editable_field("Meeting point", data, "meeting_point", widget="text_input", key_suffix="_main")
        editable_field("Policy remarks", data, "policy_remarks", widget="text_area", height=80, key_suffix="_main")
        # CONFIRMED HOUSE RULE (product owner, 2026-08-24): a document's "Please remember to bring"
        # list is great customer-facing info - it's appended to the voucher remarks at build time
        # (see builder._with_what_to_bring), and editable here so a human can correct/add to it.
        editable_field("What to bring (added to voucher remarks)", data, "what_to_bring",
                       widget="text_area", height=80, key_suffix="_main")
        if tour.get("_cancellation_link_scope"):
            st.caption(f"ℹ️ This document didn't state its own cancellation terms - the table below "
                      f"was filled in from {tour['_cancellation_link_scope']}. Edit or clear it if "
                      f"this tour needs different terms.")
        render_cancellation_policy_editor(data, "mct_main")
        editable_field("Nights", data, "nights", widget="number_input", key_suffix="_main")

        tcol1, tcol2 = st.columns(2)
        with tcol1:
            render_optional_time_input("Start Time", data, "start_time", "mct_start_time_main")
        with tcol2:
            render_optional_time_input("End Time", data, "end_time", "mct_end_time_main", default_time_str="18:00:00")

        render_child_age_band(data, "mct_main")

        dest_rows = [{"#": i + 1, "Destination": d} for i, d in enumerate(data.get("itinerary_destinations", []))]
        dest_df = pd.DataFrame(dest_rows) if dest_rows else pd.DataFrame(columns=["#", "Destination"])

        def _save_mct_destinations(edited_df, data=data):
            data["itinerary_destinations"] = [
                str(row.get("Destination") or "").strip() for _, row in edited_df.iterrows()
                if _safe_cell_str(row.get("Destination")).strip()
            ]
        editable_table(
            "Itinerary destinations (in visit order)", dest_df, "mct_destinations_main",
            on_save=_save_mct_destinations,
            column_config={"#": st.column_config.NumberColumn(disabled=True)}
        )

        st.markdown("**Images**")
        if data.get("image_urls") == [FALLBACK_IMAGE] or not data.get("image_urls"):
            st.caption("⚠️ No real image picked yet - using a generic placeholder. Pick at least one real image below.")
        else:
            st.caption(f"{len([u for u in data.get('image_urls', []) if u != FALLBACK_IMAGE])} image(s) selected.")

        def _mct_add_url_images():
            selected = render_url_image_picker(st.session_state.mct_hosted_image_candidates, "mct_found_main")
            if selected:
                current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                data["image_urls"] = current_imgs + selected
                return len(selected)
            return 0

        render_closable_image_section(
            bool(st.session_state.get("mct_hosted_image_candidates")),
            f"🖼️ Images found in your document/page ({len(st.session_state.get('mct_hosted_image_candidates') or [])})",
            "mct_found_main_closed", _mct_add_url_images
        )

        def _mct_add_doc_image():
            added = render_doc_image_picker(st.session_state.mct_doc_raw_images, "mct_doc_main")
            if added:
                current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                data["image_urls"] = current_imgs + [added]
                return 1
            return 0

        render_closable_image_section(
            bool(st.session_state.get("mct_doc_raw_images")),
            f"📥 Images needing hosting ({len(st.session_state.get('mct_doc_raw_images') or [])})",
            "mct_doc_main_closed", _mct_add_doc_image
        )

        mct_default_query = tour["label"] or data.get("tour_name", "") or (data.get("itinerary_destinations", [""])[0] if data.get("itinerary_destinations") else "")

        def _mct_add_pexels():
            selected = render_stock_photo_picker("Pexels", search_images, mct_default_query, "mct_pexels_main")
            if selected:
                current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                data["image_urls"] = current_imgs + selected
                return len(selected)
            return 0

        render_closable_image_section(True, "🖼️ Search free stock photos (Pexels)", "mct_pexels_main_closed", _mct_add_pexels)

        def _mct_add_pixabay():
            selected = render_stock_photo_picker("Pixabay", search_images_pixabay, mct_default_query, "mct_pixabay_main")
            if selected:
                current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                data["image_urls"] = current_imgs + selected
                return len(selected)
            return 0

        render_closable_image_section(True, "🖼️ Search free stock photos (Pixabay)", "mct_pixabay_main_closed", _mct_add_pixabay)

        # Supplements belong to the tour, not to a Modality - so they are set HERE, once, before
        # the Modality list. See render_closedtour_supplements.
        render_closedtour_supplements(data, "mct_main")

        st.markdown("**🤖 Tell AI what to fix**")
        mct_clarify_q = st.text_input("Your message", key="mct_clarify_input_main")
        if render_house_rule_shortcut(mct_clarify_q, "ClosedTour", "mct_main"):
            pass
        elif not mct_clarify_q.strip():
            st.caption(f"Type a message above first — Send stays disabled until there's something to send. "
                      f"Start with \"{HOUSE_RULE_CODEWORD}\" to save a standing rule for every ClosedTour "
                      f"supplier instead of a one-off fix.")
        if not mct_clarify_q.strip().upper().startswith(HOUSE_RULE_CODEWORD.upper()) and st.button(
                "Send", disabled=not mct_clarify_q.strip(), key="mct_clarify_send_main"):
            with st.spinner("Thinking..."):
                result = apply_clarification(st.session_state.mct_raw_text, data, mct_clarify_q)
                st.session_state["mct_clarify_result_main"] = result
                remember_clarification(clarify_supplier_id(supplier_id), "ClosedTour", mct_clarify_q, result)
                if result.get("changes"):
                    apply_clarify_changes(data, result, currency)
                    # CONFIRMED REAL BUG: this used to reset ONLY the Supplements table's
                    # edit-mode flag - every plain field above it (tour_name, description,
                    # hotels_text, included, excluded, meeting_point, policy_remarks, nights)
                    # could go stale the same way Stop Sales once did, if a human had one open
                    # (or later reopened it) after the AI changed it. The itinerary destinations
                    # table needed the same table-key reset the other tables already got.
                    reset_stale_editable_field_widgets(result["changes"], key_suffix="_main")
                    if "supplements" in result["changes"]:
                        st.session_state["_editing_table_mct_main_supplements"] = False
                    if "itinerary_destinations" in result["changes"]:
                        st.session_state["_editing_table_mct_destinations_main"] = False
                st.rerun()
        if st.session_state.get("mct_clarify_result_main"):
            r = st.session_state["mct_clarify_result_main"]
            render_clarify_result(r)
        remember_memory_panel(clarify_supplier_id(supplier_id), "ClosedTour", "mctmain")

        ready = bool((data.get("tour_name") or "").strip()) and bool((tour["tour_code"] or "").strip())
        if st.button("✅ Confirm main tour info & Continue to Modalities", type="primary", disabled=not ready):
            st.session_state.mct_phase = "select_modalities"
            st.rerun()
        if not ready:
            st.info("Tour name and Tour Code are required before continuing to Modalities.")
        return

    # ------------------------------------------------------------------
    # PHASE 4: AI detects distinct Modalities - human confirms which to include
    # ------------------------------------------------------------------
    if st.session_state.mct_phase == "select_modalities":
        tour = st.session_state.mct_tour
        main_name = (tour["main_data"] or {}).get("tour_name") or tour["tour_code"]
        st.subheader(f"Modalities for: {main_name}")

        if tour["modality_candidates"] is None:
            with st.spinner("Detecting pricing categories (Modalities)..."):
                try:
                    detected = detect_multiple_modalities(st.session_state.mct_raw_text)
                except Exception:
                    detected = []  # best-effort - human can still add Modalities manually below
                candidates = []
                for m in detected:
                    label = (m.get("label") or "").strip()
                    raw_code = (m.get("suggested_code") or label or "").strip()
                    clean_code = _clean_modality_code(raw_code)
                    candidates.append({"code": clean_code, "hint": label, "selected": True})
                if not candidates:
                    candidates = [{"code": "", "hint": "", "selected": True}]
                tour["modality_candidates"] = candidates

        candidates = tour["modality_candidates"]
        st.caption("Auto-detected from your document where possible - untick any you don't want, edit the "
                  "code/hint, or add more manually. At least one Modality is required (a 'Modality' is "
                  "Travel Compositor's own term for the pricing option, e.g. 'Standard' or 'Deluxe').")

        suspicious_codes = [c["code"] for c in candidates if c["selected"] and _modality_code_suspicious(c["code"])]
        if suspicious_codes:
            st.warning(
                "🤔 These Modality Codes look unusually long/descriptive for a real code, which has "
                "caused real publish failures before (Travel Compositor rejects anything that isn't "
                "the short category name itself, e.g. 'Standard' not 'Standard English min. 2 people') "
                "- please shorten them to just the core category name: " + ", ".join(f"'{c}'" for c in suspicious_codes)
            )

        for i, cand in enumerate(candidates):
            c1, c2, c3, c4 = st.columns([1, 2, 3, 1])
            with c1:
                cand["selected"] = st.checkbox("Include", value=cand["selected"], key=f"mct_modcand_sel_{i}")
            with c2:
                cand["code"] = st.text_input(
                    "Modality Code", value=cand["code"], key=f"mct_modcand_code_{i}",
                    help="Just the short category name, e.g. 'Standard' or 'Deluxe' - should NOT include "
                         "descriptive text like the language or a minimum-pax note."
                )
            with c3:
                cand["hint"] = st.text_input("AI focus hint (optional)", value=cand["hint"], key=f"mct_modcand_hint_{i}")
            with c4:
                st.write("")
                if st.button("🗑️", key=f"mct_modcand_remove_{i}", help="Remove this Modality"):
                    candidates.pop(i)
                    # Widgets here are keyed by POSITION, so after the pop the candidate that
                    # shifts into slot i would re-render with the removed one's typed code -
                    # and that code is what gets published as the option code.
                    _clear_batch_widget_state(["mct_modcand_"])
                    st.rerun()

        if st.button("➕ Add another Modality manually"):
            candidates.append({"code": "", "hint": "", "selected": True})
            st.rerun()

        selected = [c for c in candidates if c["selected"]]
        missing = [c for c in selected if not (c["code"] or "").strip()]
        seen = {}
        for c in selected:
            seen.setdefault((c["code"] or "").strip(), []).append(c)
        dup_codes = {code: v for code, v in seen.items() if code and len(v) > 1}

        if missing:
            st.error("🚫 Every included Modality needs a Modality Code.")
        if dup_codes:
            st.error(f"🚫 Duplicate Modality Codes: {list(dup_codes.keys())} - each Modality needs its own unique code.")
        if not selected:
            st.info("Include at least one Modality to continue.")

        ready = bool(selected) and not missing and not dup_codes
        if st.button("➡️ Start Reviewing Modalities", type="primary", disabled=not ready):
            # CONFIRMED BUG FIX (full-app audit MEDIUM-HIGH, 2026-09-01): clicking "Add another
            # Modality" from final_review comes back through THIS same phase (select_modalities)
            # with the tour's existing modalities still holding real, human-corrected `data` -
            # but this list used to be rebuilt unconditionally with `data: None` for every
            # entry, discarding every already-reviewed Modality's corrected pricing/supplements/
            # operational days and forcing a full re-extraction (and re-billing) from scratch,
            # even for Modalities the operator never touched. Now carries forward the existing
            # `data`/`confirmed` for any code that already had a reviewed Modality under it -
            # only a genuinely NEW code (not previously reviewed) starts blank.
            existing_by_code = {m["code"]: m for m in (tour.get("modalities") or [])}
            tour["modalities"] = [
                (
                    {**existing_by_code[c["code"].strip()], "hint": c["hint"]}
                    if c["code"].strip() in existing_by_code
                    else {"code": c["code"].strip(), "hint": c["hint"], "data": None, "confirmed": False}
                )
                for c in selected
            ]
            tour["modality_index"] = 0
            st.session_state.mct_phase = "reviewing_modality"
            st.rerun()

        with st.expander("Not what you wanted?"):
            wcol1, wcol2 = st.columns(2)
            with wcol1:
                if st.button("🔙 Back to main tour info", key="mct_back_to_main"):
                    st.session_state.mct_phase = "reviewing_main"
                    st.rerun()
            with wcol2:
                if st.button("🔙 Start over", key="mct_cancel_modsel"):
                    _reset_mct_state()
                    st.rerun()
        return

    # ------------------------------------------------------------------
    # PHASE 5: review EACH Modality individually, one at a time
    # ------------------------------------------------------------------
    if st.session_state.mct_phase == "reviewing_modality":
        tour = st.session_state.mct_tour
        modalities = tour["modalities"]
        midx = tour["modality_index"]
        mod = modalities[midx]

        st.subheader(f"Modality {midx + 1} of {len(modalities)}: **{mod['code']}**")
        st.progress(midx / len(modalities))

        # CONFIRMED PRODUCT-OWNER COMPLAINT: "the hint for the ClosedTour modality must be more
        # present, as this must be the most important tool to read the Modalities rule." It was a
        # one-line box labelled "optional", below the fold and easy to skip - on the screen that
        # does the hardest reading in the app. Given prominence, room to write, and worked
        # examples, because a good hint here is worth more than any prompt change.
        st.markdown("#### 🎯 Tell the AI which Modality this is")
        st.caption("**This is the most useful thing on the screen.** The document prices several "
                  "categories and the AI has to pick the right row or column out of a rate grid. "
                  "One sentence naming where to look is worth more than any amount of correcting "
                  "afterwards.")
        st.caption("Good hints: *“the row labelled Per Junior Suite 333 — the rates are per suite "
                  "per night”* · *“the Deluxe column, second price block, ignore the Standard "
                  "table above it”* · *“Superior Class — its dates are the three ranges under "
                  "Normal”*.")
        mod["hint"] = st.text_area(
            f"Where in the document is '{mod['code']}' priced?",
            value=mod.get("hint", ""), key=f"mct_mod_hint_{midx}", height=90,
            placeholder=f"e.g. the row labelled '{mod['code']}' — rates are per person per night",
        )
        if not (mod.get("hint") or "").strip():
            st.info(f"No hint given, so the AI will search for **{mod['code']}** on its own. That "
                    f"works on a simple sheet; on a merged rate grid it is where things go wrong.")

        if mod["data"] is None:
            with st.spinner(f"Reading '{mod['code']}' carefully - this is the slow part, and "
                            f"deliberately so..."):
                try:
                    tour_nights = (tour["main_data"] or {}).get("nights")
                    # House rules and this supplier's learned corrections were NOT reaching this
                    # call - the one that builds the price list. Every pricing rule taught to the
                    # platform was being ignored at exactly the point it mattered most.
                    mod["data"] = extract_modality_data(
                        st.session_state.mct_raw_text, tour_nights=tour_nights,
                        human_hint=with_learned_guidance(
                            clarify_supplier_id(supplier_id), "ClosedTour",
                            mod["hint"] or mod["code"])
                    )
                except Exception as e:
                    st.error(f"⚠️ Couldn't extract pricing for '{mod['code']}': {friendly_error_message(e)}")
                    if st.button("🔄 Retry extraction", key=f"mct_mod_retry_{midx}"):
                        st.rerun()
                    return

                # CONFIRMED (per your answer): a universal supplement (e.g. an
                # airport transfer upgrade that applies to every Modality) is
                # carried forward from Modality 1 as an editable starting point
                # for every Modality after it, instead of making the human
                # retype a shared surcharge on every single Modality's screen.
                # Still fully editable/removable per Modality below - this is
                # just a convenience starting point, not a hard link between
                # them (editing Modality 2's copy never touches Modality 1's).
                if midx > 0 and modalities[0]["data"]:
                    mod["data"]["supplements"] = copy.deepcopy(modalities[0]["data"].get("supplements", []))

        if st.button("🔄 Re-extract with updated hint", key=f"mct_mod_reextract_{midx}"):
            mod["data"] = None
            # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): re-extraction replaces
            # mod["data"] with a fresh read of the document, but two widgets below kept their
            # PREVIOUS extraction's values regardless - Operational Days and the Child Discount
            # % - because Streamlit ignores a widget's default/value once session_state already
            # holds an entry for its key, and this Modality's `midx` never changed, so the same
            # bare keys (f"mct_mod_days_{midx}", the child-discount widget's key) survived the
            # rerun untouched. This is exactly the bug class widget_state.py exists to close
            # (see its own module docstring) - bumping this Modality's own widget generation
            # here (its `mct_mod_{midx}` flow name already isolates it from every OTHER
            # Modality, so this only affects the two widgets below, not the whole tour) gives
            # both a fresh, ungenerationed key with no session_state entry, so the freshly
            # extracted data's own values are what's shown after re-extraction, not stale ones.
            bump_widget_generation(f"mct_mod_{midx}")
            st.rerun()

        data = mod["data"]

        if data.get("schedule_notes"):
            st.info(f"🔎 {data['schedule_notes']}")

        data["operational_days"] = st.multiselect(
            "Operational Days", ALL_WEEKDAYS, default=data.get("operational_days", ALL_WEEKDAYS),
            key=flow_widget_key(f"mct_mod_{midx}", "days")
        )
        render_stop_sales_editor(data, f"mct_mod_{midx}")

        # CONFIRMED (product owner, 2026-08-19): "display the Currency within the modalities...
        # in case the human selected a wrong currency, so he could still change it... an extra
        # check." Only for a genuine create (this loop covers every Modality of the new tour) -
        # updates keep the existing tour's currency locked, per the rule above.
        currency = render_currency_check(currency, CURRENCY_OPTIONS, "cfg_currency", f"mct_mod_currency_{midx}")

        default_price_list = sorted(
            coerce_price_list_shape(data.get("price_list"), currency)[0] or [{"name": "Example row", "startDate": "2027-01-01", "endDate": "2027-12-31",
                                        "price": {"singlePrice": {"amount": 0, "currency": currency},
                                                 "doublePrice": {"amount": 0, "currency": currency}}}],
            key=lambda e: e.get("startDate", "")
        )
        price_df_rows = []
        for entry in default_price_list:
            price = entry.get("price") if isinstance(entry.get("price"), dict) else {}
            def _amt(key, price=price):
                block = price.get(key)
                if isinstance(block, dict):
                    block = block.get("amount")
                try:
                    return float(block) if block not in (None, "") else None
                except (TypeError, ValueError):
                    return None
            price_df_rows.append({"Name": entry.get("name", ""), "Start Date": _disp(entry.get("startDate", "")),
                                  "End Date": _disp(entry.get("endDate", "")), "Single": _amt("singlePrice"),
                                  "Double": _amt("doublePrice"), "Triple": _amt("triplePrice"), "Quadruple": _amt("quadruplePrice")})
        price_df = pd.DataFrame(price_df_rows)

        def _save_mct_price_list(edited_df, data=data, currency=currency):
            def _row_to_entry(row):
                price = {}
                for col, key in [("Single", "singlePrice"), ("Double", "doublePrice"), ("Triple", "triplePrice"), ("Quadruple", "quadruplePrice")]:
                    val = row.get(col)
                    if val is not None and not pd.isna(val):
                        price[key] = {"amount": float(val), "currency": currency}
                entry = {"startDate": _iso(_safe_cell_str(row.get("Start Date"))), "endDate": _iso(_safe_cell_str(row.get("End Date"))), "price": price}
                name = _safe_cell_str(row.get("Name")).strip()
                if name:
                    entry["name"] = name
                return entry
            data["price_list"] = sorted(
                [_row_to_entry(r) for _, r in edited_df.iterrows() if _iso(_safe_cell_str(r.get("Start Date"))) and _iso(_safe_cell_str(r.get("End Date")))],
                key=lambda e: e.get("startDate", "")
            )
        editable_table(f"Pricing - {mod['code']}", price_df, f"mct_mod_pricing_{midx}", on_save=_save_mct_price_list)
        render_extra_child_notice(data, f"mct_mod_{midx}")
        # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): the Child Discount % widget
        # (built inside render_child_discount_editor from this key_prefix) is the other half
        # of the "Re-extract with updated hint" staleness bug fixed above - see the comment on
        # the re-extract button. Generation-scoping just this one call's key_prefix (not the
        # bare "mct_mod_{midx}" used by render_stop_sales_editor/render_extra_child_notice/the
        # pricing table right above, which are untouched) fixes only the widget the audit
        # confirmed goes stale, without touching sibling widgets' own key stability.
        render_child_discount_editor(data, flow_widget_key(f"mct_mod_{midx}", "cde"), currency)

        # CONFIRMED PRODUCT-OWNER CORRECTION: supplements belong to the TOUR, not to a
        # Modality - see render_closedtour_supplements. Edited once on the main tour screen.
        st.caption("💡 **Supplements are not set here.** A ClosedTour's supplements are set once "
                  "for the whole tour and apply to every Modality - they are on the main tour "
                  "screen, before the Modality list.")
        _tour_supplements = (st.session_state.get("mct_tour", {}).get("main_data", {})
                             .get("supplements") or [])
        if _tour_supplements:
            st.caption("In force for this tour: " +
                       ", ".join(x.get("name", "(unnamed)") for x in _tour_supplements if isinstance(x, dict)))

        st.markdown(f"**🤖 Tell AI what to fix - {mod['code']}**")
        mct_mod_clarify_q = st.text_input("Your message", key=f"mct_mod_clarify_input_{midx}")
        if render_house_rule_shortcut(mct_mod_clarify_q, "ClosedTour", f"mct_mod_{midx}"):
            pass
        elif not mct_mod_clarify_q.strip():
            st.caption(f"Type a message above first — Send stays disabled until there's something to send. "
                      f"Start with \"{HOUSE_RULE_CODEWORD}\" to save a standing rule for every ClosedTour "
                      f"supplier instead of a one-off fix.")
        if not mct_mod_clarify_q.strip().upper().startswith(HOUSE_RULE_CODEWORD.upper()) and st.button(
                "Send", disabled=not mct_mod_clarify_q.strip(), key=f"mct_mod_clarify_send_{midx}"):
            with st.spinner("Thinking..."):
                result = apply_clarification(st.session_state.mct_raw_text, data, mct_mod_clarify_q)
                st.session_state[f"mct_mod_clarify_result_{midx}"] = result
                remember_clarification(clarify_supplier_id(supplier_id), "ClosedTour", mct_mod_clarify_q, result)
                if result.get("changes"):
                    apply_clarify_changes(data, result, currency)
                    # Reset the affected widgets' state so they immediately reflect the
                    # AI's change instead of showing stale previously-typed/edited values -
                    # same fix applied to the main tour info's own clarify box.
                    reset_stale_editable_field_widgets(result["changes"], key_suffix=f"_{midx}")
                    if "price_list" in result["changes"]:
                        st.session_state[f"_editing_table_mct_mod_pricing_{midx}"] = False
                    if "supplements" in result["changes"]:
                        st.session_state[f"_editing_table_mct_mod_supplements_{midx}"] = False
                    if "operational_days" in result["changes"]:
                        st.session_state.pop(f"mct_mod_days_{midx}", None)
                    if "stop_sales" in result["changes"]:
                        st.session_state[f"_editing_table_mct_mod_{midx}_stop_sales"] = False
                st.rerun()
        if st.session_state.get(f"mct_mod_clarify_result_{midx}"):
            r = st.session_state[f"mct_mod_clarify_result_{midx}"]
            render_clarify_result(r)
        remember_memory_panel(clarify_supplier_id(supplier_id), "ClosedTour", "mctmod")

        is_last = midx == len(modalities) - 1
        btn_label = "✅ Confirm this Modality & Finish Modalities" if is_last else "✅ Confirm this Modality & Continue →"
        if st.button(btn_label, type="primary", disabled=not data.get("price_list")):
            mod["confirmed"] = True
            if is_last:
                st.session_state.mct_phase = "final_review"
            else:
                tour["modality_index"] += 1
            st.rerun()
        if not data.get("price_list"):
            st.info("Add at least one price row before continuing.")

        with st.expander("Not what you wanted?"):
            ncol1, ncol2, ncol3 = st.columns(3)
            with ncol1:
                if midx > 0 and st.button("⬅️ Previous Modality", key=f"mct_mod_prev_{midx}"):
                    tour["modality_index"] -= 1
                    st.rerun()
            with ncol2:
                if st.button("🔙 Back to Modality selection", key=f"mct_mod_back_{midx}"):
                    st.session_state.mct_phase = "select_modalities"
                    st.rerun()
            with ncol3:
                if st.button("🔙 Start over", key=f"mct_mod_cancel_{midx}"):
                    _reset_mct_state()
                    st.rerun()
        return

    # ------------------------------------------------------------------
    # PHASE 6: final recap of the tour + all Modalities, still editable
    # ------------------------------------------------------------------
    if st.session_state.mct_phase == "final_review":
        tour = st.session_state.mct_tour
        main_data = tour["main_data"]

        st.subheader(f"Final review: {main_data.get('tour_name') or tour['tour_code']}")
        st.caption("Review everything below before publishing - click 'Edit' on any section to go back "
                  "and adjust it, your other progress is kept.")

        with st.expander("📋 Main tour info", expanded=False):
            st.write(f"**Tour Code:** {tour['tour_code']}")
            st.write(f"**Name:** {main_data.get('tour_name')}")
            st.write(f"**Nights:** {main_data.get('nights')}")
            st.write(f"**Itinerary:** {', '.join(main_data.get('itinerary_destinations', [])) or '(none)'}")
            st.write(f"**Images:** {len([u for u in main_data.get('image_urls', []) if u != FALLBACK_IMAGE])} selected")
        if st.button("✏️ Edit main tour info"):
            st.session_state.mct_phase = "reviewing_main"
            st.rerun()

        for i, mod in enumerate(tour["modalities"]):
            mdata = mod["data"] or {}
            with st.expander(f"📋 Modality: {mod['code']}", expanded=False):
                st.write(f"**Price rows:** {len(mdata.get('price_list', []))}")
                st.write(f"**Supplements:** {len(mdata.get('supplements', []))}")
                st.write(f"**Operational Days:** {', '.join(mdata.get('operational_days', []))}")
            if st.button(f"✏️ Edit Modality '{mod['code']}'", key=f"mct_final_edit_mod_{i}"):
                tour["modality_index"] = i
                st.session_state.mct_phase = "reviewing_modality"
                st.rerun()

        if st.button("➕ Add another Modality"):
            st.session_state.mct_phase = "select_modalities"
            st.rerun()

        if st.button("✅ Confirm this tour & Finish Review", type="primary"):
            st.session_state.mct_phase = "publishing"
            st.rerun()

        with st.expander("Not what you wanted?"):
            if st.button("🔙 Start over", key="mct_cancel_final"):
                _reset_mct_state()
                st.rerun()
        return

    # ------------------------------------------------------------------
    # PHASE 7: publish - unchanged sequence (create tour + first Modality's
    # option active, then each remaining Modality's option, then deactivate
    # if the human chose draft/inactive)
    # ------------------------------------------------------------------
    if st.session_state.mct_phase == "publishing":
        tour = st.session_state.mct_tour
        main_data = tour["main_data"]
        modalities = tour["modalities"]
        st.subheader(f"Ready to publish: {main_data.get('tour_name') or tour['tour_code']}")

        # CONFIRMED REAL COMPLAINT (product owner): "Only because I forgot to change the Code, I
        # have to start all over ... the human must be able to change the code even at the last
        # step before publishing." The early check added at the "reviewing_main" phase above
        # catches most collisions before all of Steps 5/6's work happens, but that check can be
        # INCONCLUSIVE (see check_code_availability's own docstring - a transient API failure or
        # a code-variant mismatch means "couldn't verify", not "definitely free") - so a
        # collision can still only surface here, at the actual Publish click. Editing right here
        # (same pattern as the itinerary-destinations fix a few lines below) means a rejected
        # "already exists" error is a one-field fix and a re-click, never a reason to abandon the
        # whole tour and start over - every other Step 5/6 field (images, pricing, itinerary)
        # stays exactly as entered.
        tour["tour_code"] = st.text_input(
            "Tour Code", value=tour["tour_code"], key="mct_publish_tour_code",
            help="Change this here if Publish below rejects it as already taken - nothing else "
                 "on this tour needs re-entering."
        )
        _mct_publish_code_check = check_code_availability(client, "tour", supplier_id, tour["tour_code"])
        if _mct_publish_code_check and _mct_publish_code_check["exists"]:
            st.error(f"🚫 Tour Code `{tour['tour_code']}` is ALREADY TAKEN by an existing tour "
                     f"(\"{_mct_publish_code_check.get('name') or '(unnamed)'}\") - change it above "
                     f"before publishing.")

        # CONFIRMED PRODUCT-OWNER CORRECTION: "Supplement within ClosedTour is set only once and
        # applies to ALL Modalities." So there is one list, taken from the main tour record, and
        # nothing is tagged to a Modality. Modality data is merged in for pricing and schedule,
        # which is why its own "supplements" key must not be allowed to overwrite the tour's.
        combined_data = dict(main_data)
        modality_zero = dict(modalities[0]["data"])
        modality_zero.pop("supplements", None)
        combined_data.update(modality_zero)
        combined_data["supplements"] = main_data.get("supplements") or []

        extra_note = f" + {len(modalities) - 1} more Modalit{'y' if len(modalities) == 2 else 'ies'}" if len(modalities) > 1 else ""
        with st.expander(f"**{tour['tour_code']}** - Modality: {modalities[0]['code']}{extra_note}", expanded=True):
            dup_warning = check_duplicate_tour_name(client, supplier_id, main_data.get("tour_name"))
            if dup_warning:
                st.warning(dup_warning)
            preview_payloads = None
            try:
                preview_pre_config = HumanPreConfig(
                    supplier_id=supplier_id, provider_code=tour["tour_code"],
                    min_pax=min_pax, max_pax=max_pax, currency=currency,
                    modality_code=modalities[0]["code"], on_request=on_request,
                    days_available_before_release=release_days
                )
                preview_payloads = build_closed_tour_payloads(preview_pre_config, combined_data, client)
            except Exception as e:
                # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-26): a real Tour Code ("Rak-2") was
                # rejected here for not matching a strict "XXX-Number" shape - "is not needed, it
                # is just a style and the app must still be able to publish this tour." The
                # underlying HumanPreConfig.provider_code validator (schemas.py) no longer
                # enforces that shape (see its own comment), so this message no longer assumes
                # that's the cause - a Tour Code just needs to be non-blank and free of '/'/'\\'
                # now, matching every other product's code field.
                st.error(f"⚠️ Couldn't preview this tour's destinations for Tour Code "
                        f"`{tour['tour_code']}`. Details: {str(e)[:300]}. Publishing below will also "
                        f"fail until fixed - go back and correct the Tour Code.")
            mct_has_unresolved = False
            if preview_payloads:
                for res in preview_payloads.get("itinerary_resolution", []):
                    if res["valid"]:
                        st.markdown(
                            f"<div style='background-color:#d4edda; color:#155724; padding:4px 10px; "
                            f"border-radius:4px; margin-bottom:2px; font-size:0.9em;'>✅ <b>{res['input']}</b> → "
                            f"<code>{res['destination']}</code> ({res.get('resolved_name', '')})</div>",
                            unsafe_allow_html=True
                        )
                    else:
                        mct_has_unresolved = True
                        st.markdown(
                            f"<div style='background-color:#f8d7da; color:#721c24; padding:4px 10px; "
                            f"border-radius:4px; margin-bottom:2px; font-size:0.9em;'>❌ <b>{res['input']}</b> → "
                            f"NOT FOUND in Travel Compositor</div>",
                            unsafe_allow_html=True
                        )

            # CONFIRMED FIX: a human used to be stuck here with no way to fix an
            # unresolved destination short of abandoning the whole tour ("Start a
            # new ClosedTour") - the itinerary is editable right on this screen
            # now, and saving it immediately re-checks against Travel Compositor
            # above (editable_table triggers a rerun on save, which rebuilds
            # combined_data/preview_payloads fresh from the updated main_data).
            if mct_has_unresolved:
                st.warning("🚫 Fix the destination(s) marked NOT FOUND above before publishing - either "
                          "correct the spelling/name, or replace it with the exact name Travel Compositor "
                          "uses. Edit the itinerary below, then Save to re-check.")
            mct_dest_rows = [{"#": i + 1, "Destination": d} for i, d in enumerate(main_data.get("itinerary_destinations", []))]
            mct_dest_df = pd.DataFrame(mct_dest_rows) if mct_dest_rows else pd.DataFrame(columns=["#", "Destination"])

            def _save_mct_publish_destinations(edited_df, main_data=main_data):
                main_data["itinerary_destinations"] = [
                    str(row.get("Destination") or "").strip() for _, row in edited_df.iterrows()
                    if _safe_cell_str(row.get("Destination")).strip()
                ]

            editable_table(
                "Itinerary destinations (in visit order)", mct_dest_df, "mct_publish_destinations",
                on_save=_save_mct_publish_destinations,
                column_config={"#": st.column_config.NumberColumn(disabled=True)}
            )

        mct_activation_choice = st.radio(
            "After publishing, should this Tour be Active or Inactive (draft)?",
            ["Inactive (draft) - recommended, review inside Travel Compositor before it goes live",
             "Active - live immediately"],
            index=0, key="mct_activation_choice"
        )
        mct_publish_as_active = mct_activation_choice.startswith("Active")

        mct_code_taken = bool(_mct_publish_code_check and _mct_publish_code_check["exists"])
        if mct_has_unresolved:
            st.info("Publishing is disabled until every destination above resolves - fix them in the "
                   "itinerary table above and re-check.")
        if mct_code_taken:
            st.info("Publishing is disabled until the Tour Code above is changed to one that isn't "
                   "already taken.")
        if st.button("🚀 Publish to Travel Compositor", type="primary", disabled=mct_has_unresolved or mct_code_taken):
            with st.spinner(f"Publishing '{tour['tour_code']}'..."):
                try:
                    pre_config = HumanPreConfig(
                        supplier_id=supplier_id, provider_code=tour["tour_code"],
                        min_pax=min_pax, max_pax=max_pax, currency=currency,
                        modality_code=modalities[0]["code"], on_request=on_request,
                        days_available_before_release=release_days
                    )
                    payloads = build_closed_tour_payloads(pre_config, combined_data, client)
                    if payloads.get("main_tour_error"):
                        show_publish_error(f"prepare **{tour['tour_code']}**'s payload", payloads["main_tour_error"])
                    elif payloads["tour_option_error"]:
                        show_publish_error(f"prepare **{tour['tour_code']}**'s payload", payloads["tour_option_error"])
                    elif payloads["unresolved_destinations"]:
                        st.error(f"❌ Couldn't resolve destination(s) {payloads['unresolved_destinations']} - "
                                f"fix the itinerary destinations and try again.")
                    else:
                        # CONFIRMED ROOT CAUSE (3 real production failures, KNO-1 - traced against the
                        # real Swagger, which shows modalityCodes/supplements[].modalityCodes as plain
                        # freeform [string] with NO enum/pattern - so "not found in contract modalities"
                        # is a runtime check, not a schema one. It kept failing even for a single, clean,
                        # self-consistent Modality Code, which rules out "declare more codes" fixes - the
                        # only reading left is that a code must correspond to an OPTION THAT ALREADY
                        # EXISTS for this tour at the moment it's referenced. At tour-CREATE time NO
                        # option exists yet for ANY Modality, so declaring modalityCodes (or supplements
                        # referencing a Modality via SupplementVO.modalityCodes) at that point always
                        # fails. FIX: mirror the existing "active" 2-phase pattern already used below -
                        # create the tour bare (no modalityCodes, no supplements), create every option
                        # (which is what actually registers each Modality code), THEN a follow-up PUT
                        # declares modalityCodes + supplements now that they genuinely refer to options
                        # that exist, and sets the final active/inactive state in the same call.
                        creation_payload = dict(payloads["main_tour_payload"])
                        creation_payload["active"] = True
                        creation_payload["modalityCodes"] = []
                        creation_payload["supplements"] = []
                        result = client.create_closed_tour(supplier_id, creation_payload)
                        if "error" in result:
                            show_publish_error(f"create **{tour['tour_code']}**", result)
                        else:
                            real_code = result.get("code", payloads["main_tour_code"])
                            # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): see
                            # mark_code_as_taken's docstring - keeps the availability cache in
                            # sync the instant this code goes live, not just after the next
                            # full re-check.
                            mark_code_as_taken("tour", supplier_id, tour["tour_code"], result.get("name"))
                            if real_code and real_code != tour["tour_code"]:
                                mark_code_as_taken("tour", supplier_id, real_code, result.get("name"))
                            created_modality_codes = []

                            # api_client.py's _request() already retries each individual POST
                            # attempt up to 6 times internally - this loop just still tries BOTH
                            # candidate codes (genuinely two different possible identifiers).
                            option_result = None
                            used_code = None
                            for candidate_code in [tour["tour_code"], real_code]:
                                option_result = client.create_closed_tour_option(supplier_id, candidate_code, payloads["tour_option_payload"])
                                if "error" not in option_result:
                                    used_code = candidate_code
                                    break
                            if "error" in option_result:
                                show_publish_error(f"create **{tour['tour_code']}**'s option (created as `{real_code}`)", option_result)
                            else:
                                st.success(f"✅ **{tour['tour_code']}**: base modality '{modalities[0]['code']}' created (option code used: `{used_code}`).")
                                created_modality_codes.append(modalities[0]["code"])

                            for m in modalities[1:]:
                                with st.spinner(f"Creating '{tour['tour_code']}' modality '{m['code']}'..."):
                                    try:
                                        mod_pre_config = HumanPreConfig(
                                            supplier_id=supplier_id, provider_code=tour["tour_code"],
                                            min_pax=min_pax, max_pax=max_pax, currency=currency,
                                            modality_code=m["code"], on_request=on_request,
                                            days_available_before_release=release_days
                                        )
                                        mod_payloads = build_closed_tour_payloads(mod_pre_config, m["data"], client)
                                        if mod_payloads["tour_option_error"]:
                                            show_publish_error(f"prepare **{tour['tour_code']}** modality '{m['code']}'", mod_payloads["tour_option_error"])
                                            continue
                                        mod_result, mod_used_code = try_code_variants(
                                            lambda c: client.create_closed_tour_option(supplier_id, c, mod_payloads["tour_option_payload"]),
                                            [tour["tour_code"], real_code]
                                        )
                                        if "error" in mod_result:
                                            show_publish_error(f"create **{tour['tour_code']}** modality '{m['code']}'", mod_result)
                                        else:
                                            st.success(f"✅ **{tour['tour_code']}**: modality '{m['code']}' created (code used: `{mod_used_code}`).")
                                            created_modality_codes.append(m["code"])
                                    except Exception as e:
                                        show_publish_error(f"create **{tour['tour_code']}** modality '{m['code']}' (unexpected error - skipped, rest continues)", str(e))
                                        continue

                            # Now that every successfully-created option genuinely exists, declare
                            # modalityCodes for real and restore the (already correctly-scoped)
                            # supplements list - but only keep supplements whose Modality actually
                            # got created above, so a failed Modality can't drag this PUT down too.
                            finalize_payload = dict(payloads["main_tour_payload"])
                            finalize_payload["code"] = real_code
                            finalize_payload["active"] = mct_publish_as_active
                            finalize_payload["modalityCodes"] = created_modality_codes
                            finalize_payload["supplements"] = [
                                s for s in payloads["main_tour_payload"].get("supplements", [])
                                if not s.get("modalityCodes") or all(c in created_modality_codes for c in s["modalityCodes"])
                            ]
                            if created_modality_codes:
                                finalize_result = client.update_closed_tour(supplier_id, finalize_payload)
                                if "error" in finalize_result:
                                    st.warning(f"⚠️ **{tour['tour_code']}**: tour and option(s) were created, but the "
                                              f"follow-up update (registering Modality codes/supplements and setting "
                                              f"the final active state) failed - {finalize_result}. The tour exists "
                                              f"in Travel Compositor but may need this finished manually.")
                                else:
                                    state_label = "ACTIVE" if mct_publish_as_active else "inactive/draft"
                                    st.success(f"✅ **{tour['tour_code']}** published successfully as `{real_code}` ({state_label}).")
                                    # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-26): "after I created a new
                                    # Closed Tour and I published it, I then want to start a new Batch...
                                    # in none of the new stage can I add the new ClosedTour Code to the new
                                    # batch. This causes always problems, if the human not automatically
                                    # goes back to Step 3 and changes the ClosedTour Code manually." Before
                                    # this, only the LEGACY update flow's own "add_option" success path (see
                                    # just below, ~line 11430) remembered what it had just published - this
                                    # CREATE flow's own success never did, so a code just created here was
                                    # never available to prefill Step 3's "Existing Tour Code" for a follow-up
                                    # action (add a Modality, update the tour, update a Modality's pricing) - the human had
                                    # to remember and retype it by hand, exactly the "always problems" being
                                    # reported. Recording it the same way the legacy flow already does.
                                    st.session_state.just_published_tour_code = real_code
                                    st.session_state.just_published_supplier_id = supplier_id
                            else:
                                # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): the tour
                                # ITSELF was already created above with `creation_payload["active"]
                                # = True` hardcoded (necessary at create time, before any option
                                # exists - see the "2-phase pattern" comment above) - so skipping
                                # the follow-up update entirely, as this branch used to, left the
                                # tour LIVE and ACTIVE on Travel Compositor with ZERO bookable
                                # Modalities: a tour that looks published but can never actually be
                                # booked, and whose Tour Code is now permanently taken (the tour DID
                                # get created, even though every option attempt failed) - blocking a
                                # simple retry under the same code. Explicitly deactivate it instead
                                # of leaving that silent trap.
                                deactivate_payload = dict(payloads["main_tour_payload"])
                                deactivate_payload["code"] = real_code
                                deactivate_payload["active"] = False
                                deactivate_payload["modalityCodes"] = []
                                deactivate_payload["supplements"] = []
                                deactivate_result = client.update_closed_tour(supplier_id, deactivate_payload)
                                if "error" in deactivate_result:
                                    st.error(f"❌ **{tour['tour_code']}**: no Modality options were created "
                                            f"successfully, AND the tour could not be deactivated afterward "
                                            f"({deactivate_result}) - it is LIVE on Travel Compositor as "
                                            f"`{real_code}` with zero bookable Modalities. Deactivate it "
                                            f"manually in Travel Compositor, or finish it there directly. "
                                            f"Its Tour Code is now taken.")
                                else:
                                    st.error(f"❌ **{tour['tour_code']}**: no Modality options were created "
                                            f"successfully. The tour was created on Travel Compositor as "
                                            f"`{real_code}` but has been deactivated since it has no "
                                            f"bookable Modality - it will not be sold. Its Tour Code is now "
                                            f"taken; fix the error(s) above and use 'Add a Modality' to "
                                            f"finish it (a different Tour Code cannot reuse this one).")
                except Exception as e:
                    show_publish_error(f"publish **{tour['tour_code']}** (unexpected error)", str(e))

        # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-26, see the docstring at just_published_tour_code
        # above): once a code has actually been published this run, offer real next steps that carry
        # it forward - not just "start a new (different) ClosedTour", which is for someone who wants
        # to leave this code behind entirely. Mirrors the legacy update flow's own "what next" panel
        # (~line 11437) so the same two concrete choices exist here, plus a third, more general one
        # for any OTHER action (update the tour, update a Modality) that also needs this same code.
        if st.session_state.get("just_published_tour_code"):
            st.divider()
            st.caption(f"Just published: **{st.session_state.just_published_tour_code}** "
                      f"(Supplier {st.session_state.just_published_supplier_id})")
            ncol1, ncol2, ncol3 = st.columns(3)
            with ncol1:
                if st.button("🆕 Start a new ClosedTour", help="Create a DIFFERENT, brand-new ClosedTour - "
                            "this code is not carried forward."):
                    _reset_mct_state()
                    st.rerun()
            with ncol2:
                if st.button("➕ Add another Modality to this same ClosedTour"):
                    prefill_tour_code = st.session_state.just_published_tour_code
                    prefill_supplier_id = st.session_state.just_published_supplier_id
                    keep_client = st.session_state.client
                    keep_suppliers = st.session_state.suppliers_cache
                    keep_product_type = st.session_state.product_type
                    keep_tool = st.session_state["active_tool"] if "active_tool" in st.session_state else None
                    st.session_state.clear()
                    st.session_state.client = keep_client
                    st.session_state.suppliers_cache = keep_suppliers
                    st.session_state.product_type = keep_product_type
                    st.session_state.active_tool = keep_tool
                    st.session_state.cfg_action = "add_option"
                    st.session_state.cfg_supplier_id = prefill_supplier_id
                    st.session_state.cfg_existing_tour_code = prefill_tour_code
                    st.session_state.prefill_existing_tour_code = prefill_tour_code
                    st.session_state.step1_confirmed = True
                    st.rerun()
            with ncol3:
                if st.button("🔧 Do something else with this Code",
                            help="Pick any other action at Step 1 (update this tour, or update a "
                                 "Modality's pricing) - the ClosedTour Code above will already be "
                                 "filled in for you once you reach Step 3."):
                    prefill_tour_code = st.session_state.just_published_tour_code
                    prefill_supplier_id = st.session_state.just_published_supplier_id
                    keep_client = st.session_state.client
                    keep_suppliers = st.session_state.suppliers_cache
                    keep_product_type = st.session_state.product_type
                    keep_tool = st.session_state["active_tool"] if "active_tool" in st.session_state else None
                    st.session_state.clear()
                    st.session_state.client = keep_client
                    st.session_state.suppliers_cache = keep_suppliers
                    st.session_state.product_type = keep_product_type
                    st.session_state.active_tool = keep_tool
                    # Deliberately NOT setting cfg_action/step1_confirmed here - the human still
                    # picks which action they want at Step 1, same as any fresh run. Only the
                    # code (and, as a convenience, the supplier) are carried forward so whichever
                    # action they choose that needs "Existing Tour Code" at Step 3 already has it.
                    st.session_state.cfg_prefill_supplier_id = prefill_supplier_id
                    st.session_state.prefill_existing_tour_code = prefill_tour_code
                    st.rerun()
            return

        if st.button("🆕 Start a new ClosedTour"):
            _reset_mct_state()
            st.rerun()
        return



# render_closable_image_section / _add_page_images_to_doc_pool / render_url_image_picker /
# render_doc_image_picker / render_stock_photo_picker - moved to ui_components.py, same reason
# as the block above.


# CONFIRMED REAL REQUEST (human feedback): a publish failure used to just
# show the raw error and leave the human to guess what to actually go fix.
# Each tuple is (substring to match in the error text - lowercase, a short
# "step_key" naming what kind of field is at fault, a plain-English
# description of what to check) - drawn from every real failure mode this
# app has hit and fixed over the course of this project, so these are
# CONFIRMED real patterns, not guesses.
_PUBLISH_ERROR_PATTERNS = [
    ("not found in contract modalities", "modality",
     "the Modality Code - it needs to exactly match (spelling and case) an option that actually exists for this tour/ticket"),
    ("closed tour not found", "code", "the Tour Code - it doesn't match an existing ClosedTour on Travel Compositor"),
    ("ticket not found", "code", "the Ticket Code - it doesn't match an existing Ticket on Travel Compositor"),
    ("already taken", "code", "the Tour/Ticket Code - choose a different one, the one entered is already in use"),
    ("already exists", "code", "the Tour/Ticket Code - choose a different one, the one entered is already in use"),
    ("localdate", "pricing", "every Start Date / End Date field (the Pricing table and Stop Sales) - one is blank or invalid"),
    ("localtime", "pricing", "the Start Time(s) field - one of the times isn't in a valid HH:MM format"),
    ("not json compliant", "pricing", "the Pricing / Supplements numbers - one is blank or invalid and needs a real number (or 0)"),
    ("argument must be a string or a real number", "pricing", "the Pricing / Supplements numbers - one of them isn't a valid number"),
    ("geolocation", "geolocation", "the Geolocation section - confirm the City resolved to a real location, or search/pick one manually if not"),
    ("not found in travel compositor", "destinations", "the itinerary destination(s) marked as not found - edit the spelling or pick a nearby place name"),
    ("modality code cannot contain", "modality", "the Modality Code - remove the '/', '\\\\', '+', or '-' character (these break URL lookups)"),
    ("field required", "review", "the field named just above this message - it was left blank"),
]

# Real numbered-step labels, ONLY for the two flows that actually have them
# (the legacy single-tour and single-ticket flows - "Step 1" through "Step
# 7"). The newer queue-based flows (mct/mt/tk multi-item flows) don't have
# numbered steps at all, so they fall back to flow-agnostic phrasing below
# rather than a made-up step number.
_LEGACY_TOUR_STEP_NAMES = {
    "modality": "Step 5 (Review & Edit)", "code": "Step 3 (Details for this action)",
    "pricing": "Step 5 (Review & Edit)", "geolocation": "Step 6 (Destination Resolution & Payload Preview)",
    "destinations": "Step 6 (Destination Resolution & Payload Preview)", "review": "Step 5 (Review & Edit)",
}
_LEGACY_TICKET_STEP_NAMES = {
    "modality": "Step 5 (Review & Edit)", "code": "Step 3 (Details for this action)",
    "pricing": "Step 5 (Review & Edit)", "geolocation": "Step 6 (Geolocation & Payload Preview)",
    "destinations": "Step 5 (Review & Edit)", "review": "Step 5 (Review & Edit)",
}


def _publish_error_guidance(error_text, flow=None):
    """
    Pattern-matches a publish-time error against _PUBLISH_ERROR_PATTERNS and
    returns a ready-to-show "here's what to go check" hint, or None if the
    error doesn't match anything recognized (callers should show a generic
    fallback in that case rather than nothing).

    flow: "tour_legacy" / "ticket_legacy" for the two flows with real
    numbered Steps - gives a literal "Step N" pointer. Any other value (or
    None, the default) gives flow-agnostic phrasing instead, since the
    newer queue-based flows don't have numbered steps to point to.
    """
    if not error_text:
        return None
    text = str(error_text).lower()
    step_names = {"tour_legacy": _LEGACY_TOUR_STEP_NAMES, "ticket_legacy": _LEGACY_TICKET_STEP_NAMES}.get(flow)
    for pattern, step_key, what_to_check in _PUBLISH_ERROR_PATTERNS:
        if pattern in text:
            where = step_names[step_key] if step_names else "the relevant section above"
            return f"👉 To fix this: go back to {where} and check {what_to_check}."
    return None


# Pydantic validation errors name the SCHEMA's own field path, e.g.
# "priceList.0.price.triplePrice.amount". That is exactly the information a human
# needs - which row, which column - but written in a language nobody in this office
# speaks. CONFIRMED REAL COMPLAINT (product owner, ASW-6): the screen said "review/edit
# that field" without ever naming the field. The two maps below turn the path back into
# the words that appear on the actual editing screen.
_VALIDATION_SECTION_LABELS = {
    "pricelist": "Pricing row", "prices": "Pricing row", "supplements": "Supplement",
    "stopsales": "Stop sales row", "cancellationranges": "Cancellation rule",
    "modalities": "Modality", "options": "Modality", "datasheets": "Datasheet",
    "pricesbyoccupancy": "Occupancy price", "inventory": "Inventory row",
}
_VALIDATION_FIELD_LABELS = {
    "singleprice": "the Single price", "doubleprice": "the Double price",
    "tripleprice": "the Triple price", "quadrupleprice": "the Quadruple price",
    "baseadultprice": "the Adult price", "basechildrenprice": "the Child price",
    "baseinfantprice": "the Infant price", "adultpricesupplement": "the Adult supplement",
    "startdate": "the Start date", "enddate": "the End date", "starttime": "the Start time",
    "amount": "the amount", "currency": "the currency", "price": "the price",
    "name": "the Name", "description": "the Description", "code": "the Code",
}
_PYDANTIC_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z0-9_\[\]]+)+$")


def _describe_validation_path(path):
    """'priceList.0.price.triplePrice.amount' -> 'Pricing row 1 - the Triple price'."""
    parts = [p for p in str(path).split(".") if p]
    where, field = None, None
    for i, part in enumerate(parts):
        low = part.lower()
        if low in _VALIDATION_SECTION_LABELS:
            label = _VALIDATION_SECTION_LABELS[low]
            nxt = parts[i + 1] if i + 1 < len(parts) else ""
            where = f"{label} {int(nxt) + 1}" if nxt.isdigit() else label
        elif low in _VALIDATION_FIELD_LABELS and low not in ("amount", "currency", "price"):
            field = _VALIDATION_FIELD_LABELS[low]
    if field is None:
        for part in reversed(parts):
            low = part.lower()
            if low in _VALIDATION_FIELD_LABELS:
                field = _VALIDATION_FIELD_LABELS[low]
                break
    if field is None and parts:
        field = f"'{parts[-1]}'"
    return f"{where} - {field}" if where else field


def humanise_validation_error(raw_error, limit=6):
    """Plain-English lines for a pydantic validation error, or [] if it isn't one.

    Deliberately DEDUPLICATES: one blank Triple column produces two pydantic errors
    (.amount and .currency), and one empty column deserves one sentence, not two."""
    text = str(raw_error or "")
    if "validation error" not in text.lower():
        return []
    lines = text.splitlines()
    seen, out, hidden = set(), [], 0
    for i, line in enumerate(lines):
        candidate = line.strip()
        if not candidate or not _PYDANTIC_PATH_RE.match(candidate):
            continue
        detail = lines[i + 1].strip() if i + 1 < len(lines) else ""
        low = detail.lower()
        if "field required" in low or "missing" in low or "none is not an allowed value" in low:
            problem = "was left blank - enter a number, or clear that whole column if it isn't sold"
        elif "not a valid" in low or "should be a valid" in low or "type_error" in low:
            problem = "isn't a valid value - check what was typed there"
        elif detail:
            problem = detail.split("[")[0].strip().rstrip(".").lower() or "was rejected"
        else:
            problem = "was rejected"
        described = _describe_validation_path(candidate)
        if described in seen:
            continue          # same field, second complaint (.amount then .currency)
        seen.add(described)
        if len(out) < limit:
            out.append(f"{described} {problem}.")
        else:
            hidden += 1       # only DISTINCT fields we didn't have room for
    if hidden:
        out.append(f"_(…and {hidden} more field(s) — the technical details below list every one.)_")
    return out


def show_publish_error(context_label, raw_error, flow=None):
    """
    Shows a simple, human-readable error summary by default - extracted from
    Travel Compositor's own nested error message when possible - with the
    full raw technical detail available in an expander for anyone who needs
    to see or report the exact API response. Also shows an actionable
    "go back and check ___" hint (see _publish_error_guidance) so a human
    isn't just left staring at a rejected error with no idea what to change.
    """
    extracted_detail = None
    try:
        if isinstance(raw_error, dict) and "message" in raw_error:
            inner = raw_error["message"]
            if isinstance(inner, str):
                try:
                    inner_parsed = json.loads(inner)
                    if isinstance(inner_parsed, dict) and "error" in inner_parsed:
                        errs = inner_parsed["error"]
                        extracted_detail = " / ".join(str(e) for e in errs) if isinstance(errs, list) else str(errs)
                except (json.JSONDecodeError, TypeError):
                    extracted_detail = inner
            else:
                extracted_detail = str(inner)
        elif isinstance(raw_error, str):
            # e.g. a Pydantic validation error - often multi-line, so keep the summary to the first line
            first_line = raw_error.strip().split("\n")[0]
            extracted_detail = first_line + ("..." if "\n" in raw_error.strip() else "")
    except Exception:
        pass

    field_lines = humanise_validation_error(raw_error)

    if field_lines:
        # A validation error already knows exactly which field is wrong. Say so, instead of
        # showing a pydantic path and telling the human to go and find it themselves.
        st.error(f"❌ Couldn't {context_label}. These fields need attention:\n\n"
                 + "\n".join(f"- {line}" for line in field_lines))
    elif extracted_detail:
        st.error(f"❌ Couldn't {context_label}: {extracted_detail}")
    else:
        st.error(f"❌ Couldn't {context_label}.")

    guidance = _publish_error_guidance(extracted_detail or raw_error, flow)
    if not guidance:
        guidance = (
            "👉 To fix this: open the technical details below to see exactly what "
            "was rejected, then go back and review/edit that field before trying again."
        )
    st.info(guidance)

    with st.expander("🔧 Technical details"):
        st.code(str(raw_error))


def remember_memory_panel(supplier_id, product_type, key_prefix):
    """Note that this screen has memory worth showing - rendered once, at the page bottom.

    CONFIRMED PRODUCT-OWNER REQUEST: "can we put the 'What the platform remembers' on the
    bottom." It was sitting in the middle of every review screen, between the AI's answer and
    the buttons, pushing the actual work down the page. It is reference material - useful to
    have, not something to read past on the way to publishing."""
    st.session_state["_memory_panel"] = {
        "supplier_id": supplier_id, "product_type": product_type, "key_prefix": key_prefix,
    }


def render_memory_panel_footer():
    """The queued memory panel, at the very bottom of the page."""
    panel = st.session_state.get("_memory_panel")
    if not panel:
        return
    st.divider()
    st.markdown("### 🧠 What the platform remembers")
    st.caption("Reference: the rules being applied to this product type, and what this supplier "
               "has taught the app. Nothing here changes until you change it.")
    render_house_rules(panel["product_type"], panel["key_prefix"])
    render_learned_instructions(panel["supplier_id"], panel["product_type"], panel["key_prefix"])


def render_house_rules(product_type, key_prefix):
    """Rules that hold for EVERY supplier of this product type - the answer to "I repeat myself".

    CONFIRMED REAL COMPLAINT (product owner): "The AI learning must understand basics, I repeat
    myself too often. As I have often the same problem." The memory only ever filed a correction
    under one supplier, so a fact about the trade - Nile cruise rates are quoted per night - had
    to be taught again for every supplier selling one. That is not a memory that is failing; it
    is a memory filed at the wrong level.

    A rule added here is fed into every extraction of this product type, for every supplier."""
    if not product_type:
        return
    try:
        rules = extraction_memory.list_house_rules(product_type)
    except Exception:
        return
    with st.expander(f"🏛️ House rules for every {product_type} supplier ({len(rules)})"):
        st.caption("Basics that are true of the trade, not of one supplier. These go into **every** "
                   "extraction for this product type - so a rule typed once here never needs "
                   "repeating on the next supplier's document.")
        for rule in rules:
            c1, c2 = st.columns([6, 1])
            with c1:
                st.markdown(f"- {rule.get('text', '')}")
            with c2:
                if st.button("Forget", key=f"{key_prefix}_house_forget_{rule.get('key')}"):
                    extraction_memory.forget_house_rule(product_type, rule.get("key"))
                    st.rerun()

        new_rule = st.text_area("Add a house rule", key=f"{key_prefix}_house_new", height=80,
                                placeholder="e.g. Nile Cruise prices are quoted per night - single "
                                            "price is nights x nightly rate, double is half of that.")
        if st.button("➕ Add for every supplier", key=f"{key_prefix}_house_add",
                     disabled=not new_rule.strip()):
            if extraction_memory.add_house_rule(product_type, new_rule.strip()):
                st.success("Added. It will be applied to every future extraction of this product type.")
            else:
                st.warning("That rule is already in the list, or it couldn't be saved - check the "
                           "Memory line at the bottom of the page.")
            st.rerun()

        if not rules:
            st.caption("None yet. The built-in pricing rules (per-night cruise maths, occupancy "
                       "consistency) are always applied regardless of this list - add anything else "
                       "you find yourself repeating.")


def render_learned_instructions(supplier_id, product_type, key_prefix):
    """What the app has actually learned from this supplier's corrections, and a way to drop any.

    CONFIRMED REAL COMPLAINT (product owner): "I often repeat myself." Until now the learning was
    entirely invisible - there was no way to tell a rule that had been absorbed from one that had
    silently failed to stick, so the only safe assumption was to type it again. Showing the list
    turns that into something checkable.

    It also puts the one rule worth knowing where it is needed: only an instruction that actually
    CHANGED something is kept, so anything typed while the AI returned no changes was never
    learned - which is exactly when a person is most likely to type it again."""
    if not (supplier_id and product_type):
        return
    try:
        learned = extraction_memory.list_instructions(str(supplier_id), product_type)
    except Exception:
        return
    label = (f"🧠 What the app has learned from your corrections ({len(learned)})"
             if learned else "🧠 What the app has learned from your corrections")
    with st.expander(label):
        if not learned:
            st.caption("Nothing yet for this supplier and product type. A correction is remembered "
                       "only when it actually changes something - if the AI replies without changing "
                       "anything, there is no rule to learn from, which is why the same note can end "
                       "up needing to be said again.")
            return
        st.caption("These are fed to every future extraction for this supplier and product type. The "
                   "document always wins where they disagree, so an out-of-date rule fades rather "
                   "than corrupting a new rate sheet - but remove anything that is simply wrong.")
        for e in learned:
            times = int(e.get("count", 0))
            fields = ", ".join(e.get("fields") or []) or "—"
            c1, c2 = st.columns([6, 1])
            with c1:
                st.markdown(f"- {e.get('text', '')}")
                st.caption(f"said {times}× · changed: {fields}")
            with c2:
                if st.button("Forget", key=f"{key_prefix}_forget_{e.get('key')}"):
                    extraction_memory.forget_instruction(str(supplier_id), product_type, e.get("key"))
                    st.rerun()


# SUPPLEMENT_COLUMNS / render_closedtour_supplements / render_child_age_band - moved to
# ui_components.py, same reason as the blocks above.


def reset_stale_editable_field_widgets(changed_fields, key_suffix=""):
    """CONFIRMED REAL BUG CLASS (found across ClosedTour and Ticket): every "Tell AI what to
    fix" handler already resets the edit-mode flag for TABLE fields it might have touched
    (via a hand-maintained field->table-key dict), but plain single-value fields rendered
    with editable_field() (Ticket name, Description, Voucher Remarks, City, ClosedTour
    Meeting point, etc.) were never covered by that pattern anywhere in the app. If a human
    has one of those fields open for editing (or later reopens it) after an AI clarify wrote
    a new value into the same field, editable_field's own fixed widget key
    (`_widgetval_{field_key}{key_suffix}`) keeps showing the OLD text - Streamlit widgets
    ignore a freshly-computed value= once session_state already holds an entry for that key,
    exactly like editable_field's own docstring already warns about for the per-item-loop
    case. Hitting Save on that stale box then re-writes the old value straight back over the
    AI's fix, with the success banner still claiming it worked.

    Call this with result["changes"] after every apply_clarify_changes(), passing whatever
    key_suffix that screen's editable_field() calls use (e.g. f"_{idx}" in a batch loop, or
    "_main"/"" for a single-item screen). Safe to call for fields that were never rendered
    via editable_field at all (table fields, internal-only fields) - it only clears session
    keys that were never set, which is a no-op."""
    for field_name in changed_fields:
        st.session_state[f"_editing_{field_name}{key_suffix}"] = False
        st.session_state.pop(f"_widgetval_{field_name}{key_suffix}", None)


def new_widget_token():
    """A token no widget key in this session has used before - see widget_state.py's module
    docstring for the bug class this exists to close and why it replaces prefix-sweeping.
    Use bump_widget_generation()/widget_generation() for a whole flow (a fresh extraction), or
    call this directly to stamp one item that gets rebuilt on its own (the price-refresh
    re-read)."""
    return widget_state.new_token(st.session_state)


def bump_widget_generation(flow):
    """Start a new widget generation for `flow` - call wherever the flow REPLACES the data behind
    its review screen (a fresh extraction, a re-extraction, prefilling from the live record,
    starting a new batch), never on an ordinary rerun. See widget_state.bump()."""
    return widget_state.bump(st.session_state, flow)


def widget_generation(flow):
    """`flow`'s current widget generation, for building key prefixes, e.g.
    key_prefix=f"tk_{widget_generation('tk')}". See widget_state.generation()."""
    return widget_state.generation(st.session_state, flow)


def _tk_clear_geo_confirmation():
    """Un-confirms the legacy Ticket flow's "I've checked this location" box, for real.

    CONFIRMED REAL BUG (audit, 2026-08-24): seven places set st.session_state.tk_geo_confirmed =
    False - a new ticket, a re-extraction, and (most importantly) the human CHANGING the
    coordinates. All seven reset the control flag only. The checkbox's own session_state entry
    survived, so on the very next render the checkbox re-asserted True and overwrote the flag.
    A human could change the city and the "✅ I've checked this location on the map" tick would
    stay on, having verified the PREVIOUS coordinates. That tick is the only thing standing
    between a wrong location and a published ticket, so it must be cleared, not just the flag."""
    st.session_state.tk_geo_confirmed = False
    st.session_state.pop(flow_widget_key("tk", "geo_confirm_checkbox"), None)


def _mt_clear_geo_confirmation(current, idx):
    """Un-confirms the multi-Ticket BATCH flow's "I've checked this location" box, for real -
    twin of _tk_clear_geo_confirmation() above for the "mt_" (Ticket batch) flow.

    CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): this flow has its own SEPARATE
    geo-confirm state (current["geo_confirmed"], with its own checkbox keyed f"mt_geo_confirm_
    {idx}") from the legacy single-Ticket "tk_" flow the 2026-08-24 fix above already covers -
    it was never wired to that fix, so it had the exact same bug: picking a new search result
    or entering new manual coordinates set current["geo_confirmed"] = False, but the
    checkbox's OWN session_state entry survived and re-asserted True on the very next render,
    silently re-confirming a location the operator never actually re-checked. Same fix, same
    reasoning - the checkbox has to be cleared too, not just the flag."""
    current["geo_confirmed"] = False
    st.session_state.pop(f"mt_geo_confirm_{idx}", None)


def flow_widget_key(flow, name):
    """A widget key scoped to `flow`'s current widget generation. Use it for BOTH the widget's own
    `key=` and every other reference to that key by name (some AI-clarify handlers pop widget keys
    deliberately) - see widget_state.key_for()."""
    return widget_state.key_for(st.session_state, flow, name)


def _stamp_proposal_widget_tokens(proposals):
    """Gives every price-refresh proposal a fresh widget token (see new_widget_token()).

    CONFIRMED REAL BUG (audit, 2026-08-24) - this one publishes wrong prices to LIVE products,
    two ways in, both fixed by the token:
      1. "Re-read this route": the AI returns a corrected price and the proposal is replaced in
         place, but the price box was keyed f"pr_price_{index}_{code}" - unchanged by the rebuild,
         so it kept showing the OLD number and wrote it straight back into c["new"]. The human
         asks the AI to re-read, sees the old price, and publishes it. The red/green "matches the
         live price" hint compares the same stale value, so it renders green and confirms it.
      2. A SECOND refresh run in the same session: "Start again" clears pr_proposals/pr_routes/
         pr_raw_text/pr_result but nothing keyed pr_price_*/pr_ok_*, and p["index"] restarts at 0
         - so run 2's first route showed run 1's first route's price, already ticked as accepted.
    A token stamped when the proposal is BUILT changes in both cases (rebuild and fresh run) and
    only in those cases, so a price a human typed during the current review is still preserved."""
    for p in (proposals or {}).values() if isinstance(proposals, dict) else (proposals or []):
        if isinstance(p, dict):
            p["widget_token"] = new_widget_token()
    return proposals


def render_publish_blockers(payloads):
    """Shows every hard publish blocker on a built payload set, and returns True only if there are
    none. Shared by all product flows so a new blocker is added in one place.

    CONFIRMED RULES (product owner, 2026-08-24), both chosen explicitly over the silent
    alternatives:

    EXPIRED DOCUMENT -> block, don't guess. A rate sheet whose validity has entirely passed used
    to publish an INVERTED window (start floored to today, end still in the past): permanently
    unbookable, and reported as success. The alternatives were both worse - keeping the past window
    publishes something nobody can book, and flooring both ends invents a validity period the
    supplier never agreed to. An expired contract is a real-world problem for a human to resolve.

    ZERO-PRICED OCCUPANCY -> block, don't publish free inventory. Hotel already refuses to publish
    zero-priced rooms and Transport deactivates unpriced options; Ticket was the one product that
    would happily leave occupancies 5-9 bookable at 0.00 because its pricing editor materializes
    every row up to the cap with a default of 0.

    EXPIRED DATED SUPPLEMENT -> block (product owner, 2026-08-25): "A Peak Season surcharge can
    never have an End date earlier than today's date." A Ticket Modality's dated supplement
    (a season, a holiday surcharge) whose own End Date has already passed can never apply to any
    future booking - see build_ticket_payloads' expired_dated_supplements for the full rule.

    Returns True when clear, so callers can write `can_publish = ... and render_publish_blockers(p)`.
    """
    ok = True
    expired = (payloads or {}).get("expired_validity_error")
    if expired:
        st.error(f"🚫 {expired}")
        ok = False
    zero_rows = (payloads or {}).get("zero_priced_occupancies") or []
    if zero_rows:
        pretty = ", ".join(str(n) for n in zero_rows)
        st.error(
            f"🚫 These occupancies have no price (0.00) and would be sellable for free: **{pretty}**. "
            f"Enter a real price for each, or reduce Max Passengers so they aren't offered at all, "
            f"before publishing."
        )
        ok = False
    expired_supplements = (payloads or {}).get("expired_dated_supplements") or []
    if expired_supplements:
        pretty = ", ".join(str(n) for n in expired_supplements)
        st.error(
            f"🚫 These dated supplements already ended, before today: **{pretty}**. A supplement "
            f"whose End Date is in the past can never apply to a future booking - correct the "
            f"date (e.g. move it to next year's window) or remove the row before publishing."
        )
        ok = False
    return ok


def reset_child_age_band_widgets(key_prefix):
    """CONFIRMED REAL BUG (product owner, 2026-08-24): "the child age is not really working for
    ClosedTours, it always gives me the default age from 2 to 12, even though the document and
    the AI reader reads it correctly." Same widget-staleness trap as
    reset_stale_editable_field_widgets above, just never covered for render_child_age_band's two
    st.number_input widgets: they render with a FIXED key ("{key_prefix}_min_child_age" /
    "{key_prefix}_max_child_age") that has nothing to do with which tour/ticket is currently being
    reviewed. Streamlit ignores a widget's `value=` argument once session_state already holds an
    entry for that key - so after reviewing one tour, every SUBSequent tour reviewed on the same
    screen kept showing the FIRST tour's min/max child age (usually the 2/12 default), no matter
    what the freshly-extracted `data` dict actually said, and immediately wrote that stale number
    straight back into `data` since render_child_age_band assigns data[key] = widget value.

    Call this right after a fresh extraction replaces `data` (or `data.update(...)` folds in new
    modality data carrying its own child_age fields), before render_child_age_band renders again
    for that same key_prefix, so the freshly extracted band is what actually shows."""
    st.session_state.pop(f"{key_prefix}_min_child_age", None)
    st.session_state.pop(f"{key_prefix}_max_child_age", None)


def floor_start_date_for_new_data(data, widget_key=None):
    """CONFIRMED RULE (product owner, 2026-08-24, re-raised specifically for Modalities): "the
    earliest start date can be only the actual day of today, whenever the human is entering the
    tour/ticket/modality. It cannot be in the past." builder.start_date_or_today already floors a
    past start_date at BUILD time (see its docstring), but that only fixes what gets PUBLISHED -
    the "Valid From" text_input widgets (mt_start_date_*, tk's flow_widget_key start_date) were
    still handed the RAW extracted date as their `value=`, so a document dated e.g. 2025 kept
    showing 2025 on screen even though the eventual published record would be correct. That is
    exactly the appearance-of-a-bug the earlier date fix was meant to end. Extracting a NEW
    Modality (extract_ticket_modality_data) hands back its own start_date, folded in via
    data.update(...) - a second, separate place the same raw-date-from-2025 problem can re-enter.

    Call this right after fresh extraction data (whether a whole new item or just a new Modality's
    data.update(...)) lands in `data`, before the "Valid From" widget renders it. Pass widget_key
    to also drop that widget's stale session_state entry when its key does NOT already change
    between extractions (Ticket's per-index mt_start_date_{idx} key doesn't; flows that already
    re-key on every extraction via bump_widget_generation don't need this)."""
    data["start_date"] = builder_start_date_or_today(data.get("start_date"))
    if widget_key:
        st.session_state.pop(widget_key, None)


def apply_clarify_changes(data, result, currency="EUR"):
    """Merge what the AI returned into the working data, WITHOUT trusting its shape.

    CONFIRMED REAL CRASH (product owner, ClosedTour modality): a clarification came back with a
    price_list whose rows held `price` as a bare number instead of the per-occupancy object. It
    was merged straight in, and the pricing table then died on `price.get(...)` - an
    AttributeError that took the whole screen down and pointed at display code that was not at
    fault. The bad shape entered here; it only became visible three screens later.

    So the shape is checked at the door. Anything that cannot be read confidently is reported
    back to the human rather than dropped in silence - see coerce_price_list_shape."""
    notes = []
    for field_name, new_value in (result.get("changes") or {}).items():
        if field_name == "price_list":
            new_value, price_notes = coerce_price_list_shape(new_value, currency)
            notes.extend(price_notes)
        elif field_name == "occupancy_prices":
            # Same reasoning as price_list above - a Ticket Modality's occupancy_prices is the
            # PRIMARY pricing shape now (see render_ticket_pricing_editor), so a shape mistake
            # here isn't a minor field, it's the whole price table. Checked at the door rather
            # than trusted, same as price_list.
            coerced, occ_notes = coerce_ticket_occupancy_prices_shape(new_value)
            if not coerced and new_value:
                # Nothing readable came back - keep the existing table rather than replacing a
                # working price table with an empty one the human never asked for.
                notes.extend(occ_notes)
                continue
            new_value = coerced
            notes.extend(occ_notes)
        data[field_name] = new_value
    if notes:
        result["shape_notes"] = notes
    return notes


def render_clarify_result(result, review_hint="review above before continuing"):
    """Show what "Tell AI what to fix" actually did - never just what it said it did.

    CONFIRMED REAL INCIDENT (product owner, ClosedTour "Luxury Cabin"): the AI returned a long,
    fluent, past-tense report - "all 8 seasonal periods are now included with correct start/end
    dates" - and changed nothing at all. The price list was still empty, and the only clue was
    the ABSENCE of a small green caption underneath. Reading a paragraph that says the work is
    done and then being expected to notice a missing confirmation line is not a workable check.

    So the outcome now leads, and the AI's own words come second. When nothing changed, that is
    stated first, in a colour that means "act on this"."""
    if not result:
        return
    summary = (result.get("summary") or "").strip()
    changes = result.get("changes") or {}

    if changes:
        st.success(f"✅ Applied changes to: {', '.join(changes.keys())} — {review_hint}.")
        if result.get("recovered_after_empty_claim"):
            st.caption("(It first replied without actually returning the changes; it was asked "
                       "again and this time it did.)")
        # Anything the shape check could not read confidently. Shown rather than swallowed: a
        # price that quietly failed to land looks identical to one that was never sent.
        for note in result.get("shape_notes") or []:
            st.warning(f"⚠️ Pricing: {note}.")
        if summary:
            st.info(summary)
        return

    if result.get("claimed_but_changed_nothing"):
        st.warning("⚠️ **Nothing was changed.** The AI described work it did not actually return, "
                   "and it stood by that on a second attempt — so whatever it says below, your "
                   "data is exactly as it was. Try naming one specific field and value (e.g. "
                   "\"the Normal season runs 01-10-2026 to 30-11-2026 at 1450 per person double\"), "
                   "or edit the table directly.")
    else:
        # CONFIRMED REAL COMPLAINT (product owner): "I never ask anything in this tool, I only
        # order what AI did misread." So "treated as a question" was both wrong and unhelpful -
        # it blamed the wording of an instruction that was perfectly clear, when what actually
        # happened is that the AI judged nothing needed changing.
        st.warning("⚠️ **Nothing was changed.** The AI judged the data was already correct, so your "
                   "instruction had no effect. Read its reasoning below — if it disagrees with what "
                   "the document actually says, name the field and the exact value it should hold "
                   "(e.g. \"the Normal season ends 30/11/2026, not 30/10/2026\"), or edit the table "
                   "directly.")
    if summary:
        st.info(summary)


# EN first (the base language every ticket has by default), then the same 19 codes the
# Translation tool already offers, so there's one shared list of language codes across the app
# rather than two that could drift apart.
TICKET_LANGUAGE_OPTIONS = ["EN"] + DEFAULT_TARGET_LANGUAGES
# LANGUAGE_CODE_NAMES now lives in builder.py (imported above) - it's also the source for the
# "You can choose between X-speaking Guide or Y-speaking Guide" Includes line build_ticket_
# payloads writes for a multi-language Modality, so there's exactly one code->name mapping
# instead of two that could quietly drift apart.


def render_ticket_language_options(data, key_prefix):
    """Which language(s) this Modality runs in, at the SAME price - Travel Compositor's own
    "Language Options" tab on the Modality screen.

    CONFIRMED REAL GAP (product owner, 2026-08-24): "we must include the language options within
    a ticket, as so far only one language is allowed. But often we receive two or more language
    options for the same price, if so, we must include it within the modality." The schema
    (ContractTicketModalityVO.languages) and builder.py already accepted a real list here - it
    was extraction and the UI that never surfaced it, so every ticket silently published as
    English-only even when a document listed "English/German-speaking guide" as equal standard
    options. See ai_extractor.py's `languages` field rule for the extraction side (and how it's
    kept distinct from a language that costs EXTRA, which needs its own Modality - see the
    "Needs own Modality?" note below).

    Editable here too, independent of what extraction found, since a human reading the source
    directly may catch a language the AI missed or want to add one the document didn't spell out
    explicitly (e.g. "and other languages on request at no extra charge").

    CONFIRMED REAL INCIDENT (2026-08-25): "different languages are always a problem within
    creating a ticket. Travel C logic would add every single language up and the price would be
    too high." Whatever is selected HERE publishes as this SAME Modality's price - never add a
    language here just because the document mentions it, if it actually costs more. Two or more
    languages selected here also get one line added to Includes automatically ("You can choose
    between X-speaking Guide or Y-speaking Guide" - see builder.same_price_language_includes_line).
    """
    current = [c for c in (data.get("languages") or ["EN"]) if c in TICKET_LANGUAGE_OPTIONS] or ["EN"]
    chosen = st.multiselect(
        "Language Options (offered at this SAME price)",
        TICKET_LANGUAGE_OPTIONS,
        default=current,
        format_func=lambda code: f"{code} — {LANGUAGE_CODE_NAMES.get(code, code)}",
        key=f"{key_prefix}_languages",
        help="A language that costs MORE than the base price is a different product, not a language "
             "option here - enter it as a row under \"Supplements by dates\" below and tick "
             "\"Needs own Modality?\" instead. It will be excluded from this Modality's price and "
             "reported so you can set it up as its own Modality afterward.",
    )
    data["languages"] = chosen or ["EN"]


# CONFIRMED REAL CORRECTION (product owner, 2026-08-24): "extra costs within tickets are
# supplement by dates. No need to distinguish that at the app. All Extra costs are Supplement by
# dates and can also be named all in one like this." This retires the "Extra Costs -> a separate
# future Modality" section that used to sit here (the render_ticket_extra_costs UI function that
# lived in this file has been removed entirely; build_ticket_modality_combinations() itself is
# still defined in builder.py and still covered by its own tests, it's just no longer called from
# Ticket creation) - every priced extra on a Modality, whatever kind, now goes through
# render_ticket_modality_supplements_editor's "Supplements by dates" instead, matching Travel
# Compositor's own single mechanism for this. See that function's docstring (ui_components.py)
# and build_ticket_supplement_vos' docstring (builder.py) for the full rule, including how an
# undated row now defaults to the Modality's own validity window instead of being dropped.
#
# PARTIALLY REVERSED (2026-08-25, CONFIRMED REAL INCIDENT): merging a priced CHOICE (a foreign-
# language guide, a vehicle upgrade) into the SAME "Supplements by dates" table as a genuinely
# dated change let it stack onto this Modality's price as if it were just another date-window
# surcharge - wrong, and expensive. The table itself is still the one place to enter either kind
# (no separate "Extra Costs" UI came back), but a row now carries an is_priced_choice flag (the
# "Needs own Modality?" checkbox) - checked rows are excluded from what publishes on THIS
# Modality (build_ticket_payloads' excluded_language_choice_extras) rather than added to its
# price, since Ticket creation still only ever publishes one Modality at a time.


def get_existing_tour_names(client, supplier_id):
    """
    Fetches the list of ClosedTours already published for this supplier, so
    a new tour's name can be checked against them before uploading - catches
    accidental duplicate uploads (e.g. re-running the same document twice).
    Cached per-supplier in session_state for the rest of the session (cleared
    on demand via the "Refresh" control next to the duplicate-name warning).
    Returns (names_list, error_message). error_message is None on success -
    if the API call fails or the response shape isn't recognized, this
    returns ([], "reason") so the caller can skip the check gracefully
    instead of blocking publishing over a check that couldn't run.
    """
    if "_existing_tours_cache" not in st.session_state:
        st.session_state._existing_tours_cache = {}
    cache = st.session_state._existing_tours_cache
    if supplier_id in cache:
        return cache[supplier_id]

    try:
        result = client.get_closed_tours(supplier_id, first=0, limit=200)
    except Exception as e:
        cache[supplier_id] = ([], friendly_error_message(e))
        return cache[supplier_id]

    if isinstance(result, dict) and "error" in result:
        cache[supplier_id] = ([], "couldn't reach Travel Compositor to check existing tours")
        return cache[supplier_id]

    # Normalize whatever shape came back - a bare list, or a dict wrapping
    # the list under one of a few likely keys - into a flat list of items.
    items = []
    if isinstance(result, list):
        items = result
    elif isinstance(result, dict):
        for key in ("closedTour", "closedTours", "items", "data", "results", "content"):
            if isinstance(result.get(key), list):
                items = result[key]
                break

    names = []
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            names.append({"name": item["name"], "code": item.get("code", "")})

    if not items and not names:
        cache[supplier_id] = ([], "no existing tours found (or couldn't recognize the response format)")
    else:
        cache[supplier_id] = (names, None)
    return cache[supplier_id]


def get_existing_ticket_codes(client, supplier_id):
    """
    Ticket equivalent of get_existing_tour_names() - fetches and caches the
    full list of Tickets already published for this supplier, so a
    candidate code/name can be cross-checked against what Travel
    Compositor actually has, not just a single direct GET-by-code lookup
    (see check_code_availability's docstring for why the direct lookup
    alone isn't reliable enough on its own).
    Returns (items_list, error_message) - each item is {"name": str,
    "code": str}. error_message is None on success.
    """
    if "_existing_tickets_cache" not in st.session_state:
        st.session_state._existing_tickets_cache = {}
    cache = st.session_state._existing_tickets_cache
    if supplier_id in cache:
        return cache[supplier_id]

    try:
        result = client.get_tickets(supplier_id, first=0, limit=200)
    except Exception as e:
        cache[supplier_id] = ([], friendly_error_message(e))
        return cache[supplier_id]

    if isinstance(result, dict) and "error" in result:
        cache[supplier_id] = ([], "couldn't reach Travel Compositor to check existing tickets")
        return cache[supplier_id]

    items = []
    if isinstance(result, list):
        items = result
    elif isinstance(result, dict):
        for key in ("ticket", "tickets", "items", "data", "results", "content"):
            if isinstance(result.get(key), list):
                items = result[key]
                break

    names = []
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            names.append({"name": item["name"], "code": item.get("code", "")})

    if not items and not names:
        cache[supplier_id] = ([], "no existing tickets found (or couldn't recognize the response format)")
    else:
        cache[supplier_id] = (names, None)
    return cache[supplier_id]


def get_existing_ticket_modality_codes(client, supplier_id):
    """
    CONFIRMED REAL REQUEST (product owner, 2026-08-24): "check if the ticket number from the
    supplier has been already added. the supplier often provides a ticket code and this must
    be the modality code for the ticket... we can avoid double tickets in the travel c system."

    A supplier's own reference code for a specific excursion/service (e.g. "LXR05") becomes this
    app's Modality Code, per the app's own convention - but the CONTAINER Ticket record around it
    gets an arbitrary, human-chosen Ticket Code (e.g. "LXR-T2") that has no relationship to the
    supplier's code at all. That means the existing Ticket-Code uniqueness check
    (check_code_availability) can never catch the real duplicate this creates: the same supplier
    product, re-imported from the same or a re-sent document, published a second time under a
    DIFFERENT Ticket Code wrapper with the identical Modality Code inside it.

    Fetches every existing Ticket for this supplier (get_existing_ticket_codes), then GETs each
    one individually to read its `modalityCodes` list (confirmed field on the real GET
    /tickets/{supplierId}/{ticketCode} response - see the "Existing modality codes" display this
    app already showed on the Update/Add-modality screen before this check existed). This is
    O(N) GET calls for N existing tickets, all uncached the first time - unavoidable, since the
    list endpoint itself doesn't carry each ticket's modality codes. Cached per supplier_id in
    session_state so it only costs this once per supplier per session, exactly like
    get_existing_ticket_codes()/get_existing_tour_names() already do.

    A single failed per-ticket GET is skipped rather than aborting the whole sweep (GETs don't
    auto-retry - api_client._request) - but that means a real duplicate COULD be missed if the
    one ticket that actually holds it happened to fail. Returns (items, warning) where items is
    [{"ticket_code", "ticket_name", "modality_code"}, ...] (best-effort, always returned even on
    partial failure) and warning is None on full success or a string naming how many tickets
    couldn't be checked, so callers can tell the human this check may be incomplete rather than
    silently presenting a partial sweep as a clean "not a duplicate".
    """
    if "_existing_modality_codes_cache" not in st.session_state:
        st.session_state._existing_modality_codes_cache = {}
    cache = st.session_state._existing_modality_codes_cache
    if supplier_id in cache:
        return cache[supplier_id]

    tickets, list_error = get_existing_ticket_codes(client, supplier_id)
    if list_error is not None:
        cache[supplier_id] = ([], "couldn't reach Travel Compositor to check existing tickets")
        return cache[supplier_id]

    items = []
    failed = 0
    for t in tickets:
        t_code = (t.get("code") or "").strip()
        if not t_code:
            continue
        try:
            result = client.get_ticket(supplier_id, t_code)
        except Exception:
            result = None
        if not isinstance(result, dict) or "error" in result:
            failed += 1
            continue
        for m_code in (result.get("modalityCodes") or []):
            m_code = (m_code or "").strip()
            if m_code:
                items.append({"ticket_code": t_code, "ticket_name": t.get("name") or t_code, "modality_code": m_code})

    warning = f"{failed} of {len(tickets)} existing ticket(s) couldn't be checked - this duplicate check may be incomplete." if failed else None
    cache[supplier_id] = (items, warning)
    return cache[supplier_id]


def check_modality_code_availability(client, supplier_id, modality_code, ignore_ticket_code=None):
    """
    Cross-checks a candidate Modality Code against every Modality Code already published for
    this supplier's tickets - see get_existing_ticket_modality_codes()'s docstring for why this
    catches a class of duplicate the Ticket-Code check alone cannot.

    `ignore_ticket_code`: when adding/updating a modality on a ticket the human is already
    working WITH (action in add_option/update_option), that ticket's own existing modality
    codes are expected matches, not duplicates - pass its code here to exclude it from the
    comparison so re-saving a ticket's own modality never triggers a false "already used".

    Returns {"exists": bool, "ticket_code": str, "ticket_name": str} | None. None means the
    code is either blank or the check couldn't be completed with confidence (matches
    check_code_availability's own "None = inconclusive, don't call it available" convention).
    """
    clean_code = (modality_code or "").strip()
    if not clean_code:
        return None
    items, warning = get_existing_ticket_modality_codes(client, supplier_id)
    if warning is not None and not items:
        return None
    clean_code_lower = clean_code.lower()
    ignore_lower = (ignore_ticket_code or "").strip().lower()
    match = next(
        (it for it in items
         if it["modality_code"].strip().lower() == clean_code_lower
         and it["ticket_code"].strip().lower() != ignore_lower),
        None
    )
    if match:
        return {"exists": True, "ticket_code": match["ticket_code"], "ticket_name": match["ticket_name"]}
    if warning is not None:
        # Some tickets couldn't be checked - a partial "not found" isn't confident enough to
        # call available outright, but IS worth surfacing so a human can decide (unlike
        # check_code_availability's binary case, a partial sweep still has real signal).
        return {"exists": False, "ticket_code": None, "ticket_name": None, "incomplete": warning}
    return {"exists": False, "ticket_code": None, "ticket_name": None}


def render_modality_code_availability_check(client, supplier_id, modality_code, ignore_ticket_code=None):
    """Same immediate-feedback pattern as render_code_availability_check, for Modality Codes -
    see check_modality_code_availability()'s docstring for what this actually catches."""
    result = check_modality_code_availability(client, supplier_id, modality_code, ignore_ticket_code)
    if result is None:
        return
    if result["exists"]:
        st.error(f"🚫 Modality Code `{(modality_code or '').strip()}` is ALREADY USED by ticket "
                f"**{result['ticket_name']}** (`{result['ticket_code']}`) for this supplier. If the "
                f"supplier's own reference code is the same, this looks like the same product being "
                f"added again - double-check before continuing, or use an Update/Add-modality action "
                f"on the existing ticket instead.")
    elif result.get("incomplete"):
        st.warning(f"⚠️ `{(modality_code or '').strip()}` wasn't found among this supplier's existing "
                  f"modality codes, but {result['incomplete']}")


def get_existing_hotel_names(client, supplier_id):
    """
    Hotel equivalent of get_existing_tour_names()/get_existing_ticket_codes() - fetches and
    caches the full list of Hotels already published for this supplier. Added for the
    Update/Refresh existing Service screen's Hotel picker (render_update_refresh_flow), so a
    human picks an existing hotel by name from a real list instead of having to already know
    and type its exact providerCode by hand.
    Returns (items_list, error_message) - each item is {"name": str, "code": str}.
    """
    if "_existing_hotels_cache" not in st.session_state:
        st.session_state._existing_hotels_cache = {}
    cache = st.session_state._existing_hotels_cache
    if supplier_id in cache:
        return cache[supplier_id]

    try:
        result = client.get_hotels(supplier_id)
    except Exception as e:
        cache[supplier_id] = ([], friendly_error_message(e))
        return cache[supplier_id]

    if isinstance(result, dict) and "error" in result:
        cache[supplier_id] = ([], "couldn't reach Travel Compositor to check existing hotels")
        return cache[supplier_id]

    items = []
    if isinstance(result, list):
        items = result
    elif isinstance(result, dict):
        for key in ("hotel", "hotels", "items", "data", "results", "content"):
            if isinstance(result.get(key), list):
                items = result[key]
                break

    names = []
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            names.append({"name": item["name"], "code": item.get("code", "")})

    if not items and not names:
        cache[supplier_id] = ([], "no existing hotels found (or couldn't recognize the response format)")
    else:
        cache[supplier_id] = (names, None)
    return cache[supplier_id]


# ----------------------------------------------------------------------
# "Recently updated via this screen" memory for the Update/Refresh flow's code pickers
# (ClosedTour/Hotel/Ticket - CONFIRMED PRODUCT-OWNER REQUEST: "this would be more a mapping
# which will be done in the database and so the App could learn"). Deliberately simple for
# this round: it boosts recently-picked services to the top of the dropdown per supplier, on
# durable (Postgres-backed, when DATABASE_URL is set) storage via platform_store - the same
# mechanism transfer_matcher.py already uses for its route->id memory. Genuine AI-driven
# auto-matching from a freshly uploaded document (the OTHER option described in the request)
# is NOT built yet for these three types - ClosedTour/Hotel/Ticket already carry a real
# human-assigned code, so "which exact service" only needs a pick-from-a-list step, not the
# fuzzy departure/arrival matching Transfer needs (it has no such code at all).
# ----------------------------------------------------------------------
_UPDATE_REFRESH_RECENTS_NAMESPACE = "update_refresh_recent_picks"
_UPDATE_REFRESH_RECENTS_MAX = 8


def _remember_update_refresh_pick(kind: str, supplier_id: str, code: str, name: str) -> None:
    if not code:
        return
    key = f"{kind}:{supplier_id}"
    recents = platform_store.get(_UPDATE_REFRESH_RECENTS_NAMESPACE, key) or []
    recents = [r for r in recents if r.get("code") != code]
    recents.insert(0, {"code": code, "name": name})
    platform_store.set(_UPDATE_REFRESH_RECENTS_NAMESPACE, key, recents[:_UPDATE_REFRESH_RECENTS_MAX])


def _recent_update_refresh_picks(kind: str, supplier_id: str) -> list:
    return platform_store.get(_UPDATE_REFRESH_RECENTS_NAMESPACE, f"{kind}:{supplier_id}") or []


def check_duplicate_tour_name(client, supplier_id, tour_name):
    """
    Returns a human-readable warning string if `tour_name` (case/whitespace-
    insensitive) matches an existing tour already published for this
    supplier, else None. Never raises - a failed lookup just means no
    warning is shown, since this is a helpful heads-up, not a hard gate.
    """
    clean_name = (tour_name or "").strip().lower()
    if not clean_name:
        return None
    names, _ = get_existing_tour_names(client, supplier_id)
    for existing in names:
        if existing["name"].strip().lower() == clean_name:
            return (f"⚠️ A tour named **'{existing['name']}'** already exists for this supplier "
                    f"(code: `{existing['code']}`). Double-check this isn't a duplicate upload before publishing.")
    return None


def check_code_availability(client, kind, supplier_id, code):
    """
    Asks Travel Compositor whether a ClosedTour/Ticket CODE already exists
    for this supplier - the real, authoritative check for the "code already
    exists" publish error (different from - and more definitive than - the
    name-based duplicate check above, since the actual API rejection is
    keyed on the code, not the name). `kind` is "tour" or "ticket". Cached
    per (kind, supplier_id, code) in session_state so re-checking the same
    code (e.g. re-rendering on every keystroke elsewhere on the page) costs
    nothing extra.

    CONFIRMED REAL BUG (reported: "LXR-2 is available" while LXR-2 was
    actually already taken): this used to treat ANY non-200 response from a
    direct GET-by-code (client.get_closed_tour/get_ticket) as "doesn't
    exist" - but a non-200 here isn't reliably a clean 404. It can also be a
    transient failure (rate limit, brief 5xx - GET calls deliberately don't
    auto-retry, see api_client._request), or the code being stored under a
    different variant than what was typed (the same CLOSEDTOUR-XXXXX-vs-
    human-code ambiguity that try_code_variants() exists to handle
    elsewhere) - any of which would wrongly report a genuinely taken code as
    free. Fixed by treating a failed direct GET as INCONCLUSIVE, not a
    confirmed miss: it now cross-checks the candidate code against the
    supplier's full existing-items list (get_existing_tour_names() /
    get_existing_ticket_codes() - a different endpoint with different
    failure modes) as a second opinion before ever calling a code available.

    Returns {"exists": bool, "name": str|None} on a successful/confident
    lookup, or None if the check couldn't be completed with confidence
    either way - callers should treat None as "couldn't verify" rather than
    either a pass or a fail.
    """
    clean_code = (code or "").strip()
    if not clean_code:
        return None
    if "_code_exists_cache" not in st.session_state:
        st.session_state._code_exists_cache = {}
    cache = st.session_state._code_exists_cache
    cache_key = (kind, supplier_id, clean_code)
    if cache_key in cache:
        return cache[cache_key]

    # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): the "code is free" answer this
    # function caches was never invalidated after a successful publish under that code - see
    # mark_code_as_taken() below, called from every successful create-with-a-new-code path. A
    # stale cached "available" for a code the operator just published under looked free right
    # up until the actual publish/submit rejected it as already taken.

    try:
        result = client.get_closed_tour(supplier_id, clean_code) if kind == "tour" else client.get_ticket(supplier_id, clean_code)
    except Exception:
        result = None

    if isinstance(result, dict) and "error" not in result:
        # Direct GET succeeded - definitive, real data, no need for a
        # second opinion.
        outcome = {"exists": True, "name": result.get("name")}
        cache[cache_key] = outcome
        return outcome

    # The direct GET did NOT confirm the code exists - but per the bug above,
    # that alone doesn't mean it's free. Cross-check the supplier's full
    # existing-items list before concluding "available".
    existing_items, list_error = (
        get_existing_tour_names(client, supplier_id) if kind == "tour"
        else get_existing_ticket_codes(client, supplier_id)
    )
    clean_code_lower = clean_code.lower()
    match = next(
        (item for item in existing_items if (item.get("code") or "").strip().lower() == clean_code_lower),
        None
    )
    if match:
        outcome = {"exists": True, "name": match.get("name")}
    elif list_error is not None:
        # Neither the direct GET nor the list check could be completed with
        # confidence - don't claim "available" off of two failed checks.
        return None
    else:
        outcome = {"exists": False, "name": None}

    cache[cache_key] = outcome
    return outcome


def mark_code_as_taken(kind, supplier_id, code, name=None):
    """CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): call this immediately after a
    successful create-with-a-new-code publish (ClosedTour or Ticket) so check_code_availability's
    cache reflects reality right away - otherwise a stale "available" cached from before the
    publish (or simply never having been checked as unavailable) would let the operator re-use
    the just-published code again in the SAME session, look free on screen, and only fail once
    they actually submit."""
    clean_code = (code or "").strip()
    if not clean_code:
        return
    if "_code_exists_cache" not in st.session_state:
        st.session_state._code_exists_cache = {}
    st.session_state._code_exists_cache[(kind, supplier_id, clean_code)] = {"exists": True, "name": name}


def render_code_availability_check(client, kind, supplier_id, code, label):
    """
    Shows an immediate, automatic (no button needed) availability check
    right under a Tour/Ticket Code input field - so a human sees "this code
    is already taken" the moment they type it, instead of only discovering
    it after filling out the entire form and pressing Publish.
    """
    result = check_code_availability(client, kind, supplier_id, code)
    if result is None:
        return
    if result["exists"]:
        st.error(f"🚫 `{(code or '').strip()}` is ALREADY TAKEN by an existing {label} "
                f"(\"{result.get('name') or '(unnamed)'}\"). Choose a different code, or use an "
                f"Update/Add-modality action instead if you meant to add to this existing one.")
    else:
        st.success(f"✅ `{(code or '').strip()}` is available.")


def _diff_tour_price_list(old_list, new_list):
    """
    Compares two ClosedTour price_list arrays (each entry: startDate/endDate/
    price{singlePrice,doublePrice,triplePrice,quadruplePrice}), matched by
    (startDate, endDate) - not by 'name', since that's just a free-text label
    that can differ without the actual price changing. Returns only the
    periods that actually differ: [{"period": str, "status": "added"|
    "removed"|"changed", "old": dict|None, "new": dict|None}] - unchanged
    periods are omitted so the human only sees what matters.
    """
    def _key(row):
        return (row.get("startDate", ""), row.get("endDate", ""))

    def _amounts(row):
        price = row.get("price") or {}
        out = {}
        for k, label in [("singlePrice", "Single"), ("doublePrice", "Double"),
                         ("triplePrice", "Triple"), ("quadruplePrice", "Quad")]:
            block = price.get(k)
            if isinstance(block, dict) and block.get("amount") is not None:
                out[label] = block["amount"]
        return out

    old_by_key = {_key(r): r for r in (old_list or [])}
    new_by_key = {_key(r): r for r in (new_list or [])}
    changes = []
    for key in sorted(set(old_by_key) | set(new_by_key)):
        old_row, new_row = old_by_key.get(key), new_by_key.get(key)
        period = f"{key[0]} → {key[1]}"
        if old_row and not new_row:
            changes.append({"period": period, "status": "removed", "old": _amounts(old_row), "new": None})
        elif new_row and not old_row:
            changes.append({"period": period, "status": "added", "old": None, "new": _amounts(new_row)})
        else:
            old_amt, new_amt = _amounts(old_row), _amounts(new_row)
            if old_amt != new_amt:
                changes.append({"period": period, "status": "changed", "old": old_amt, "new": new_amt})
    return changes


def _map_fetched_supplements(fetched_supplements):
    """
    Best-effort reverse mapping of GET-response SupplementVO dicts back into
    the internal editing shape (name/price/single_price/.../applies_to/
    travel_start_date/travel_end_date) used throughout the review UI and by
    build_closed_tour_payloads(). Some detail (e.g. exactly how per_pax was
    originally set) isn't recoverable from the GET response, so this
    defaults conservatively - always double-check supplements on the review
    screen after they're pulled in this way.
    """
    mapped = []
    for s in (fetched_supplements or []):
        if not isinstance(s, dict):
            continue
        translations = s.get("translations") or {}
        name = (translations.get("EN") or {}).get("name", "")
        price = s.get("price") or {}
        modality_codes = s.get("modalityCodes") or []
        if not modality_codes:
            applies_to = "All Modalities"
        elif len(modality_codes) == 1:
            applies_to = modality_codes[0]
        else:
            applies_to = modality_codes[0]  # editing UI only supports one code per row - keep the first, flag via name
            name = f"{name} (also applies to: {', '.join(modality_codes[1:])})".strip()
        windows = s.get("travelWindows") or []
        travel_start = (windows[0] or {}).get("start", "") if windows else ""
        travel_end = (windows[0] or {}).get("end", "") if windows else ""
        flat_price = price.get("singlePrice", 0) or 0
        mapped.append({
            "name": name,
            "price": flat_price,
            "single_price": price.get("singlePrice", flat_price),
            "double_price": price.get("doublePrice", flat_price),
            "triple_price": price.get("triplePrice", flat_price),
            "quadruple_price": price.get("quadruplePrice", flat_price),
            "per_pax": True,
            "mandatory": s.get("mandatory", False),
            "on_request": s.get("onRequest", False),
            "applies_to": applies_to,
            "travel_start_date": travel_start,
            "travel_end_date": travel_end,
        })
    return mapped


def _map_fetched_tour_to_data(fetched):
    """
    CONFIRMED FIX (real near-data-loss report): "Update an existing tour's
    details" used to require a FRESH extraction from a newly-uploaded
    document/URL before Step 5 (the review/edit screen) would render at
    all - if the human didn't have a new source handy (e.g. they just
    wanted to tweak one field), every field started completely BLANK, and
    nothing stopped them from publishing that blank data straight over the
    real, live tour.

    This builds the SAME internal `data` shape extract_structured_data()
    produces, but sourced from the tour's OWN currently-live GET response
    (already fetched in Step 3's "Check what's already online for this
    code") - so the review screen always starts from the tour's real
    values, never blank ones. If the human also runs a fresh extraction
    from a newly-uploaded document, that gets merged ON TOP of this
    baseline (see _merge_extraction_over_baseline) rather than replacing it
    outright, so an incomplete new extraction can't blank out real fields
    the fresh source just didn't happen to mention.

    price_list/operational_days/stop_sales are intentionally left at their
    empty defaults here - those live on the OPTION, not the tour, and this
    action ("update tour details") never touches them.
    """
    if not isinstance(fetched, dict) or "error" in fetched:
        return {}
    datasheet = (fetched.get("datasheets") or {}).get("EN", {}) or {}
    itinerary = fetched.get("itinerary") or []
    return {
        "tour_name": datasheet.get("name", "") or fetched.get("name", ""),
        "description": datasheet.get("description", ""),
        "hotels_text": datasheet.get("hotels", ""),
        "hotels_count": fetched.get("hotels", 1),
        "supplements": _map_fetched_supplements(fetched.get("supplements")),
        "included": datasheet.get("included", ""),
        "excluded": datasheet.get("excluded", ""),
        "meeting_point": datasheet.get("meetingPoint", ""),
        "policy_remarks": datasheet.get("remarksDescription", ""),
        "itinerary_destinations": [d.get("destination", "") for d in itinerary if isinstance(d, dict) and d.get("destination")],
        "nights": fetched.get("nights", 0),
        "start_time": fetched.get("startTime", ""),
        "end_time": fetched.get("endTime", ""),
        "min_child_age": fetched.get("minChildAge", 2),
        "max_child_age": fetched.get("maxChildAge", 12),
        "image_urls": fetched.get("images") or [FALLBACK_IMAGE],
        "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
        "schedule_notes": "",
        "pricing_notes": "",
        "price_list": [],
        "release_days_mentions": [],
    }


def _map_fetched_ticket_to_data(fetched):
    """
    Ticket equivalent of _map_fetched_tour_to_data() - see that function for
    the full rationale. Pre-fills "Update an existing ticket's details" from
    the ticket's own live GET response instead of leaving every field blank
    until a fresh document/URL is extracted.
    """
    if not isinstance(fetched, dict) or "error" in fetched:
        return {}
    datasheet = (fetched.get("datasheets") or {}).get("EN", {}) or {}
    geoloc = fetched.get("geolocation") or {}
    return {
        "ticket_name": datasheet.get("name", "") or fetched.get("name", ""),
        "description": datasheet.get("description", ""),
        "city": fetched.get("city", "") or geoloc.get("name", ""),
        "includes": datasheet.get("includes") or [],
        "excludes": datasheet.get("excludes") or [],
        "meeting_points": [],
        "meeting_point_summary": datasheet.get("meetingPoint", ""),
        "duration": fetched.get("duration", 0),
        "duration_type": fetched.get("durationType", "HOURS"),
        "activity_type": datasheet.get("activityType") or "",
        "is_private": False,
        "image_urls": fetched.get("imageUrls") or [FALLBACK_IMAGE],
    }


def _merge_extraction_over_baseline(baseline, fresh):
    """
    Merges a freshly-extracted dict ON TOP of an existing baseline (e.g. a
    tour/ticket's real live values pulled via _map_fetched_tour_to_data /
    _map_fetched_ticket_to_data) - keeps the baseline's value for any field
    the fresh extraction left empty/default, instead of letting an
    incomplete new extraction silently blank out real data that was already
    correctly pre-filled. Only used for "update" actions; "create" always
    uses the fresh extraction as-is (no baseline exists to merge over).
    """
    if not baseline:
        return fresh
    merged = dict(baseline)
    # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): `0` used to be in this tuple, and
    # Python's `in` uses `==` for membership - `0 == False` is True, so `v in empty_values` also
    # caught a genuine `False` (e.g. a boolean flag correctly re-extracted as False) as if it
    # were "nothing extracted," silently keeping the baseline's stale True. And a genuinely
    # re-extracted `0` (e.g. a price or a count that really is now zero) was reverted to
    # whatever non-zero value the baseline happened to have. Dropped `0` from this tuple - a
    # real 0/False is a value the fresh extraction actually found, not an empty field.
    empty_values = (None, "", [], {})
    for k, v in (fresh or {}).items():
        if v not in empty_values:
            merged[k] = v
    return merged


def render_tour_update_comparison(publish_action, data, payloads, client, supplier_id,
                                  existing_tour_code, working_tour_code, modality_code):
    """
    For "Update an existing tour's details" / "Update an existing option":
    fetches what's CURRENTLY live on Travel Compositor (already cached in
    st.session_state.fetched_tour from Step 3's "Check what's already
    online") and compares it against the freshly-extracted new data, so a
    human sees exactly what's changing before publishing an update instead
    of blindly overwriting whatever was there.

    CONFIRMED RULE #1: a ClosedTour's number of NIGHTS is a structural fact
    about the product, not a detail that gets "updated" - if the new source
    describes a different night count than what's currently live, this is a
    DIFFERENT tour, not a revision of the same one (the itinerary/pricing
    structure is built around a fixed night count). Returns True if this
    hard block applies - the caller must then refuse to let the human
    publish, since Travel Compositor's PUT is meant for genuine detail
    corrections, not restructuring the whole product.
    """
    st.subheader("🔄 Comparing with what's already online")
    blocks_publish = False
    old = st.session_state.get("fetched_tour")
    have_old_tour = isinstance(old, dict) and "error" not in old

    if publish_action == "Update an existing tour's details":
        if not have_old_tour:
            st.info("ℹ️ No 'what's already online' data was fetched for this tour - skipping the "
                   "before/after comparison. Go back to Step 3 and click 'Check what's already online "
                   "for this code' to compare against what's currently live before publishing this update.")
            return False

        old_nights, new_nights = old.get("nights"), data.get("nights")
        if old_nights is not None and new_nights is not None and int(old_nights) != int(new_nights):
            blocks_publish = True
            st.error(
                f"🚫 **Number of nights changed: {old_nights} → {new_nights}.** This is treated as a "
                f"DIFFERENT tour, not an update of `{existing_tour_code}` - the itinerary and pricing "
                f"structure is built around a fixed night count, so pushing this through as an update "
                f"would corrupt the existing tour rather than genuinely revise it.\n\n"
                f"**What to do instead:** go back to Step 1 and choose **'Create a brand-new tour "
                f"(+ first option)'**, with a NEW ClosedTour Code and Modality Code for this "
                f"{new_nights}-night variant."
            )
        else:
            st.caption(f"✅ Nights unchanged ({new_nights}) - safe to update in place.")

        old_name, new_name = old.get("name"), data.get("tour_name")
        if old_name and new_name and old_name.strip() != new_name.strip():
            st.info(f"✏️ Name changing: **{old_name}** → **{new_name}**")

        old_stops, new_stops = len(old.get("itinerary") or []), len(data.get("itinerary_destinations") or [])
        if old_stops and new_stops and old_stops != new_stops:
            st.warning(f"🗺️ Itinerary stop count changing: **{old_stops}** → **{new_stops}** stops - "
                      f"double-check the new itinerary reflects a genuine route change, not a misread "
                      f"source document.")

        old_hotels, new_hotels = old.get("hotels"), data.get("hotels_count")
        if old_hotels is not None and new_hotels is not None and old_hotels != new_hotels:
            st.info(f"🏨 Hotel count changing: **{old_hotels}** → **{new_hotels}**")

    elif publish_action == "Update an existing option":
        # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): this cache was keyed only on
        # modality_code, which is NOT unique across tours/suppliers - two different tours can
        # both have a "Standard" modality. Switching from tour A to tour B without a full app
        # restart could show tour A's cached live prices as tour B's "what's currently live"
        # sanity check, right up until the modality code happened to differ. Now scoped by
        # supplier + the actual tour code the lookup uses, too.
        cache_key = f"_cmp_fetched_option_{supplier_id}_{working_tour_code or existing_tour_code}_{modality_code}"
        if cache_key not in st.session_state:
            with st.spinner("Fetching current live pricing for this modality..."):
                st.session_state[cache_key] = client.get_closed_tour_option(
                    supplier_id, working_tour_code or existing_tour_code, modality_code
                )
        old_option = st.session_state[cache_key]
        if isinstance(old_option, dict) and "error" not in old_option:
            old_price_list = old_option.get("priceList", [])
            new_price_list = (payloads.get("tour_option_payload") or {}).get("priceList", [])
            changes = _diff_tour_price_list(old_price_list, new_price_list)
            if not changes:
                st.success("✅ No pricing changes detected for this modality vs. what's currently live.")
            else:
                st.write(f"**{len(changes)} price period(s) changing:**")
                for c in changes:
                    if c["status"] == "changed":
                        st.markdown(f"- 🔁 **{c['period']}**: {c['old']} → **{c['new']}**")
                    elif c["status"] == "added":
                        st.markdown(f"- ➕ **{c['period']}** (new): **{c['new']}**")
                    else:
                        st.markdown(f"- ➖ **{c['period']}** (removed, was {c['old']})")
        else:
            err_detail = old_option.get("message", old_option) if isinstance(old_option, dict) else old_option
            st.warning(f"⚠️ Couldn't fetch this modality's live pricing for comparison: {err_detail}")

    return blocks_publish


def _diff_ticket_option_pricing(old_option, new_payload):
    """
    Compares an existing (GET) ContractTicketModalityVO dict against a
    freshly-built new one (same field names, confirmed against the real
    GET response) - returns a list of human-readable "field: old → new"
    strings for whichever priced fields actually changed. Handles all three
    pricing modes (Distribution/Occupancy/Service).
    """
    changes = []
    old_type = old_option.get("priceType", "DISTRIBUTION")
    new_type = new_payload.get("priceType", "DISTRIBUTION")
    if old_type != new_type:
        changes.append(f"Pricing mode: **{old_type}** → **{new_type}**")

    for field, label in [("baseAdultPrice", "Adult price"), ("baseChildrenPrice", "Child price"),
                         ("baseInfantPrice", "Infant price"), ("baseServicePrice", "Service price")]:
        old_val, new_val = old_option.get(field), new_payload.get(field)
        if old_val is not None and new_val is not None and float(old_val) != float(new_val):
            changes.append(f"{label}: **{old_val}** → **{new_val}**")

    old_occ = {o.get("occupancy"): o.get("amount") for o in (old_option.get("occupancyPrices") or [])}
    new_occ = {o.get("occupancy"): o.get("amount") for o in (new_payload.get("occupancyPrices") or [])}
    if old_occ != new_occ:
        for k in sorted(set(old_occ) | set(new_occ), key=lambda x: (x is None, x)):
            if old_occ.get(k) != new_occ.get(k):
                changes.append(f"Occupancy {k} pax: **{old_occ.get(k, '-')}** → **{new_occ.get(k, '-')}**")

    old_dates = (old_option.get("startDate"), old_option.get("endDate"))
    new_dates = (new_payload.get("startDate"), new_payload.get("endDate"))
    if old_dates != new_dates:
        changes.append(f"Validity dates: **{old_dates[0]} → {old_dates[1]}** → **{new_dates[0]} → {new_dates[1]}**")

    return changes


def render_ticket_update_comparison(publish_action, data, payloads, client, supplier_id,
                                    existing_ticket_code, modality_code):
    """
    Ticket equivalent of render_tour_update_comparison() - see that function
    for the full rationale. Tickets have no "nights" concept (single-day
    excursions), so there's no hard-block rule here - just a clear
    before/after comparison so an update is never a silent overwrite.
    Always returns False (nothing about a Ticket update is hard-blocked).
    """
    st.subheader("🔄 Comparing with what's already online")
    old = st.session_state.get("tk_fetched_ticket")
    have_old_ticket = isinstance(old, dict) and "error" not in old

    if publish_action == "Update an existing ticket's details":
        if not have_old_ticket:
            st.info("ℹ️ No 'what's already online' data was fetched for this ticket - skipping the "
                   "before/after comparison. Go back to Step 3 and click 'Check what's already online "
                   "for this code' to compare against what's currently live before publishing this update.")
            return False

        old_name, new_name = old.get("name"), data.get("ticket_name")
        if old_name and new_name and old_name.strip() != new_name.strip():
            st.info(f"✏️ Name changing: **{old_name}** → **{new_name}**")

        old_duration, new_duration = old.get("duration"), data.get("duration")
        if old_duration is not None and new_duration is not None and old_duration != new_duration:
            st.warning(f"⏱️ Duration changing: **{old_duration}** → **{new_duration}** "
                      f"({data.get('duration_type', '')}) - double-check this is a genuine change, not a "
                      f"misread source value.")

        # The real GET response's geolocation only has latitude/longitude (no city name stored) -
        # compare coordinates instead, with a loose threshold since minor geocoding rounding
        # shouldn't itself read as "the city changed".
        old_geo = old.get("geolocation") or {}
        old_lat, old_lng = old_geo.get("latitude"), old_geo.get("longitude")
        new_lat, new_lng = payloads.get("geolocation_latitude"), payloads.get("geolocation_longitude")
        if None not in (old_lat, old_lng, new_lat, new_lng):
            moved_far = abs(old_lat - new_lat) > 0.05 or abs(old_lng - new_lng) > 0.05  # roughly > ~5km
            if moved_far:
                st.warning(f"📍 Location moved noticeably: was ({old_lat:.4f}, {old_lng:.4f}), now resolves to "
                          f"({new_lat:.4f}, {new_lng:.4f}) for city '{data.get('city', '')}' - a big location "
                          f"shift usually means a genuinely different excursion, not just a detail update. "
                          f"Double-check this is intentional.")

    elif publish_action == "Update an existing ticket option":
        # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): see the matching fix for
        # ClosedTour options above - scoped by supplier + ticket code too, not modality code
        # alone.
        cache_key = f"_cmp_fetched_tk_option_{supplier_id}_{existing_ticket_code}_{modality_code}"
        if cache_key not in st.session_state:
            with st.spinner("Fetching current live pricing for this modality..."):
                st.session_state[cache_key] = client.get_ticket_option(supplier_id, existing_ticket_code, modality_code)
        old_option = st.session_state[cache_key]
        if isinstance(old_option, dict) and "error" not in old_option:
            changes = _diff_ticket_option_pricing(old_option, payloads.get("ticket_option_payload") or {})
            if not changes:
                st.success("✅ No pricing changes detected for this modality vs. what's currently live.")
            else:
                st.write(f"**{len(changes)} change(s):**")
                for c in changes:
                    st.markdown(f"- 🔁 {c}")
        else:
            err_detail = old_option.get("message", old_option) if isinstance(old_option, dict) else old_option
            st.warning(f"⚠️ Couldn't fetch this modality's live pricing for comparison: {err_detail}")

    return False


def _summarize_modality_pricing(kind, data, currency):
    """
    Renders a compact, read-only summary of one modality's key facts
    (pricing, operational days, stop sales) inside whatever container is
    currently open (an expander, typically). `kind` is "tour" or "ticket" -
    the two use different pricing shapes.
    """
    if not data:
        st.warning("No pricing data entered yet for this modality.")
        return

    if kind == "tour":
        price_list = data.get("price_list", []) or []
        if price_list:
            st.write(f"**{len(price_list)} price period(s):**")
            for p in price_list:
                price = p.get("price", {}) or {}
                st.caption(
                    f"{p.get('startDate', '?')} → {p.get('endDate', '?')}: "
                    f"Single {price.get('singlePrice', {}).get('amount', '-')}, "
                    f"Double {price.get('doublePrice', {}).get('amount', '-')}, "
                    f"Triple {price.get('triplePrice', {}).get('amount', '-')}, "
                    f"Quad {price.get('quadruplePrice', {}).get('amount', '-')} {currency}"
                )
        else:
            st.warning("No price rows entered yet.")
    else:  # ticket
        price_type = data.get("price_type", "DISTRIBUTION")
        if price_type == "OCCUPANCY":
            occ = data.get("occupancy_prices", []) or []
            st.write(f"**Occupancy pricing** - {len(occ)} tier(s):")
            for o in occ:
                child_amt = o.get("child_amount")
                child_part = f" (Child: {child_amt} {currency})" if child_amt not in (None, "") else ""
                st.caption(f"{o.get('occupancy', '?')} pax: {o.get('amount', '?')} {currency}{child_part}")
        elif price_type == "SERVICE":
            st.write(f"**Flat service price:** {data.get('base_service_price', 0)} {currency}")
        else:
            st.write(f"**Adult:** {data.get('base_adult_price', 0)} · "
                    f"**Child:** {data.get('base_children_price', 0)} · "
                    f"**Infant:** {data.get('base_infant_price', 0)} {currency}")

    st.caption(f"Operational days: {', '.join(data.get('operational_days', []) or []) or '(not set)'}")
    if data.get("stop_sales"):
        st.caption(f"🚫 Stop sales: {len(data['stop_sales'])} date range(s) blocked")


def render_modalities_review(kind, base_code, base_label, base_data, extra_modalities, currency):
    """
    Consolidated "review everything before you publish" step for when a
    Ticket or ClosedTour is getting MORE THAN ONE modality/service created
    together (a base modality + any "Add another Modality" entries). Each
    extra modality was entered and edited in its own section further up the
    page, which by the time a human reaches the publish button has usually
    scrolled out of view - this shows every modality's code, label, and key
    pricing facts together in one place so nothing entered earlier gets
    forgotten or silently dropped before publishing.
    Only renders anything if there's actually more than one modality -
    a single modality is already fully visible right above the publish
    button, so a review step here would just be a redundant restatement.
    """
    all_modalities = [{"code": base_code, "label": base_label, "data": base_data}]
    for m in extra_modalities:
        all_modalities.append({"code": m.get("code"), "label": m.get("hint") or "(no label)", "data": m.get("data")})

    if len(all_modalities) <= 1:
        return

    st.subheader(f"📋 Review — {len(all_modalities)} modalities will be created together")
    st.caption("Double-check everything below before publishing - once sent, each modality is created "
              "as its own separate call to Travel Compositor.")
    for i, mod in enumerate(all_modalities):
        code_display = mod["code"] or "(code not set)"
        icon = "🟢 Base" if i == 0 else f"➕ Extra {i}"
        with st.expander(f"{icon}: `{code_display}` — {mod['label']}", expanded=False):
            _summarize_modality_pricing(kind, mod["data"], currency)


def _clear_batch_widget_state(prefixes, keep=None):
    """
    Sweeps st.session_state for every key starting with any of the given
    prefixes and removes it.

    CONFIRMED REAL BUG this exists to prevent: every per-item widget in the
    three batch/queue review flows (multi-tour, multi-ticket, multi-modality)
    is keyed off the item's POSITIONAL index in the queue (e.g.
    key=f"mct_days_{idx}", editable_field's key_suffix=f"_{idx}"). Streamlit
    widgets with a fixed key ignore the value= argument after first render -
    so whenever a positional slot gets reused by a DIFFERENT item, the new
    item displays the previous occupant's stale typed/edited values instead
    of its own data. This happens in two real situations:
      1. Skipping a non-last item: queue.pop(idx) shifts every later item
         down one slot, so the item now AT that slot inherits whatever the
         skipped item's widgets held.
      2. Starting a new batch: a fresh batch's first item is always idx==0,
         so it can inherit leftover state from the PREVIOUS batch's idx==0
         item if only a few top-level keys get cleared.
    Clearing every key under the flow's own prefix(es) - not just a short
    fixed list - closes both holes: the next render has nothing stale to
    fall back on, so every widget genuinely re-reads from the (correct,
    freshly-positioned) `data` dict again.

    CONFIRMED REAL BUG (found by audit, 2026-08-09): a flow's own control state shares its
    widget prefix. Transfer's queue lives in `xtf_queue` and its widgets in `xtf_adult_0`
    etc, so sweeping "xtf_" removed the queue, the extracted document text and the phase
    marker along with the widgets - and skipping one item mid-batch silently threw the
    operator back to the upload screen, losing every remaining item's AI extraction and
    every human edit already made. `keep` is how a caller protects the keys it is about to
    rely on; render_skip_item_button passes the flow's control keys.
    """
    protected = set(keep or ())
    for key in list(st.session_state.keys()):
        if key in protected:
            continue
        if any(key.startswith(p) for p in prefixes):
            st.session_state.pop(key, None)


# When detection comes back empty, this is the instruction the "detect again" button sends.
# Deliberately blunt: the operator has looked at the document and said these ARE transports,
# so the model's own Transfer-vs-Transport judgement is the thing being overruled.
FORCE_ALL_ROUTES_HINT = (
    "Treat EVERY route in this document as a product of the type being uploaded, including "
    "short local airport-to-hotel routes. Do not exclude any route on the grounds that it "
    "looks like a local transfer rather than a long-distance connection - that decision has "
    "already been made by the operator. List every distinct route and service-class "
    "combination the document prices, one candidate each."
)


def render_detection_diagnosis(noun):
    """Say what the last detection run actually did.

    An empty result has several very different causes - the document never reached the AI, the
    AI read it and returned nothing, or it returned candidates that were then discarded - and
    on screen they were identical. That ambiguity cost a real afternoon: a run that HAD found
    the routes and thrown them away looked exactly like one that found none."""
    info = getattr(ai_extractor_module, "LAST_DETECTION", None) or {}
    if not info:
        return
    with st.expander("🔬 What the AI actually did", expanded=False):
        st.caption(
            f"It read **{info.get('document_chars', 0):,} characters** of your document in "
            f"**{info.get('sections_read', 0)} pass(es)** and returned "
            f"**{info.get('count', 0)} {noun} candidate(s)**."
        )
        if not info.get("document_chars"):
            st.error("The document reached the AI empty - the file may not have converted to "
                     "text. Try exporting it again, or paste the rates in as text.")
        elif not info.get("count"):
            st.caption("The document was read in full, so this is the AI's judgement rather than "
                      "a technical failure. The instruction below overrules it.")


def render_empty_detection_retry(raw_text, noun, key_prefix, detect_fn, on_candidates):
    """Offer a second run when detection found nothing, instead of leaving a blank box.

    CONFIRMED REAL DEAD END (product owner, on a real transfer rate sheet uploaded as
    Transport): detection returned nothing, and the only thing on screen was an empty text
    field. The instruction box that would have fixed it lives on the PREVIOUS screen, so
    acting on the advice meant going back and re-uploading the document. The retry runs here,
    against the text already extracted, so nothing is uploaded twice."""
    st.markdown("**Try again, telling it what you can see and it can't:**")
    instruction = st.text_area(
        "Instruction for a second attempt", value=FORCE_ALL_ROUTES_HINT, height=110,
        key=f"{key_prefix}_retry_hint", label_visibility="collapsed")
    rcol1, rcol2 = st.columns([2, 3])
    with rcol1:
        if st.button(f"🔄 Detect {noun}s again with this instruction", type="primary",
                     key=f"{key_prefix}_retry_btn", use_container_width=True):
            with st.spinner(f"Reading the document again as {noun}s..."):
                try:
                    found = detect_fn(raw_text, human_hint=instruction)
                except Exception as e:
                    st.error(f"Detection failed: {friendly_error_message(e)}")
                    found = None
            if found:
                on_candidates(found)
                _clear_batch_widget_state([f"{key_prefix}_sel_", f"{key_prefix}_label_"])
                st.rerun()
            elif found is not None:
                st.error(f"Still nothing found. This document may genuinely not contain "
                         f"{noun}s — or name one route by hand in the box below.")
    with rcol2:
        st.caption("This re-reads the text already extracted from your document — nothing is "
                  "uploaded again. Edit the wording above to narrow it, e.g. *only the "
                  "Hurghada section, private transfers only*.")


def _swapped_label(label, dep, arr):
    """Rewrite a route label so it reads in the other direction.

    Falls back to appending "(return)" rather than producing something wrong: a label is what
    a human scans the list by, and a mislabelled row that says the opposite of what it does is
    worse than one that is merely verbose."""
    if dep and arr and dep in label and arr in label:
        placeholder = "\x00"
        return label.replace(dep, placeholder).replace(arr, dep).replace(placeholder, arr)
    return f"{label} (return)"


def ensure_return_candidates(candidates):
    """Guarantee that every route in the list has its opposite direction too.

    CONFIRMED REAL RULE (product owner): "when one Transport or Transfer is being created, it
    always has to be the second one as well, for the return option." Travel Compositor stores
    a route as departure -> arrival, so selling it both ways is two records.

    Done in CODE rather than left to the detection prompt, because "always" is an invariant and
    a prompt is a request. The prompt asks for both directions as well, so this usually adds
    nothing - but when the model lists a route only one way, the return leg still exists, and
    nobody has to notice that it is missing.

    Returns (candidates, how_many_added). Added rows are ticked, so the default behaviour is to
    publish both - unticking one is a deliberate act."""
    seen = {(str(c.get("departure_hint") or "").strip().lower(),
             str(c.get("arrival_hint") or "").strip().lower(),
             str(c.get("service_name") or "").strip().lower())
            for c in candidates}
    added = 0
    for cand in list(candidates):
        dep = str(cand.get("departure_hint") or "").strip()
        arr = str(cand.get("arrival_hint") or "").strip()
        if not (dep and arr) or dep.lower() == arr.lower():
            continue
        key = (arr.lower(), dep.lower(), str(cand.get("service_name") or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        mirrored = dict(cand)
        mirrored["departure_hint"], mirrored["arrival_hint"] = arr, dep
        label = str(cand.get("label") or "").strip()
        mirrored["label"] = (_swapped_label(label, dep, arr) if label
                             else f"{cand.get('service_name') or 'Return'}: {arr} to {dep}")
        mirrored["selected"] = True
        mirrored["is_return_leg"] = True
        candidates.append(mirrored)
        added += 1
    return candidates, added


def render_candidate_filter(candidates, key_prefix, noun):
    """A search box and three bulk buttons above a long candidate list.

    WHY: a real supplier rate sheet prices TWO services on every row - a Shuttle and a
    Private version of the same route - so a forty-route document detects as ~80 separate
    products, all pre-ticked. A human who only wants the Private ones was left un-ticking
    forty boxes by hand, which is both tedious and easy to get wrong by one.

    Matching is on the whole candidate (label, service name and both location hints), so
    typing "private" isolates a service class and typing "luxor" isolates a destination.

    The widget keys are swept after a bulk action on purpose: a Streamlit checkbox with a
    fixed key ignores its `value=` argument on every render after the first, so setting
    cand["selected"] alone would change the data and leave every box on screen showing its
    old state - the confirmed failure that _clear_batch_widget_state exists for."""
    if len(candidates) < 2:
        return
    total = len(candidates)
    chosen = sum(1 for c in candidates if c.get("selected"))

    fcol1, fcol2, fcol3, fcol4 = st.columns([3, 1.4, 1.2, 1.2])
    with fcol1:
        term = st.text_input(f"Filter {noun}s", key=f"{key_prefix}_filter",
                             placeholder="e.g. Private   ·   Shuttle   ·   Luxor",
                             label_visibility="collapsed").strip().lower()

    def _matches(cand):
        haystack = " ".join(str(cand.get(k) or "") for k in
                            ("label", "service_name", "departure_hint", "arrival_hint")).lower()
        return term in haystack

    def _apply(fn):
        for cand in candidates:
            fn(cand)
        _clear_batch_widget_state([f"{key_prefix}_sel_"])
        st.rerun()

    with fcol2:
        if st.button("Keep only these", key=f"{key_prefix}_only", disabled=not term,
                     use_container_width=True,
                     help="Tick every row matching the filter and untick every other row."):
            _apply(lambda c: c.__setitem__("selected", _matches(c)))
    with fcol3:
        if st.button("Select all", key=f"{key_prefix}_all", use_container_width=True):
            _apply(lambda c: c.__setitem__("selected", True))
    with fcol4:
        if st.button("Clear all", key=f"{key_prefix}_none", use_container_width=True):
            _apply(lambda c: c.__setitem__("selected", False))

    # Travel Compositor stores a route in ONE direction, and a "per way" rate sheet lists it
    # once - so selling the return leg means a second product per route. Sixteen routes is
    # sixteen more rows to type by hand, which is exactly the kind of work this screen exists
    # to remove. Added as candidates rather than silently doubling the queue, so the return
    # legs sit in the list and can be unticked or renamed like any other.
    ticked = [c for c in candidates if c.get("selected")]
    if ticked and st.button(f"↔️ Add the return direction for the {len(ticked)} ticked route(s)",
                            key=f"{key_prefix}_returns", use_container_width=True,
                            help="Creates a mirrored candidate for each ticked route, with the "
                                 "departure and arrival swapped. Prices are read from the "
                                 "document again for each one, so a return leg priced "
                                 "differently is still read correctly."):
        existing = {(str(c.get("departure_hint") or "").strip().lower(),
                     str(c.get("arrival_hint") or "").strip().lower(),
                     str(c.get("service_name") or "").strip().lower()) for c in candidates}
        added = 0
        for cand in ticked:
            dep = str(cand.get("departure_hint") or "").strip()
            arr = str(cand.get("arrival_hint") or "").strip()
            if not (dep and arr):
                continue
            key = (arr.lower(), dep.lower(), str(cand.get("service_name") or "").strip().lower())
            if key in existing:
                continue          # the document already listed this direction separately
            existing.add(key)
            mirrored = dict(cand)
            mirrored["departure_hint"], mirrored["arrival_hint"] = arr, dep
            label = str(cand.get("label") or "").strip()
            mirrored["label"] = (f"{cand.get('service_name') or 'Return'}: {arr} to {dep}"
                                 if not label else _swapped_label(label, dep, arr))
            mirrored["selected"] = True
            candidates.append(mirrored)
            added += 1
        _clear_batch_widget_state([f"{key_prefix}_sel_", f"{key_prefix}_label_"])
        st.session_state[f"{key_prefix}_returns_added"] = added
        st.rerun()

    if st.session_state.get(f"{key_prefix}_returns_added"):
        st.success(f"Added {st.session_state.pop(f'{key_prefix}_returns_added')} return "
                   f"direction(s) to the list below.")

    if term:
        st.caption(f"{sum(1 for c in candidates if _matches(c))} of {total} row(s) match "
                   f"“{term}”. **{chosen} currently ticked.**")
    else:
        st.caption(f"**{chosen} of {total} ticked.** Only ticked rows are reviewed and published.")


def with_learned_guidance(supplier_id, product_type, hint):
    """The operator's hint for this run, with what was learned from past corrections in front.

    Past corrections go FIRST and this run's hint LAST, because the hint is about the document
    in hand and must be able to override a habit learned from an older one. Returns the hint
    unchanged when nothing has been learned, so a supplier with no history behaves exactly as
    before."""
    guidance = extraction_memory.instruction_guidance(supplier_id, product_type) if supplier_id else ""
    hint = (hint or "").strip()
    if not guidance:
        return hint or None
    return f"{guidance}\n\nFOR THIS DOCUMENT: {hint}" if hint else guidance


def clarify_supplier_id(*preferred):
    """The supplier this correction belongs to, however the current flow happens to hold it.

    Each flow keeps its supplier under its own session key, and one of them
    (render_multi_modality_flow) has none in scope at all - so reading a local variable
    would have raised a NameError on the ClosedTour modality screen, which is one of the
    screens this feature exists for. Resolving from session state keeps every call site
    identical and cannot fail."""
    for value in preferred:
        if value:
            return str(value)
    for key in ("cfg_supplier_id", "tk_cfg_supplier_id", "tf_cfg_supplier_id",
                "tp_cfg_supplier_id", "hp_cfg_supplier_id"):
        value = st.session_state[key] if key in st.session_state else None
        if value:
            return str(value)
    return None


def remember_clarification(supplier_id, product_type, instruction, result):
    """Learn from an instruction typed into "Tell AI what to fix".

    CONFIRMED REAL REQUEST (product owner): "it would be extremely helpful if the included
    database could learn from the 'Tell AI what to fix' as this is the biggest issue."

    These are the highest-quality signal the app has. A value correction says what was wrong;
    an instruction says WHY, in the operator's own words - "this supplier puts the triple price
    in the third column" - which is a rule about how this supplier writes, and precisely what
    the extractor cannot work out alone. Only instructions that actually changed something are
    kept, so questions do not bury the rules."""
    changed = list((result or {}).get("changes") or {})
    if not (supplier_id and product_type and changed):
        return []
    extraction_memory.record_instruction(supplier_id, product_type, instruction, changed)
    return changed


HOUSE_RULE_CODEWORD = "Remember:"


def render_house_rule_shortcut(message: str, product_type: str, key_prefix: str) -> bool:
    """
    CONFIRMED PRODUCT-OWNER REQUEST (2026-08-13): "a Word that the AI tool/App knows, that this
    information is repeated might would help" - given as three examples of things repeated
    across many documents (a holiday surcharge rule, a per-night pricing convention, a stop-sale
    rule), none of which are true of just one supplier's document. The fix already existed for
    ONE of the three (house rules - see render_house_rules() above, "Nile Cruise prices are
    quoted per night" is its own documented example) but was buried in a collapsed expander at
    the bottom of the page, disconnected from the "Tell AI what to fix" box where a human
    actually types corrections. This surfaces it right there: typing "REMEMBER: <rule>" into any
    clarify box saves the rule as a permanent house rule for EVERY supplier of this product type
    (via extraction_memory.add_house_rule) instead of running a one-off AI correction against
    just this document.

    Returns True if the codeword was detected (the caller should render this and skip its normal
    Send/apply_clarification flow for this message - a codeword message is never sent to the
    per-document clarifier)."""
    text = (message or "").strip()
    if not text.upper().startswith(HOUSE_RULE_CODEWORD.upper()):
        return False
    rule_text = text[len(HOUSE_RULE_CODEWORD):].strip()
    if not rule_text:
        st.caption(f"Type the rule after \"{HOUSE_RULE_CODEWORD}\" - e.g. "
                  f"\"{HOUSE_RULE_CODEWORD} Nile Cruise prices are quoted per night - single price "
                  f"is nights x nightly rate.\"")
        return True
    st.info(f"🧠 Detected \"{HOUSE_RULE_CODEWORD}\" - this will be saved as a standing rule for "
            f"**every {product_type} supplier**, not just this document.")
    if st.button(f"✅ Remember this for every {product_type} supplier", key=f"{key_prefix}_house_rule_save", type="primary"):
        if extraction_memory.add_house_rule(product_type, rule_text):
            st.success(f"Saved. Applied to every future {product_type} extraction, for every "
                      f"supplier, from now on - see \"🏛️ House rules\" at the bottom of the page.")
        else:
            st.info("That rule is already saved - no change needed.")
        st.rerun()
    return True


def seed_transport_from_candidate(item, data, chosen_currency):
    """Fill in everything the app ALREADY KNOWS, so the review screen is never blank.

    CONFIRMED REAL FAILURE (product owner): "when I say focus on Marsa Alam to Hurghada, the
    arrival and the departure are already set, but it is never seen. Always empty... at this
    moment the App is not useful."

    He was exactly right. Detection had established the route, the human had chosen the
    currency at Step 2, and the house conventions fix the name, description, company and type -
    yet all of it was left to the extraction step, so when the model under-delivered the screen
    came back empty and the operator had to retype facts the app was already holding.

    Only PRICES genuinely require the document. Everything else is seeded here, deterministically
    and without an AI call. Seeding never overwrites: a value the extractor did produce always
    wins, so this can only ever add.

    Returns the list of field names it had to fill in, so the screen can say so - a pre-filled
    value that looks extracted is worse than a blank one."""
    seeded = []

    def _fill(key, value):
        if value and not str(data.get(key) or "").strip():
            data[key] = value
            seeded.append(key)

    _fill("departure_name", (item.get("departure_hint") or "").strip())
    _fill("arrival_name", (item.get("arrival_hint") or "").strip())

    service = (item.get("service_name") or "").strip()
    if not service and item.get("label"):
        # Labels look like "Private Transfer: Marsa Alam <-> Hurghada".
        service = str(item["label"]).split(":")[0].strip()
    _fill("service_name", service)
    _fill("transport_type_hint", service)
    _fill("company_name", builder_transport_company_name(service))
    # The human picked this at Step 2; it should never come back blank.
    _fill("currency", chosen_currency)
    _fill("description", builder_transport_description(
        service, data.get("departure_name"), data.get("arrival_name")))
    _fill("start_date", builder_start_date_or_today(""))
    return seeded


def render_batch_bulk_controls(queue, queue_key, index_key, phase_key, state_keys,
                               widget_prefixes, noun, key_prefix):
    """Leaving a review batch, without doing it one item at a time.

    CONFIRMED REAL NEED (product owner): "if multiple transports are detected, I must be able
    to remove with one click all transports instead of removing every detected transport
    manually." Six items meant six clicks on "Don't want this one"; forty would mean forty.

    Two DIFFERENT exits, because the existing "Cancel this batch" conflated them and always
    took the expensive one:

      * Back to the list - keeps the extracted document AND the detected candidates, and
        returns to the tick-box screen. Nothing is re-uploaded and nothing is re-detected, so
        changing your mind about which six of eighty to do costs one click, not another
        upload and another detection run.
      * Discard the rest - drops every item still unreviewed, keeping anything already
        published. This is the one that answers "remove them all".

    Neither touches Travel Compositor: an item already published stays published."""
    unpublished = [q for q in queue if q.get("publish_status") != "success"]
    published = len(queue) - len(unpublished)
    if len(queue) < 2:
        return
    bcol1, bcol2, bcol3 = st.columns([2, 2, 3])
    with bcol1:
        if st.button(f"⬅️ Back to the list of {noun}s", key=f"{key_prefix}_back_to_list",
                     use_container_width=True,
                     help="Return to the tick boxes without re-uploading or re-reading the "
                          "document. What you have already published stays published."):
            st.session_state[phase_key] = "prepare_queue"
            for key in (queue_key, index_key):
                st.session_state.pop(key, None)
            _clear_batch_widget_state(widget_prefixes, keep=state_keys)
            st.rerun()
    with bcol2:
        if st.button(f"🗑️ Discard the remaining {len(unpublished)}", key=f"{key_prefix}_discard_rest",
                     use_container_width=True, disabled=not unpublished,
                     help="Removes every one still to be reviewed, in one click."):
            remaining = [q for q in queue if q.get("publish_status") == "success"]
            if remaining:
                st.session_state[queue_key] = remaining
                st.session_state[index_key] = 0
            else:
                for key in state_keys:
                    st.session_state.pop(key, None)
            _clear_batch_widget_state(widget_prefixes, keep=state_keys)
            st.rerun()
    with bcol3:
        st.caption(f"{len(unpublished)} still to review"
                   + (f", {published} already published." if published else "."))


def render_skip_item_button(item_label, queue, idx, queue_session_key, index_session_key, cleanup_keys, button_key,
                            widget_state_prefixes=None):
    """
    Lets a human bail out on ONE item mid-batch-review (e.g. after seeing the
    AI-extracted name/description and deciding "I don't want this one"),
    without having to go through the rest of that item's review (geolocation,
    pricing, etc) or cancel the WHOLE batch. Removes just this item from the
    queue and reruns; if it was the last item left, clears the batch entirely
    since there's nothing left to review or publish.

    `widget_state_prefixes`: prefixes for _clear_batch_widget_state - pass
    this whenever skipping can leave a later item sitting in a queue slot
    whose widget keys were populated by the just-removed item (see that
    function's docstring). Without it, a skip can otherwise show the human
    the WRONG item's stale edited data on the very next render.
    """
    if st.button(f"❌ Don't want this one - remove '{item_label}' from the batch", key=button_key):
        queue.pop(idx)
        if not queue:
            for key in cleanup_keys:
                st.session_state.pop(key, None)
        else:
            st.session_state[queue_session_key] = queue
            st.session_state[index_session_key] = min(idx, len(queue) - 1)
            if widget_state_prefixes:
                # keep=cleanup_keys: those ARE the flow's queue/phase/source keys and they
                # start with the same prefix as its widgets - see _clear_batch_widget_state.
                _clear_batch_widget_state(widget_state_prefixes, keep=cleanup_keys)
        st.rerun()


def fetched_tour_matches_code(existing_tour_code):
    """
    CONFIRMED BUG FIX (full-app audit CRITICAL #3, 2026-09-01): Step 3's "Check what's already
    online for this code" button populates fetched_tour_provider_code/min_pax/max_pax/currency
    from whatever tour it fetched - but those globals used to be set once and never cleared, and
    every guard/usage site downstream only tested whether they were PRESENT, never whether they
    actually belonged to the tour currently being configured. Real failure mode: check tour A,
    click "Change details", type in tour B's code, forget to click "Check" again (or it fails) -
    the stale fields still read as "present," so tour B silently published with tour A's
    currency, provider code, and pax capacity, with nothing on the review screen to show it.

    fetched_tour_for_code (set alongside the other fetched_tour_* fields, on both success AND
    failure - see the Step 3 button handler) records which code the fetch was actually run
    against. Every site that used to just check truthiness of fetched_tour_provider_code etc.
    now calls this first and treats a mismatch exactly like "never fetched."
    """
    fetched = st.session_state.get("fetched_tour")
    return (
        bool(existing_tour_code)
        and st.session_state.get("fetched_tour_for_code") == existing_tour_code
        and isinstance(fetched, dict)
        and "error" not in fetched
    )


def try_code_variants(call_fn, code):
    """
    Tries `code` (or, if a list, each code in `code`) as given, then falls back
    to toggling the 'CLOSEDTOUR-' prefix on each - we've seen conflicting
    evidence about whether Travel Compositor's lookup needs the human
    ClosedTour/Provider Code (e.g. 'DPS-3') or the internal CLOSEDTOUR-XXXXX
    code returned by creation, so try both rather than betting on just one.

    CONFIRMED FIX (real production failure): the additional-Modality creation
    loop used to call this with ONLY the internal CLOSEDTOUR-XXXXX code (never
    the human tour code) - for at least one real supplier/tour, Travel
    Compositor's lookup only recognized the human code, so every Modality
    after the first 404'd with "Closed tour not found" even though the base
    Modality (which DOES try the human code first) succeeded moments earlier.
    Accepting a list here lets every caller try every known-good candidate,
    not just one.

    Returns (result_dict, code_that_worked_or_None).
    """
    codes = code if isinstance(code, (list, tuple)) else [code]
    variants = []
    for c in codes:
        if not c or c in variants:
            continue
        variants.append(c)
        alt = c[len("CLOSEDTOUR-"):] if c.upper().startswith("CLOSEDTOUR-") else f"CLOSEDTOUR-{c}"
        if alt not in variants:
            variants.append(alt)

    if not variants:
        # CONFIRMED BUG FIX (full-app audit LOW-MED, 2026-09-01): a blank/None `code` (every
        # candidate falsy) used to fall straight through the loop below with `result` still at
        # its initial `None` - every call site does `if "error" in result:`, and `"error" in
        # None` raises an unhandled TypeError instead of a friendly "no code provided" message.
        # Returning a proper error-shaped dict here means every existing call site's normal
        # error-handling path already does the right thing, with no call-site changes needed.
        return {"error": True, "message": "No code was provided to look up."}, None

    result = None
    for v in variants:
        result = call_fn(v)
        if "error" not in result:
            return result, v
    return result, None


def render_multi_ticket_flow(client, supplier_id, currency, on_request, release_days, tk_url, tk_files, min_passengers=1, max_passengers=9, default_ticket_code=""):
    """
    Batch flow for creating MULTIPLE full Tickets from one document that
    describes several distinct excursions:
    1. Reuse the URL/document(s) already provided above, detect distinct
       excursions, let the human explicitly SELECT which to create + assign
       each its own Ticket Code and Modality Code
    2. Review each SELECTED one individually - its OWN focused AI extraction
       (via a per-item hint), so excursions never get mixed up
    3. Publish all of them SEQUENTIALLY - each gets its own full
       create-ticket -> create-option -> deactivate sequence, with its own
       clear success/failure status (not one opaque batch call)
    """
    if "mt_phase" not in st.session_state:
        st.session_state.mt_phase = "gather"

    # ------------------------------------------------------------------
    # PHASE 1: detect excursions from the source already provided above
    # ------------------------------------------------------------------
    if st.session_state.mt_phase == "gather":
        if not (tk_url or tk_files):
            st.info("Provide a URL and/or upload document(s) above, then click below.")
        if st.button("🔎 Detect Excursions", disabled=not (tk_url or tk_files)):
            with st.spinner("Gathering content and detecting distinct excursions..."):
                try:
                    combined_parts = []
                    doc_raw_images = []
                    doc_image_urls = []
                    seen_image_hashes = set()
                    if tk_url:
                        page_text, page_text_err = _fetch_url_text_safe(tk_url)
                        if page_text is not None:
                            combined_parts.append(f"--- SOURCE: WEB PAGE ({tk_url}) ---\n{page_text}")
                        else:
                            st.warning(f"⚠️ Couldn't fetch the product page URL: {page_text_err}.")
                    for uploaded in (tk_files or []):
                        suffix = os.path.splitext(uploaded.name)[1]
                        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                            tmp.write(uploaded.getbuffer())
                            tmp_path = tmp.name
                        _doc_text = extract_raw_text(tmp_path)
                        _scan_warning = document_reader_scanned_warning(tmp_path, _doc_text)
                        if _scan_warning:
                            st.session_state.setdefault("_scanned_doc_warnings", []).append(_scan_warning)
                        combined_parts.append(f"--- SOURCE: UPLOADED DOCUMENT ({uploaded.name}) ---\n{_doc_text}")
                        remaining_budget = 12 - len(doc_raw_images)
                        _doc_image_errors = []
                        embedded_images = extract_images(tmp_path, max_images=remaining_budget, seen_hashes=seen_image_hashes, errors=_doc_image_errors, label=uploaded.name) if remaining_budget > 0 else []
                        if embedded_images:
                            for i, (img_bytes, ext) in enumerate(embedded_images):
                                doc_raw_images.append((f"{os.path.splitext(uploaded.name)[0]}_img{i+1}.{ext or 'jpg'}", img_bytes))
                            try:
                                new_urls, _upload_errors = upload_images_r2_with_errors(embedded_images)
                                doc_image_urls.extend(new_urls)
                                _doc_image_errors.extend(_upload_errors)
                            except Exception as e:
                                _doc_image_errors.append(f"'{uploaded.name}': R2 upload failed entirely - {e}")
                        _warn_page_image_upload_errors(_doc_image_errors)
                        os.remove(tmp_path)

                    if not combined_parts:
                        st.error("Nothing to extract - the product page URL couldn't be fetched and no document(s) were provided.")
                        st.stop()

                    raw_text = "\n\n".join(combined_parts)
                    detected = detect_ticket_variants(raw_text)

                    candidates = []
                    for e in detected:
                        # CONFIRMED (product owner, 2026-08-22): code and client-facing name are
                        # two different things. The base name below ("Standard"/"Standard
                        # Private") is what the CLIENT sees and never changes just because the
                        # supplier happens to print their own reference code on this row (e.g. a
                        # "Tour Code" column reading "WT1", "WT2", ...). That supplier code
                        # exists for the SUPPLIER's benefit only, so it's appended to the CODE,
                        # never substituted into the name. When the document gives no such code
                        # (the normal case), modality_code stays exactly the base name, unchanged
                        # from before this split existed.
                        _base_modality_name = "Standard Private" if e.get("is_private") else "Standard"
                        _supplier_code = str(e.get("supplier_code") or "").strip()
                        _modality_code = (
                            f"{_base_modality_name.upper().replace(' ', '_')}_{_supplier_code}"
                            if _supplier_code else _base_modality_name
                        )
                        candidates.append({
                            "label": e.get("label", ""), "ticket_code": "",
                            "modality_code": _modality_code,
                            "modality_name": _base_modality_name,
                            "selected": True,
                            # Real AI-detected excursion - safe to later restrict extraction
                            # to just this one (see is_genuine_variant usage in PHASE 3 below).
                            "is_genuine_variant": True,
                        })
                    if not candidates:
                        # No real excursion variants detected (single-excursion case) - the
                        # "Ticket Name" typed in PHASE 2 is just a display label, not a real
                        # variant to filter the source by - is_genuine_variant stays False so
                        # PHASE 3 never sends it to the AI as a variant filter (doing so
                        # caused the AI to search for a nonexistent named variant and return
                        # an empty extraction - same bug as the ClosedTour flow had).
                        # Prefill the Ticket Code from what was already entered back in Step 3
                        # (default_ticket_code) so the human doesn't have to type it again here.
                        candidates = [{"label": "", "ticket_code": default_ticket_code, "modality_code": "Standard",
                                      "modality_name": "Standard",
                                      "selected": True, "is_genuine_variant": False}]

                    _warn_page_image_upload_errors(_add_page_images_to_doc_pool(tk_url, doc_raw_images, doc_image_urls))

                    if len(doc_image_urls) >= len(doc_raw_images):
                        doc_raw_images = []

                    st.session_state.mt_raw_text = raw_text
                    st.session_state.mt_candidates = candidates
                    st.session_state.mt_doc_raw_images = doc_raw_images
                    st.session_state.mt_hosted_image_candidates = list(dict.fromkeys(doc_image_urls))
                    st.session_state.mt_phase = "prepare_queue"
                    st.rerun()
                except Exception as e:
                    st.error(f"Detection failed: {friendly_error_message(e)}")
        return

    # ------------------------------------------------------------------
    # PHASE 2: explicitly SELECT which excursions to create as Tickets
    # ------------------------------------------------------------------
    if st.session_state.mt_phase == "prepare_queue":
        candidates = st.session_state.mt_candidates
        single_ticket = len(candidates) == 1

        # Same distinction as the ClosedTour batch flow: an "excursion" here
        # means a genuinely different Ticket PRODUCT, never a "Modality"
        # (the pricing option within one Ticket) - most documents describe
        # only ONE excursion, and the wording must say so plainly rather
        # than implying variants were found when none were.
        if single_ticket:
            st.subheader("Set up this Ticket")
            st.caption("This document describes one excursion - no other variants were found, so nothing "
                      "to choose between here. The Ticket Code is already carried over from Step 3 below "
                      "(edit it here if you want to change it). It still needs a Modality Code before it "
                      "can be created (a 'Modality', Travel Compositor's own term, is the pricing option "
                      "for the Ticket, e.g. 'Standard' - you can add more Modalities for this same Ticket "
                      "in the next step).")
        else:
            st.subheader(f"{len(candidates)} excursions detected - choose which ones to create as Tickets")
            st.caption("The AI found what look like several different excursions below - each ticked row "
                      "becomes its own separate Ticket. Untick any row you don't actually want. For each "
                      "ticked row, fill in the two code fields on the right (hover the ⓘ next to each for "
                      "what it means).")

        for i, cand in enumerate(candidates):
            cand.setdefault("modality_name", cand.get("modality_code", "Standard"))
            ccol1, ccol2, ccol3, ccol4, ccol5 = st.columns([1, 3, 2, 2, 2])
            with ccol1:
                cand["selected"] = st.checkbox("Include", value=cand["selected"], key=f"mt_sel_{i}")
            with ccol2:
                label_text = "Ticket Name" if single_ticket else "Excursion"
                cand["label"] = st.text_input(label_text, value=cand["label"], key=f"mt_label_{i}")
            with ccol3:
                cand["ticket_code"] = st.text_input(
                    "Ticket Code", value=cand["ticket_code"], key=f"mt_code_{i}", placeholder="e.g. BALI-T1",
                    help="Your own reference code for THIS ticket - make it up yourself, e.g. 'BALI-T1'."
                )
            with ccol4:
                cand["modality_name"] = st.text_input(
                    "Modality Name", value=cand["modality_name"], key=f"mt_modname_{i}",
                    help="What the CLIENT sees, e.g. 'Standard' or 'Standard Private' - always the "
                         "normal descriptive name, never a supplier reference code."
                )
            with ccol5:
                cand["modality_code"] = st.text_input(
                    "Modality Code", value=cand["modality_code"], key=f"mt_modcode_{i}",
                    help="What the SUPPLIER sees. If the document assigns this exact service its own "
                         "reference code (e.g. a 'Tour Code' column reading 'WT1'), that code has "
                         "already been appended here automatically - edit if needed. Otherwise this "
                         "matches the Modality Name above."
                )

        if st.button("➕ Add another excursion manually"):
            candidates.append({"label": "", "ticket_code": "", "modality_code": "Standard",
                              "modality_name": "Standard",
                              "selected": True, "is_genuine_variant": False})
            st.rerun()

        missing_codes = []
        new_queue = []
        seen_ticket_codes = {}
        seen_modality_codes = {}
        for cand in candidates:
            if not cand["selected"]:
                continue
            code = cand["ticket_code"].strip()
            mod_code = cand["modality_code"].strip()
            if not code or not mod_code:
                missing_codes.append(cand["label"] or "(unnamed excursion)")
                continue
            seen_ticket_codes.setdefault(code, []).append(cand["label"] or "(unnamed excursion)")
            seen_modality_codes.setdefault(mod_code.lower(), []).append(cand["label"] or "(unnamed excursion)")
            new_queue.append({"label": cand["label"], "ticket_code": code, "modality_code": mod_code,
                             "modality_name": (cand.get("modality_name") or mod_code).strip(), "data": None,
                             "confirmed": False, "is_genuine_variant": cand.get("is_genuine_variant", False)})

        duplicate_codes = {code: labels for code, labels in seen_ticket_codes.items() if len(labels) > 1}
        # CONFIRMED REAL REQUEST (product owner, 2026-08-24): two rows in the SAME batch sharing a
        # Modality Code is a strong signal the same supplier product got detected/entered twice -
        # block it here, same severity as a duplicate Ticket Code, rather than only warning about
        # it against Travel Compositor's existing tickets below.
        duplicate_modality_codes = {mc: labels for mc, labels in seen_modality_codes.items() if len(labels) > 1}

        if missing_codes:
            st.error(f"🚫 These selected excursions are missing a Ticket Code or Modality Code and were "
                    f"excluded - enter one for each before continuing: {missing_codes}")
        if duplicate_codes:
            for code, labels in duplicate_codes.items():
                st.error(f"🚫 Ticket Code `{code}` is used by more than one selected excursion ({', '.join(labels)}) "
                        f"- each Ticket needs its own unique code.")
        if duplicate_modality_codes:
            for mc, labels in duplicate_modality_codes.items():
                st.error(f"🚫 Modality Code `{mc}` is used by more than one selected excursion ({', '.join(labels)}) "
                        f"- this usually means the same supplier product was detected/entered twice. Give each "
                        f"a distinct Modality Code, or untick the duplicate.")

        for q in new_queue:
            existing_check = check_code_availability(client, "ticket", supplier_id, q["ticket_code"])
            if existing_check and existing_check["exists"]:
                st.error(f"🚫 Ticket Code `{q['ticket_code']}` ({q['label'] or '(unnamed)'}) is ALREADY TAKEN "
                        f"by an existing ticket (\"{existing_check.get('name') or '(unnamed)'}\") - choose a "
                        f"different code before publishing, or this will fail.")
            # CONFIRMED REAL REQUEST (product owner, 2026-08-24): the supplier's own code is often
            # reused as this Modality Code - see check_modality_code_availability's docstring for
            # why this catches a duplicate the Ticket-Code check above cannot.
            mod_check = check_modality_code_availability(client, supplier_id, q["modality_code"])
            if mod_check and mod_check["exists"]:
                st.warning(f"⚠️ Modality Code `{q['modality_code']}` ({q['label'] or '(unnamed)'}) is ALREADY "
                          f"USED by existing ticket **{mod_check['ticket_name']}** (`{mod_check['ticket_code']}`) "
                          f"for this supplier - if that's the same supplier product, this would create a "
                          f"duplicate. Double-check before continuing.")
            elif mod_check and mod_check.get("incomplete"):
                st.caption(f"ℹ️ Modality Code `{q['modality_code']}`: {mod_check['incomplete']}")

        ready_to_review = new_queue and not missing_codes and not duplicate_codes and not duplicate_modality_codes
        st.caption(f"**{len(new_queue)}** ticket(s) ready to review." if ready_to_review else
                  "Fix the issues above before continuing.")

        if st.button("➡️ Start Reviewing", type="primary", disabled=not ready_to_review):
            st.session_state.mt_queue = new_queue
            st.session_state.mt_queue_index = 0
            st.session_state.mt_phase = "reviewing"
            st.rerun()
        return

    # ------------------------------------------------------------------
    # PHASE 3: review each selected ticket individually, one at a time.
    # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-12): "The App must first detect the main
    # Information of the Ticket and only after the first step, the App must detect the
    # Modality of the chosen Ticket... we must separate main information from the modality."
    # This is now two real steps (current["step"] == "main" then "modality"), each backed by
    # its OWN separate AI call (extract_ticket_main_info then extract_ticket_modality_data) -
    # previously a single extract_ticket_data() call tried to read the name/description AND
    # a complex pricing table at once, which is the likely cause of a real bug where
    # ticket_name/description came back empty on a multi-excursion document with heavy
    # seasonal price tables (the pricing table crowded out the main-info reading).
    # UPDATED (2026-08-13, product-owner request): a new Ticket must only ever be CREATED with
    # ONE Modality (extra costs are Modality-specific, so mixing Modalities during creation was
    # causing real errors). detect_ticket_modalities() still runs to spot other Modalities in the
    # document, but now only INFORMS the human about them - it never auto-extracts or
    # auto-queues them for creation. Other Modalities are added afterward via "2: Add new
    # Modality to existing Ticket".
    # ------------------------------------------------------------------
    if st.session_state.mt_phase == "reviewing":
        idx = st.session_state.mt_queue_index
        queue = st.session_state.mt_queue
        current = queue[idx]
        current.setdefault("step", "main")

        st.subheader(f"Reviewing ticket {idx + 1} of {len(queue)}: **{current['label'] or current['ticket_code']}** (code: {current['ticket_code']})")
        st.progress(idx / len(queue))
        with st.expander("Not what you wanted?"):
            if st.button("🔙 Cancel this batch - return to single-Ticket flow", key=f"mt_cancel_{idx}"):
                for key in ["mt_phase", "mt_raw_text", "mt_candidates", "mt_queue", "mt_queue_index",
                           "mt_doc_raw_images", "mt_hosted_image_candidates"]:
                    st.session_state.pop(key, None)
                _clear_batch_widget_state(SHARED_WIDGET_STATE_PREFIXES)
                st.rerun()

        # Same fix as the ClosedTour batch flow: only pass a variant_hint when this
        # label came from a REAL AI-detected excursion (is_genuine_variant) - the
        # human-typed "Ticket Name" (single-excursion case) is just a display label,
        # not a real variant present in the source, and passing it as a filter caused
        # the AI to find no match and return an empty extraction. Used by BOTH the
        # main-info call and the modality call below, so both stay focused on the
        # same excursion in a multi-excursion document.
        variant_hint = current["label"] if current.get("is_genuine_variant") else None

        if current["data"] is None:
            with st.spinner(f"Extracting main ticket info{f' focused on ' + repr(current['label']) if variant_hint else ''}..."):
                # Same crash-prevention as the ClosedTour batch flow - never leave a
                # call that can genuinely fail (rate limit, network hiccup) unguarded.
                try:
                    # CONFIRMED REAL BUG (audit, 2026-08-28): with_learned_guidance (past
                    # corrections for this supplier/product type - see its own docstring) was
                    # wired into the single-Ticket flow only. The batch flow, which is what's
                    # actually used for volume work, extracted every excursion with no memory
                    # of anything corrected before - same defect already named for a different
                    # gap in audit-2026-08-24-followup.md's D-6 note ("the path used for volume
                    # work is the one that ignores everything the platform has learned").
                    current["data"] = extract_ticket_main_info(
                        st.session_state.mt_raw_text, variant_hint=variant_hint,
                        human_hint=with_learned_guidance(supplier_id, "Ticket", ""))
                    current["data"]["image_urls"] = [FALLBACK_IMAGE]
                    # Only fills in when this document didn't state its own cancellation
                    # terms - see apply_cancellation_link_default's docstring. Runs once,
                    # here at extraction time, not inside the review widgets below.
                    current["_cancellation_link_scope"] = cancellation_links.apply_cancellation_link_default(
                        current["data"], supplier_id, "Ticket")
                except Exception as e:
                    st.error(f"⚠️ Couldn't extract main info for this excursion: {friendly_error_message(e)}")
                    if st.button("🔄 Retry extraction", key=f"mt_retry_extract_{idx}"):
                        st.rerun()
                    return

        data = current["data"]

        # ==================================================================
        # STEP A: MAIN TICKET INFO - name, description, city, includes/excludes,
        # meeting points, duration, cancellation policy, images. No pricing here.
        # ==================================================================
        if current["step"] == "main":
            st.caption("**Step 1 of 2: Main ticket info.** Pricing/Modality comes next, as its own step.")

            editable_field("Ticket name", data, "ticket_name", widget="text_input", key_suffix=f"_{idx}")
            editable_field("Description", data, "description", widget="html_text_area", height=120, key_suffix=f"_{idx}")
            # CONFIRMED PRODUCT-OWNER RULE: the AI now retries once if either field comes back
            # blank (see extract_ticket_main_info's safety net), but this is the last line of
            # defense - a ticket can never publish with no name/description, so flag it plainly
            # rather than let a still-empty field slip through to publish unnoticed.
            if not (data.get("ticket_name") or "").strip():
                st.error("🚫 Ticket name is empty - fill it in above before continuing.")
            if not (data.get("description") or "").strip():
                st.error("🚫 Description is empty - fill it in above before continuing.")
            if current.get("_cancellation_link_scope"):
                st.caption(f"ℹ️ This document didn't state its own cancellation terms - the table "
                          f"below was filled in from {current['_cancellation_link_scope']}. Edit or "
                          f"clear it if this ticket needs different terms.")
            render_cancellation_policy_editor(data, f"mt_{idx}")
            editable_field("Condition (internal remarks)", data, "cancellation_policy_text", widget="text_area", height=80, key_suffix=f"_{idx}")
            # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-25): "Voucher Remarks" and "What to
            # bring" combined into one editable box - see merge_what_to_bring_into_voucher_
            # remarks' docstring; they always ended up concatenated at publish time anyway.
            merge_what_to_bring_into_voucher_remarks(data)
            editable_field("Voucher Remarks (shown to the customer, includes what to bring)", data,
                           "voucher_remarks", widget="text_area", height=100, key_suffix=f"_{idx}")
            # CONFIRMED PRODUCT-OWNER RULE (2026-08-12): the separate Manual Notes box is no longer
            # needed for Tickets - every field (Voucher Remarks, Condition, Stop Sales, Modality
            # Supplements, etc.) is now directly editable with its own pencil/text box, so a human
            # can add anything a document doesn't say straight into the real field instead of a
            # side note that only gets appended to Voucher Remarks at publish time.

            render_skip_item_button(
                current['label'] or current['ticket_code'], queue, idx,
                "mt_queue", "mt_queue_index",
                ["mt_phase", "mt_raw_text", "mt_candidates", "mt_queue", "mt_queue_index",
                 "mt_doc_raw_images", "mt_hosted_image_candidates"],
                button_key=f"mt_skip_{idx}",
                widget_state_prefixes=["mt_"] + SHARED_WIDGET_STATE_PREFIXES
            )

            editable_field("City", data, "city", widget="text_input", key_suffix=f"_{idx}")

            # ------------------------------------------------------------------
            # Geolocation resolve + human confirm - REQUIRED before this ticket
            # can move on to the Modality/Pricing step. Without this, an unresolved/wrong
            # city silently fails at publish time with a raw "GeolocationVO validation
            # error" and no way to fix it from inside the batch flow.
            # ------------------------------------------------------------------
            st.markdown(f"**📍 Location for {current['label'] or current['ticket_code']}**")
            mt_city = data.get("city", "")
            # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): manual coordinates (from a
            # search-pick or manual lat/lng entry, below) used to be shown with `display_name`
            # set to whatever the City field CURRENTLY says - so editing City after picking
            # coordinates silently relabeled the OLD, now-unrelated coordinates with the NEW
            # city name, looking exactly like a correctly re-verified location while actually
            # publishing a mismatch. `manual_coords_for_city` records which city the manual
            # coordinates were actually chosen for; a City edit since then invalidates them the
            # same way picking a brand-new location already does (both change what publishes,
            # both must un-confirm the "I've checked this" tick - see _mt_clear_geo_confirmation
            # below), forcing a fresh geocode/re-pick against the new city instead of a stale
            # coordinate pair wearing the new city's name.
            if (data.get("manual_latitude") is not None and data.get("manual_longitude") is not None
                    and data.get("manual_coords_for_city") != mt_city):
                data["manual_latitude"] = None
                data["manual_longitude"] = None
                data.pop("manual_coords_for_city", None)
                _mt_clear_geo_confirmation(current, idx)
            if data.get("manual_latitude") is not None and data.get("manual_longitude") is not None:
                mt_geo = {"latitude": data["manual_latitude"], "longitude": data["manual_longitude"],
                          "display_name": mt_city, "valid": True}
            else:
                mt_geo = geocode(mt_city)  # cached in geocoding_client - cheap to call every rerun

            if mt_geo.get("valid"):
                mt_lat, mt_lng = mt_geo["latitude"], mt_geo["longitude"]
                mt_maps_link = f"https://www.google.com/maps?q={mt_lat},{mt_lng}"
                st.markdown(
                    f"<div style='background-color:#d4edda; color:#155724; padding:8px 12px; "
                    f"border-radius:4px;'>📍 Resolved: <strong>{mt_geo.get('display_name') or mt_city}</strong>"
                    f"<br>Coordinates: {mt_lat:.6f}, {mt_lng:.6f} — "
                    f"<a href='{mt_maps_link}' target='_blank'>Open in Google Maps to verify</a></div>",
                    unsafe_allow_html=True
                )
                st.caption("Geocoding data © OpenStreetMap contributors")
            else:
                st.markdown(
                    "<div style='background-color:#f8d7da; color:#721c24; padding:6px 12px; "
                    "border-radius:4px;'>❌ Geolocation NOT resolved - the City name may not match a known "
                    "location. Search below or enter coordinates manually.</div>",
                    unsafe_allow_html=True
                )

            with st.expander("🔍 Search for a better match / fix this location", expanded=not mt_geo.get("valid")):
                mt_geo_query = st.text_input("Search for a location", value=mt_city, key=f"mt_geo_query_{idx}")
                if st.button("🔎 Search", key=f"mt_geo_search_btn_{idx}"):
                    with st.spinner("Searching..."):
                        current["geo_search_results"] = geocode_search(mt_geo_query, limit=5)
                if current.get("geo_search_results"):
                    for gi, candidate in enumerate(current["geo_search_results"]):
                        ggcol1, ggcol2 = st.columns([4, 1])
                        with ggcol1:
                            st.write(f"**{candidate['display_name']}**")
                            st.caption(f"{candidate['latitude']:.6f}, {candidate['longitude']:.6f} ({candidate.get('type', '')})")
                        with ggcol2:
                            if st.button("Use this", key=f"mt_geo_pick_{idx}_{gi}"):
                                data["manual_latitude"] = candidate["latitude"]
                                data["manual_longitude"] = candidate["longitude"]
                                data["manual_coords_for_city"] = mt_city
                                _mt_clear_geo_confirmation(current, idx)
                                current["geo_search_results"] = None
                                st.rerun()

                st.markdown("**Or enter coordinates manually:**")
                mgcol1, mgcol2 = st.columns(2)
                with mgcol1:
                    mt_man_lat = st.number_input("Latitude", value=data.get("manual_latitude"), format="%.6f", key=f"mt_geo_manlat_{idx}", placeholder="e.g. 27.394900")
                with mgcol2:
                    mt_man_lng = st.number_input("Longitude", value=data.get("manual_longitude"), format="%.6f", key=f"mt_geo_manlng_{idx}", placeholder="e.g. 33.678400")
                if st.button("📍 Use these coordinates", key=f"mt_geo_manual_btn_{idx}", disabled=mt_man_lat is None or mt_man_lng is None):
                    data["manual_latitude"] = mt_man_lat
                    data["manual_longitude"] = mt_man_lng
                    data["manual_coords_for_city"] = mt_city
                    _mt_clear_geo_confirmation(current, idx)
                    st.rerun()

            current["geo_confirmed"] = st.checkbox(
                "✅ I've checked this location and it's correct for this ticket",
                value=current.get("geo_confirmed", False), key=f"mt_geo_confirm_{idx}",
                disabled=not mt_geo.get("valid")
            )
            if not mt_geo.get("valid"):
                st.info("👆 Resolve the location above before this ticket can be confirmed.")
            elif not current["geo_confirmed"]:
                st.info("👆 Please check the location above and confirm it's correct.")

            st.markdown(f"**Images for {current['label'] or current['ticket_code']}**")
            if data.get("image_urls") == [FALLBACK_IMAGE] or not data.get("image_urls"):
                st.caption("⚠️ No real image picked yet - using a generic placeholder. Pick at least one real "
                          "image below (Travel Compositor requires at least one image per Ticket).")
            else:
                st.caption(f"{len([u for u in data.get('image_urls', []) if u != FALLBACK_IMAGE])} image(s) selected.")

            def _mt_add_url_images():
                selected = render_url_image_picker(st.session_state.mt_hosted_image_candidates, f"mt_found_{idx}")
                if selected:
                    current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                    data["image_urls"] = current_imgs + selected
                    return len(selected)
                return 0

            render_closable_image_section(
                bool(st.session_state.get("mt_hosted_image_candidates")),
                f"🖼️ Images found in your document/page ({len(st.session_state.get('mt_hosted_image_candidates') or [])})",
                f"mt_found_{idx}_closed", _mt_add_url_images
            )

            def _mt_add_doc_image():
                added = render_doc_image_picker(st.session_state.mt_doc_raw_images, f"mt_doc_{idx}")
                if added:
                    current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                    data["image_urls"] = current_imgs + [added]
                    return 1
                return 0

            render_closable_image_section(
                bool(st.session_state.get("mt_doc_raw_images")),
                f"📥 Images needing hosting ({len(st.session_state.get('mt_doc_raw_images') or [])})",
                f"mt_doc_{idx}_closed", _mt_add_doc_image
            )

            mt_default_query = current["label"] or data.get("ticket_name", "") or data.get("city", "")

            def _mt_add_pexels():
                selected = render_stock_photo_picker("Pexels", search_images, mt_default_query, f"mt_pexels_{idx}")
                if selected:
                    current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                    data["image_urls"] = current_imgs + selected
                    return len(selected)
                return 0

            render_closable_image_section(True, "🖼️ Search free stock photos (Pexels)", f"mt_pexels_{idx}_closed", _mt_add_pexels)

            def _mt_add_pixabay():
                selected = render_stock_photo_picker("Pixabay", search_images_pixabay, mt_default_query, f"mt_pixabay_{idx}")
                if selected:
                    current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                    data["image_urls"] = current_imgs + selected
                    return len(selected)
                return 0

            render_closable_image_section(True, "🖼️ Search free stock photos (Pixabay)", f"mt_pixabay_{idx}_closed", _mt_add_pixabay)

            # CONFIRMED FIX (2026-08-19): min_value=0 so an unset duration stays honestly blank
            # (0) instead of the shared widget silently substituting a fabricated 1.
            editable_field("Duration (hours)", data, "duration", widget="number_input", key_suffix=f"_{idx}",
                          min_value=0)

            inc_df = pd.DataFrame([{"Item": x} for x in data.get("includes", [])]) if data.get("includes") else pd.DataFrame(columns=["Item"])
            def _save_mt_includes(edf, data=data):
                data["includes"] = [str(r.get("Item") or "").strip() for _, r in edf.iterrows() if _safe_cell_str(r.get("Item")).strip()]
            editable_table("Includes", inc_df, f"mt_includes_{idx}", on_save=_save_mt_includes)

            exc_df = pd.DataFrame([{"Item": x} for x in data.get("excludes", [])]) if data.get("excludes") else pd.DataFrame(columns=["Item"])
            def _save_mt_excludes(edf, data=data):
                data["excludes"] = [str(r.get("Item") or "").strip() for _, r in edf.iterrows() if _safe_cell_str(r.get("Item")).strip()]
            editable_table("Excludes", exc_df, f"mt_excludes_{idx}", on_save=_save_mt_excludes)

            mp_default = [{"Description": m.get("description", "")} for m in data.get("meeting_points", [])] or [{"Description": "Hotel Lobby"}]
            mp_df = pd.DataFrame(mp_default)
            def _save_mt_mp(edf, data=data):
                data["meeting_points"] = [
                    {"description": str(r.get("Description") or "").strip(), "variable_location": str(r.get("Description") or "").strip().lower() == "hotel lobby"}
                    for _, r in edf.iterrows() if _safe_cell_str(r.get("Description")).strip()
                ]
            editable_table("Meeting Points", mp_df, f"mt_mp_{idx}", on_save=_save_mt_mp)

            name_and_description_valid = bool((data.get("ticket_name") or "").strip()) and bool((data.get("description") or "").strip())
            ready_for_modality = name_and_description_valid and mt_geo.get("valid") and current.get("geo_confirmed")

            if st.button("➡️ Continue to Modality/Pricing", type="primary", disabled=not ready_for_modality, key=f"mt_continue_modality_{idx}"):
                with st.spinner(f"Extracting pricing/Modality{f' focused on ' + repr(current['label']) if variant_hint else ''} - this is a separate AI call from the main info above..."):
                    try:
                        modality_data = extract_ticket_modality_data(
                            st.session_state.mt_raw_text, variant_hint=variant_hint,
                            human_hint=with_learned_guidance(supplier_id, "Ticket", ""))
                    except Exception as e:
                        st.error(f"⚠️ Couldn't extract pricing/Modality for this excursion: {friendly_error_message(e)}")
                        return
                    data.update(modality_data)
                    reset_child_age_band_widgets(f"mt_{idx}")
                    floor_start_date_for_new_data(data, widget_key=f"mt_start_date_{idx}")
                    # Same fixed-key staleness as the child-age boxes above (see
                    # reset_child_age_band_widgets' docstring) - the languages multiselect is
                    # keyed on this same positional slot, so a fresh Modality extraction needs
                    # its stale selection cleared too, or a re-used slot shows the PREVIOUS
                    # item's language picks instead of this one's freshly extracted default.
                    st.session_state.pop(f"mt_{idx}_languages", None)
                    # CONFIRMED BUG FIX (full-app audit MEDIUM (plausible), 2026-09-01): this
                    # re-extraction updates `data` with fresh operational days, end date, price
                    # type and service price - but 4 widgets bound to those exact fields were
                    # never cleared, same fixed-key staleness as every other widget reset in
                    # this handler. A widget with a fixed key ignores a freshly computed `value=`
                    # after its first render, so the OLD (pre-re-extraction) value would render
                    # right back into `data` on the very next run, silently reverting the fresh
                    # extraction for exactly these 4 fields.
                    st.session_state.pop(f"mt_op_days_{idx}", None)
                    st.session_state.pop(f"mt_end_date_{idx}", None)
                    st.session_state.pop(f"mt_{idx}_price_type", None)
                    st.session_state.pop(f"mt_{idx}_service_price", None)
                # CONFIRMED PRODUCT-OWNER REQUEST: when creating a new Ticket, only ever create
                # ONE Modality. If the document describes other pricing categories for this same
                # excursion (e.g. a second price table for another guide language), do NOT
                # auto-extract or auto-queue them for creation here - just detect and INFORM the
                # human. Other Modalities get added separately afterward, via "2: Add new Modality
                # to existing Ticket" (reachable through Update/Refresh existing Service once this
                # Ticket is published).
                if not current.get("modalities_auto_detected"):
                    try:
                        detected_mods = detect_ticket_modalities(st.session_state.mt_raw_text, variant_hint=variant_hint)
                    except Exception:
                        detected_mods = []  # best-effort - informational only
                    current["other_modalities_detected"] = [
                        (m.get("label") or "").strip() for m in detected_mods if (m.get("label") or "").strip()
                    ]
                    current["modalities_auto_detected"] = True
                current["step"] = "modality"
                st.rerun()
            if not ready_for_modality:
                st.info("Fill in Ticket name/Description and confirm the location above before continuing to Modality/Pricing.")
            return

        # ==================================================================
        # STEP B: MODALITY / PRICING - base price, occupancy, extra costs,
        # seasonal supplements, operational days, time slots, stop sales.
        # Reached only after Step A's main info is confirmed.
        # ==================================================================
        st.caption(f"**Step 2 of 2: Modality/Pricing for {current['label'] or current['ticket_code']}.**")
        if st.button("🔙 Back to main info", key=f"mt_back_to_main_{idx}"):
            current["step"] = "main"
            st.rerun()

        # CONFIRMED FIX (2026-08-19 audit): was inline `... or 2)` / `... or 12)`, which silently
        # rewrote a legitimate child-age minimum of 0 back to 2 - the exact trap ClosedTour already
        # fixed in render_child_age_band. Ticket had never adopted that shared helper; now it does,
        # so Ticket also gets the min>max/min==max sanity warnings ClosedTour already had.
        render_child_age_band(data, key_prefix=f"mt_{idx}",
                              min_key="child_age_min", max_key="child_age_max")

        st.markdown("**Start Time(s)**")
        tt_df = pd.DataFrame([{"Time (HH:MM)": t} for t in data.get("time_tables", [])]) if data.get("time_tables") else pd.DataFrame(columns=["Time (HH:MM)"])
        def _save_mt_timetables(edf, data=data):
            data["time_tables"] = _clean_time_table_rows(edf)
        editable_table("Start Time(s)", tt_df, f"mt_timetables_{idx}", on_save=_save_mt_timetables)
        if not data.get("time_tables"):
            st.caption("ℹ️ No start time set yet - optional, but add one if the excursion has a fixed departure time.")

        data["operational_days"] = st.multiselect(
            "Operational Days", ALL_WEEKDAYS, default=data.get("operational_days", ALL_WEEKDAYS), key=f"mt_op_days_{idx}"
        )

        # CONFIRMED (product owner, 2026-08-19): "display the Currency within the modalities...
        # in case the human selected a wrong currency, so he could still change it... an extra
        # check." Replaces the old read-only "Pricing (in {currency})" caption with an
        # editable one - still shows the currency right where the pricing is, just catchable
        # now instead of only informational.
        currency = render_currency_check(currency, CURRENCY_OPTIONS, "tk_cfg_currency", f"mt_currency_{idx}")
        st.markdown(f"**Pricing (in {currency})**")
        render_ticket_pricing_editor(data, f"mt_{idx}", currency, max_passengers)
        mt_price_type = data["price_type"]

        dcol1, dcol2 = st.columns(2)
        with dcol1:
            data["start_date"] = _iso(st.text_input("Valid From (DD/MM/YYYY)", value=_disp(data.get("start_date", "")), key=f"mt_start_date_{idx}"))
        with dcol2:
            data["end_date"] = _iso(st.text_input("Valid Until (DD/MM/YYYY)", value=_disp(data.get("end_date", "")), key=f"mt_end_date_{idx}"))
        if data.get("pricing_notes"):
            st.warning(f"⚠️ {data['pricing_notes']}")

        # CONFIRMED REAL BUG (product owner report): "Applied changes to: stop_sales" via
        # "Tell AI what to fix" reported success but the box never actually updated - the raw
        # JSON st.text_area below was keyed on a fixed key, so it kept showing/re-saving its
        # own stale cached text every rerun and silently overwrote whatever the clarify had
        # just written into `data`. Also, hand-typing a JSON array was never an "easy way to
        # add a stop sale manually" (second half of the same report) - replaced with the same
        # friendly Start/End Date table already used for ClosedTour (render_stop_sales_editor,
        # ui_components.py), which defaults to read-only display and only opens a live editor
        # on demand, so it can never go stale like the always-live text_area did.
        render_stop_sales_editor(data, f"mt_{idx}")

        # CORRECTED 2026-08-12 (product owner): a Ticket Modality DOES have its own dated
        # supplements (a seasonal price row, a holiday guide surcharge) - only the main Ticket
        # record has none. See render_ticket_modality_supplements_editor's docstring.
        render_ticket_modality_supplements_editor(data, f"mt_{idx}")

        render_ticket_language_options(data, f"mt_{idx}")

        # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-13): a new Ticket must only ever be created
        # with ONE Modality. Any other Modalities described in the document are surfaced here as
        # information only - never auto-extracted or auto-created - and must be added afterward
        # via "2: Add new Modality to existing Ticket" (Update/Refresh existing Service -> Ticket).
        if "extra_modalities" not in current:
            current["extra_modalities"] = []
        _mt_other_mods = current.get("other_modalities_detected") or []
        if _mt_other_mods:
            _mt_other_list = "".join(f"\n- {label}" for label in _mt_other_mods)
            st.info(
                f"ℹ️ This document also seems to describe other Modalit{'y' if len(_mt_other_mods) == 1 else 'ies'} "
                f"for {current['label'] or current['ticket_code']}:{_mt_other_list}\n\n"
                f"This Ticket will be created with just its one Modality above. Add the other one(s) "
                f"afterward via **Update existing Service -> Ticket -> \"2: Add new Modality to "
                f"existing Ticket\"**."
            )

        st.markdown(f"**🤖 Tell AI what to fix - {current['label'] or current['ticket_code']}**")
        mt_clarify_q = st.text_input("Your message", key=f"mt_clarify_input_{idx}")
        if render_house_rule_shortcut(mt_clarify_q, "Ticket", f"mt_{idx}"):
            pass
        elif not mt_clarify_q.strip():
            st.caption(f"Type a message above first — Send stays disabled until there's something to send. "
                      f"Start with \"{HOUSE_RULE_CODEWORD}\" to save a standing rule for every Ticket "
                      f"supplier instead of a one-off fix.")
        if not mt_clarify_q.strip().upper().startswith(HOUSE_RULE_CODEWORD.upper()) and st.button(
                "Send", disabled=not mt_clarify_q.strip(), key=f"mt_clarify_send_{idx}"):
            with st.spinner("Thinking..."):
                result = apply_clarification(st.session_state.mt_raw_text, data, mt_clarify_q)
                st.session_state[f"mt_clarify_result_{idx}"] = result
                remember_clarification(clarify_supplier_id(supplier_id), "Ticket", mt_clarify_q, result)
                if result.get("changes"):
                    apply_clarify_changes(data, result, currency)
                    # CONFIRMED REAL BUG (product owner report): "Applied changes to: stop_sales"
                    # showed success, but the Stop Sales box never actually changed. Cause: the
                    # Stop Sales editor used to be a raw st.text_area on a fixed key - a
                    # Streamlit widget with a fixed key ignores a freshly computed value= after
                    # its first render (same class of bug documented throughout this file, e.g.
                    # _clear_batch_widget_state's docstring). So even though apply_clarify_changes
                    # correctly wrote the new stop_sales into `data`, the STALE widget immediately
                    # overwrote it right back on the very next render - the fix was applied and
                    # instantly undone. Stop Sales is now render_stop_sales_editor (an
                    # editable_table, see the caption above), which is READ-ONLY by default and
                    # only goes stale if a human had it open in live-edit mode at the exact moment
                    # they clarified - resetting its edit-mode flag below covers even that case,
                    # same as every other editable_table field this per-item review renders.
                    mt_field_to_table_key = {
                        "includes": f"_editing_table_mt_includes_{idx}",
                        "excludes": f"_editing_table_mt_excludes_{idx}",
                        "meeting_points": f"_editing_table_mt_mp_{idx}",
                        "time_tables": f"_editing_table_mt_timetables_{idx}",
                        "stop_sales": f"_editing_table_mt_{idx}_stop_sales",
                        "modality_supplements": f"_editing_table_mt_{idx}_modality_supplements",
                        # CONFIRMED REAL GAP: this box can return an occupancy_prices change
                        # (nothing scopes apply_clarification's output to "fields rendered above
                        # this box") but the reset for it was missing here, unlike the pricing
                        # box below - a corrected occupancy row could go stale the same way
                        # Stop Sales once did.
                        "occupancy_prices": f"_editing_table_mt_{idx}_occupancy",
                    }
                    for field_name in result["changes"]:
                        table_key = mt_field_to_table_key.get(field_name)
                        if table_key:
                            st.session_state[table_key] = False
                    # Plain text/number fields (Ticket name, Description, Condition, Voucher
                    # Remarks, City, Duration) were never covered by the table-key reset above -
                    # see reset_stale_editable_field_widgets' docstring for why they can go
                    # stale the same way.
                    reset_stale_editable_field_widgets(result["changes"], key_suffix=f"_{idx}")
                    if "operational_days" in result["changes"]:
                        st.session_state.pop(f"mt_op_days_{idx}", None)
                st.rerun()
        if st.session_state.get(f"mt_clarify_result_{idx}"):
            r = st.session_state[f"mt_clarify_result_{idx}"]
            render_clarify_result(r)
        remember_memory_panel(clarify_supplier_id(supplier_id), "Ticket", "mt")

        if mt_price_type == "SERVICE":
            price_valid = bool(data.get("base_service_price", 0))
        elif mt_price_type == "OCCUPANCY":
            # CONFIRMED RULE (product owner, 2026-08-24): EVERY offered occupancy needs a real
            # price, not just one of them. `any(...)` passed a table where rows 1-4 were priced and
            # 5-9 were left at the editor's default of 0, publishing those as free. See
            # render_publish_blockers.
            _occ_rows = data.get("occupancy_prices") or []
            _zero_occ = [o.get("occupancy") for o in _occ_rows if not _safe_float(o.get("amount"), fallback=0.0)]
            price_valid = bool(_occ_rows) and not _zero_occ
            if _zero_occ:
                st.error(f"🚫 No price for occupancy: **{', '.join(str(o) for o in _zero_occ)}** - "
                         f"these would be sellable for free. Enter a price for each, or remove the row.")
        else:
            price_valid = any([data.get("base_adult_price", 0), data.get("base_children_price", 0), data.get("base_infant_price", 0)])
        name_and_description_valid = bool((data.get("ticket_name") or "").strip()) and bool((data.get("description") or "").strip())
        # Geolocation was already required and confirmed back in Step A (main info) before this
        # step could even be reached - no need to recheck mt_geo here (it isn't in scope).
        can_continue = price_valid and name_and_description_valid

        is_last = idx == len(queue) - 1
        btn_label = "✅ Confirm this Ticket & Finish Review" if is_last else "✅ Confirm this Ticket & Continue →"
        if st.button(btn_label, type="primary", disabled=not can_continue):
            current["confirmed"] = True
            if is_last:
                st.session_state.mt_phase = "publishing"
            else:
                st.session_state.mt_queue_index += 1
            st.rerun()
        if not price_valid:
            st.info("Add at least one non-zero price before continuing.")
        return

    # ------------------------------------------------------------------
    # PHASE 4: publish all confirmed Tickets, ONE BY ONE
    # ------------------------------------------------------------------
    if st.session_state.mt_phase == "publishing":
        queue = st.session_state.mt_queue
        if "mt_failed_items" not in st.session_state:
            st.session_state.mt_failed_items = []
        st.subheader(f"Ready to publish {len(queue)} Tickets - one by one")
        for q in queue:
            extra_count = len(q.get("extra_modalities", []))
            extra_note = f" + {extra_count} additional modalit{'y' if extra_count == 1 else 'ies'}" if extra_count else ""
            st.write(f"- **{q['ticket_code']}** ({q['label']}) - Modality: {q['modality_code']}{extra_note}")

        mt_activation_choice = st.radio(
            "After publishing, should these Tickets be Active or Inactive (draft)?",
            ["Inactive (draft) - recommended, review inside Travel Compositor before they go live",
             "Active - live immediately"],
            index=0, key="mt_activation_choice"
        )
        mt_publish_as_active = mt_activation_choice.startswith("Active")

        if st.button("🚀 Publish all (one by one)", type="primary"):
            for q in queue:
                with st.spinner(f"Publishing '{q['ticket_code']}'..."):
                    try:
                        pre_config = TicketHumanPreConfig(
                            supplier_id=supplier_id, ticket_code=q["ticket_code"], currency=currency,
                            modality_code=q["modality_code"], modality_name=q.get("modality_name"), on_request=on_request,
                            days_available_before_release=release_days, min_passengers=min_passengers, max_passengers=max_passengers
                        )
                        payloads = build_ticket_payloads(pre_config, q["data"], client)
                        if payloads["main_ticket_error"] or payloads["ticket_option_error"]:
                            show_publish_error(f"prepare **{q['ticket_code']}**'s payload",
                                              payloads['main_ticket_error'] or payloads['ticket_option_error'])
                            continue
                        if not payloads["geolocation_resolved"]:
                            st.error(f"❌ **{q['ticket_code']}**: geolocation not resolved - skipped. Fix the City "
                                    f"field and create this one individually via the normal Create flow instead.")
                            continue
                        # CONFIRMED REAL GAP (functionality audit, 2026-08-24): render_publish_blockers
                        # (expired validity window / zero-priced occupancies - see its own docstring)
                        # was wired into the single-ticket "Publish to Travel Compositor" flow but never
                        # into THIS batch "Publish all" loop, which is what mass ticket production
                        # actually uses - an expired rate sheet or a zero-priced occupancy row could
                        # reach a real POST here with no gate at all. Same check, same place it
                        # actually matters: right before the real API calls.
                        if not render_publish_blockers(payloads):
                            st.error(f"🚫 **{q['ticket_code']}**: skipped - see the error(s) above.")
                            continue

                        creation_payload = dict(payloads["main_ticket_payload"])
                        creation_payload["active"] = True
                        result = client.create_ticket(supplier_id, creation_payload)
                        if "error" in result:
                            show_publish_error(f"create **{q['ticket_code']}**", result)
                            continue
                        real_code = result.get("code", payloads["main_ticket_code"])
                        # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): see
                        # mark_code_as_taken's docstring.
                        mark_code_as_taken("ticket", supplier_id, q["ticket_code"], result.get("name"))
                        if real_code and real_code != q["ticket_code"]:
                            mark_code_as_taken("ticket", supplier_id, real_code, result.get("name"))

                        # api_client.py's _request() already retries every write call (incl. this
                        # POST) up to 6 times internally now - no need to also loop here.
                        option_result = client.create_ticket_option(supplier_id, real_code, payloads["ticket_option_payload"])
                        if "error" in option_result:
                            show_publish_error(f"create **{q['ticket_code']}**'s option (created as `{real_code}`)", option_result)
                            # The ticket itself WAS created (real_code) and is still ACTIVE - only the
                            # option failed. Don't force the human to abandon the whole batch and start
                            # over: remember this item (with its real_code, and the SAME editable data
                            # dict) so they can adjust it and retry just this option below, without
                            # re-running the other tickets or losing their edits.
                            st.session_state.mt_failed_items.append({
                                "ticket_code": q["ticket_code"], "label": q["label"], "real_code": real_code,
                                "modality_code": q["modality_code"], "data": q["data"],
                            })
                            continue
                        else:
                            st.success(f"✅ **{q['ticket_code']}**: base modality '{q['modality_code']}' created.")

                        for mod in q.get("extra_modalities", []):
                            if not mod.get("code") or not mod.get("data"):
                                st.warning(f"⚠️ **{q['ticket_code']}**: skipped an extra modality - missing code or pricing data.")
                                continue
                            with st.spinner(f"Creating '{q['ticket_code']}' modality '{mod['code']}'..."):
                                try:
                                    mod_pre_config = TicketHumanPreConfig(
                                        supplier_id=supplier_id, ticket_code=q["ticket_code"], currency=currency,
                                        modality_code=mod["code"], on_request=on_request,
                                        days_available_before_release=release_days, min_passengers=min_passengers, max_passengers=max_passengers
                                    )
                                    mod_payloads = build_ticket_payloads(mod_pre_config, mod["data"], client)
                                    if mod_payloads["ticket_option_error"]:
                                        show_publish_error(f"prepare **{q['ticket_code']}** modality '{mod['code']}'", mod_payloads["ticket_option_error"])
                                        continue
                                    # Same expired-window / zero-priced-occupancy gate as the base
                                    # modality above - see render_publish_blockers.
                                    if not render_publish_blockers(mod_payloads):
                                        st.error(f"🚫 **{q['ticket_code']}** modality '{mod['code']}': skipped - see the error(s) above.")
                                        continue
                                    mod_option_result = client.create_ticket_option(supplier_id, real_code, mod_payloads["ticket_option_payload"])
                                    if "error" in mod_option_result:
                                        show_publish_error(f"create **{q['ticket_code']}** modality '{mod['code']}'", mod_option_result)
                                    else:
                                        st.success(f"✅ **{q['ticket_code']}**: modality '{mod['code']}' created.")
                                except Exception as e:
                                    show_publish_error(f"create **{q['ticket_code']}** modality '{mod['code']}' (unexpected error - skipped, rest continues)", str(e))
                                    continue

                        if mt_publish_as_active:
                            st.success(f"✅ **{q['ticket_code']}** published and left ACTIVE as `{real_code}` (as chosen above).")
                        else:
                            deactivate_payload = dict(creation_payload)
                            deactivate_payload["active"] = False
                            deactivate_payload["code"] = real_code
                            deactivate_result = client.update_ticket(supplier_id, deactivate_payload)
                            if "error" in deactivate_result:
                                st.warning(f"⚠️ **{q['ticket_code']}**: created and published, but switching back to "
                                          f"inactive failed - {deactivate_result}")
                            else:
                                st.success(f"✅ **{q['ticket_code']}** published successfully as `{real_code}` (inactive/draft).")
                    except Exception as e:
                        show_publish_error(f"publish **{q['ticket_code']}** (unexpected error - skipped, rest of batch continues)", str(e))
                        continue

        if st.session_state.mt_failed_items:
            st.divider()
            st.subheader(f"⚠️ {len(st.session_state.mt_failed_items)} ticket(s) created but their Modality failed")
            st.caption("These tickets themselves were created successfully (and are still ACTIVE) - only "
                      "the Modality failed, so retrying 'Publish all' would try to create duplicate "
                      "tickets. Adjust whatever needs fixing below (e.g. a start time), then retry just the "
                      "Modality for that one ticket - no need to redo the whole batch.")
            for fi_idx, fi in enumerate(list(st.session_state.mt_failed_items)):
                with st.expander(f"🔧 {fi['ticket_code']} (created as `{fi['real_code']}`) — {fi['label']}", expanded=True):
                    fdata = fi["data"]

                    # CONFIRMED REAL BUG (product owner report, real API rejection):
                    # "Number of passengers in occupancy is greater than max passengers allowed
                    # in the contract" - this box used to ALWAYS show Adult/Child/Infant price
                    # fields regardless of what price_type the ticket actually used, so a
                    # ticket priced by Occupancy had no way to even SEE its occupancy rows here,
                    # let alone fix the one that exceeded Max Passengers - the human's only
                    # option was starting the whole batch over. Mirror the same price-type-aware
                    # pricing block (and the same Max Passengers cap) used in the main per-item
                    # review above, so whatever actually caused the rejection is editable here.
                    # Extra check (product owner, 2026-08-19): currency shown here too, editable,
                    # for the same "catch a wrong pick before publishing" reason as the main
                    # per-item pricing block above.
                    currency = render_currency_check(currency, CURRENCY_OPTIONS, "tk_cfg_currency", f"mtf_currency_{fi_idx}")
                    render_ticket_pricing_editor(fdata, f"mtf_{fi_idx}", currency, max_passengers)

                    ftt_df = pd.DataFrame([{"Time (HH:MM)": t} for t in fdata.get("time_tables", [])]) if fdata.get("time_tables") else pd.DataFrame(columns=["Time (HH:MM)"])
                    def _save_mtf_tt(edf, fdata=fdata):
                        fdata["time_tables"] = _clean_time_table_rows(edf)
                    editable_table("Start Time(s)", ftt_df, f"mtf_tt_{fi_idx}", on_save=_save_mtf_tt)
                    fdcol1, fdcol2 = st.columns(2)
                    with fdcol1:
                        fdata["start_date"] = _iso(st.text_input("Valid From (DD/MM/YYYY)", value=_disp(fdata.get("start_date", "")), key=f"mtf_start_{fi_idx}"))
                    with fdcol2:
                        fdata["end_date"] = _iso(st.text_input("Valid Until (DD/MM/YYYY)", value=_disp(fdata.get("end_date", "")), key=f"mtf_end_{fi_idx}"))

                    if st.button(f"🔄 Retry Modality for `{fi['real_code']}`", key=f"mtf_retry_{fi_idx}", type="primary"):
                        with st.spinner(f"Retrying '{fi['ticket_code']}'..."):
                            try:
                                retry_pre_config = TicketHumanPreConfig(
                                    supplier_id=supplier_id, ticket_code=fi["real_code"], currency=currency,
                                    modality_code=fi["modality_code"], on_request=on_request,
                                    days_available_before_release=release_days, min_passengers=min_passengers, max_passengers=max_passengers
                                )
                                retry_payloads = build_ticket_payloads(retry_pre_config, fdata, client)
                                if retry_payloads["ticket_option_error"]:
                                    show_publish_error(f"prepare **{fi['ticket_code']}**'s payload", retry_payloads["ticket_option_error"])
                                elif not retry_payloads["geolocation_resolved"]:
                                    st.error("❌ Geolocation not resolved - fix the City field via the normal Create flow instead.")
                                elif not render_publish_blockers(retry_payloads):
                                    pass  # render_publish_blockers already showed the specific error(s)
                                else:
                                    retry_option_result = client.create_ticket_option(supplier_id, fi["real_code"], retry_payloads["ticket_option_payload"])
                                    if "error" in retry_option_result:
                                        show_publish_error(f"retry **{fi['ticket_code']}**'s option", retry_option_result)
                                    else:
                                        st.success(f"✅ **{fi['ticket_code']}**: option created on retry.")
                                        # Match the activation choice made above for this batch.
                                        if not mt_publish_as_active:
                                            retry_deactivate_payload = dict(retry_payloads["main_ticket_payload"])
                                            retry_deactivate_payload["active"] = False
                                            retry_deactivate_payload["code"] = fi["real_code"]
                                            retry_deactivate_result = client.update_ticket(supplier_id, retry_deactivate_payload)
                                            if "error" in retry_deactivate_result:
                                                st.warning(f"⚠️ Option created, but switching back to inactive/draft "
                                                          f"failed: {retry_deactivate_result}.")
                                        st.session_state.mt_failed_items = [
                                            x for x in st.session_state.mt_failed_items if x is not fi
                                        ]
                                        st.rerun()
                            except Exception as e:
                                show_publish_error(f"retry **{fi['ticket_code']}**'s option (unexpected error)", str(e))

        st.write("")
        st.divider()
        if st.button("🆕 Start a new batch"):
            for key in ["mt_phase", "mt_raw_text", "mt_candidates", "mt_queue", "mt_queue_index",
                       "mt_doc_raw_images", "mt_hosted_image_candidates", "mt_failed_items"]:
                st.session_state.pop(key, None)
            _clear_batch_widget_state(SHARED_WIDGET_STATE_PREFIXES)
            st.rerun()
        return


def render_ticket_flow(client):
    """
    Full Ticket wizard (Steps 1-6), mirroring the ClosedTour flow's proven
    patterns but adapted for Tickets' real structural differences: one
    geolocation instead of an itinerary, passenger-type pricing (adult/
    child/infant) instead of room-occupancy, ONE price+date range per
    Modality instead of a seasonal array, structured meeting points.
    Uses tk_-prefixed session_state keys throughout to avoid any collision
    with the ClosedTour flow's state.
    """
    if "tk_step1_confirmed" not in st.session_state:
        st.session_state.tk_step1_confirmed = False
    if "tk_step2_confirmed" not in st.session_state:
        st.session_state.tk_step2_confirmed = False

    # ------------------------------------------------------------------
    # TICKET STEP 2: Action + Supplier
    # ------------------------------------------------------------------
    st.header("Ticket — Step 2: What do you want to do?")

    if st.session_state.tk_step1_confirmed:
        st.success(f"✅ Action: **{TICKET_ACTION_LABELS[st.session_state.tk_cfg_action]}** | "
                   f"Supplier ID: **{st.session_state.tk_cfg_supplier_id}**")
        if st.button("🔄 Change action / supplier", key="tk_change_action"):
            st.session_state.tk_step1_confirmed = False
            st.session_state.tk_step2_confirmed = False
            st.rerun()
    else:
        action_key = st.radio(
            "Choose one:", list(TICKET_ACTION_LABELS.keys()),
            format_func=lambda k: TICKET_ACTION_LABELS[k], key="tk_action_radio"
        )
        if st.session_state.suppliers_cache is None:
            with st.spinner("Loading supplier list from Travel Compositor..."):
                try:
                    st.session_state.suppliers_cache = client.get_all_suppliers()
                except Exception as e:
                    # This is the very first real network call in the flow -
                    # a transient connection issue here used to crash the
                    # whole app before the human could even pick a supplier.
                    st.error(f"❌ Couldn't load the supplier list: {friendly_error_message(e)}")
                    st.session_state.suppliers_cache = []

        supplier_id_choice = None
        if st.session_state.suppliers_cache:
            # LOCKED: only "Momira_"-prefixed suppliers may be picked - forces
            # the human to explicitly choose a real Momira supplier instead of
            # any other supplier that happens to exist in the account.
            momira_suppliers = [
                s for s in st.session_state.suppliers_cache
                if (s.get("commercialName") or s.get("legalName") or "").strip().lower().startswith("momira_")
            ]
            if not momira_suppliers:
                st.error("🚫 No suppliers starting with 'Momira_' were found in this account - can't continue. "
                        "Check the supplier exists in Travel Compositor with the correct naming, or refresh below.")
            else:
                supplier_options = {
                    f"{s.get('commercialName') or s.get('legalName')} — ID {s.get('id')}": s.get("id")
                    for s in momira_suppliers
                }
                selected_label = st.selectbox("Select Supplier", list(supplier_options.keys()), key="tk_supplier_select")
                supplier_id_choice = str(supplier_options[selected_label])
            if st.button("🔄 Refresh supplier list", key="tk_refresh_suppliers"):
                st.session_state.suppliers_cache = None
                st.rerun()
        else:
            st.error("Could not load the supplier list from Travel Compositor.")
            with st.expander("⚠️ Emergency manual entry"):
                st.caption("Bypasses the Momira_ check above - only use this if you've already confirmed the "
                          "numeric ID belongs to a real Momira_ supplier.")
                supplier_id_choice = st.text_input("Supplier ID (numeric)", value="", key="tk_supplier_manual")

        if st.button("➡️ Continue to Step 3", type="primary", disabled=not supplier_id_choice, key="tk_continue1"):
            st.session_state.tk_cfg_action = action_key
            st.session_state.tk_cfg_supplier_id = supplier_id_choice
            st.session_state.tk_step1_confirmed = True
            st.rerun()
        return

    cancellation_links.render_cancellation_link_editor(st.session_state.tk_cfg_supplier_id, "Ticket", key_suffix="_setup")

    # ------------------------------------------------------------------
    # TICKET STEP 3: Action-specific details
    # ------------------------------------------------------------------
    st.header("Ticket — Step 3: Details for this action")
    action = st.session_state.tk_cfg_action
    needed = TICKET_ACTION_FIELDS[action]
    supplier_id = st.session_state.tk_cfg_supplier_id

    if st.session_state.tk_step2_confirmed:
        st.success("✅ Step 3 details confirmed.")
        if st.button("🔄 Change details", key="tk_change_details"):
            st.session_state.tk_step2_confirmed = False
            st.rerun()
    else:
        ticket_code_in = min_pass_in = max_pass_in = currency_in = modality_code_in = existing_ticket_code_in = None
        on_request_in = False
        release_days_in = 30

        if "existing_ticket_code" in needed:
            tk_prefill = st.session_state.pop("tk_prefill_existing_ticket_code", "")
            existing_ticket_code_in = st.text_input(
                "Existing Ticket Code", value=tk_prefill, placeholder="e.g. JAP-T1", key="tk_existing_code"
            ).strip()

            if st.button("🔍 Check what's already online for this code", disabled=not existing_ticket_code_in, key="tk_check_online"):
                with st.spinner("Fetching from Travel Compositor..."):
                    fetched = client.get_ticket(supplier_id, existing_ticket_code_in)
                    st.session_state.tk_fetched_ticket = fetched
                    st.session_state.tk_fetched_option = None
                    if isinstance(fetched, dict) and "error" not in fetched:
                        st.session_state.tk_fetched_currency = fetched.get("currency")
                        # Same fix as ClosedTours: pre-fill Step 5 from this ticket's OWN live
                        # data immediately, instead of leaving it blank until a fresh document
                        # is extracted - see _map_fetched_ticket_to_data()'s docstring.
                        if action == "update_ticket":
                            st.session_state.tk_extracted = _map_fetched_ticket_to_data(fetched)
                            # Prefilling from the LIVE record replaces the review data just as an
                            # extraction does - so it needs the same fresh widget generation, or
                            # every widget still shows the previously-reviewed ticket's values and
                            # writes them onto this live ticket. (This path was the hole left by
                            # the first child-age fix, which only covered the extraction paths.)
                            bump_widget_generation("tk")
                            st.session_state.tk_raw_preview = (
                                f"(No new document/URL provided - these fields were pre-filled from "
                                f"the ticket's CURRENT live data on Travel Compositor, code "
                                f"`{existing_ticket_code_in}`. Edit below, or provide a new source and "
                                f"click Extract to bring in updates - your existing values won't be "
                                f"blanked out by an incomplete new extraction.)"
                            )
                            st.session_state.tk_payloads = None
                            _tk_clear_geo_confirmation()
                            st.session_state.tk_doc_raw_images = []
                            st.session_state.tk_hosted_image_candidates = []

            if st.session_state.get("tk_fetched_ticket"):
                t = st.session_state.tk_fetched_ticket
                if "error" in t:
                    st.error(f"Not found or error: {t.get('message', t)}")
                else:
                    st.success(f"Found: **{t.get('name', '(no name)')}**")
                    st.caption(f"Will reuse Currency **{t.get('currency')}** from this ticket.")
                    existing_modalities = t.get("modalityCodes", [])
                    st.write(f"Existing modality codes: {existing_modalities if existing_modalities else '(none)'}")

        tk_update_scope_in = "whole_ticket"
        if action == "update_ticket":
            # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-28): "Update price only or check the
            # whole ticket" - asked up front so a human who only needs a price fix doesn't
            # pay for (or wait through) the full name/description/cancellation extraction,
            # and so "whole ticket" - which now actually publishes the pricing it extracts,
            # see TICKET_ACTION_FIELDS's comment - knows to expect a Modality Code below.
            st.markdown("##### What do you want to update?")
            _tk_scope_choice = st.radio(
                "Update scope", label_visibility="collapsed",
                options=["Price only (fast, cheaper - skips re-checking name/description/etc.)",
                        "Whole ticket (also re-checks name, description, cancellation policy, etc.)"],
                key="tk_update_scope_radio",
            )
            tk_update_scope_in = "price_only" if _tk_scope_choice.startswith("Price only") else "whole_ticket"

        if "ticket_code" in needed:
            ticket_code_in = st.text_input("Ticket Code", value="", placeholder="e.g. JAP-T1", key="tk_ticket_code")
            render_code_availability_check(client, "ticket", supplier_id, ticket_code_in, "ticket")
        if "min_passengers" in needed:
            min_pass_in = st.selectbox("Min Passengers", [1, 2], key="tk_min_pass")
        if "max_passengers" in needed:
            max_pass_in = st.selectbox("Max Passengers", list(range(2, 21)), index=7, key="tk_max_pass")
        if "currency" in needed:
            # CONFIRMED PRODUCT-OWNER RULE (2026-09-01, full-app audit HIGH #1 fix - same rule
            # and same "Change details" re-entry hole as ClosedTour's twin lock above): "Once
            # a currency has been set, it can never be changed and all Modalities are using
            # the same Currency."
            _tk_currency_already_set = bool(st.session_state.get("tk_cfg_currency"))
            if _tk_currency_already_set:
                currency_in = st.session_state.tk_cfg_currency
                st.selectbox(
                    "Currency", CURRENCY_OPTIONS,
                    index=CURRENCY_OPTIONS.index(currency_in) if currency_in in CURRENCY_OPTIONS else 0,
                    disabled=True, key="tk_currency",
                    help="Locked - a currency, once set, cannot be changed. It applies to "
                         "every Modality of this ticket.",
                )
            else:
                currency_in = st.selectbox("Currency", CURRENCY_OPTIONS, key="tk_currency")
        if "modality_code" in needed:
            # "update_ticket" needs the SAME "which existing Modality" semantics as
            # "update_option" now (see TICKET_ACTION_FIELDS's comment) - both are asking
            # for an ALREADY-LIVE modality's code, not a brand-new one.
            default_modality = st.session_state.get("tk_check_modality_pick", "") if action in ("update_option", "update_ticket") else ""
            label = "Modality Code to update" if action in ("update_option", "update_ticket") else "Unique Modality Code"
            modality_code_in = st.text_input(label, value=default_modality or "", placeholder="e.g. Standard 7 Days", key="tk_modality_code")
            # CONFIRMED REAL REQUEST (product owner, 2026-08-24): the supplier's own code is often
            # reused as this Modality Code, and the same product can get re-imported under a
            # DIFFERENT (arbitrary, human-chosen) Ticket Code - the Ticket-Code check above can't
            # catch that. See check_modality_code_availability's docstring. For "update_option"/
            # "add_option"/"update_ticket", the ticket being worked on right now is excluded from
            # the comparison (via ignore_ticket_code) - its own existing modality is an expected
            # match there, not a duplicate; a match on any OTHER ticket still warns.
            if action in ("update_option", "add_option", "update_ticket"):
                render_modality_code_availability_check(client, supplier_id, modality_code_in, existing_ticket_code_in)
            else:
                render_modality_code_availability_check(client, supplier_id, modality_code_in)
        if "on_request" in needed:
            on_request_in = st.checkbox("On Request", value=False, key="tk_on_request")
        if "release_days" in needed:
            release_days_in = st.number_input(
                "Release Day (days before departure this ticket becomes bookable)",
                min_value=0, value=30, key="tk_release_days"
            )

        required_ok = True
        if "ticket_code" in needed and not (ticket_code_in or "").strip():
            required_ok = False
        if "currency" in needed and not (currency_in or "").strip():
            required_ok = False
        if "modality_code" in needed and not (modality_code_in or "").strip():
            required_ok = False
        if "existing_ticket_code" in needed and not existing_ticket_code_in:
            required_ok = False
        # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): "update_option" was missing from
        # this tuple - TICKET_ACTION_FIELDS deliberately excludes "currency" from
        # update_option's own needed fields ("an UPDATE never asks for things the live record
        # already has"), relying entirely on it being inherited from the fetched ticket
        # (tk_fetched_currency, used further down once Steps 4+ render). But this gate - the
        # only thing standing between Step 3 and Step 4 - never required that fetch to have
        # happened for update_option, so an operator could click Continue having never checked
        # what's online, and currency would fall through to whatever tk_cfg_currency last held
        # (blank on a fresh session) - the SAME empty-currency-defaults-to-EUR fallback this
        # already blocks for add_option/update_ticket, just left open on the fourth action.
        if action in ("add_option", "update_ticket", "update_option") and not st.session_state.get("tk_fetched_currency"):
            required_ok = False
            st.info("Click 'Check what's already online for this code' above first - this fetches the "
                   "existing Currency so you don't have to re-enter it.")

        if st.button("➡️ Continue to Step 4", type="primary", disabled=not required_ok, key="tk_continue2"):
            if action in ("add_option", "update_ticket"):
                currency_in = st.session_state.get("tk_fetched_currency") or ""
            st.session_state.tk_cfg_ticket_code = ticket_code_in or ""
            st.session_state.tk_cfg_min_passengers = min_pass_in or 1
            st.session_state.tk_cfg_max_passengers = max_pass_in or 9
            st.session_state.tk_cfg_currency = currency_in or ""
            st.session_state.tk_cfg_modality_code = modality_code_in or ""
            st.session_state.tk_cfg_on_request = on_request_in
            st.session_state.tk_cfg_release_days = release_days_in
            st.session_state.tk_cfg_existing_ticket_code = existing_ticket_code_in or ""
            st.session_state.tk_cfg_update_scope = tk_update_scope_in
            st.session_state.tk_step2_confirmed = True
            st.rerun()
        return

    # From here: everything reads from confirmed tk_cfg_* values.
    supplier_id = st.session_state.tk_cfg_supplier_id
    ticket_code = st.session_state.tk_cfg_ticket_code
    min_passengers = st.session_state.tk_cfg_min_passengers
    max_passengers = st.session_state.tk_cfg_max_passengers
    currency = st.session_state.tk_cfg_currency
    modality_code = st.session_state.tk_cfg_modality_code
    on_request = st.session_state.tk_cfg_on_request
    release_days = st.session_state.tk_cfg_release_days
    existing_ticket_code = st.session_state.tk_cfg_existing_ticket_code
    # Only meaningful for action == "update_ticket" - see TICKET_ACTION_FIELDS's comment and
    # the "What do you want to update?" radio in Step 3. Defaults to "whole_ticket" for
    # every other action so nothing below has to special-case "key not set yet".
    tk_update_scope = st.session_state.get("tk_cfg_update_scope", "whole_ticket")

    # Same product-owner rule as ClosedTour: on an update the live ticket's own code,
    # currency and passenger limits win over anything Step 2 holds. TICKET_ACTION_FIELDS
    # already stops asking for them; this is what makes the published payload agree.
    if action in ("update_ticket", "update_option", "add_option"):
        _tk_live = st.session_state.get("tk_fetched_ticket") or {}
        if isinstance(_tk_live, dict) and "error" not in _tk_live:
            currency = _tk_live.get("currency") or st.session_state.get("tk_fetched_currency") or currency
            if _tk_live.get("minPassengers") not in (None, ""):
                min_passengers = _tk_live["minPassengers"]
            if _tk_live.get("maxPassengers") not in (None, ""):
                max_passengers = _tk_live["maxPassengers"]
            ticket_code = _tk_live.get("code") or existing_ticket_code or ticket_code

    _tk_action_to_publish_label = {
        "create": "Create a brand-new ticket (+ first option)",
        "add_option": "Add a new option to an existing ticket",
        "update_ticket": "Update an existing ticket's details",
        "update_option": "Update an existing ticket option",
    }
    publish_action = _tk_action_to_publish_label[action]
    # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-28): "Price only" under action "update_ticket"
    # is structurally IDENTICAL to action "update_option" from here on - same cheap
    # extraction, same review (pricing/schedule only, no name/description/cancellation),
    # same publish call. Relabeling publish_action here, rather than adding new branches
    # further down, is what makes that reuse automatic instead of duplicated.
    tk_price_only_via_update_ticket = action == "update_ticket" and tk_update_scope == "price_only"
    if tk_price_only_via_update_ticket:
        publish_action = "Update an existing ticket option"
    tk_is_option_only = action in ("add_option", "update_option") or tk_price_only_via_update_ticket

    # ------------------------------------------------------------------
    # TICKET STEP 4: Input Source
    # ------------------------------------------------------------------
    st.header("Ticket — Step 4: Input Source")
    tk_url = st.text_input("Product page URL (optional)", key="tk_url")
    tk_files = st.file_uploader("Upload document(s) (optional)", type=["pdf", "docx", "xlsx"],
                                accept_multiple_files=True, key="tk_files")
    tk_hint = st.text_input("Extraction hint (optional)", key="tk_hint")

    # "Create" always routes through the batch-capable flow now, regardless
    # of how many excursions the source actually turns out to describe - it
    # transparently handles a single excursion exactly like the old
    # single-Ticket flow did (just one row to fill in), and auto-detects/
    # handles multiple excursions without the human needing to pre-declare
    # "this has several" via a checkbox first. This removes the old upfront
    # single-vs-multiple choice per the confirmed design (always
    # auto-detect, one unified queue-based UI regardless of count).
    if action == "create":
        render_multi_ticket_flow(client, supplier_id, currency, on_request, release_days, tk_url, tk_files,
                                min_passengers=min_passengers, max_passengers=max_passengers,
                                default_ticket_code=ticket_code)
        return

    if st.button("🔎 Extract", disabled=not (tk_url or tk_files), key="tk_extract_btn"):
        with st.spinner("Gathering content..."):
            try:
                combined_parts = []
                doc_raw_images = []
                doc_image_urls = []
                seen_image_hashes = set()
                if tk_url:
                    page_text, page_text_err = _fetch_url_text_safe(tk_url)
                    if page_text is not None:
                        combined_parts.append(f"--- SOURCE: WEB PAGE ({tk_url}) ---\n{page_text}")
                    else:
                        st.warning(f"⚠️ Couldn't fetch the product page URL: {page_text_err}.")
                for uploaded in (tk_files or []):
                    suffix = os.path.splitext(uploaded.name)[1]
                    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                        tmp.write(uploaded.getbuffer())
                        tmp_path = tmp.name
                    _doc_text = extract_raw_text(tmp_path)
                    _scan_warning = document_reader_scanned_warning(tmp_path, _doc_text)
                    if _scan_warning:
                        st.session_state.setdefault("_scanned_doc_warnings", []).append(_scan_warning)
                    combined_parts.append(f"--- SOURCE: UPLOADED DOCUMENT ({uploaded.name}) ---\n{_doc_text}")
                    remaining_budget = 12 - len(doc_raw_images)
                    _doc_image_errors = []
                    embedded_images = extract_images(tmp_path, max_images=remaining_budget, seen_hashes=seen_image_hashes, errors=_doc_image_errors, label=uploaded.name) if remaining_budget > 0 else []
                    if embedded_images:
                        for i, (img_bytes, ext) in enumerate(embedded_images):
                            doc_raw_images.append((f"{os.path.splitext(uploaded.name)[0]}_img{i+1}.{ext or 'jpg'}", img_bytes))
                        try:
                            new_urls, _upload_errors = upload_images_r2_with_errors(embedded_images)
                            doc_image_urls.extend(new_urls)
                            _doc_image_errors.extend(_upload_errors)
                        except Exception as e:
                            _doc_image_errors.append(f"'{uploaded.name}': R2 upload failed entirely - {e}")
                    _warn_page_image_upload_errors(_doc_image_errors)
                    os.remove(tmp_path)

                if not combined_parts:
                    st.error("Nothing to extract - the product page URL couldn't be fetched and no document(s) were provided.")
                    st.stop()

                if len(doc_image_urls) >= len(doc_raw_images):
                    doc_raw_images = []

                raw_text = "\n\n".join(combined_parts)

                if tk_is_option_only:
                    data = extract_ticket_option_only_data(raw_text, human_hint=tk_hint or None)
                    floor_start_date_for_new_data(data)
                    st.session_state.tk_extracted = data
                    bump_widget_generation("tk")
                    st.session_state.tk_raw_preview = raw_text
                    st.session_state.tk_payloads = None
                    _tk_clear_geo_confirmation()
                    st.session_state.tk_doc_raw_images = doc_raw_images
                    st.success("Extraction complete. Review and edit below.")
                else:
                    excursions = detect_ticket_variants(raw_text)
                    if excursions:
                        st.session_state.tk_pending_variants = excursions
                        st.session_state.tk_pending_raw_text = raw_text
                        st.session_state.tk_pending_hint = tk_hint or None
                        st.session_state.tk_pending_url = tk_url or None
                        st.session_state.tk_pending_doc_images = doc_image_urls
                        st.session_state.tk_pending_doc_raw_images = doc_raw_images
                    else:
                        data = extract_ticket_data(
                            raw_text,
                            # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): see the
                            # matching fix below - pass the local supplier_id explicitly rather
                            # than relying on the ambiguous ClosedTour-first fallback order.
                            human_hint=with_learned_guidance(clarify_supplier_id(supplier_id), "Ticket", tk_hint))
                        # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): the placeholder
                        # used to be set to [FALLBACK_IMAGE] BEFORE the merge below - a non-empty
                        # value, so _merge_extraction_over_baseline (which only keeps the
                        # baseline's value for fields the fresh side left EMPTY) always preferred
                        # the placeholder over the update's real, already-live photos. Set to []
                        # (genuinely empty, so the merge falls back to the baseline's real
                        # images) and only fall back to the placeholder afterward, when there's
                        # still nothing - a create with no baseline, or an update whose baseline
                        # itself had no images either.
                        data["image_urls"] = []
                        if action == "update_ticket":
                            data = _merge_extraction_over_baseline(st.session_state.get("tk_extracted") or {}, data)
                        if not data.get("image_urls"):
                            data["image_urls"] = [FALLBACK_IMAGE]
                        floor_start_date_for_new_data(data)
                        # Only fills in when this document (and, for an update, the live
                        # baseline it was just merged over) had no cancellation terms of its
                        # own - see apply_cancellation_link_default's docstring.
                        st.session_state.tk_cancellation_link_scope = cancellation_links.apply_cancellation_link_default(
                            data, supplier_id, "Ticket")
                        st.session_state.tk_extracted = data
                        # Supersedes the earlier reset_child_age_band_widgets("tk") call: a fresh
                        # generation re-keys EVERY tk widget built through widget_generation(),
                        # not just the two child-age boxes.
                        bump_widget_generation("tk")
                        st.session_state.tk_raw_preview = raw_text
                        st.session_state.tk_payloads = None
                        _tk_clear_geo_confirmation()
                        _warn_page_image_upload_errors(_add_page_images_to_doc_pool(tk_url, doc_raw_images, doc_image_urls))
                        st.session_state.tk_doc_raw_images = doc_raw_images
                        st.session_state.tk_hosted_image_candidates = list(dict.fromkeys(doc_image_urls))
                        st.success("Extraction complete. Review and edit below.")
            except Exception as e:
                st.error(f"Extraction failed: {friendly_error_message(e)}")

    if st.session_state.get("tk_pending_variants"):
        excursions = st.session_state.tk_pending_variants
        st.warning(f"⚠️ This content describes {len(excursions)} distinct excursions — which one(s) do you want to add?")
        st.caption("Tick just one to continue in the normal single-Ticket flow below, or tick several to create "
                  "them all as separate Tickets in one batch (you'll assign each its own Code next).")

        if "tk_pending_variant_selection" not in st.session_state:
            st.session_state.tk_pending_variant_selection = [
                {"label": e.get("label", f"Excursion {i+1}"), "selected": False,
                 "ticket_code": "", "modality_code": "Standard Private" if e.get("is_private") else "Standard"}
                for i, e in enumerate(excursions)
            ]
        tkpv_selection = st.session_state.tk_pending_variant_selection

        for i, sel in enumerate(tkpv_selection):
            sel["selected"] = st.checkbox(sel["label"], value=sel["selected"], key=f"tkpv_sel_{i}")

        tkpv_num_selected = sum(1 for s in tkpv_selection if s["selected"])

        if tkpv_num_selected > 1:
            st.caption("Multiple selected - each needs its own Ticket Code and Modality Code:")
            for i, sel in enumerate(tkpv_selection):
                if not sel["selected"]:
                    continue
                tkpvcol1, tkpvcol2 = st.columns(2)
                with tkpvcol1:
                    sel["ticket_code"] = st.text_input(f"Ticket Code — {sel['label']}", value=sel["ticket_code"], key=f"tkpv_code_{i}", placeholder="e.g. BALI-T1")
                with tkpvcol2:
                    sel["modality_code"] = st.text_input(f"Modality Code — {sel['label']}", value=sel["modality_code"], key=f"tkpv_modcode_{i}")

        tkpv_btn_label = "✅ Confirm and Extract Full Details" if tkpv_num_selected <= 1 else f"✅ Confirm and Start Batch Review ({tkpv_num_selected} tickets)"
        if st.button(tkpv_btn_label, key="tk_confirm_variant", disabled=tkpv_num_selected == 0):
            if tkpv_num_selected <= 1:
                with st.spinner("Extracting full details for the selected excursion..."):
                    try:
                        chosen = next(s for s in tkpv_selection if s["selected"])
                        chosen_label = chosen["label"]
                        data = extract_ticket_data(
                            st.session_state.tk_pending_raw_text, variant_hint=chosen_label,
                            human_hint=st.session_state.get("tk_pending_hint")
                        )
                        tk_pending_url = st.session_state.get("tk_pending_url")
                        # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): see the matching
                        # fix above - the placeholder must not be set before the merge, or it
                        # always wins over the update's real live photos.
                        data["image_urls"] = []
                        if action == "update_ticket":
                            data = _merge_extraction_over_baseline(st.session_state.get("tk_extracted") or {}, data)
                        if not data.get("image_urls"):
                            data["image_urls"] = [FALLBACK_IMAGE]

                        floor_start_date_for_new_data(data)
                        st.session_state.tk_cancellation_link_scope = cancellation_links.apply_cancellation_link_default(
                            data, supplier_id, "Ticket")
                        st.session_state.tk_extracted = data
                        bump_widget_generation("tk")  # see the sibling extraction path above
                        st.session_state.tk_raw_preview = f"(Extracted excursion: {chosen_label})\n\n{st.session_state.tk_pending_raw_text}"
                        st.session_state.tk_payloads = None
                        _tk_clear_geo_confirmation()
                        pending_doc_raw_images = list(st.session_state.get("tk_pending_doc_raw_images", []))
                        pending_doc_image_urls = list(st.session_state.get("tk_pending_doc_images", []))
                        _warn_page_image_upload_errors(_add_page_images_to_doc_pool(tk_pending_url, pending_doc_raw_images, pending_doc_image_urls))
                        st.session_state.tk_doc_raw_images = pending_doc_raw_images
                        st.session_state.tk_hosted_image_candidates = list(dict.fromkeys(pending_doc_image_urls))
                        st.session_state.tk_pending_variants = None
                        st.session_state.tk_pending_raw_text = None
                        st.session_state.tk_pending_variant_selection = None
                        st.rerun()
                    except Exception as e:
                        st.error(f"Extraction failed: {friendly_error_message(e)}")
            else:
                tkpv_missing = [s["label"] for s in tkpv_selection if s["selected"] and (not s["ticket_code"].strip() or not s["modality_code"].strip())]
                tkpv_codes_seen = {}
                tkpv_mod_codes_seen = {}
                for s in tkpv_selection:
                    if s["selected"] and s["ticket_code"].strip():
                        tkpv_codes_seen.setdefault(s["ticket_code"].strip(), []).append(s["label"])
                    if s["selected"] and s["modality_code"].strip():
                        tkpv_mod_codes_seen.setdefault(s["modality_code"].strip().lower(), []).append(s["label"])
                tkpv_dupes = {c: labs for c, labs in tkpv_codes_seen.items() if len(labs) > 1}
                # CONFIRMED REAL REQUEST (product owner, 2026-08-24) - same rationale as the sibling
                # candidate-selection screen: two rows sharing a Modality Code usually means the
                # same supplier product was detected twice.
                tkpv_mod_dupes = {c: labs for c, labs in tkpv_mod_codes_seen.items() if len(labs) > 1}
                tkpv_existing = []
                tkpv_mod_existing = []
                for s in tkpv_selection:
                    if s["selected"] and s["ticket_code"].strip():
                        existing_check = check_code_availability(client, "ticket", supplier_id, s["ticket_code"])
                        if existing_check and existing_check["exists"]:
                            tkpv_existing.append(s["ticket_code"].strip())
                    if s["selected"] and s["modality_code"].strip():
                        mod_check = check_modality_code_availability(client, supplier_id, s["modality_code"])
                        if mod_check and mod_check["exists"]:
                            tkpv_mod_existing.append(
                                f"{s['modality_code'].strip()} (already on ticket {mod_check['ticket_code']})")

                if tkpv_missing:
                    st.error(f"🚫 These selected excursions are missing a Ticket Code or Modality Code: {tkpv_missing}")
                elif tkpv_dupes:
                    st.error(f"🚫 These Ticket Codes are used by more than one selected excursion: {list(tkpv_dupes.keys())}")
                elif tkpv_mod_dupes:
                    st.error(f"🚫 These Modality Codes are used by more than one selected excursion - give each "
                            f"a distinct one: {list(tkpv_mod_dupes.keys())}")
                elif tkpv_existing:
                    st.error(f"🚫 These Ticket Codes are ALREADY TAKEN by existing tickets - choose different "
                            f"ones: {tkpv_existing}")
                else:
                    if tkpv_mod_existing:
                        st.warning(f"⚠️ These Modality Codes are already used by an existing ticket for this "
                                  f"supplier - double-check these aren't the same product added again: "
                                  f"{tkpv_mod_existing}")
                    tk_pending_url = st.session_state.get("tk_pending_url")
                    new_mt_queue = [
                        {"label": s["label"], "ticket_code": s["ticket_code"].strip(), "modality_code": s["modality_code"].strip(),
                         "data": None, "confirmed": False}
                        for s in tkpv_selection if s["selected"]
                    ]
                    st.session_state.mt_raw_text = st.session_state.tk_pending_raw_text
                    mt_pending_doc_raw_images = list(st.session_state.get("tk_pending_doc_raw_images", []))
                    mt_pending_doc_image_urls = list(st.session_state.get("tk_pending_doc_images", []))
                    _warn_page_image_upload_errors(_add_page_images_to_doc_pool(tk_pending_url, mt_pending_doc_raw_images, mt_pending_doc_image_urls))
                    st.session_state.mt_doc_raw_images = mt_pending_doc_raw_images
                    st.session_state.mt_hosted_image_candidates = list(dict.fromkeys(mt_pending_doc_image_urls))
                    st.session_state.mt_queue = new_mt_queue
                    st.session_state.mt_queue_index = 0
                    st.session_state.mt_phase = "reviewing"
                    st.session_state.tk_pending_variants = None
                    st.session_state.tk_pending_raw_text = None
                    st.session_state.tk_pending_variant_selection = None
                    st.rerun()

    # ------------------------------------------------------------------
    # TICKET STEP 5: Review & Edit
    # ------------------------------------------------------------------
    if st.session_state.get("tk_extracted"):
        data = st.session_state.tk_extracted
        st.header("Ticket — Step 5: Review & Edit")

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Original Source")
            render_readonly_source(st.session_state.tk_raw_preview, height=500)

        with col2:
            if tk_is_option_only:
                st.subheader("Only pricing/schedule needed for this action")
                st.caption("Ticket details (name, description, city, meeting points) are skipped - "
                          "they belong to the existing ticket and aren't touched here.")
            else:
                st.subheader("Extracted Data (click ✏️ to edit)")
                editable_field("Ticket name", data, "ticket_name", widget="text_input")
                editable_field("Description", data, "description", widget="html_text_area", height=150)
                # CONFIRMED PRODUCT-OWNER RULE: the AI now retries once if either field comes
                # back blank (see extract_ticket_data's safety net), but this is the last line
                # of defense - a ticket can never publish with no name/description.
                if not (data.get("ticket_name") or "").strip():
                    st.error("🚫 Ticket name is empty - fill it in above before continuing.")
                if not (data.get("description") or "").strip():
                    st.error("🚫 Description is empty - fill it in above before continuing.")
                editable_field("City", data, "city", widget="text_input")
                render_cancellation_policy_editor(data, "legacy_ticket")
                editable_field("Condition (internal remarks)", data, "cancellation_policy_text", widget="text_area", height=80)
                # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-25): "Voucher Remarks" and "What to
                # bring" combined into one editable box - see merge_what_to_bring_into_voucher_
                # remarks' docstring; they always ended up concatenated at publish time anyway.
                merge_what_to_bring_into_voucher_remarks(data)
                editable_field("Voucher Remarks (shown to the customer, includes what to bring)", data,
                               "voucher_remarks", widget="text_area", height=100)
                # CONFIRMED PRODUCT-OWNER RULE (2026-08-12): Manual Notes removed here too, same
                # reasoning as the batch flow above - every field is directly editable now.

                if data.get("is_private") and "private" not in (modality_code or "").lower():
                    st.info(f"💡 This excursion is described as **PRIVATE** in the source - a genuine "
                           f"selling point. Your current Modality Code is `{modality_code}` - consider "
                           f"going back to Step 3 (Details) and adding \"Private\" to it if you'd like "
                           f"this reflected there.")

                # CONFIRMED FIX (2026-08-19): min_value=0 so an unset duration stays honestly
                # blank (0) instead of the shared widget silently substituting a fabricated 1.
                editable_field("Duration (hours)", data, "duration", widget="number_input", min_value=0)

                # CONFIRMED FIX (2026-08-19 audit): same "0 is falsy" trap as the batch Ticket
                # screen above - now routed through the shared helper instead of a local copy.
                render_child_age_band(data, key_prefix=f"tk_{widget_generation('tk')}",
                                      min_key="child_age_min", max_key="child_age_max")

                # Engines (Search Engines to Sell through): always ALL of them - this was
                # previously a review multiselect, but there's never a real reason to sell
                # through fewer than all engines, so it's set silently in the background and
                # not shown to the human at all (adjustable afterward in Travel Compositor
                # under Settings > Engine if ever needed).
                data["product_types"] = [
                    "MULTI", "GROUPS", "ONLY_HOTEL", "ONLY_HOUSE", "ONLY_FLIGHT", "ONLY_TRAIN",
                    "FLIGHT_HOTEL", "FLIGHT_HOUSE", "ONLY_TICKET", "EVENT_TICKET", "GOLF", "ONLY_CAR",
                    "ONLY_TRANSFER", "HOLIDAYS", "GIFTCARD", "EXTERNAL_SEARCH_BOX", "GIFT_BOX", "ROUTING",
                    "PRIVATE_TOUR", "MAGIC_BOX", "CRUISES", "AI_TRIP", "MEMBERSHIP", "ONLY_INSURANCE",
                    "ONLY_ITEM", "TRIP_PLANNER",
                ]

                inc_df = pd.DataFrame([{"Item": x} for x in data.get("includes", [])]) if data.get("includes") else pd.DataFrame(columns=["Item"])
                def _save_tk_includes(edf, data=data):
                    data["includes"] = [str(r.get("Item") or "").strip() for _, r in edf.iterrows() if _safe_cell_str(r.get("Item")).strip()]
                editable_table("Includes", inc_df, flow_widget_key("tk", "includes"), on_save=_save_tk_includes)

                exc_df = pd.DataFrame([{"Item": x} for x in data.get("excludes", [])]) if data.get("excludes") else pd.DataFrame(columns=["Item"])
                def _save_tk_excludes(edf, data=data):
                    data["excludes"] = [str(r.get("Item") or "").strip() for _, r in edf.iterrows() if _safe_cell_str(r.get("Item")).strip()]
                editable_table("Excludes", exc_df, flow_widget_key("tk", "excludes"), on_save=_save_tk_excludes)

                mp_default = [{"Description": m.get("description", "")} for m in data.get("meeting_points", [])] or [{"Description": "Hotel Lobby"}]
                mp_df = pd.DataFrame(mp_default)
                def _save_tk_mp(edf, data=data):
                    data["meeting_points"] = [
                        {"description": str(r.get("Description") or "").strip(),
                         "variable_location": str(r.get("Description") or "").strip().lower() == "hotel lobby"}
                        for _, r in edf.iterrows() if _safe_cell_str(r.get("Description")).strip()
                    ]
                editable_table("Meeting Points", mp_df, flow_widget_key("tk", "meeting_points"), on_save=_save_tk_mp)

                # CONFIRMED REAL BUG (audit, 2026-08-24): this key was a bare literal, so it
                # survived every re-extraction - ticket #2 published ticket #1's photos. (The
                # legacy ClosedTour flow always cleared its equivalent field explicitly; Ticket
                # never did.) Generation-scoped now, so a fresh extraction re-seeds it from the
                # new data. All three references below must use the SAME expression.
                _tk_images_key = flow_widget_key("tk", "images_text_value")
                if _tk_images_key not in st.session_state:
                    st.session_state[_tk_images_key] = "\n".join(data.get("image_urls", []))
                if st.session_state.get("_tk_pending_images_update") is not None:
                    st.session_state[_tk_images_key] = st.session_state._tk_pending_images_update
                    st.session_state._tk_pending_images_update = None

                images_text = st.text_area("Image URLs (one per line)", key=_tk_images_key)
                data["image_urls"] = [u.strip() for u in images_text.split("\n") if u.strip()] or [FALLBACK_IMAGE]

                default_tk_img_query = data.get("ticket_name", "") or data.get("city", "")

                def _tk_add_pexels():
                    selected = render_stock_photo_picker("Pexels", search_images, default_tk_img_query, "tk_pexels")
                    if selected:
                        current = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                        new_list = current + selected
                        data["image_urls"] = new_list
                        st.session_state._tk_pending_images_update = "\n".join(new_list)
                        return len(selected)
                    return 0

                render_closable_image_section(True, "🖼️ Or search free stock photos (Pexels)", "tk_pexels_closed", _tk_add_pexels)

                def _tk_add_pixabay():
                    selected = render_stock_photo_picker("Pixabay", search_images_pixabay, default_tk_img_query, "tk_pixabay")
                    if selected:
                        current = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                        new_list = current + selected
                        data["image_urls"] = new_list
                        st.session_state._tk_pending_images_update = "\n".join(new_list)
                        return len(selected)
                    return 0

                render_closable_image_section(True, "🖼️ Or search free stock photos (Pixabay)", "tk_pixabay_closed", _tk_add_pixabay)

                def _tk_add_url_images():
                    selected = render_url_image_picker(st.session_state.tk_hosted_image_candidates, "tk_found_images")
                    if selected:
                        current = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                        new_list = current + selected
                        data["image_urls"] = new_list
                        st.session_state._tk_pending_images_update = "\n".join(new_list)
                        return len(selected)
                    return 0

                render_closable_image_section(
                    bool(st.session_state.get("tk_hosted_image_candidates")),
                    f"🖼️ Images found ({len(st.session_state.get('tk_hosted_image_candidates') or [])}) - from the page/document",
                    "tk_found_images_closed", _tk_add_url_images
                )

                def _tk_add_doc_image():
                    added = render_doc_image_picker(st.session_state.tk_doc_raw_images, "tk_doc_images")
                    if added:
                        current = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                        new_list = current + [added]
                        data["image_urls"] = new_list
                        st.session_state._tk_pending_images_update = "\n".join(new_list)
                        return 1
                    return 0

                render_closable_image_section(
                    bool(st.session_state.get("tk_doc_raw_images")),
                    f"📥 Images extracted from your document(s) ({len(st.session_state.get('tk_doc_raw_images') or [])}) - need hosting",
                    "tk_doc_images_closed", _tk_add_doc_image
                )

        st.subheader("🤖 Tell AI what to fix or clarify (optional)")
        tk_clarify_q = st.text_input("Your message", key="tk_clarify_input")
        if render_house_rule_shortcut(tk_clarify_q, "Ticket", "tk_main"):
            pass
        elif not tk_clarify_q.strip():
            st.caption(f"Type a message above first — Send stays disabled until there's something to send. "
                      f"Start with \"{HOUSE_RULE_CODEWORD}\" to save a standing rule for every Ticket "
                      f"supplier instead of a one-off fix.")
        if not tk_clarify_q.strip().upper().startswith(HOUSE_RULE_CODEWORD.upper()) and st.button(
                "Send", disabled=not tk_clarify_q.strip(), key="tk_clarify_send"):
            with st.spinner("Thinking..."):
                result = apply_clarification(st.session_state.tk_raw_preview, data, tk_clarify_q)
                st.session_state.tk_clarify_result = result
                remember_clarification(clarify_supplier_id(supplier_id), "Ticket", tk_clarify_q, result)
                if result.get("changes"):
                    apply_clarify_changes(data, result, currency)
                    # Built from flow_widget_key(), NOT hardcoded "_editing_table_tk_*" strings:
                    # those tables are generation-scoped now (see new_widget_token()), so a
                    # literal name here would silently stop matching and this reset would quietly
                    # do nothing - exactly the drift this bug class keeps coming back through.
                    # Stop Sales / modality_supplements come from render_*_editor helpers, which
                    # build their own table names from the prefix they are handed.
                    _tkg = widget_generation("tk")
                    tk_field_to_table_key = {
                        "includes": f"_editing_table_{flow_widget_key('tk', 'includes')}",
                        "excludes": f"_editing_table_{flow_widget_key('tk', 'excludes')}",
                        "meeting_points": f"_editing_table_{flow_widget_key('tk', 'meeting_points')}",
                        "time_tables": f"_editing_table_{flow_widget_key('tk', 'timetables')}",
                        "stop_sales": f"_editing_table_tk_{_tkg}_stop_sales",
                        "modality_supplements": f"_editing_table_tk_{_tkg}_modality_supplements",
                        "occupancy_prices": f"_editing_table_tk_{_tkg}_occupancy",
                    }
                    for field_name in result["changes"]:
                        table_key = tk_field_to_table_key.get(field_name)
                        if table_key:
                            st.session_state[table_key] = False
                    # Plain text/number fields (Ticket name, Description, City, Condition,
                    # Voucher Remarks, Duration) - see reset_stale_editable_field_widgets'
                    # docstring for why these need the same treatment as table fields.
                    reset_stale_editable_field_widgets(result["changes"])
                    if "operational_days" in result["changes"]:
                        st.session_state.pop(flow_widget_key("tk", "op_days"), None)
                st.rerun()
        if st.session_state.get("tk_clarify_result"):
            r = st.session_state.tk_clarify_result
            render_clarify_result(r)
        remember_memory_panel(clarify_supplier_id(supplier_id), "Ticket", "tk")

        st.markdown("**Start Time(s)**")
        st.caption("A Ticket can have multiple valid start times (e.g. a 09:00 and a 14:00 departure). "
                  "If the document doesn't state one, please add at least one manually.")
        tt_df = pd.DataFrame([{"Time (HH:MM)": t} for t in data.get("time_tables", [])]) if data.get("time_tables") else pd.DataFrame(columns=["Time (HH:MM)"])
        def _save_tk_timetables(edf, data=data):
            data["time_tables"] = _clean_time_table_rows(edf)
        editable_table("Start Time(s)", tt_df, flow_widget_key("tk", "timetables"), on_save=_save_tk_timetables)
        if not data.get("time_tables"):
            st.caption("ℹ️ No start time set yet - optional, but add one if the ticket has a fixed departure time.")

        st.subheader("Departure Schedule")
        if data.get("schedule_notes"):
            st.info(f"🔎 {data['schedule_notes']}")
        data["operational_days"] = st.multiselect("Operational Days", ALL_WEEKDAYS,
                                                   default=data.get("operational_days", ALL_WEEKDAYS), key=flow_widget_key("tk", "op_days"))

        # CONFIRMED REAL BUG (product owner, 2026-08-24 - "surcharges are not working correctly"):
        # Valid From/Valid Until used to be set FURTHER DOWN the script (after the pricing editor),
        # while render_ticket_modality_supplements_editor was called up here - a classic Streamlit
        # ordering bug. render_ticket_modality_supplements_editor reads data["start_date"]/
        # data["end_date"] to default undated supplement rows and clip out-of-window dates, but at
        # the point it ran, those two fields still held whatever was in `data` BEFORE this render's
        # Valid From/Until edit took effect (Streamlit widgets only update `data` at the line they're
        # called, and that line ran later) - a full script rerun behind. The table shown to the human,
        # and what got saved into modality_supplements on edit, was defaulted/clipped against a STALE
        # Modality window, not the one they'd just typed. Moved here, ABOVE both the Stop Sales and
        # Supplements editors, so both always see this render's real Valid From/Valid Until.
        dcol1, dcol2 = st.columns(2)
        with dcol1:
            data["start_date"] = _iso(st.text_input("Valid From (DD/MM/YYYY)", value=_disp(data.get("start_date", "")), key=flow_widget_key("tk", "start_date")))
        with dcol2:
            data["end_date"] = _iso(st.text_input("Valid Until (DD/MM/YYYY)", value=_disp(data.get("end_date", "")), key=flow_widget_key("tk", "end_date")))

        # Same fix and reasoning as the multi-Ticket batch flow's Stop Sales editor (see the
        # "CONFIRMED REAL BUG" comment there): the raw JSON text_area went stale under "Tell AI
        # what to fix" and wasn't an easy way to add one by hand either. render_stop_sales_editor
        # is the same friendly Start/End Date table already used for ClosedTour.
        render_stop_sales_editor(data, f"tk_{widget_generation('tk')}")
        render_ticket_modality_supplements_editor(data, f"tk_{widget_generation('tk')}")

        render_ticket_language_options(data, f"tk_{widget_generation('tk')}")

        num_days = len(data.get("operational_days", []))
        num_stops = len(data.get("stop_sales", []))
        if num_days == 0:
            sched_label, sched_bg, sched_fg = "⚠️ No Operational Days selected", "#f8d7da", "#721c24"
        elif num_days == 7 and num_stops == 0:
            sched_label, sched_bg, sched_fg = "🟢 DAILY departure - runs every day", "#d4edda", "#155724"
        elif num_stops > 0:
            sched_label, sched_bg, sched_fg = (
                f"🟠 SPECIFIC DATE departure - {num_days} weekday(s) minus {num_stops} blocked range(s)",
                "#fff3cd", "#856404"
            )
        else:
            sched_label, sched_bg, sched_fg = (
                f"🔵 WEEKLY departure - runs every {', '.join(data.get('operational_days', []))}",
                "#d1ecf1", "#0c5460"
            )
        st.markdown(
            f"<div style='background-color:{sched_bg}; color:{sched_fg}; padding:10px 14px; "
            f"border-radius:4px; font-weight:bold; margin-bottom:10px;'>{sched_label}</div>",
            unsafe_allow_html=True
        )

        st.subheader(f"Pricing (in {currency or '(set Currency in Step 3)'})")
        render_ticket_pricing_editor(data, f"tk_{widget_generation('tk')}", currency, max_passengers)
        price_type = data["price_type"]

        # Valid From/Valid Until now render further up the page (right before the Stop Sales and
        # Supplements by dates editors, which both depend on data["start_date"]/data["end_date"] being
        # fresh for THIS render) - see the "CONFIRMED REAL BUG" comment there. Kept out of this spot.
        if data.get("pricing_notes"):
            st.warning(f"⚠️ {data['pricing_notes']}")

        if action == "create":
            # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-13): a new Ticket must only ever be
            # created with ONE Modality - extra costs are Modality-specific, so mixing
            # Modalities during creation was causing real errors. Extra Modalities described
            # in the same document are no longer extractable/creatable here; they must be
            # added afterward via "2: Add new Modality to existing Ticket".
            if "tk_extra_modalities" not in st.session_state:
                st.session_state.tk_extra_modalities = []
            st.info("ℹ️ This Ticket will be created with just this one Modality. If your document "
                    "describes other variants (e.g. a different guide language or vehicle class), "
                    "add them afterward via **Update existing Service -> Ticket -> \"2: Add "
                    "new Modality to existing Ticket\"**.")


        if price_type == "SERVICE":
            price_valid = bool(data.get("base_service_price", 0))
        elif price_type == "OCCUPANCY":
            # CONFIRMED RULE (product owner, 2026-08-24): EVERY offered occupancy needs a real
            # price, not just one of them. `any(...)` passed a table where rows 1-4 were priced and
            # 5-9 were left at the editor's default of 0, publishing those as free. See
            # render_publish_blockers.
            _occ_rows = data.get("occupancy_prices") or []
            _zero_occ = [o.get("occupancy") for o in _occ_rows if not _safe_float(o.get("amount"), fallback=0.0)]
            price_valid = bool(_occ_rows) and not _zero_occ
            if _zero_occ:
                st.error(f"🚫 No price for occupancy: **{', '.join(str(o) for o in _zero_occ)}** - "
                         f"these would be sellable for free. Enter a price for each, or remove the row.")
        else:
            price_valid = any([data.get("base_adult_price", 0), data.get("base_children_price", 0), data.get("base_infant_price", 0)])
        if not price_valid:
            st.error("Add at least one non-zero price (Adult/Child/Infant) before continuing.")

        can_build = price_valid

        st.subheader("🤖 Tell AI what to fix or clarify (optional)")
        st.caption("Ask a question, or tell it to fix something about the pricing/schedule above (e.g. 'the "
                  "adult price should be 89 not 79'). It applies real changes when you ask for them - always "
                  "shows exactly what changed so you can double-check.")
        tk_clarify_q2 = st.text_input("Your message", key="tk_clarify_input_pricing",
                                      placeholder="e.g. 'Fix the adult price to 89' or 'Is the child price for under 12?'")
        if render_house_rule_shortcut(tk_clarify_q2, "Ticket", "tk_pricing"):
            pass
        elif not tk_clarify_q2.strip():
            st.caption(f"Type a message above first — Send stays disabled until there's something to send. "
                      f"Start with \"{HOUSE_RULE_CODEWORD}\" to save a standing rule for every Ticket "
                      f"supplier instead of a one-off fix.")
        if not tk_clarify_q2.strip().upper().startswith(HOUSE_RULE_CODEWORD.upper()) and st.button(
                "Send", disabled=not tk_clarify_q2.strip(), key="tk_clarify_send_pricing"):
            with st.spinner("Thinking..."):
                result = apply_clarification(st.session_state.tk_raw_preview, data, tk_clarify_q2)
                # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): a bare clarify_supplier_id()
                # call checks session-state keys in a fixed priority order that puts ClosedTour's
                # own key FIRST - if the operator had used ClosedTour earlier in this same browser
                # session (its key never gets cleared just by switching product types), a Ticket
                # correction here could get filed under that stale ClosedTour supplier instead of
                # this Ticket's real one. Passing the local `supplier_id` explicitly (it's already
                # in scope here) makes that the preferred value, bypassing the ambiguous fallback.
                remember_clarification(clarify_supplier_id(supplier_id), "Ticket", tk_clarify_q2, result)
                st.session_state.tk_clarify_result_pricing = result
                if result.get("changes"):
                    apply_clarify_changes(data, result, currency)
                    # Same generation-scoping as the box above - see its comment.
                    _tkg2 = widget_generation("tk")
                    tk_field_to_table_key2 = {
                        "occupancy_prices": f"_editing_table_tk_{_tkg2}_occupancy",
                        "time_tables": f"_editing_table_{flow_widget_key('tk', 'timetables')}",
                        "stop_sales": f"_editing_table_tk_{_tkg2}_stop_sales",
                        "modality_supplements": f"_editing_table_tk_{_tkg2}_modality_supplements",
                    }
                    for field_name in result["changes"]:
                        table_key = tk_field_to_table_key2.get(field_name)
                        if table_key:
                            st.session_state[table_key] = False
                    reset_stale_editable_field_widgets(result["changes"])
                    if "operational_days" in result["changes"]:
                        st.session_state.pop(flow_widget_key("tk", "op_days"), None)
                st.rerun()
        if st.session_state.get("tk_clarify_result_pricing"):
            r = st.session_state.tk_clarify_result_pricing
            render_clarify_result(r)
        remember_memory_panel(clarify_supplier_id(supplier_id), "Ticket", "tkp")

        if st.button("🔎 Check Locations & Continue", disabled=not can_build, key="tk_build_payload"):
            pre_config = TicketHumanPreConfig(
                supplier_id=supplier_id, ticket_code=ticket_code or existing_ticket_code or "XXX",
                currency=currency, modality_code=modality_code, on_request=on_request,
                days_available_before_release=release_days, min_passengers=min_passengers, max_passengers=max_passengers
            )
            with st.spinner("Resolving geolocation..."):
                st.session_state.tk_payloads = build_ticket_payloads(pre_config, data, client)
                st.session_state.tk_payloads_data_fingerprint = _data_fingerprint(data)

        # CONFIRMED REAL BUG (internal audit) - see _data_fingerprint's docstring:
        # the pricing/supplements/occupancy tables above stay editable after a
        # payload was already built, and an edit there used to publish silently
        # using the STALE pre-edit payload. Discard it here the moment `data`
        # no longer matches what it was built from, forcing an explicit rebuild.
        if st.session_state.get("tk_payloads") and _data_fingerprint(data) != st.session_state.get("tk_payloads_data_fingerprint"):
            st.session_state.tk_payloads = None
            st.warning("✏️ You edited the data above after building the payload - click "
                      "**🔎 Check Locations & Continue** again to refresh it before publishing.")

        # ------------------------------------------------------------------
        # TICKET STEP 6: Geolocation & Payload Preview
        # ------------------------------------------------------------------
        if st.session_state.get("tk_payloads"):
            payloads = st.session_state.tk_payloads
            st.header("Ticket — Step 6: Geolocation & Payload Preview")

            render_modalities_review(
                "ticket", modality_code, "Base Modality", data,
                st.session_state.get("tk_extra_modalities", []), currency
            )

            if not tk_is_option_only:
                if payloads["geolocation_resolved"]:
                    lat, lng = payloads["geolocation_latitude"], payloads["geolocation_longitude"]
                    maps_link = f"https://www.google.com/maps?q={lat},{lng}"
                    st.markdown(
                        f"<div style='background-color:#d4edda; color:#155724; padding:10px 14px; "
                        f"border-radius:4px;'>📍 Resolved location: <strong>{payloads['geolocation_name'] or '(no name)'}</strong>"
                        f"<br>Coordinates: {lat:.6f}, {lng:.6f} (source: {payloads['geolocation_source']})</div>",
                        unsafe_allow_html=True
                    )
                    st.markdown(f"[🗺️ Open in Google Maps to verify]({maps_link})")
                    if payloads['geolocation_source'] not in ("manual override", "not_found", None):
                        st.caption("Geocoding data © OpenStreetMap contributors")

                    with st.expander("🔍 This looks wrong or too imprecise? Search for a better match"):
                        st.caption("Broad place names (e.g. 'Bali') often resolve to the centroid of a whole "
                                  "region, which can be far from the actual location. Try something more "
                                  "specific - a landmark, neighborhood, or meeting point name - and pick the "
                                  "correct result below.")
                        tk_geo_search_query = st.text_input("Search for a location", value=data.get("city", ""), key=flow_widget_key("tk", "geo_search_query"))
                        if st.button("🔎 Search", key="tk_geo_search_btn"):
                            with st.spinner("Searching..."):
                                st.session_state.tk_geo_search_results = geocode_search(tk_geo_search_query, limit=5)
                        if st.session_state.get("tk_geo_search_results"):
                            for gi, candidate in enumerate(st.session_state.tk_geo_search_results):
                                gcol_info, gcol_btn = st.columns([4, 1])
                                with gcol_info:
                                    st.write(f"**{candidate['display_name']}**")
                                    st.caption(f"{candidate['latitude']:.6f}, {candidate['longitude']:.6f} ({candidate.get('type', '')})")
                                with gcol_btn:
                                    if st.button("Use this", key=f"tk_geo_pick_{gi}"):
                                        data["manual_latitude"] = candidate["latitude"]
                                        data["manual_longitude"] = candidate["longitude"]
                                        pre_config = TicketHumanPreConfig(
                                            supplier_id=supplier_id, ticket_code=ticket_code or existing_ticket_code or "XXX",
                                            currency=currency, modality_code=modality_code, on_request=on_request,
                                            days_available_before_release=release_days, min_passengers=min_passengers, max_passengers=max_passengers
                                        )
                                        st.session_state.tk_payloads = build_ticket_payloads(pre_config, data, client)
                                        st.session_state.tk_payloads_data_fingerprint = _data_fingerprint(data)
                                        _tk_clear_geo_confirmation()
                                        st.session_state.tk_geo_search_results = None
                                        st.rerun()

                    if payloads.get("is_indonesia"):
                        st.info(f"🇮🇩 Indonesia detected — Vesak Day and Nyepi are automatically blocked as "
                                f"stop-sale dates, no excursion may start on either day. "
                                f"{payloads.get('indonesia_holiday_note', '')}")

                    if payloads.get("is_vietnam") and payloads.get("tet_overlap"):
                        _tk_tet = payloads["tet_overlap"]
                        st.warning(f"🇻🇳 This Ticket's validity dates overlap **Tet Holiday {_tk_tet['year']}** "
                                  f"({_tk_tet['start']} to {_tk_tet['end']}) — check whether the source "
                                  f"document/contract needs a Tet surcharge added as a dated Supplement. "
                                  f"{payloads.get('tet_holiday_note', '')}")

                    if payloads.get("release_days_overridden"):
                        st.info(f"📅 The document mentions its own booking/release deadline, so the release "
                                f"period being used is **{payloads['effective_release_days']} days** instead of "
                                f"your default - if the source mentioned more than one deadline, the longer "
                                f"(safer) one was used.")

                    st.session_state.tk_geo_confirmed = st.checkbox(
                        "✅ I've checked this location on the map and it's correct for this ticket",
                        value=st.session_state.get("tk_geo_confirmed", False),
                        # CONFIRMED REAL BUG (audit, 2026-08-24): every place that sets
                        # tk_geo_confirmed=False (a new ticket, changed coordinates) reset the
                        # CONTROL flag but not this checkbox's own key, so the box stayed ticked
                        # and immediately re-asserted True - the one human check between a wrong
                        # city and a published ticket, silently pre-satisfied. Generation-scoped
                        # for the cross-ticket case; _tk_clear_geo_confirmation() below handles
                        # coordinates changing within one ticket.
                        key=flow_widget_key("tk", "geo_confirm_checkbox")
                    )
                    if not st.session_state.tk_geo_confirmed:
                        st.info("👆 Please verify the location above before publishing.")
                else:
                    st.markdown(
                        "<div style='background-color:#f8d7da; color:#721c24; padding:6px 12px; "
                        "border-radius:4px;'>❌ Geolocation NOT resolved - the City name may not match a known "
                        "destination.</div>",
                        unsafe_allow_html=True
                    )
                    st.caption("Search for the correct location below (easier than looking up exact "
                              "coordinates), or enter coordinates manually if you already have them.")

                    tk_geo_search_query2 = st.text_input("Search for a location", value=data.get("city", ""), key="tk_geo_search_query2")
                    if st.button("🔎 Search", key="tk_geo_search_btn2"):
                        with st.spinner("Searching..."):
                            st.session_state.tk_geo_search_results2 = geocode_search(tk_geo_search_query2, limit=5)
                    if st.session_state.get("tk_geo_search_results2"):
                        for gi, candidate in enumerate(st.session_state.tk_geo_search_results2):
                            gcol_info, gcol_btn = st.columns([4, 1])
                            with gcol_info:
                                st.write(f"**{candidate['display_name']}**")
                                st.caption(f"{candidate['latitude']:.6f}, {candidate['longitude']:.6f} ({candidate.get('type', '')})")
                            with gcol_btn:
                                if st.button("Use this", key=f"tk_geo_pick2_{gi}"):
                                    data["manual_latitude"] = candidate["latitude"]
                                    data["manual_longitude"] = candidate["longitude"]
                                    pre_config = TicketHumanPreConfig(
                                        supplier_id=supplier_id, ticket_code=ticket_code or existing_ticket_code or "XXX",
                                        currency=currency, modality_code=modality_code, on_request=on_request,
                                        days_available_before_release=release_days, min_passengers=min_passengers, max_passengers=max_passengers
                                    )
                                    st.session_state.tk_payloads = build_ticket_payloads(pre_config, data, client)
                                    st.session_state.tk_payloads_data_fingerprint = _data_fingerprint(data)
                                    _tk_clear_geo_confirmation()
                                    st.session_state.tk_geo_search_results2 = None
                                    st.rerun()

                    st.markdown("**Or enter coordinates manually:**")
                    gcol1, gcol2 = st.columns(2)
                    with gcol1:
                        manual_lat = st.number_input("Latitude", value=None, format="%.6f", key=flow_widget_key("tk", "manual_lat"), placeholder="e.g. 27.394900")
                    with gcol2:
                        manual_lng = st.number_input("Longitude", value=None, format="%.6f", key=flow_widget_key("tk", "manual_lng"), placeholder="e.g. 33.678400")
                    manual_geo_ready = manual_lat is not None and manual_lng is not None and not (manual_lat == 0 and manual_lng == 0)
                    if manual_lat == 0 and manual_lng == 0:
                        st.caption("⚠️ 0, 0 is a real point in the ocean, not a valid location - enter real coordinates.")
                    if st.button("📍 Use these coordinates & rebuild payload", key="tk_use_manual_geo", disabled=not manual_geo_ready):
                        data["manual_latitude"] = manual_lat
                        data["manual_longitude"] = manual_lng
                        pre_config = TicketHumanPreConfig(
                            supplier_id=supplier_id, ticket_code=ticket_code or existing_ticket_code or "XXX",
                            currency=currency, modality_code=modality_code, on_request=on_request,
                            days_available_before_release=release_days, min_passengers=min_passengers, max_passengers=max_passengers
                        )
                        st.session_state.tk_payloads = build_ticket_payloads(pre_config, data, client)
                        st.session_state.tk_payloads_data_fingerprint = _data_fingerprint(data)
                        _tk_clear_geo_confirmation()
                        st.rerun()
            else:
                st.info("ℹ️ This action only affects a ticket Option/Modality, which has no geolocation "
                        "of its own (geolocation lives on the main ticket only) - nothing to confirm here.")

            if publish_action in ("Update an existing ticket's details", "Update an existing ticket option"):
                render_ticket_update_comparison(
                    publish_action, data, payloads, client, supplier_id, existing_ticket_code, modality_code
                )

            with st.expander("🔧 Main Ticket Payload", expanded=False):
                if payloads["main_ticket_error"]:
                    st.error(f"Invalid: {payloads['main_ticket_error']}")
                else:
                    st.json(payloads["main_ticket_payload"])
            with st.expander("🔧 Ticket Option Payload", expanded=False):
                if payloads["ticket_option_error"]:
                    st.error(f"Invalid: {payloads['ticket_option_error']}")
                else:
                    st.json(payloads["ticket_option_payload"])

            # ------------------------------------------------------------------
            # TICKET STEP 7: Publish
            # ------------------------------------------------------------------
            st.header("Ticket — Step 7: Publish")
            creating_new = publish_action == "Create a brand-new ticket (+ first option)"
            target_ticket_code = payloads["main_ticket_code"] if creating_new else existing_ticket_code
            # Geolocation only lives on the MAIN ticket - "Add option"/"Update option" only
            # ever touch a ContractTicketModalityVO, which has no geolocation field at all.
            # Their source data always comes from extract_ticket_option_only_data(), which
            # never fills in a real city, so geolocation_resolved is always False for these
            # two actions - requiring it here would make "Add option"/"Update option"
            # permanently unpublishable. Only require geolocation confirmation when this
            # publish action actually writes the main ticket (create / update_ticket).
            can_publish = not payloads["main_ticket_error"] and not payloads["ticket_option_error"]
            # CONFIRMED RULES (product owner, 2026-08-24), both "block publish, tell the operator":
            #  1. An EXPIRED document must not publish - it used to silently produce an inverted,
            #     unbookable date window (startDate floored to today, endDate still in the past).
            #  2. A ticket must never go live with an occupancy priced at 0.00 - the pricing editor
            #     materializes rows 1..cap defaulting to 0, so a document pricing only 1-4 pax left
            #     5-9 bookable for free. Hotel already hard-blocks this; Ticket was the last product
            #     that didn't.
            can_publish = can_publish and render_publish_blockers(payloads)
            if not tk_is_option_only:
                can_publish = can_publish and payloads.get("geolocation_resolved") and st.session_state.get("tk_geo_confirmed", False)
                if payloads.get("geolocation_resolved") and not st.session_state.get("tk_geo_confirmed", False):
                    st.warning("⚠️ Confirm the location above (checkbox in Step 6) before you can publish.")

            action_descriptions = {
                "Create a brand-new ticket (+ first option)": "Will POST a new ticket, then POST a new option.",
                "Add a new option to an existing ticket": f"Will POST a new option under existing ticket `{target_ticket_code}`.",
                "Update an existing ticket's details": f"Will PUT (update) ticket `{target_ticket_code}`'s details, then PUT (update) Modality `{modality_code}`'s pricing/schedule.",
                "Update an existing ticket option": f"Will PUT (update) the option under ticket `{target_ticket_code}`.",
            }
            st.caption(action_descriptions[publish_action])

            tk_publish_as_active = True
            if creating_new:
                tk_activation_choice = st.radio(
                    "After publishing, should this Ticket be Active or Inactive (draft)?",
                    ["Inactive (draft) - recommended, review inside Travel Compositor before it goes live",
                     "Active - live immediately"],
                    index=0, key="tk_activation_choice"
                )
                tk_publish_as_active = tk_activation_choice.startswith("Active")

            if st.button("🚀 Publish to Travel Compositor", disabled=not can_publish, type="primary", key="tk_publish_btn"):
                with st.spinner("Publishing..."):
                    try:
                        if publish_action == "Create a brand-new ticket (+ first option)":
                            creation_payload = dict(payloads["main_ticket_payload"])
                            creation_payload["active"] = True
                            result = client.create_ticket(supplier_id, creation_payload)
                            if "error" in result:
                                show_publish_error("create the ticket", result, flow="ticket_legacy")
                            else:
                                real_code = result.get("code", payloads["main_ticket_code"])
                                # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): see
                                # mark_code_as_taken's docstring.
                                mark_code_as_taken("ticket", supplier_id, payloads["main_ticket_code"], result.get("name"))
                                if real_code and real_code != payloads["main_ticket_code"]:
                                    mark_code_as_taken("ticket", supplier_id, real_code, result.get("name"))
                                st.success(f"✅ Ticket created (active) with real Code: **{real_code}** — save this exact value.")

                                # api_client.py's _request() already retries every write call
                                # (incl. this POST) up to 6 times internally now.
                                option_result = client.create_ticket_option(supplier_id, real_code, payloads["ticket_option_payload"])

                                if "error" in option_result:
                                    show_publish_error("create the ticket option after retrying", option_result, flow="ticket_legacy")
                                    st.info("💡 Adjustments to a Ticket require it to be ACTIVE - inactive tickets aren't visible via the API.")
                                    # The ticket itself WAS created successfully (real_code) and is still
                                    # ACTIVE - only the option failed. Don't leave the human stuck on this
                                    # page with no way forward: surface the same "what next" block used on
                                    # success, so they can immediately retry adding the option to this
                                    # already-created ticket ("Add another Modality" below prefills exactly
                                    # that: action=add_option, existing_ticket_code=real_code) or start a
                                    # fresh import instead.
                                    st.session_state.tk_just_published_code = real_code
                                    st.session_state.tk_just_published_supplier_id = supplier_id
                                    st.session_state.tk_just_published_is_inactive = False
                                    st.session_state.tk_publish_partial_failure = True
                                    st.session_state.tk_partial_failure_kind = "create"
                                else:
                                    st.success("✅ Ticket option created.")

                                    tk_extra_modalities = st.session_state.get("tk_extra_modalities", [])
                                    if tk_extra_modalities:
                                        st.markdown("**Creating additional modalities...**")
                                        for mod in tk_extra_modalities:
                                            if not mod.get("code") or not mod.get("data"):
                                                st.warning("⚠️ Skipped a modality - missing code or pricing data.")
                                                continue
                                            with st.spinner(f"Creating modality '{mod['code']}'..."):
                                                try:
                                                    mod_pre_config = TicketHumanPreConfig(
                                                        supplier_id=supplier_id, ticket_code=ticket_code, currency=currency,
                                                        modality_code=mod["code"], on_request=on_request,
                                                        days_available_before_release=release_days,
                                                        min_passengers=min_passengers, max_passengers=max_passengers
                                                    )
                                                    mod_payloads = build_ticket_payloads(mod_pre_config, mod["data"], client)
                                                    if mod_payloads["ticket_option_error"]:
                                                        show_publish_error(f"prepare modality '{mod['code']}'", mod_payloads["ticket_option_error"], flow="ticket_legacy")
                                                        continue
                                                    if not render_publish_blockers(mod_payloads):
                                                        continue
                                                    mod_option_result = client.create_ticket_option(supplier_id, real_code, mod_payloads["ticket_option_payload"])
                                                    if "error" in mod_option_result:
                                                        show_publish_error(f"create modality '{mod['code']}'", mod_option_result, flow="ticket_legacy")
                                                    else:
                                                        st.success(f"✅ Modality '{mod['code']}' created.")
                                                except Exception as e:
                                                    show_publish_error(f"create modality '{mod['code']}' (unexpected error - skipped, rest continues)", str(e), flow="ticket_legacy")
                                                    continue

                                    if tk_publish_as_active:
                                        st.success(f"✅ Ticket `{real_code}` left ACTIVE, as chosen above - it's live now.")
                                        st.session_state.tk_just_published_code = real_code
                                        st.session_state.tk_extra_modalities = []
                                        st.session_state.tk_just_published_supplier_id = supplier_id
                                        st.session_state.tk_just_published_is_inactive = False
                                        st.session_state.tk_publish_partial_failure = False
                                    else:
                                        deactivate_payload = dict(creation_payload)
                                        deactivate_payload["active"] = False
                                        deactivate_payload["code"] = real_code
                                        deactivate_result = client.update_ticket(supplier_id, deactivate_payload)
                                        if "error" in deactivate_result:
                                            st.warning(f"⚠️ Ticket and option created successfully, but switching back "
                                                      f"to inactive/draft failed: {deactivate_result}.")
                                        else:
                                            st.success(f"✅ Ticket `{real_code}` switched back to inactive/draft. "
                                                      f"Ready for human review — activate it inside Travel Compositor when ready.")
                                            st.session_state.tk_just_published_code = real_code
                                            st.session_state.tk_extra_modalities = []
                                            st.session_state.tk_just_published_supplier_id = supplier_id
                                            st.session_state.tk_just_published_is_inactive = True
                                            st.session_state.tk_publish_partial_failure = False

                        elif publish_action == "Add a new option to an existing ticket":
                            result = client.create_ticket_option(supplier_id, target_ticket_code, payloads["ticket_option_payload"])
                            if "error" in result:
                                show_publish_error("add the option", result, flow="ticket_legacy")
                                st.info(f"💡 Adjustments require the Ticket to be ACTIVE - activate `{target_ticket_code}` inside Travel Compositor first.")
                            else:
                                st.success(f"✅ New option added to ticket `{target_ticket_code}`. Verify inside Travel Compositor.")
                                st.session_state.tk_just_published_code = target_ticket_code
                                st.session_state.tk_just_published_supplier_id = supplier_id
                                st.session_state.tk_just_published_is_inactive = False
                                st.session_state.tk_publish_partial_failure = False

                        elif publish_action == "Update an existing ticket's details":
                            # This branch only runs for action=="update_ticket" with scope
                            # "whole_ticket" (the "price_only" scope relabels publish_action to
                            # "Update an existing ticket option" above, routing through that
                            # branch instead - see tk_price_only_via_update_ticket). CONFIRMED
                            # FIX (product owner, 2026-08-28): "whole ticket" already extracts
                            # and builds a full ticket_option_payload via build_ticket_payloads
                            # (it always builds both), but historically only ever published the
                            # main ticket details, silently discarding the pricing/schedule it
                            # just asked the human to review. Now it publishes both.
                            update_payload = dict(payloads["main_ticket_payload"])
                            update_payload["code"] = target_ticket_code
                            # CONFIRMED BUG FIX (audit CRITICAL #2, 2026-09-01): build_ticket_payloads
                            # always sets active=False ("LOCKED default" - correct for a brand-new
                            # ticket, which must land as a draft), but this same payload is reused
                            # verbatim for UPDATE. Sent as-is, every "update this ticket's details"
                            # silently took a live, active ticket off sale - the UI still said
                            # "updated" while the ticket vanished from sale, and the very next call
                            # (pricing update) then failed the app's own ACTIVE-required guard. The
                            # live record's own active state (fetched by "Check what's already
                            # online", same source already used for currency/min/maxPassengers above)
                            # must win here instead.
                            _tk_live_for_active = st.session_state.get("tk_fetched_ticket") or {}
                            if isinstance(_tk_live_for_active, dict) and "error" not in _tk_live_for_active \
                                    and _tk_live_for_active.get("active") is not None:
                                update_payload["active"] = _tk_live_for_active["active"]
                            result = client.update_ticket(supplier_id, update_payload)
                            if "error" in result:
                                show_publish_error("update the ticket", result, flow="ticket_legacy")
                                st.info(f"💡 Adjustments require the Ticket to be ACTIVE - activate `{target_ticket_code}` inside Travel Compositor first.")
                            else:
                                st.success(f"✅ Ticket `{target_ticket_code}` updated.")

                                update_option_payload = dict(payloads["ticket_option_payload"])
                                update_option_payload["code"] = modality_code
                                option_result = client.update_ticket_option(supplier_id, target_ticket_code, update_option_payload)
                                if "error" in option_result:
                                    show_publish_error("update the ticket's pricing/modality after retrying", option_result, flow="ticket_legacy")
                                    st.info(f"💡 The ticket's own details ARE saved. Only the Modality `{modality_code}`'s "
                                           f"pricing/schedule failed - fix and retry with **'Update existing Ticket "
                                           f"Modality'** against `{target_ticket_code}` / `{modality_code}`, no need to "
                                           f"redo the ticket details.")
                                    st.session_state.tk_just_published_code = target_ticket_code
                                    st.session_state.tk_just_published_supplier_id = supplier_id
                                    st.session_state.tk_just_published_is_inactive = False
                                    st.session_state.tk_publish_partial_failure = True
                                    st.session_state.tk_partial_failure_kind = "update_ticket"
                                else:
                                    st.success(f"✅ Modality `{modality_code}` pricing/schedule updated.")
                                    st.session_state.tk_just_published_code = target_ticket_code
                                    st.session_state.tk_just_published_supplier_id = supplier_id
                                    st.session_state.tk_just_published_is_inactive = False
                                    st.session_state.tk_publish_partial_failure = False

                        elif publish_action == "Update an existing ticket option":
                            update_option_payload = dict(payloads["ticket_option_payload"])
                            update_option_payload["code"] = modality_code
                            result = client.update_ticket_option(supplier_id, target_ticket_code, update_option_payload)
                            if "error" in result:
                                show_publish_error("update the option", result, flow="ticket_legacy")
                                st.info(f"💡 Adjustments require the Ticket to be ACTIVE - activate `{target_ticket_code}` inside Travel Compositor first.")
                            else:
                                st.success(f"✅ Option `{modality_code}` under ticket `{target_ticket_code}` updated.")
                                st.session_state.tk_just_published_code = target_ticket_code
                                st.session_state.tk_just_published_supplier_id = supplier_id
                                st.session_state.tk_just_published_is_inactive = False
                                st.session_state.tk_publish_partial_failure = False
                    except Exception as e:
                        # This used to be able to crash the whole app on any
                        # unhandled exception partway through publishing -
                        # now it shows a contained error instead.
                        show_publish_error("publish the ticket (unexpected error)", str(e), flow="ticket_legacy")

    if st.session_state.get("tk_just_published_code"):
        st.divider()
        if st.session_state.get("tk_publish_partial_failure"):
            if st.session_state.get("tk_partial_failure_kind") == "update_ticket":
                st.subheader("⚠️ Ticket details updated, but the pricing/modality failed — here's how to continue")
                st.write(f"The ticket's own details (**{st.session_state.tk_just_published_code}**, Supplier "
                        f"{st.session_state.tk_just_published_supplier_id}) were updated successfully - see the "
                        f"error above for what went wrong with the Modality's pricing/schedule. Don't redo the "
                        f"ticket details. Instead, use **'Update existing Ticket Modality'** below to retry just "
                        f"the pricing/schedule against the Modality code shown in the error.")
            else:
                st.subheader("⚠️ Ticket created, but the option failed — here's how to continue")
                st.write(f"The ticket itself (**{st.session_state.tk_just_published_code}**, Supplier "
                        f"{st.session_state.tk_just_published_supplier_id}) was created successfully and is "
                        f"still **ACTIVE**, but its first option/modality failed - see the error above. Don't "
                        f"retry 'Create a brand-new ticket' (that would try to create a duplicate). Instead, "
                        f"use **'Add another Modality to this same Ticket'** below to retry just the option "
                        f"against the ticket that already exists, or start a completely fresh import.")
        else:
            st.subheader("✅ Ticket published — what would you like to do next?")
            st.write(f"Just published: **{st.session_state.tk_just_published_code}** "
                    f"(Supplier {st.session_state.tk_just_published_supplier_id})")

        if st.session_state.get("tk_just_published_is_inactive"):
            st.warning("⚠️ **This Ticket is now INACTIVE.** It was created, given its first Modality, then "
                      "switched back to draft/inactive for your review — this is expected. To add more "
                      "Modalities or make further changes, first **activate it manually inside Travel "
                      "Compositor**, then come back and use 'Add new Modality to existing Ticket'.")
            if st.button("🆕 Start a new Ticket", type="primary", key="tk_new_import_inactive"):
                keep_client = st.session_state.client
                keep_suppliers = st.session_state.suppliers_cache
                keep_product_type = st.session_state.product_type
                keep_tool = st.session_state["active_tool"] if "active_tool" in st.session_state else None
                st.session_state.clear()
                st.session_state.client = keep_client
                st.session_state.suppliers_cache = keep_suppliers
                st.session_state.product_type = keep_product_type
                st.session_state.active_tool = keep_tool
                st.rerun()
        else:
            fcol1, fcol2 = st.columns(2)
            with fcol1:
                if st.button("🆕 Start a new Ticket", type="primary", key="tk_new_import_active"):
                    keep_client = st.session_state.client
                    keep_suppliers = st.session_state.suppliers_cache
                    keep_product_type = st.session_state.product_type
                    keep_tool = st.session_state["active_tool"] if "active_tool" in st.session_state else None
                    st.session_state.clear()
                    st.session_state.client = keep_client
                    st.session_state.suppliers_cache = keep_suppliers
                    st.session_state.product_type = keep_product_type
                    st.session_state.active_tool = keep_tool
                    st.rerun()
            with fcol2:
                if st.button("➕ Add another Modality to this same Ticket", key="tk_add_modality_followup"):
                    prefill_ticket_code = st.session_state.tk_just_published_code
                    prefill_supplier_id = st.session_state.tk_just_published_supplier_id
                    keep_client = st.session_state.client
                    keep_suppliers = st.session_state.suppliers_cache
                    keep_product_type = st.session_state.product_type
                    keep_tool = st.session_state["active_tool"] if "active_tool" in st.session_state else None
                    st.session_state.clear()
                    st.session_state.client = keep_client
                    st.session_state.suppliers_cache = keep_suppliers
                    st.session_state.product_type = keep_product_type
                    st.session_state.active_tool = keep_tool
                    st.session_state.tk_cfg_action = "add_option"
                    st.session_state.tk_cfg_supplier_id = prefill_supplier_id
                    st.session_state.tk_cfg_existing_ticket_code = prefill_ticket_code
                    st.session_state.tk_step1_confirmed = True
                    st.rerun()


# ============================================================================
# TRANSFER FLOW
# Confirmed design (extensive back-and-forth with the product owner, working
# from the real Swagger + 13 real GET examples + 3 real supplier rate
# sheets): unlike ClosedTour/Ticket, there is no explicit create-vs-update
# action to pick upfront - Travel Compositor's Transfer schema has no
# human-assigned code, so "is this a new transfer or an update to an
# existing one" is answered PER ITEM by transfer_matcher.py's matching step
# (app-tracked id first, departure/arrival similarity as a human-confirmed
# fallback), not by a top-level radio button. A rate sheet routinely
# describes many distinct transfer products at once (per-route, per-class,
# sometimes per guide-language table) - see detect_transfer_products - so
# this always runs as a batch/queue review, the same pattern already proven
# for multi-excursion Ticket documents.
# ============================================================================

def render_direction_image_section(current, data, product_type, widget_key):
    """Shared by Transfer and Transport's review screens: shows the image
    supplier_images.resolve_and_host_image just picked for this route's detected direction
    (or explains why nothing was picked), plus a manual override the human can always type
    over it with.

    `current` must carry "_image_direction" - set once at extraction time (see the
    resolve_and_host_image call in render_multi_transfer_flow / render_multi_transport_flow),
    the classified direction or None. `data["image_urls"]` holds the resolved (or manually
    overridden) URL as a one-item list, same shape every other product type already uses.

    `widget_key` MUST start with the calling flow's own prefix (e.g. "xtf_"/"xtp_") - see
    _clear_batch_widget_state's docstring: it sweeps stale per-item widget state by prefix
    whenever a queue slot gets reused by a different item (skip, or a fresh batch reusing
    idx==0), and a key outside that prefix would silently escape the sweep, leaking one
    route's typed-in image URL onto a completely different route in the same slot.

    CONFIRMED RULE (product owner, 2026-08-28): never guess when the route can't be
    classified - warn instead so a human sets it by hand."""
    st.markdown("##### Image")
    direction = current.get("_image_direction")
    current_url = (data.get("image_urls") or [None])[0]

    if direction is None:
        st.warning(
            "⚠️ Couldn't tell whether this route goes Airport/Harbor → Hotel or Hotel → "
            "Airport/Harbor - \"Airport\"/\"Harbor\" needs to appear in exactly ONE of the "
            "two location names, and it appears in both or neither here. No image was "
            "auto-picked - paste one below by hand, or fix the route names above."
        )
    elif current_url:
        st.image(current_url, width=200)
        st.caption(f"Auto-picked from this supplier's saved \"{supplier_images.DIRECTION_LABELS[direction]}\" "
                  f"{product_type} image - replaces whatever image is already live when you publish.")
    elif current.get("_image_upload_error"):
        # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): an image WAS already saved for
        # this supplier/direction - the R2 upload of that saved image just failed (bad
        # credentials, R2 down, network error) - see resolve_and_host_image's own docstring on
        # why this is a genuinely different situation from "nothing saved yet" and must not be
        # shown the same way. Re-uploading the same image in Setup would not fix this; the
        # underlying R2 problem needs fixing (or the operator can still paste a URL by hand
        # below as a workaround for this one item).
        st.error(
            f"🔴 An image IS saved for this supplier's **{supplier_images.DIRECTION_LABELS[direction]}** "
            f"{product_type} direction, but hosting it just failed: {current['_image_upload_error']}. "
            f"Re-uploading it in Setup won't fix this - it's an R2 connection/credentials problem, not "
            f"a missing image. Paste a URL below by hand for just this one, or fix R2 and re-open this item."
        )
    else:
        st.info(
            f"ℹ️ Detected direction: **{supplier_images.DIRECTION_LABELS[direction]}** - but no "
            f"image is saved yet for this supplier/direction. Upload one in Step 2's setup "
            f"section above, or paste a URL below by hand for just this one."
        )

    manual_url = st.text_input(
        "Image URL (overrides the auto-picked one above; leave blank to keep it)",
        value="", key=widget_key,
        placeholder="https://...",
    ).strip()
    if manual_url:
        data["image_urls"] = [manual_url]


def render_transfer_flow(client):
    """
    Transfer wizard entry point: Supplier + Currency + release window, then
    Input Source, then hands off to render_multi_transfer_flow for
    detection/review/matching/publish. Uses tf_-prefixed session_state keys
    throughout to avoid any collision with the ClosedTour/Ticket flows.
    """
    if "tf_step1_confirmed" not in st.session_state:
        st.session_state.tf_step1_confirmed = False

    st.header("Transfer — Step 2: Supplier & defaults")

    if st.session_state.tf_step1_confirmed:
        st.success(f"✅ Supplier ID: **{st.session_state.tf_cfg_supplier_id}** | "
                   f"Currency: **{st.session_state.tf_cfg_currency}**")
        if st.button("🔄 Change supplier / defaults", key="tf_change_action"):
            st.session_state.tf_step1_confirmed = False
            st.rerun()
    else:
        if st.session_state.suppliers_cache is None:
            with st.spinner("Loading supplier list from Travel Compositor..."):
                try:
                    st.session_state.suppliers_cache = client.get_all_suppliers()
                except Exception as e:
                    st.error(f"❌ Couldn't load the supplier list: {friendly_error_message(e)}")
                    st.session_state.suppliers_cache = []

        supplier_id_choice = None
        if st.session_state.suppliers_cache:
            momira_suppliers = [
                s for s in st.session_state.suppliers_cache
                if (s.get("commercialName") or s.get("legalName") or "").strip().lower().startswith("momira_")
            ]
            if not momira_suppliers:
                st.error("🚫 No suppliers starting with 'Momira_' were found in this account - can't continue.")
            else:
                supplier_options = {
                    f"{s.get('commercialName') or s.get('legalName')} — ID {s.get('id')}": s.get("id")
                    for s in momira_suppliers
                }
                selected_label = st.selectbox("Select Supplier", list(supplier_options.keys()), key="tf_supplier_select")
                supplier_id_choice = str(supplier_options[selected_label])
            if st.button("🔄 Refresh supplier list", key="tf_refresh_suppliers"):
                st.session_state.suppliers_cache = None
                st.rerun()
        else:
            st.error("Could not load the supplier list from Travel Compositor.")
            with st.expander("⚠️ Emergency manual entry"):
                st.caption("Only use this if the supplier list above failed to load - type the numeric Travel Compositor supplier ID directly.")
                supplier_id_choice = st.text_input("Supplier ID (numeric)", value="", key="tf_supplier_manual")

        currency_in = st.selectbox("Currency", CURRENCY_OPTIONS, key="tf_currency")
        st.caption("Only used when CREATING a new transfer. Updating an existing one keeps the currency it already has — a rate sheet changes prices, not the currency a live contract is denominated in.")
        release_days_in = st.number_input(
            "Release Contract (days before arrival this transfer becomes bookable)",
            min_value=0, value=5, key="tf_release_days",
            help="Confirmed real field name is releaseContract - confirmed real value seen in live data = 5."
        )

        if st.button("➡️ Continue to Step 3", type="primary", disabled=not supplier_id_choice, key="tf_continue1"):
            st.session_state.tf_cfg_supplier_id = supplier_id_choice
            st.session_state.tf_cfg_currency = currency_in
            st.session_state.tf_cfg_release_days = release_days_in
            st.session_state.tf_step1_confirmed = True
            st.rerun()
        return

    supplier_id = st.session_state.tf_cfg_supplier_id
    currency = st.session_state.tf_cfg_currency
    release_days = st.session_state.tf_cfg_release_days

    # Reachable here, with no document loaded, because "the pickup point for all of this
    # supplier's transfers changed" is a standalone maintenance task - not something you
    # should have to start a batch upload to record.
    service_notes.render_standing_note_editor(supplier_id, "Transfer", key_suffix="_setup")
    cancellation_links.render_cancellation_link_editor(supplier_id, "Transfer", key_suffix="_setup")
    supplier_images.render_supplier_image_editor(supplier_id, "Transfer", key_suffix="_setup")

    st.header("Transfer — Step 3: Input Source")
    st.caption("Rate sheets commonly describe MANY distinct transfer products at once (per route, per "
              "vehicle class, sometimes repeated per guide language) - all of them get detected and "
              "queued for review below, same as multi-excursion Ticket documents.")
    tf_url = st.text_input("Product page URL (optional)", key="tf_url")
    tf_files = st.file_uploader("Upload document(s) (optional)", type=["pdf", "docx", "xlsx"],
                                accept_multiple_files=True, key="tf_files")
    tf_hint = st.text_input(
        "Instruction (optional)", key="tf_hint",
        placeholder="e.g. only the Hurghada section, private transfers only",
        help="Plain English, and it steers BOTH steps: which products get detected, and how "
             "each one is read. It overrides the tool's own judgement.")

    render_multi_transfer_flow(client, supplier_id, currency, release_days, tf_url, tf_files, tf_hint)


def render_multi_transfer_flow(client, supplier_id, currency, release_days, tf_url, tf_files, tf_hint):
    """
    Batch/queue flow for Transfers - mirrors render_multi_ticket_flow's
    proven 3-phase pattern (gather -> select/prepare_queue -> review one at
    a time), adapted for Transfers' real structural differences: no
    create-vs-update action to pre-select (matching decides that per item -
    see transfer_matcher.py), occupancy-tiered pricing instead of simple
    passenger-type pricing, and a mandatory human-confirmed matching step
    before any publish.
    """
    if "xtf_phase" not in st.session_state:
        st.session_state.xtf_phase = "gather"

    # ------------------------------------------------------------------
    # PHASE 1: detect distinct transfer products from the source provided above
    # ------------------------------------------------------------------
    if st.session_state.xtf_phase == "gather":
        if not (tf_url or tf_files):
            st.info("Provide a URL and/or upload document(s) above, then click below.")
        if st.button("🔎 Detect Transfer Products", disabled=not (tf_url or tf_files)):
            with st.spinner("Gathering content and detecting distinct transfer products..."):
                try:
                    combined_parts = []
                    if tf_url:
                        page_text, page_text_err = _fetch_url_text_safe(tf_url)
                        if page_text is not None:
                            combined_parts.append(f"--- SOURCE: WEB PAGE ({tf_url}) ---\n{page_text}")
                        else:
                            st.warning(f"⚠️ Couldn't fetch the product page URL: {page_text_err}.")
                    for uploaded in (tf_files or []):
                        suffix = os.path.splitext(uploaded.name)[1]
                        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                            tmp.write(uploaded.getbuffer())
                            tmp_path = tmp.name
                        _doc_text = extract_raw_text(tmp_path)
                        _scan_warning = document_reader_scanned_warning(tmp_path, _doc_text)
                        if _scan_warning:
                            st.session_state.setdefault("_scanned_doc_warnings", []).append(_scan_warning)
                        combined_parts.append(f"--- SOURCE: UPLOADED DOCUMENT ({uploaded.name}) ---\n{_doc_text}")
                        os.remove(tmp_path)

                    if not combined_parts:
                        st.error("Nothing to extract - the product page URL couldn't be fetched and no document(s) were provided.")
                        st.stop()

                    raw_text = "\n\n".join(combined_parts)
                    detected = detect_transfer_products(raw_text, human_hint=tf_hint)

                    candidates = []
                    for t in detected:
                        candidates.append({
                            "label": t.get("label", ""), "service_name": t.get("service_name", ""),
                            "departure_hint": t.get("departure_hint", ""), "arrival_hint": t.get("arrival_hint", ""),
                            "selected": True, "is_genuine_multiple": True,
                        })
                    if not candidates:
                        candidates = [{"label": "", "service_name": "", "departure_hint": "", "arrival_hint": "",
                                      "selected": True, "is_genuine_multiple": False}]

                    # Both directions, always - see ensure_return_candidates.
                    candidates, _returns_added = ensure_return_candidates(candidates)
                    if _returns_added:
                        st.session_state.xtf_auto_returns = _returns_added
                    st.session_state.xtf_raw_text = raw_text
                    st.session_state.xtf_candidates = candidates
                    st.session_state.xtf_phase = "prepare_queue"
                    st.rerun()
                except Exception as e:
                    st.error(f"Detection failed: {friendly_error_message(e)}")
        return

    # ------------------------------------------------------------------
    # PHASE 2: explicitly SELECT which transfer products to review/publish
    # ------------------------------------------------------------------
    if st.session_state.xtf_phase == "prepare_queue":
        candidates = st.session_state.xtf_candidates
        single_transfer = len(candidates) == 1

        if single_transfer:
            st.subheader("Set up this Transfer")
            if (candidates[0].get("label") or "").strip():
                st.caption("Only one distinct transfer product was found in this document.")
            else:
                st.warning("**No transfer products were detected in this document.** Name the one "
                           "you want below, or go back and check the document actually contains "
                           "transfer rates.")
                def _tf_accept(found):
                    st.session_state.xtf_candidates = [
                        {"label": t.get("label", ""), "service_name": t.get("service_name", ""),
                         "departure_hint": t.get("departure_hint", ""),
                         "arrival_hint": t.get("arrival_hint", ""), "selected": True,
                         "is_genuine_multiple": True}
                        for t in found]
                    st.session_state.xtf_candidates, _n = ensure_return_candidates(
                        st.session_state.xtf_candidates)
                    if _n:
                        st.session_state.xtf_auto_returns = _n

                render_detection_diagnosis("transfer")
                render_empty_detection_retry(st.session_state.xtf_raw_text, "transfer", "xtf",
                                             detect_transfer_products, _tf_accept)
                st.markdown("---")
                st.caption("Or name one route by hand below — the closer to the document's own "
                          "wording, the better.")
        else:
            st.subheader(f"{len(candidates)} distinct transfer products detected - choose which to review")
            st.caption("Each ticked row becomes its own separate Transfer, reviewed one at a time next. "
                      "Guide-language variants are already folded into each row, not listed separately.")

        if st.session_state.get("xtf_auto_returns"):
            st.info(f"↔️ {st.session_state['xtf_auto_returns']} return direction(s) were added "
                    f"automatically — every route is created both ways. Untick any you don't sell.")
        render_candidate_filter(candidates, "xtf", "transfer")

        for i, cand in enumerate(candidates):
            ccol1, ccol2 = st.columns([1, 5])
            with ccol1:
                cand["selected"] = st.checkbox("Include", value=cand["selected"], key=f"xtf_sel_{i}")
            with ccol2:
                cand["label"] = st.text_input(
                    "Which route?", value=cand["label"], key=f"xtf_label_{i}",
                    placeholder="e.g. Private Transfer: HRG Airport to Sahl Hashish",
                    help="Name ONE route the way the document writes it — the service or class, "
                         "then where it goes from and to. This is what the AI is told to look for "
                         "when it reads the document for this row, so the closer it is to the "
                         "document's own wording the better.")

        if st.button("➕ Add another transfer product manually"):
            candidates.append({"label": "", "service_name": "", "departure_hint": "", "arrival_hint": "",
                              "selected": True, "is_genuine_multiple": False})
            st.rerun()

        new_queue = [
            {"label": c["label"], "service_name": c["service_name"], "departure_hint": c["departure_hint"],
             "arrival_hint": c["arrival_hint"], "data": None, "match": None, "publish_status": None}
            for c in candidates if c["selected"]
        ]

        st.caption(f"**{len(new_queue)}** transfer(s) ready to review." if new_queue else
                  "Select at least one transfer product to continue.")

        if st.button("➡️ Start Reviewing", type="primary", disabled=not new_queue):
            st.session_state.xtf_queue = new_queue
            st.session_state.xtf_queue_index = 0
            st.session_state.xtf_phase = "reviewing"
            st.rerun()
        return

    # ------------------------------------------------------------------
    # PHASE 3: review + match + publish each transfer, one at a time
    # ------------------------------------------------------------------
    if st.session_state.xtf_phase == "reviewing":
        idx = st.session_state.xtf_queue_index
        queue = st.session_state.xtf_queue
        current = queue[idx]

        st.subheader(f"Reviewing transfer {idx + 1} of {len(queue)}: {current['label'] or '(unnamed)'}")

        render_batch_bulk_controls(queue, "xtf_queue", "xtf_queue_index", "xtf_phase",
                                   ["xtf_phase", "xtf_raw_text", "xtf_candidates", "xtf_queue",
                                    "xtf_queue_index"],
                                   ["xtf_"] + SHARED_WIDGET_STATE_PREFIXES, "transfer", "xtf")

        if st.button("🔙 Start over - upload a different document", key=f"xtf_cancel_{idx}"):
            for key in ["xtf_phase", "xtf_raw_text", "xtf_candidates", "xtf_queue", "xtf_queue_index"]:
                st.session_state.pop(key, None)
            _clear_batch_widget_state(["xtf_"] + SHARED_WIDGET_STATE_PREFIXES)
            st.rerun()

        if current["data"] is None:
            with st.spinner("Extracting this transfer's details..."):
                try:
                    transfer_hint = None
                    if current.get("service_name") or current.get("departure_hint") or current.get("arrival_hint"):
                        transfer_hint = (f"{current.get('service_name', '')} - "
                                         f"{current.get('departure_hint', '')} to {current.get('arrival_hint', '')}")
                    elif current["label"]:
                        transfer_hint = current["label"]
                    current["data"] = extract_transfer_data(st.session_state.xtf_raw_text,
                                                             transfer_hint=transfer_hint,
                                                             human_hint=with_learned_guidance(
                                                                 supplier_id, "Transfer", tf_hint))
                except Exception as e:
                    st.error(f"Extraction failed for this transfer: {friendly_error_message(e)}")
                    current["data"] = {}
            # Snapshot the raw extractor output and pre-fill anything this supplier's past
            # corrections have already taught. Must happen HERE - inside the run that
            # extracted - so the snapshot is the extractor's own output, before a human has
            # touched it. Snapshotting later would compare corrected values against
            # corrected values and learn nothing at all.
            extraction_memory.prepare(supplier_id, "Transfer", current)
            # Only fills in when this document didn't state its own cancellation terms -
            # see apply_cancellation_link_default's docstring. Must run here, once, right
            # after extraction - not inside the review widgets below, which rerun on every
            # interaction and would re-inject the link even after a human deliberately
            # cleared the table.
            current["_cancellation_link_scope"] = cancellation_links.apply_cancellation_link_default(
                current["data"], supplier_id, "Transfer")
            # Auto-picks this supplier's saved Airport/Harbor<->Hotel image for the route's
            # detected direction - see supplier_images.resolve_and_host_image's docstring.
            # Runs once, here, right after extraction - not on every rerender, so a human's
            # manual override below (typed once) isn't clobbered on the next widget interaction.
            _si_url, current["_image_direction"], current["_image_upload_error"] = (
                supplier_images.resolve_and_host_image(
                    supplier_id, "Transfer",
                    current["data"].get("departure_name"), current["data"].get("arrival_name")))
            if _si_url:
                current["data"]["image_urls"] = [_si_url]

        data = current["data"]
        key_suffix = f"_{idx}"
        extraction_memory.render_applied_banner(current.get("_learned_applied") or [])

        render_skip_item_button(
            current["label"] or "(unnamed transfer)", queue, idx, "xtf_queue", "xtf_queue_index",
            ["xtf_phase", "xtf_raw_text", "xtf_candidates", "xtf_queue", "xtf_queue_index"],
            f"xtf_skip_{idx}", widget_state_prefixes=["xtf_"] + SHARED_WIDGET_STATE_PREFIXES,
        )
        # render_skip_item_button reruns immediately on click, so if we got
        # here the item is still in the queue - safe to keep rendering it.

        st.markdown("#### Which existing Transfer does this update, if any?")
        st.caption("Travel Compositor has no human-assigned code for Transfers, so this app tracks its own "
                  "id->route mapping locally, falling back to a departure/arrival similarity match against "
                  "this supplier's full live list - either way, YOU always confirm before anything publishes.")

        # CONFIRMED FIX (real bug found via audit): a match check used to be cached forever once
        # clicked, even after Departure/Arrival were edited afterward - a human could end up
        # confirming a candidate that was matched against now-outdated route text. Fingerprint the
        # route text the check was run against, and invalidate the cached result the moment it
        # no longer matches the CURRENT route text, forcing a fresh check.
        current_route_fingerprint = f"{data.get('departure_name', '')}::{data.get('arrival_name', '')}"
        if current.get("match_route_fingerprint") != current_route_fingerprint:
            current["match_result"] = None
            current["match_route_fingerprint"] = current_route_fingerprint

        if st.button("🔎 Check for a matching existing transfer", key=f"xtf_checkmatch_{idx}"):
            with st.spinner("Checking..."):
                current["match_result"] = transfer_matcher.resolve_transfer_match(
                    client, supplier_id, data.get("departure_name", ""), data.get("arrival_name", "")
                )
                current["match_route_fingerprint"] = current_route_fingerprint

        match_result = current.get("match_result")
        chosen_existing_id = None
        if match_result:
            if match_result.get("fetch_error"):
                st.warning(f"⚠️ Couldn't fetch this supplier's existing transfers to check for a match: "
                          f"{match_result['fetch_error'].get('message', match_result['fetch_error'])}. "
                          f"Will create as new unless you already know the id below.")
            if match_result.get("tracked_id"):
                tracked_id = match_result["tracked_id"]
                # CONFIRMED REAL RULE (product owner): a tracked/remembered match must not
                # silently pre-apply - fetch it and show its key details right here (not
                # lazily, after the checkbox) so the human actually looks at what they're
                # about to update, same safety bar ClosedTour/Ticket already enforce (they
                # force an explicit fetch-and-glance before an update can proceed). Default
                # the confirm checkbox to UNCHECKED, so applying it is a deliberate choice
                # made after seeing the details, not a pre-ticked box someone breezes past.
                if current.get("_tracked_snapshot_id") != tracked_id:
                    with st.spinner(f"Fetching {tracked_id} to show you what it currently looks like..."):
                        current["_tracked_snapshot"] = client.get_transfer(supplier_id, tracked_id)
                    current["_tracked_snapshot_id"] = tracked_id
                tracked_snapshot = current.get("_tracked_snapshot")
                if isinstance(tracked_snapshot, dict) and "error" not in tracked_snapshot:
                    st.success(f"✅ This app has already created/confirmed a match for this exact route before: "
                              f"**{tracked_id}**.")
                    st.caption(f"Existing record: departure **{(tracked_snapshot.get('departure') or {}).get('name', '?')}**, "
                              f"arrival **{(tracked_snapshot.get('arrival') or {}).get('name', '?')}**, "
                              f"currency **{tracked_snapshot.get('currency', '?')}**, "
                              f"valid **{tracked_snapshot.get('startDate', '?')}** to **{tracked_snapshot.get('endDate', '?')}**.")
                    use_tracked = st.checkbox("Yes, this is the right one - update it", value=False,
                                              key=f"xtf_usetracked_{idx}")
                    chosen_existing_id = tracked_id if use_tracked else None
                else:
                    st.warning(f"⚠️ This app remembers a match for this route (**{tracked_id}**) but couldn't "
                              f"fetch it just now to confirm it still exists - won't auto-apply it blind. "
                              f"Click Check again, or enter/confirm manually if you know it's still correct.")
            elif match_result.get("fallback_candidates"):
                options = ["Create as a NEW transfer"] + [
                    f"Update: {c['name'] or '(unnamed)'} — {c['transfer_id']} "
                    f"(departure: {c['departure_name']!r}, arrival: {c['arrival_name']!r}, match score {c['score']})"
                    for c in match_result["fallback_candidates"]
                ]
                picked = st.radio("Pick one - nothing publishes until you explicitly confirm a match:",
                                  options, key=f"xtf_matchpick_{idx}")
                if picked != options[0]:
                    picked_idx = options.index(picked) - 1
                    chosen_existing_id = match_result["fallback_candidates"][picked_idx]["transfer_id"]
            else:
                st.info("No existing transfers found for this supplier - will create as new.")

        current["confirmed_existing_id"] = chosen_existing_id

        # CONFIRMED RULE (product owner): "Transfers which are getting updated, have already
        # allowed bookings until 2049" - an update must be surgical, not a full overwrite. When
        # updating an existing transfer, fetch its current live record so build_transfer_payload
        # can merge into it (preserving its existing startDate/endDate/images/properties) rather
        # than clobbering them with whatever this rate-sheet document happens to say. Cached per
        # id so re-fetches don't happen on every widget rerun, only when the chosen id changes.
        existing_transfer_snapshot = None
        if chosen_existing_id:
            if current.get("existing_snapshot_id") != chosen_existing_id:
                with st.spinner(f"Fetching existing transfer {chosen_existing_id} to merge into..."):
                    snapshot_result = client.get_transfer(supplier_id, chosen_existing_id)
                if isinstance(snapshot_result, dict) and "error" in snapshot_result:
                    st.warning(f"⚠️ Couldn't fetch existing transfer {chosen_existing_id} to merge into "
                              f"({snapshot_result.get('message', snapshot_result)}) - this update will use the "
                              f"document's own dates/images/properties instead of preserving the existing ones.")
                    current["existing_snapshot"] = None
                else:
                    current["existing_snapshot"] = snapshot_result
                current["existing_snapshot_id"] = chosen_existing_id
            existing_transfer_snapshot = current.get("existing_snapshot")
        else:
            current["existing_snapshot"] = None
            current["existing_snapshot_id"] = None

        st.markdown("#### Route")
        rcol1, rcol2 = st.columns(2)
        with rcol1:
            editable_field("Departure", data, "departure_name", key_suffix=key_suffix)
        with rcol2:
            editable_field("Arrival", data, "arrival_name", key_suffix=key_suffix)
        data["is_zone_based"] = st.checkbox(
            "This is a named AREA covering multiple localities (zone-based routing), not one specific point",
            value=bool(data.get("is_zone_based", False)), key=f"xtf_zone_{idx}",
            help="Resolves against this supplier's Transfer Zones (real TC zone IDs) instead of raw GPS "
                 "coordinates - use this for area-style routes like 'South Bali (Tuban/Kuta/...)'."
        )

        st.markdown("#### Service")
        scol1, scol2, scol3 = st.columns(3)
        with scol1:
            editable_field("Service name", data, "service_name", key_suffix=key_suffix)
        with scol2:
            editable_field("Class / tier", data, "class_or_product_type", key_suffix=key_suffix)
        with scol3:
            editable_field("Vehicle hint", data, "vehicle_hint", key_suffix=key_suffix)

        ccol1, ccol2, ccol3, ccol4 = st.columns(4)
        with ccol1:
            data["charge_unit"] = st.selectbox(
                "Charge unit", ["per_pax", "per_service"],
                index=0 if data.get("charge_unit", "per_pax") != "per_service" else 1,
                key=f"xtf_chargeunit_{idx}",
                format_func=lambda v: "Per person" if v == "per_pax" else "Flat price for the whole vehicle",
                help="\"Per person\" charges by headcount. \"Flat price for the whole vehicle\" is one "
                     "price no matter how many people are in it."
            )
        with ccol2:
            # CONFIRMED REAL RULE (product owner): once a match against an existing transfer is
            # confirmed above, currency is never asked again - build_transfer_payload already
            # locks it to the existing record's own currency via _locked_on_update regardless of
            # what's in data["currency"], so re-asking here was a pointless question with no
            # effect. Only shown for a genuine create, where there's no existing currency yet.
            if chosen_existing_id and existing_transfer_snapshot:
                data["currency"] = existing_transfer_snapshot.get("currency") or data.get("currency")
                st.text_input("Currency", value=data["currency"] or "(existing)", disabled=True,
                              key=f"xtf_currency_locked_{idx}",
                              help="Inherited from the existing transfer being updated - can't be changed.")
            else:
                data["currency"] = st.selectbox(
                    "Currency", CURRENCY_OPTIONS,
                    index=CURRENCY_OPTIONS.index(data["currency"]) if data.get("currency") in CURRENCY_OPTIONS else 0,
                    key=f"xtf_currency_{idx}"
                )
        with ccol3:
            data["min_occupancy"] = st.number_input("Min occupancy", min_value=1, value=int(data.get("min_occupancy") or 1), key=f"xtf_minocc_{idx}")
        with ccol4:
            data["max_occupancy"] = st.number_input("Max occupancy", min_value=1, value=int(data.get("max_occupancy") or 4), key=f"xtf_maxocc_{idx}")

        st.markdown("#### Pricing by occupancy")
        # CONFIRMED REAL RULE (product owner): "when the document says min. 2 Pax, we can offer
        # this for 1 Pax by simply increasing the cost - 1 pax pays the price what 2 pax would
        # pay together." When the document states a minimum party size for its per-person rate
        # (e.g. "valid for Min.2 pax"), enter it here and a 1-pax bracket at that minimum's total
        # is added automatically at publish time - flagged for you to check, never invented
        # silently. Leave at 1 when the document states no minimum.
        data["min_billable_pax"] = st.number_input(
            "Minimum billable pax (leave at 1 if the document states no minimum party size)",
            min_value=1, max_value=9, value=int(data.get("min_billable_pax") or 1),
            key=f"xtf_minbillable_{idx}",
            help="A per-person rate valid from e.g. 2 pax up means a solo traveller pays the "
                 "2-pax total, not half of it - set this to 2 and that 1-pax bracket is added "
                 "automatically. Only applies to per-pax pricing, ignored for per-service.")
        st.caption("Top-level basePrice is the DEFAULT rate; only add a row here for an occupancy whose "
                  "rate genuinely DIFFERS from the default - unless the document gives a fully explicit "
                  "rate per bracket (like a 1/2/3-5/6-8/9-14 table), in which case list every tier "
                  "explicitly. Leave Child/Infant price blank (not 0) when the document doesn't state one.")
        occ_df = pd.DataFrame(data.get("occupancy_price_tiers") or [{"occupancy": 1, "price": 0.0, "child_price": None, "infant_price": None}])
        for col in ["occupancy", "price", "child_price", "infant_price"]:
            if col not in occ_df.columns:
                occ_df[col] = None

        def _save_occ_tiers(edited_df):
            # CONFIRMED FIX (real bug found via audit): the old "skip only if BOTH occupancy and
            # price are blank" check let a row with occupancy filled in but price left blank
            # survive as NaN -> _safe_float silently coerced it to 0.0 downstream, meaning a
            # transfer could publish with a genuinely FREE tier and no warning anywhere. Now a
            # half-filled row (one of the two blank) is dropped and flagged, not silently zeroed.
            rows = []
            dropped_incomplete = 0
            for _, row in edited_df.iterrows():
                occ_blank = pd.isna(row.get("occupancy"))
                price_blank = pd.isna(row.get("price"))
                if occ_blank and price_blank:
                    continue
                if occ_blank or price_blank:
                    dropped_incomplete += 1
                    continue
                rows.append({
                    "occupancy": _safe_int(row.get("occupancy"), fallback=1),
                    "price": _safe_float(row.get("price"), fallback=0.0),
                    "child_price": None if pd.isna(row.get("child_price")) else _safe_float(row.get("child_price"), fallback=0.0),
                    "infant_price": None if pd.isna(row.get("infant_price")) else _safe_float(row.get("infant_price"), fallback=0.0),
                })
            data["occupancy_price_tiers"] = rows
            if dropped_incomplete:
                st.warning(f"⚠️ Dropped {dropped_incomplete} occupancy row(s) that had only an occupancy OR "
                          f"only a price filled in, not both - fill in both fields to keep a row.")

        editable_table("Occupancy price tiers", occ_df, f"xtf_occ_{idx}", on_save=_save_occ_tiers)
        editable_field("Blanket child/infant rule (if the document states one instead of per-row prices)",
                       data, "child_infant_rule_text", key_suffix=key_suffix)

        st.markdown("#### Optional extras (additionalServices) — child seats, non-default guide languages, etc.")
        add_svc_df = pd.DataFrame(data.get("additional_services") or [{"name": "", "price": 0.0, "currency": currency, "max_quantity": 1, "on_request": False}])
        for col in ["name", "price", "currency", "max_quantity", "on_request"]:
            if col not in add_svc_df.columns:
                add_svc_df[col] = None

        def _save_add_svc(edited_df):
            rows = []
            for _, row in edited_df.iterrows():
                if not (row.get("name") or "").strip():
                    continue
                rows.append({
                    "name": str(row.get("name") or "").strip(),
                    "price": _safe_float(row.get("price"), fallback=0.0),
                    "currency": row.get("currency") or currency,
                    "max_quantity": _safe_int(row.get("max_quantity"), fallback=1),
                    "on_request": bool(row.get("on_request", False)),
                })
            data["additional_services"] = rows

        editable_table("Optional / on-request extras", add_svc_df, f"xtf_addsvc_{idx}", on_save=_save_add_svc)

        st.markdown("#### Guide-language surcharges (driver-only is always the base — no guide by default)")
        lang_df = pd.DataFrame(data.get("guide_language_surcharges") or [{"language": "", "surcharge_estimate": 0.0}])
        for col in ["language", "surcharge_estimate"]:
            if col not in lang_df.columns:
                lang_df[col] = None

        def _save_lang_surcharges(edited_df):
            rows = []
            for _, row in edited_df.iterrows():
                if not (row.get("language") or "").strip():
                    continue
                rows.append({
                    "language": str(row.get("language") or "").strip(),
                    "surcharge_estimate": _safe_float(row.get("surcharge_estimate"), fallback=0.0),
                })
            data["guide_language_surcharges"] = rows

        editable_table("Other guide languages (each becomes its own optional extra)", lang_df,
                       f"xtf_langsurcharge_{idx}", on_save=_save_lang_surcharges)

        st.markdown("#### Mandatory supplements — genuinely unconditional charges only")
        st.caption("Never put a location-conditional cost here (e.g. a harbor-only pickup fee on a route "
                  "that also serves airport pickups) - that belongs in the location note below instead, "
                  "since this schema can't apply a charge conditionally by pickup point.")
        st.caption("**type** is PERCENT or ABSOLUTE. For a percentage, put the percentage itself in "
                  "**amount** (50 for a 50% night surcharge) - Travel Compositor applies it to the base "
                  "price, so it must never be converted into a currency figure here. **start_time / "
                  "end_time** are 24-hour and may cross midnight (22:00 → 08:00 is correct as written). "
                  "Leave **start_date / end_date** empty unless the surcharge itself is seasonal - empty "
                  "means it inherits this transfer's own validity window.")
        _supp_cols = ["name", "amount", "type", "start_time", "end_time", "start_date", "end_date", "notes"]
        supp_df = pd.DataFrame(data.get("mandatory_supplements") or [
            {"name": "", "amount": 0.0, "type": "ABSOLUTE", "start_time": "", "end_time": "",
             "start_date": "", "end_date": "", "notes": ""}])
        for col in _supp_cols:
            if col not in supp_df.columns:
                supp_df[col] = "" if col not in ("amount",) else 0.0
        supp_df = supp_df[_supp_cols]

        def _save_supplements(edited_df):
            rows, bad_type = [], False
            for _, row in edited_df.iterrows():
                if not (row.get("name") or "").strip():
                    continue
                raw_type = str(row.get("type") or "ABSOLUTE").strip().upper()
                if raw_type not in ("PERCENT", "ABSOLUTE"):
                    # Never silently coerce: a supplement meant as 50% that quietly becomes
                    # ABSOLUTE would charge 50 currency units instead of half the fare.
                    bad_type = True
                    continue
                rows.append({
                    "name": str(row.get("name") or "").strip(),
                    "amount": _safe_float(row.get("amount"), fallback=0.0),
                    "type": raw_type,
                    "start_time": str(row.get("start_time") or "").strip(),
                    "end_time": str(row.get("end_time") or "").strip(),
                    "start_date": str(row.get("start_date") or "").strip(),
                    "end_date": str(row.get("end_date") or "").strip(),
                    "notes": str(row.get("notes") or ""),
                })
            data["mandatory_supplements"] = rows
            st.session_state[f"_xtf_supp_bad_type_{idx}"] = bad_type

        editable_table("Mandatory supplements", supp_df, f"xtf_supp_{idx}", on_save=_save_supplements)
        if st.session_state.get(f"_xtf_supp_bad_type_{idx}"):
            st.warning("⚠️ A supplement row was skipped because its **type** wasn't PERCENT or ABSOLUTE. "
                      "It was left out rather than guessed - a 50% surcharge saved as ABSOLUTE would "
                      "charge 50 in currency instead of half the fare.")

        st.markdown("#### Notes, validity & cancellation")
        editable_field("Location note (e.g. a harbor-only pickup fee) — goes to Voucher Remarks, never applied to price",
                       data, "location_notes", key_suffix=key_suffix)
        editable_field("Description", data, "description", key_suffix=key_suffix)
        editable_field("Pickup information", data, "pickup_information", key_suffix=key_suffix)

        dcol1, dcol2 = st.columns(2)
        with dcol1:
            editable_field("Start date (DD/MM/YYYY)", data, "start_date", key_suffix=key_suffix)
        with dcol2:
            editable_field("End date (DD/MM/YYYY)", data, "end_date", key_suffix=key_suffix)

        render_direction_image_section(current, data, "Transfer", f"xtf_image_manual_{idx}")

        if current.get("_cancellation_link_scope"):
            st.caption(f"ℹ️ This document didn't state its own cancellation terms - the table below "
                      f"was filled in from {current['_cancellation_link_scope']}. Edit or clear it if "
                      f"this product needs different terms.")
        render_cancellation_policy_editor(data, f"xtf_cancel_{idx}")
        editable_field("Cancellation policy text (customer-facing summary)", data, "cancellation_policy_text", key_suffix=key_suffix)

        service_notes.render_notes_editor(supplier_id, "Transfer", data, key_suffix=key_suffix)


        st.markdown("#### Publish")
        pre_config = TransferHumanPreConfig(supplier_id=supplier_id, currency=currency, days_available_before_release=release_days)
        build_result = build_transfer_payload(
            pre_config, data, client,
            existing_transfer_id=chosen_existing_id,
            existing_transfer_snapshot=existing_transfer_snapshot,
        )
        current["build_result"] = build_result

        # CONFIRMED FIX (real bug found via audit): checking for an existing match used to be
        # entirely optional - hitting Publish without ever clicking "Check" always created a new
        # transfer, with no safety net against duplicating one that already exists (Transfers have
        # no human-assigned code, unlike Tour/Ticket's code-availability check). Require at least
        # one check against the CURRENT route text before Publish is enabled.
        match_checked = match_result is not None
        dates_ok = bool((data.get("start_date") or "").strip()) and bool((data.get("end_date") or "").strip())
        geoloc_ok = bool(build_result.get("departure_geolocation_resolved")) and bool(build_result.get("arrival_geolocation_resolved"))

        if build_result.get("transfer_error"):
            st.error(f"⚠️ This transfer can't be built yet: {build_result['transfer_error']}")
        else:
            # CONFIRMED REAL RULE (product owner): "when the document says min. 2 Pax, we can
            # offer this for 1 Pax by simply increasing the cost" - a 1-pax bracket the document
            # itself never stated. Flagged here rather than applied silently, so a human catches
            # it if this route genuinely shouldn't get the treatment (e.g. it's not really a
            # minimum-party rate at all).
            if build_result.get("synthesized_solo_tier"):
                _solo_entry = next((e for e in (build_result["transfer_payload"].get("pricesByOccupancy") or [])
                                    if e.get("occupancy") == 1), None)
                _solo_amount = (_solo_entry or {}).get("basePrice", {}).get("amount")
                st.info(f"ℹ️ The document only prices this from **{data.get('min_billable_pax') or '2+'} pax** "
                        f"up, so a **1-pax bracket at {_solo_amount} {data.get('currency', '')}** was "
                        f"synthesized automatically (the minimum-party rate, charged to one person) - the "
                        f"document itself doesn't state this number. Check it before publishing.")
            with st.expander("🔎 Preview payload"):
                st.json(build_result["transfer_payload"])
            if not geoloc_ok:
                st.warning("⚠️ Departure and/or arrival location couldn't be resolved to real coordinates/zone - "
                          "fix the names above before publishing.")
            if not dates_ok:
                st.warning("⚠️ Start date and/or end date is blank - enter the document's real season validity "
                          "(or your own default) before publishing; Travel Compositor requires both.")
            if not match_checked:
                st.warning("⚠️ Click **Check for a matching existing transfer** above before publishing - this "
                          "is the only safeguard against accidentally creating a duplicate of a transfer that "
                          "already exists in Travel Compositor.")

            publish_label = (f"🚀 Publish — UPDATE existing transfer {chosen_existing_id}" if chosen_existing_id
                             else "🚀 Publish — CREATE new transfer")
            # CONFIRMED RULE (product owner, 2026-08-24): an expired document blocks publish
            # rather than silently producing an inverted date window - see render_publish_blockers.
            publish_disabled = (bool(build_result.get("transfer_error")) or not match_checked
                                or not dates_ok or not geoloc_ok
                                or not render_publish_blockers(build_result))
            if st.button(publish_label, type="primary", key=f"xtf_publish_{idx}", disabled=publish_disabled):
                with st.spinner("Publishing to Travel Compositor..."):
                    try:
                        if chosen_existing_id:
                            result = client.update_transfer(supplier_id, build_result["transfer_payload"])
                        else:
                            result = client.create_transfer(supplier_id, build_result["transfer_payload"])
                        if isinstance(result, dict) and "error" in result:
                            show_publish_error(f"publish transfer **{current['label'] or '(unnamed)'}**", result)
                        else:
                            new_id = result.get("id") if isinstance(result, dict) else None
                            final_id = chosen_existing_id or new_id
                            if final_id:
                                transfer_matcher.remember_transfer_id(
                                    supplier_id, data.get("departure_name", ""), data.get("arrival_name", ""), final_id
                                )
                            st.success(f"✅ Published successfully (id: {final_id or 'unknown'}).")
                            current["publish_status"] = "success"
                            # Learn only from what was actually published. A correction made
                            # and then abandoned is not a decision anyone stood behind.
                            _learned = extraction_memory.commit(
                                supplier_id, "Transfer", current, current.get("label") or "")
                            if _learned:
                                st.caption(f"🧠 Remembered {len(_learned)} correction(s) for this "
                                           f"supplier — see “What the platform remembers”.")
                    except Exception as e:
                        show_publish_error(f"publish transfer **{current['label'] or '(unnamed)'}**", str(e))

        nav_col1, nav_col2 = st.columns(2)
        with nav_col1:
            if idx > 0 and st.button("⬅️ Previous", key=f"xtf_prev_{idx}"):
                st.session_state.xtf_queue_index -= 1
                st.rerun()
        with nav_col2:
            if idx < len(queue) - 1 and st.button("➡️ Next", key=f"xtf_next_{idx}"):
                st.session_state.xtf_queue_index += 1
                st.rerun()

        if all(q.get("publish_status") == "success" for q in queue):
            st.balloons()
            st.success(f"🎉 All {len(queue)} transfer(s) in this batch published.")
            st.write("")
            st.divider()
            if st.button("🆕 Start a new batch", key="xtf_new_batch"):
                for key in ["xtf_phase", "xtf_raw_text", "xtf_candidates", "xtf_queue", "xtf_queue_index"]:
                    st.session_state.pop(key, None)
                _clear_batch_widget_state(["xtf_"] + SHARED_WIDGET_STATE_PREFIXES)
                st.rerun()
        return


# ======================================================================
# TRANSPORT FLOW
# Mirrors the Transfer flow's proven 3-phase queue pattern (gather ->
# select -> review one at a time) since Transport documents are confirmed
# to be "usually the same style as documents from transfers". The real
# structural difference is at publish time: a Transport is TWO API calls
# minimum - the parent record, then one Option sub-resource per occupancy
# bracket (see builder.build_transport_payloads / schemas'
# ContractTransportOptionVO for the confirmed additive-supplement model).
# Uses xtp_-prefixed session keys so nothing collides with the other flows.
# ======================================================================
def render_transport_flow(client):
    """Transport wizard entry point: Supplier + Currency + release window, then Input Source,
    then hands off to render_multi_transport_flow for detection/review/matching/publish."""
    if "tp_step1_confirmed" not in st.session_state:
        st.session_state.tp_step1_confirmed = False

    st.header("Transport — Step 2: Supplier & defaults")

    if st.session_state.tp_step1_confirmed:
        st.success(f"✅ Supplier ID: **{st.session_state.tp_cfg_supplier_id}** | "
                   f"Currency: **{st.session_state.tp_cfg_currency}**")
        if st.button("🔄 Change supplier / defaults", key="tp_change_action"):
            st.session_state.tp_step1_confirmed = False
            st.rerun()
    else:
        if st.session_state.suppliers_cache is None:
            with st.spinner("Loading supplier list from Travel Compositor..."):
                try:
                    st.session_state.suppliers_cache = client.get_all_suppliers()
                except Exception as e:
                    st.error(f"❌ Couldn't load the supplier list: {friendly_error_message(e)}")
                    st.session_state.suppliers_cache = []

        supplier_id_choice = None
        if st.session_state.suppliers_cache:
            momira_suppliers = [
                s for s in st.session_state.suppliers_cache
                if (s.get("commercialName") or s.get("legalName") or "").strip().lower().startswith("momira_")
            ]
            if not momira_suppliers:
                st.error("🚫 No suppliers starting with 'Momira_' were found in this account - can't continue.")
            else:
                supplier_options = {
                    f"{s.get('commercialName') or s.get('legalName')} — ID {s.get('id')}": s.get("id")
                    for s in momira_suppliers
                }
                selected_label = st.selectbox("Select Supplier", list(supplier_options.keys()), key="tp_supplier_select")
                supplier_id_choice = str(supplier_options[selected_label])
            if st.button("🔄 Refresh supplier list", key="tp_refresh_suppliers"):
                st.session_state.suppliers_cache = None
                st.rerun()
        else:
            st.error("Could not load the supplier list from Travel Compositor.")
            with st.expander("⚠️ Emergency manual entry"):
                st.caption("Only use this if the supplier list above failed to load - type the numeric Travel Compositor supplier ID directly.")
                supplier_id_choice = st.text_input("Supplier ID (numeric)", value="", key="tp_supplier_manual")

        currency_in = st.selectbox("Currency", CURRENCY_OPTIONS, key="tp_currency")
        st.caption("Only used when CREATING a new transport. Updating an existing one keeps the currency it already has — a rate sheet changes prices, not the currency a live contract is denominated in.")
        release_days_in = st.number_input(
            "Release Contract (days before departure this transport becomes bookable)",
            min_value=0, value=5, key="tp_release_days",
            help="Confirmed real field name is releaseContract - real values seen in live data range 5-14."
        )

        if st.button("➡️ Continue to Step 3", type="primary", disabled=not supplier_id_choice, key="tp_continue1"):
            st.session_state.tp_cfg_supplier_id = supplier_id_choice
            st.session_state.tp_cfg_currency = currency_in
            st.session_state.tp_cfg_release_days = release_days_in
            st.session_state.tp_step1_confirmed = True
            st.rerun()
        return

    supplier_id = st.session_state.tp_cfg_supplier_id
    currency = st.session_state.tp_cfg_currency
    release_days = st.session_state.tp_cfg_release_days

    service_notes.render_standing_note_editor(supplier_id, "Transport", key_suffix="_setup")
    cancellation_links.render_cancellation_link_editor(supplier_id, "Transport", key_suffix="_setup")
    supplier_images.render_supplier_image_editor(supplier_id, "Transport", key_suffix="_setup")

    st.header("Transport — Step 3: Input Source")
    st.caption("Transport = a connection between two Travel Compositor destinations (e.g. Aswan → Hurghada, "
              "Praslin → La Digue), priced per occupancy bracket. Rate sheets are usually the same style as "
              "Transfer documents and often describe several routes at once - all get detected and queued below.")
    tp_url = st.text_input("Product page URL (optional)", key="tp_url")
    tp_files = st.file_uploader("Upload document(s) (optional)", type=["pdf", "docx", "xlsx"],
                                accept_multiple_files=True, key="tp_files")
    tp_hint = st.text_input(
        "Instruction (optional)", key="tp_hint",
        placeholder="e.g. only the Hurghada section, private transfers only",
        help="Plain English, and it now steers BOTH steps: which products get detected, and "
             "how each one is read. It overrides the tool's own judgement — say 'all of them, "
             "including the local airport routes' and it will list them all.")

    render_multi_transport_flow(client, supplier_id, currency, release_days, tp_url, tp_files, tp_hint)


def render_multi_transport_flow(client, supplier_id, currency, release_days, tp_url, tp_files, tp_hint):
    """Batch/queue flow for Transports - same 3-phase pattern as render_multi_transfer_flow,
    with a two-stage publish (parent transport, then one Option per occupancy bracket)."""
    if "xtp_phase" not in st.session_state:
        st.session_state.xtp_phase = "gather"

    # ------------------------------------------------------------------
    # PHASE 1: detect distinct transport products from the source provided above
    # ------------------------------------------------------------------
    if st.session_state.xtp_phase == "gather":
        if not (tp_url or tp_files):
            st.info("Provide a URL and/or upload document(s) above, then click below.")
        if st.button("🔎 Detect Transport Products", disabled=not (tp_url or tp_files)):
            with st.spinner("Gathering content and detecting distinct transport products..."):
                try:
                    combined_parts = []
                    if tp_url:
                        page_text, page_text_err = _fetch_url_text_safe(tp_url)
                        if page_text is not None:
                            combined_parts.append(f"--- SOURCE: WEB PAGE ({tp_url}) ---\n{page_text}")
                        else:
                            st.warning(f"⚠️ Couldn't fetch the product page URL: {page_text_err}.")
                    for uploaded in (tp_files or []):
                        suffix = os.path.splitext(uploaded.name)[1]
                        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                            tmp.write(uploaded.getbuffer())
                            tmp_path = tmp.name
                        _doc_text = extract_raw_text(tmp_path)
                        _scan_warning = document_reader_scanned_warning(tmp_path, _doc_text)
                        if _scan_warning:
                            st.session_state.setdefault("_scanned_doc_warnings", []).append(_scan_warning)
                        combined_parts.append(f"--- SOURCE: UPLOADED DOCUMENT ({uploaded.name}) ---\n{_doc_text}")
                        os.remove(tmp_path)

                    if not combined_parts:
                        st.error("Nothing to extract - the product page URL couldn't be fetched and no document(s) were provided.")
                        st.stop()

                    raw_text = "\n\n".join(combined_parts)
                    detected = detect_transport_products(raw_text, human_hint=tp_hint)

                    candidates = []
                    for t in detected:
                        candidates.append({
                            "label": t.get("label", ""), "service_name": t.get("service_name", ""),
                            "departure_hint": t.get("departure_hint", ""), "arrival_hint": t.get("arrival_hint", ""),
                            "selected": True,
                        })
                    if not candidates:
                        candidates = [{"label": "", "service_name": "", "departure_hint": "", "arrival_hint": "",
                                      "selected": True}]

                    # Both directions, always - see ensure_return_candidates.
                    candidates, _returns_added = ensure_return_candidates(candidates)
                    if _returns_added:
                        st.session_state.xtp_auto_returns = _returns_added
                    st.session_state.xtp_raw_text = raw_text
                    st.session_state.xtp_candidates = candidates
                    st.session_state.xtp_phase = "prepare_queue"
                    st.rerun()
                except Exception as e:
                    st.error(f"Detection failed: {friendly_error_message(e)}")
        return

    # ------------------------------------------------------------------
    # PHASE 2: explicitly SELECT which transport products to review/publish
    # ------------------------------------------------------------------
    if st.session_state.xtp_phase == "prepare_queue":
        candidates = st.session_state.xtp_candidates
        if len(candidates) == 1:
            st.subheader("Set up this Transport")
            if (candidates[0].get("label") or "").strip():
                st.caption("Only one distinct transport product was found in this document.")
            else:
                # An EMPTY single row means detection found nothing, which is a different
                # situation from "found exactly one" and used to look identical on screen -
                # a blank box with no explanation, which reads as a bug rather than as a
                # question. The most common cause with a real rate sheet is that every row
                # is a local airport-to-resort transfer, i.e. genuinely not a Transport.
                st.warning("**No transport products were detected in this document.** That usually "
                           "means every route in it is a local airport-to-hotel journey, which is a "
                           "**Transfer**, not a Transport — Travel Compositor treats those as different "
                           "products. If that is the case, switch to the Transfer flow.")
                st.caption("If these ARE the products you want — you may be selling these routes as "
                          "Transports deliberately — say so below and it will list them all.")

                def _tp_accept(found):
                    st.session_state.xtp_candidates = [
                        {"label": t.get("label", ""), "service_name": t.get("service_name", ""),
                         "departure_hint": t.get("departure_hint", ""),
                         "arrival_hint": t.get("arrival_hint", ""), "selected": True}
                        for t in found]
                    st.session_state.xtp_candidates, _n = ensure_return_candidates(
                        st.session_state.xtp_candidates)
                    if _n:
                        st.session_state.xtp_auto_returns = _n

                render_detection_diagnosis("transport")
                render_empty_detection_retry(st.session_state.xtp_raw_text, "transport", "xtp",
                                             detect_transport_products, _tp_accept)
                st.markdown("---")
                st.caption("Or name one route by hand below. One route per row.")
        else:
            st.subheader(f"{len(candidates)} distinct transport products detected - choose which to review")
            st.caption("Each ticked row becomes its own separate Transport, reviewed one at a time next.")

        if st.session_state.get("xtp_auto_returns"):
            st.info(f"↔️ {st.session_state['xtp_auto_returns']} return direction(s) were added "
                    f"automatically — every route is created both ways. Untick any you don't sell.")
        render_candidate_filter(candidates, "xtp", "transport")

        for i, cand in enumerate(candidates):
            ccol1, ccol2 = st.columns([1, 5])
            with ccol1:
                cand["selected"] = st.checkbox("Include", value=cand["selected"], key=f"xtp_sel_{i}")
            with ccol2:
                cand["label"] = st.text_input(
                    "Which route?", value=cand["label"], key=f"xtp_label_{i}",
                    placeholder="e.g. Private Transfer: HRG Airport to Luxor",
                    help="Name ONE route the way the document writes it — the service or class, "
                         "then where it goes from and to. This is what the AI is told to look for "
                         "when it reads the document for this row, so the closer it is to the "
                         "document's own wording the better. It is not the product name in Travel "
                         "Compositor; that comes from the extraction and you can edit it next.")

        if st.button("➕ Add another transport product manually"):
            candidates.append({"label": "", "service_name": "", "departure_hint": "", "arrival_hint": "",
                              "selected": True})
            st.rerun()

        new_queue = [
            {"label": c["label"], "service_name": c["service_name"], "departure_hint": c["departure_hint"],
             "arrival_hint": c["arrival_hint"], "data": None, "publish_status": None}
            for c in candidates if c["selected"]
        ]

        st.caption(f"**{len(new_queue)}** transport(s) ready to review." if new_queue else
                  "Select at least one transport product to continue.")

        if st.button("➡️ Start Reviewing", type="primary", disabled=not new_queue):
            st.session_state.xtp_queue = new_queue
            st.session_state.xtp_queue_index = 0
            st.session_state.xtp_phase = "reviewing"
            st.rerun()
        return

    # ------------------------------------------------------------------
    # PHASE 3: review + match + publish each transport, one at a time
    # ------------------------------------------------------------------
    if st.session_state.xtp_phase == "reviewing":
        idx = st.session_state.xtp_queue_index
        queue = st.session_state.xtp_queue
        current = queue[idx]
        XTP_STATE_KEYS = ["xtp_phase", "xtp_raw_text", "xtp_candidates", "xtp_queue", "xtp_queue_index"]

        st.subheader(f"Reviewing transport {idx + 1} of {len(queue)}: {current['label'] or '(unnamed)'}")

        render_batch_bulk_controls(queue, "xtp_queue", "xtp_queue_index", "xtp_phase",
                                   XTP_STATE_KEYS, ["xtp_"] + SHARED_WIDGET_STATE_PREFIXES,
                                   "transport", "xtp")

        if st.button("🔙 Start over - upload a different document", key=f"xtp_cancel_{idx}"):
            for key in XTP_STATE_KEYS:
                st.session_state.pop(key, None)
            _clear_batch_widget_state(["xtp_"] + SHARED_WIDGET_STATE_PREFIXES)
            st.rerun()

        if current["data"] is None:
            with st.spinner("Extracting this transport's details..."):
                try:
                    transport_hint = None
                    if current.get("service_name") or current.get("departure_hint") or current.get("arrival_hint"):
                        transport_hint = (f"{current.get('service_name', '')} - "
                                          f"{current.get('departure_hint', '')} to {current.get('arrival_hint', '')}")
                    elif current["label"]:
                        transport_hint = current["label"]
                    current["data"] = extract_transport_data(st.session_state.xtp_raw_text,
                                                              transport_hint=transport_hint,
                                                              human_hint=with_learned_guidance(
                                                                  supplier_id, "Transport", tp_hint))
                except Exception as e:
                    st.error(f"Extraction failed for this transport: {friendly_error_message(e)}")
                    current["data"] = {}
            # Everything the app already knows, filled in before the human ever sees the form.
            current["_seeded_fields"] = seed_transport_from_candidate(
                current, current["data"], currency)
            extraction_memory.prepare(supplier_id, "Transport", current)
            # See the matching comment in render_multi_transfer_flow - only fills in when
            # this document didn't state its own cancellation terms, and must run here once
            # rather than inside the review widgets below.
            current["_cancellation_link_scope"] = cancellation_links.apply_cancellation_link_default(
                current["data"], supplier_id, "Transport")
            # See the matching comment in render_multi_transfer_flow - auto-picks this
            # supplier's saved Airport/Harbor<->Hotel image for the route's detected
            # direction. Runs once, here, at extraction time.
            _si_url, current["_image_direction"], current["_image_upload_error"] = (
                supplier_images.resolve_and_host_image(
                    supplier_id, "Transport",
                    current["data"].get("departure_name"), current["data"].get("arrival_name")))
            if _si_url:
                current["data"]["image_urls"] = [_si_url]

        data = current["data"]
        key_suffix = f"_{idx}"
        extraction_memory.render_applied_banner(current.get("_learned_applied") or [])

        if not (data.get("occupancy_brackets") or []):
            # No prices means the document was not read for this route. Everything else on the
            # screen came from the route you picked and your Step 2 settings, so say which -
            # a seeded value that looks extracted is worse than an empty one.
            st.error(
                "🔴 **No prices were read from the document for this route.** The fields below "
                "were filled in from the route you selected and your Step 2 settings — they are "
                "NOT from the document. Add the occupancy brackets by hand, or try reading this "
                "route again."
            )
            if current.get("_seeded_fields"):
                st.caption("Filled in by the app, not read from the document: "
                           + ", ".join(f"`{f}`" for f in current["_seeded_fields"]))
        if st.button("🔁 Read this route from the document again", key=f"xtp_reextract_{idx}",
                     help="Re-runs the extraction for this one route only. The rest of the batch "
                          "is untouched."):
            current["data"] = None
            current.pop("_seeded_fields", None)
            # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): this was the only "re-read"/
            # "start over" reset in the whole file that swept `["xtp_"]` alone, without also
            # sweeping SHARED_WIDGET_STATE_PREFIXES (the generic editable-table edit-mode flags
            # etc. - see its own sibling "skip this item" button just below, which already
            # includes it) - so a table left in live-edit mode before re-reading could still show
            # the previous extraction's edited values after the fresh read.
            _clear_batch_widget_state(["xtp_"] + SHARED_WIDGET_STATE_PREFIXES, keep=XTP_STATE_KEYS)
            st.rerun()

        render_skip_item_button(
            current["label"] or "(unnamed transport)", queue, idx, "xtp_queue", "xtp_queue_index",
            XTP_STATE_KEYS, f"xtp_skip_{idx}", widget_state_prefixes=["xtp_"] + SHARED_WIDGET_STATE_PREFIXES,
        )

        st.markdown("#### Which existing Transport does this update, if any?")
        st.caption("Travel Compositor assigns Transport ids itself (e.g. TRANSPORT-412579) with no human code, "
                  "so this app tracks its own id->route mapping locally and falls back to a route-name "
                  "similarity match - either way, YOU always confirm before anything publishes.")

        current_route_fingerprint = f"{data.get('departure_name', '')}::{data.get('arrival_name', '')}"
        if current.get("match_route_fingerprint") != current_route_fingerprint:
            current["match_result"] = None
            current["match_route_fingerprint"] = current_route_fingerprint

        if st.button("🔎 Check for a matching existing transport", key=f"xtp_checkmatch_{idx}"):
            with st.spinner("Checking..."):
                current["match_result"] = transport_matcher.resolve_transport_match(
                    client, supplier_id, data.get("departure_name", ""), data.get("arrival_name", "")
                )
                current["match_route_fingerprint"] = current_route_fingerprint

        match_result = current.get("match_result")
        chosen_existing_id = None
        if match_result:
            if match_result.get("fetch_error"):
                st.warning(f"⚠️ Couldn't fetch this supplier's existing transports to check for a match: "
                          f"{match_result['fetch_error'].get('message', match_result['fetch_error'])}. "
                          f"Will create as new.")
            if match_result.get("tracked_id"):
                tracked_id = match_result["tracked_id"]
                # CONFIRMED REAL RULE (product owner): a tracked/remembered match must not
                # silently pre-apply - fetch and show its key details before it can be used,
                # same safety bar as Transfer's tracked matches now enforce (mirrors
                # ClosedTour/Ticket's forced fetch-and-glance before an update can proceed).
                if current.get("_tracked_snapshot_id") != tracked_id:
                    with st.spinner(f"Fetching {tracked_id} to show you what it currently looks like..."):
                        current["_tracked_snapshot"] = client.get_transport(supplier_id, tracked_id)
                    current["_tracked_snapshot_id"] = tracked_id
                tracked_snapshot = current.get("_tracked_snapshot")
                if isinstance(tracked_snapshot, dict) and "error" not in tracked_snapshot:
                    st.success(f"✅ This app has already created/confirmed a match for this exact route before: "
                              f"**{tracked_id}**.")
                    st.caption(f"Existing record: **{tracked_snapshot.get('name', '?')}**, "
                              f"currency **{tracked_snapshot.get('currency', '?')}**, "
                              f"valid **{tracked_snapshot.get('startDate', '?')}** to **{tracked_snapshot.get('endDate', '?')}**.")
                    use_tracked = st.checkbox("Yes, this is the right one - update it", value=False,
                                              key=f"xtp_usetracked_{idx}")
                    chosen_existing_id = tracked_id if use_tracked else None
                else:
                    st.warning(f"⚠️ This app remembers a match for this route (**{tracked_id}**) but couldn't "
                              f"fetch it just now to confirm it still exists - won't auto-apply it blind. "
                              f"Click Check again, or enter/confirm manually if you know it's still correct.")
            elif match_result.get("fallback_candidates"):
                options = ["Create as a NEW transport"] + [
                    f"Update: {c['name'] or '(unnamed)'} — {c['transport_id']} (match score {c['score']})"
                    for c in match_result["fallback_candidates"]
                ]
                picked = st.radio("Pick one - nothing publishes until you explicitly confirm a match:",
                                  options, key=f"xtp_matchpick_{idx}")
                if picked != options[0]:
                    picked_idx = options.index(picked) - 1
                    chosen_existing_id = match_result["fallback_candidates"][picked_idx]["transport_id"]
            else:
                st.info("No existing transports found for this supplier - will create as new.")

        current["confirmed_existing_id"] = chosen_existing_id

        # Merge-on-update: fetch the live parent record AND its existing options so
        # build_transport_payloads can preserve existing dates/images and match each new bracket
        # onto the right existing Option (by min/maxPassengers overlap) instead of duplicating.
        existing_transport_snapshot = None
        existing_options_snapshot = None
        if chosen_existing_id:
            if current.get("existing_snapshot_id") != chosen_existing_id:
                with st.spinner(f"Fetching existing transport {chosen_existing_id} and its options to merge into..."):
                    snapshot_result = client.get_transport(supplier_id, chosen_existing_id)
                    if isinstance(snapshot_result, dict) and "error" in snapshot_result:
                        st.warning(f"⚠️ Couldn't fetch existing transport {chosen_existing_id} "
                                  f"({snapshot_result.get('message', snapshot_result)}) - this update will use the "
                                  f"document's own dates/images instead of preserving the existing ones.")
                        current["existing_snapshot"] = None
                        current["existing_options"] = None
                    else:
                        current["existing_snapshot"] = snapshot_result
                        opts = []
                        for opt_code in (snapshot_result.get("optionCodes") or []):
                            opt = client.get_transport_option(supplier_id, chosen_existing_id, opt_code)
                            if isinstance(opt, dict) and "error" not in opt:
                                opts.append(opt)
                        current["existing_options"] = opts
                current["existing_snapshot_id"] = chosen_existing_id
            existing_transport_snapshot = current.get("existing_snapshot")
            existing_options_snapshot = current.get("existing_options")
        else:
            current["existing_snapshot"] = None
            current["existing_options"] = None
            current["existing_snapshot_id"] = None

        st.markdown("#### Route")
        st.caption("Departure/arrival resolve against Travel Compositor's Transport Bases (the same master "
                  "location list the Transport screen itself uses), not raw GPS coordinates.")
        rcol1, rcol2 = st.columns(2)
        with rcol1:
            editable_field("Departure", data, "departure_name", key_suffix=key_suffix)
        with rcol2:
            editable_field("Arrival", data, "arrival_name", key_suffix=key_suffix)

        st.markdown("#### Service")
        scol1, scol2, scol3 = st.columns(3)
        with scol1:
            editable_field("Service name", data, "service_name", key_suffix=key_suffix)
        with scol2:
            editable_field("Transport type hint", data, "transport_type_hint", key_suffix=key_suffix)
        with scol3:
            editable_field("Company name", data, "company_name", key_suffix=key_suffix)

        # CONFIRMED PRODUCT-OWNER DECISION (2026-08-19 audit): only CAR/COMBINED/PLANE are
        # confirmed out of the 8-value transportType enum - anything else used to default
        # silently to CAR. Now flagged so it can be corrected before publish.
        if not transport_type_is_confirmed_match(data.get("transport_type_hint"),
                                                  data.get("service_name")):
            st.warning("⚠️ Couldn't confidently match this to a known transport type (car/"
                      "combined/plane) — it will be sent as **CAR** unless you correct the "
                      "hint above. Double-check this is actually a car service before publishing.")

        vcol1, vcol2, vcol3 = st.columns(3)
        with vcol1:
            editable_field("Vehicle / aircraft model", data, "vehicle_model", key_suffix=key_suffix)
        with vcol2:
            editable_field("Service / flight number", data, "service_number", key_suffix=key_suffix)
        with vcol3:
            # CONFIRMED REAL RULE (product owner): once a match against an existing transport is
            # confirmed above, currency is never asked again - build_transport_payloads already
            # locks it to the existing record's own currency via _locked_on_update regardless of
            # what's in data["currency"]. Only shown for a genuine create.
            if chosen_existing_id and existing_transport_snapshot:
                data["currency"] = existing_transport_snapshot.get("currency") or data.get("currency")
                st.text_input("Currency", value=data["currency"] or "(existing)", disabled=True,
                              key=f"xtp_currency_locked_{idx}",
                              help="Inherited from the existing transport being updated - can't be changed.")
            else:
                data["currency"] = st.selectbox(
                    "Currency", CURRENCY_OPTIONS,
                    index=CURRENCY_OPTIONS.index(data["currency"]) if data.get("currency") in CURRENCY_OPTIONS else 0,
                    key=f"xtp_currency_{idx}"
                )

        ccol1, ccol2, ccol3, ccol4 = st.columns(4)
        with ccol1:
            data["charge_unit"] = st.selectbox(
                "Charge unit", ["per_pax", "per_service"],
                index=0 if data.get("charge_unit", "per_pax") != "per_service" else 1,
                key=f"xtp_chargeunit_{idx}",
                format_func=lambda v: "Per person" if v == "per_pax" else "Flat price for the whole vehicle",
                help="\"Per person\" charges by headcount. \"Flat price for the whole vehicle\" is one "
                     "price no matter how many people are in it (this also enables combining multiple "
                     "vehicles for larger groups, up to the 9-passenger system cap)."
            )
        with ccol2:
            # CONFIRMED REAL RULE (product owner): "the human shall in best case only select
            # Departure time." Duration is a fact about the route, departure is the operator's
            # choice, and arrival is arithmetic - so arrival is shown, never typed. Both times
            # used to default to 09:00, which published a five-hour drive as instantaneous.
            data["departure_time"] = st.text_input(
                "Departure time", value=str(data.get("departure_time") or "09:00:00"),
                key=f"xtp_deptime_{idx}", help="24-hour, HH:MM:SS. The one time you set.")
        with ccol3:
            data["duration_time"] = st.text_input(
                "Journey duration", value=str(data.get("duration_time") or ""),
                key=f"xtp_dur_{idx}", placeholder="HH:MM:SS",
                help="How long the journey actually takes. Read from the document when it says, "
                     "otherwise estimated by the AI from the real route — check it.")
        with ccol4:
            _arr, _pd = derive_arrival_from_duration(data.get("departure_time"),
                                                     data.get("duration_time"))
            if _arr:
                data["arrival_time"] = _arr
                data["plus_days"] = _pd
                st.metric("Arrival (calculated)", _arr + (f"  +{_pd}d" if _pd else ""))
            else:
                st.metric("Arrival (calculated)", "—")
                st.caption("Enter a duration to calculate it.")

        if data.get("duration_estimated") and (data.get("duration_time") or "").strip():
            st.caption(f"⏱️ The document didn't state a duration, so **{data['duration_time']}** is the "
                      f"AI's estimate for {data.get('departure_name') or 'A'} → "
                      f"{data.get('arrival_name') or 'B'}. Correct it if you know better — arrival "
                      f"recalculates.")
        if not (data.get("duration_time") or "").strip():
            st.warning("⚠️ No journey duration, so arrival will be published equal to departure — a "
                       "journey that appears to take no time. Enter one before publishing.")

        st.markdown("#### Pricing by occupancy bracket")
        # CONFIRMED REAL RULE (product owner): same minimum-party-size rule as Transfer - "when
        # the document says min. 2 Pax, we can offer this for 1 Pax by simply increasing the
        # cost." Was already built and applied silently for Transport; now shown here to edit
        # and flagged at Publish time so a human can check it rather than discover it later.
        data["min_billable_pax"] = st.number_input(
            "Minimum billable pax (leave at 1 if the document states no minimum party size)",
            min_value=1, max_value=9, value=int(data.get("min_billable_pax") or 1),
            key=f"xtp_minbillable_{idx}",
            help="A per-person rate valid from e.g. 2 pax up means a solo traveller pays the "
                 "2-pax total, not half of it - set this to 2 and that 1-pax bracket is added "
                 "automatically. Only applies to per-pax pricing, ignored for per-service.")
        st.caption("One row per group size the document actually states (e.g. 1-2 Pax, 3-4 Pax). Each price is "
                  "the ACTUAL final price for that group size - never interpolated between rows, since real "
                  "contracts have shown non-monotonic patterns. Rows above 9 pax are dropped automatically "
                  "(system cap). For a per-vehicle transport, larger groups are priced automatically as needing "
                  "multiple vehicles. Leave Child/Infant blank (not 0) when the document states no price.")
        br_df = pd.DataFrame(data.get("occupancy_brackets") or
                             [{"min_occupancy": 1, "max_occupancy": 4, "price": 0.0, "child_price": None, "infant_price": None}])
        for col in ["min_occupancy", "max_occupancy", "price", "child_price", "infant_price"]:
            if col not in br_df.columns:
                br_df[col] = None

        def _save_brackets(edited_df):
            rows = []
            dropped_incomplete = 0
            for _, row in edited_df.iterrows():
                min_blank = pd.isna(row.get("min_occupancy"))
                max_blank = pd.isna(row.get("max_occupancy"))
                price_blank = pd.isna(row.get("price"))
                if min_blank and max_blank and price_blank:
                    continue
                if min_blank or max_blank or price_blank:
                    dropped_incomplete += 1
                    continue
                rows.append({
                    "min_occupancy": _safe_int(row.get("min_occupancy"), fallback=1),
                    "max_occupancy": _safe_int(row.get("max_occupancy"), fallback=1),
                    "price": _safe_float(row.get("price"), fallback=0.0),
                    "child_price": None if pd.isna(row.get("child_price")) else _safe_float(row.get("child_price"), fallback=0.0),
                    "infant_price": None if pd.isna(row.get("infant_price")) else _safe_float(row.get("infant_price"), fallback=0.0),
                })
            data["occupancy_brackets"] = rows
            if dropped_incomplete:
                st.warning(f"⚠️ Dropped {dropped_incomplete} bracket row(s) missing a min, max or price - "
                          f"all three are required to keep a row.")

        editable_table("Occupancy brackets", br_df, f"xtp_brackets_{idx}", on_save=_save_brackets)
        editable_field("Blanket child/infant rule (if the document states one instead of per-row prices)",
                       data, "child_infant_rule_text", key_suffix=key_suffix)

        st.markdown("#### Notes, validity & cancellation")
        st.caption("Transport has no supplements/additionalServices field at all, so any priced extra "
                  "(guide language, permit fee, etc) is folded into the description as informational text.")
        editable_field("Additional notes (priced extras with no structured home)", data, "additional_notes",
                       widget="text_area", height=80, key_suffix=key_suffix)
        editable_field("Description", data, "description", widget="text_area", height=100, key_suffix=key_suffix)

        dcol1, dcol2 = st.columns(2)
        with dcol1:
            editable_field("Start date (DD/MM/YYYY)", data, "start_date", key_suffix=key_suffix)
        with dcol2:
            editable_field("End date (DD/MM/YYYY)", data, "end_date", key_suffix=key_suffix)
        st.caption("Inventory/availability convention: Transports are normally left open-ended (2049) so they "
                  "stay bookable and simply pick up new prices when rates refresh.")

        render_direction_image_section(current, data, "Transport", f"xtp_image_manual_{idx}")

        if current.get("_cancellation_link_scope"):
            st.caption(f"ℹ️ This document didn't state its own cancellation terms - the table below "
                      f"was filled in from {current['_cancellation_link_scope']}. Edit or clear it if "
                      f"this product needs different terms.")
        render_cancellation_policy_editor(data, f"xtp_cancel_{idx}")
        editable_field("Cancellation policy text (customer-facing summary)", data, "cancellation_policy_text",
                       widget="text_area", height=80, key_suffix=key_suffix)

        service_notes.render_notes_editor(supplier_id, "Transport", data, key_suffix=key_suffix)


        st.markdown("#### Publish")
        pre_config = TransportHumanPreConfig(supplier_id=supplier_id, currency=currency,
                                              days_available_before_release=release_days)
        build_result = build_transport_payloads(
            pre_config, data, client,
            existing_transport_id=chosen_existing_id,
            existing_transport_snapshot=existing_transport_snapshot,
            existing_options_snapshot=existing_options_snapshot,
        )
        current["build_result"] = build_result

        match_checked = match_result is not None
        dates_ok = bool((data.get("start_date") or "").strip()) and bool((data.get("end_date") or "").strip())
        bases_ok = bool(build_result.get("departure_base_resolved")) and bool(build_result.get("arrival_base_resolved"))
        option_actions = build_result.get("option_actions") or []
        option_errors = [a for a in option_actions if a.get("option_error")]

        if build_result.get("transport_error"):
            st.error(f"⚠️ This transport can't be built yet: {build_result['transport_error']}")
        else:
            # CONFIRMED REAL RULE (product owner): "one transport can have more than one
            # modality - one for 1 pax and one for 2 to 9 pax." Each occupancy bracket IS a
            # Modality/Option in Travel Compositor, so the thing being published is a list of
            # modalities, not one product with a price. That was only visible by opening a raw
            # JSON payload, which is the wrong place to check money: shown as a table here so
            # the 1-pax surcharge can be verified at a glance before anything goes live.
            st.markdown(f"#### Modalities to publish ({len(option_actions)})")
            # CONFIRMED REAL RULE (product owner): "the human shall manually add this field."
            # The generated name follows the house pattern, but only a person knows whether
            # this particular run carries a guide or is door to door - so it is editable per
            # modality, and the edit is what gets published. EN only: every other language is
            # filled in by Travel Compositor's own translation tooling, never by this tool.
            data.setdefault("modality_names", {})
            with st.expander("✏️ Modality names (English only — edit before publishing)",
                             expanded=False):
                st.caption("This is the name a person sees against each passenger range. The "
                          "suggestion follows your house pattern; change any of it. Only "
                          "English is sent — other languages come from Travel Compositor.")
                for _a in option_actions:
                    _key = f"{_a.get('min_occupancy')}-{_a.get('max_occupancy')}"
                    _suggested = ((_a.get("option_payload") or {}).get("translations") or {}) \
                        .get("EN", {}).get("name", "")
                    # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): compare the typed
                    # text against the STABLE house-pattern name (auto_generated_name), not
                    # against `_suggested` above - `_suggested` is read from option_payload,
                    # which already has any saved override baked into it by builder.py. Comparing
                    # against the post-override name meant: type an override -> saved (differs
                    # from the OLD suggestion) -> next build bakes it in -> now "differs" is
                    # False -> override gets popped -> next build reverts to the auto name ->
                    # "differs" is True again -> override gets re-added. The custom name only
                    # ended up live on whichever parity a given Publish click happened to land
                    # on. auto_generated_name never changes just because an override was applied,
                    # so this comparison is stable.
                    _auto = _a.get("auto_generated_name") or _suggested
                    _typed = st.text_input(
                        f"{_key} pax  ·  code `{_a.get('code')}`",
                        value=data["modality_names"].get(_key, _suggested),
                        key=f"xtp_modname_{idx}_{_key}")
                    if _typed.strip() and _typed.strip() != _auto:
                        data["modality_names"][_key] = _typed.strip()
                    else:
                        data["modality_names"].pop(_key, None)
            _base = _safe_float(build_result["transport_payload"].get("baseAdultPrice"), fallback=0.0)
            _per_pax = bool(build_result["transport_payload"].get("pricePerPax"))
            # CONFIRMED REAL RULE (product owner): "when the document says min. 2 Pax, we can
            # offer this for 1 Pax by simply increasing the cost" - flagged here rather than
            # applied silently, so a human catches it if this route genuinely shouldn't get the
            # treatment. This rule was already live for Transport before it had any visible
            # confirmation step - added alongside the same fix for Transfer.
            if build_result.get("synthesized_solo_bracket"):
                _solo_action = next((a for a in option_actions if _safe_int(a.get("min_occupancy", 0), fallback=0) == 1), None)
                if _solo_action:
                    _solo_sup = 0.0
                    for _pr in (_solo_action.get("option_payload", {}).get("prices") or []):
                        if isinstance(_pr, dict):
                            _solo_sup = _safe_float(_pr.get("adultPriceSupplement"), fallback=0.0)
                    _solo_price = round(_base + _solo_sup, 2)
                    st.info(f"ℹ️ The document only prices this from **{data.get('min_billable_pax') or '2+'} pax** "
                            f"up, so a **1-pax bracket at {_solo_price} {currency}** was synthesized "
                            f"automatically (the minimum-party rate, charged to one person) - the document "
                            f"itself doesn't state this number. Check it before publishing.")
            _rows = []
            for a in option_actions:
                _sup = 0.0
                for _pr in (a.get("option_payload", {}).get("prices") or []):
                    if isinstance(_pr, dict):
                        _sup = _safe_float(_pr.get("adultPriceSupplement"), fallback=0.0)
                _unit = round(_base + _sup, 2)
                _lo = _safe_int(a.get("min_occupancy", 1), fallback=1)
                _rows.append({
                    "Modality code": a.get("code"),
                    "Passengers": f"{a.get('min_occupancy')}–{a.get('max_occupancy')}",
                    ("Price per person" if _per_pax else "Price per vehicle"): _unit,
                    f"Total at {_lo} pax": round(_unit * _lo, 2) if _per_pax else _unit,
                    "New or update": a.get("action", "").upper(),
                })
            st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True)
            if any(a.get("min_occupancy") == 1 and a.get("max_occupancy") == 1
                   for a in option_actions) and _per_pax:
                st.caption("The 1-pax modality carries the minimum-party surcharge: a solo traveller "
                          "pays the same total as the smallest party the supplier will sell to.")
            st.caption("Each row becomes its own Modality in Travel Compositor. A booking is priced "
                      "by whichever modality covers the party size.")

            # A second reading before anything goes live. Deliberately on demand rather than
            # automatic: it costs an AI call per press, and an advisor that runs on every
            # keystroke becomes noise people scroll past.
            acol1, acol2 = st.columns([2, 5])
            with acol1:
                if st.button("🧠 Ask for a second opinion", key=f"xtp_advice_{idx}",
                             use_container_width=True):
                    with st.spinner("Reading it back…"):
                        current["advice"] = publish_advisor.advise_transport(data, build_result)
                    st.rerun()
            with acol2:
                st.caption("Checks the price against the route, the duration against the real "
                          "journey, and the wording against your house style. Advice only — it "
                          "never blocks publishing.")
            if current.get("advice"):
                publish_advisor.render_advice(current["advice"])

            with st.expander("🔎 Preview payloads"):
                st.markdown("**Transport (parent record)**")
                st.json(build_result["transport_payload"])
                st.markdown(f"**Options ({len(option_actions)} occupancy bracket(s))**")
                for a in option_actions:
                    st.caption(f"{a['action'].upper()} — `{a['code']}` "
                              f"({a['min_occupancy']}-{a['max_occupancy']} pax)")
                    st.json(a.get("option_payload"))
                if build_result.get("options_to_deactivate"):
                    st.markdown("**Options to deactivate (bracket no longer in this rate sheet)**")
                    st.json(build_result["options_to_deactivate"])

            if not bases_ok:
                st.warning(f"⚠️ Departure and/or arrival couldn't be resolved to a real Travel Compositor "
                          f"Transport Base (departure: {build_result.get('departure_base_match_type')}, "
                          f"arrival: {build_result.get('arrival_base_match_type')}) - fix the names above "
                          f"before publishing. Transport Bases are named after PLACES, so an airport code "
                          f"on its own often won't match - try the city it serves (e.g. 'Marsa Alam' "
                          f"rather than 'RMF Airport').")
            else:
                # An airport that resolved via its city is a substitution, and a substitution a
                # human hasn't seen is one they find out about from a published route. Say it here.
                for _side in ("departure", "arrival"):
                    _via = build_result.get(f"{_side}_base_resolved_via")
                    if _via:
                        st.info(f"ℹ️ The {_side} was read as an airport and matched on the city it "
                                f"serves — **{_via}** → Transport Base "
                                f"**{build_result.get(f'{_side}_base_name')}**. Check that is the right "
                                f"place before publishing.")
            if not dates_ok:
                st.warning("⚠️ Start date and/or end date is blank - enter the document's real validity range "
                          "before publishing; Travel Compositor requires both.")
            if not option_actions:
                st.warning("⚠️ No occupancy brackets - add at least one priced bracket above before publishing.")
            if option_errors:
                st.error(f"⚠️ {len(option_errors)} occupancy bracket(s) couldn't be built: "
                        f"{option_errors[0].get('option_error')}")
            if not match_checked:
                st.warning("⚠️ Click **Check for a matching existing transport** above before publishing - this "
                          "is the only safeguard against accidentally creating a duplicate.")

            publish_label = (f"🚀 Publish — UPDATE existing transport {chosen_existing_id}" if chosen_existing_id
                             else "🚀 Publish — CREATE new transport")
            # CONFIRMED RULE (product owner, 2026-08-24) - see render_publish_blockers.
            publish_disabled = (bool(build_result.get("transport_error")) or not match_checked or not dates_ok
                                or not bases_ok or not option_actions or bool(option_errors)
                                or not render_publish_blockers(build_result))
            if st.button(publish_label, type="primary", key=f"xtp_publish_{idx}", disabled=publish_disabled):
                with st.spinner("Publishing to Travel Compositor..."):
                    try:
                        # STAGE 1 - the parent transport record.
                        if chosen_existing_id:
                            result = client.update_transport(supplier_id, build_result["transport_payload"])
                        else:
                            result = client.create_transport(supplier_id, build_result["transport_payload"])

                        if isinstance(result, dict) and "error" in result:
                            show_publish_error(f"publish transport **{current['label'] or '(unnamed)'}**", result)
                        else:
                            new_id = result.get("id") if isinstance(result, dict) else None
                            final_id = chosen_existing_id or new_id
                            if not final_id:
                                st.error("❌ Travel Compositor didn't return a transport id, so the occupancy "
                                        "brackets can't be attached. Nothing further was sent - check the "
                                        "transport in Travel Compositor before retrying.")
                            else:
                                # STAGE 2 - one Option per occupancy bracket, then deactivate stale ones.
                                option_failures = []
                                for a in option_actions:
                                    if not a.get("option_payload"):
                                        continue
                                    if a["action"] == "update":
                                        opt_result = client.update_transport_option(supplier_id, final_id, a["option_payload"])
                                    else:
                                        opt_result = client.create_transport_option(supplier_id, final_id, a["option_payload"])
                                    if isinstance(opt_result, dict) and "error" in opt_result:
                                        option_failures.append((a["code"], opt_result))

                                for stale in (build_result.get("options_to_deactivate") or []):
                                    stale_result = client.update_transport_option(supplier_id, final_id, stale)
                                    if isinstance(stale_result, dict) and "error" in stale_result:
                                        option_failures.append((stale.get("code"), stale_result))

                                transport_matcher.remember_transport_id(
                                    supplier_id, data.get("departure_name", ""), data.get("arrival_name", ""), final_id
                                )

                                if option_failures:
                                    st.error(f"⚠️ The transport itself published (id: {final_id}), but "
                                            f"{len(option_failures)} occupancy bracket(s) failed: "
                                            f"{', '.join(str(c) for c, _ in option_failures)}. Fix and re-publish - "
                                            f"re-running is safe, brackets are matched and updated in place.")
                                else:
                                    st.success(f"✅ Published successfully (id: {final_id}) with "
                                              f"{len(option_actions)} occupancy bracket(s).")
                                    current["publish_status"] = "success"
                                    _learned = extraction_memory.commit(
                                        supplier_id, "Transport", current, current.get("label") or "")
                                    if _learned:
                                        st.caption(f"🧠 Remembered {len(_learned)} correction(s) for "
                                                   f"this supplier.")
                    except Exception as e:
                        show_publish_error(f"publish transport **{current['label'] or '(unnamed)'}**", str(e))

        nav_col1, nav_col2 = st.columns(2)
        with nav_col1:
            if idx > 0 and st.button("⬅️ Previous", key=f"xtp_prev_{idx}"):
                st.session_state.xtp_queue_index -= 1
                st.rerun()
        with nav_col2:
            if idx < len(queue) - 1 and st.button("➡️ Next", key=f"xtp_next_{idx}"):
                st.session_state.xtp_queue_index += 1
                st.rerun()

        if all(q.get("publish_status") == "success" for q in queue):
            st.balloons()
            st.success(f"🎉 All {len(queue)} transport(s) in this batch published.")
            st.write("")
            st.divider()
            if st.button("🆕 Start a new batch", key="xtp_new_batch"):
                for key in XTP_STATE_KEYS:
                    st.session_state.pop(key, None)
                _clear_batch_widget_state(["xtp_"] + SHARED_WIDGET_STATE_PREFIXES)
                st.rerun()
        return


# ======================================================================
# HOTEL FLOW
# Deliberately a straight linear wizard rather than the batch/queue
# pattern the other product types use: one hotel contract document
# normally describes exactly ONE property (its many rooms/seasons/rates
# all belong to that same hotel record), and a hotel's providerCode is
# human-assigned up front - so there's nothing to detect-and-queue the way
# there is for multi-route Transfer/Transport rate sheets.
#
# Publishing is genuinely TWO-PHASE and that's visible in the UI: the
# hotel contract (with its rooms and meal plans) must be created first
# because Travel Compositor assigns each room a system-generated
# providerCode that only comes back in that response - and rates can't
# reference a room until they have it. See builder.py's HOTEL BUILDER
# section for the full sequencing rationale.
# ======================================================================
def _hp_dist_to_str(distributions):
    """[{'adults':2,'children':1}, ...] -> '2+1, ...' (the same "Adult + child" shorthand Travel
    Compositor's own room Distribution grid uses, so the table reads the way the system does)."""
    parts = []
    for d in distributions or []:
        if isinstance(d, dict):
            parts.append(f"{_safe_int(d.get('adults'), fallback=1)}+{_safe_int(d.get('children'), fallback=0)}")
    return ", ".join(parts)


def _hp_str_to_dist(text):
    """'2+1, 1+0' -> [{'adults':2,'children':1}, {'adults':1,'children':0}]. Silently skips
    anything unparseable rather than crashing the whole save on one typo."""
    result = []
    for chunk in str(text or "").replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        match = re.match(r"^(\d+)\s*\+\s*(\d+)$", chunk)
        if match:
            result.append({"adults": int(match.group(1)), "children": int(match.group(2))})
        elif chunk.isdigit():
            result.append({"adults": int(chunk), "children": 0})
    return result


def _hp_nums_to_str(values):
    return ", ".join(str(_safe_float(v)) for v in (values or []))


def _hp_str_to_nums(text):
    result = []
    for chunk in str(text or "").replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            result.append(_safe_float(chunk, fallback=0.0))
    return result


def _hp_names_to_str(values):
    return ", ".join(str(v) for v in (values or []) if str(v).strip())


def _hp_str_to_names(text):
    return [c.strip() for c in str(text or "").replace(";", ",").split(",") if c.strip()]


def _hp_first_window(windows, key):
    for w in windows or []:
        if isinstance(w, dict) and w.get(key):
            return w[key]
    return ""


def _hp_window_list(start, end):
    start, end = str(start or "").strip(), str(end or "").strip()
    return [{"start": start, "end": end}] if start and end else []


def render_hotel_flow(client):
    """Hotel wizard entry point: Supplier + hotel code + currency + release window, then Input
    Source, then a single review screen, then the two-phase publish."""
    if "hp_step1_confirmed" not in st.session_state:
        st.session_state.hp_step1_confirmed = False

    st.header("Hotel — Step 2: Supplier & hotel code")

    if st.session_state.hp_step1_confirmed:
        st.success(f"✅ Supplier ID: **{st.session_state.hp_cfg_supplier_id}** | "
                   f"Hotel code: **{st.session_state.hp_cfg_provider_code}** | "
                   f"Currency: **{st.session_state.hp_cfg_currency}**")
        if st.button("🔄 Change supplier / hotel code", key="hp_change_action"):
            st.session_state.hp_step1_confirmed = False
            st.rerun()
    else:
        if st.session_state.suppliers_cache is None:
            with st.spinner("Loading supplier list from Travel Compositor..."):
                try:
                    st.session_state.suppliers_cache = client.get_all_suppliers()
                except Exception as e:
                    st.error(f"❌ Couldn't load the supplier list: {friendly_error_message(e)}")
                    st.session_state.suppliers_cache = []

        supplier_id_choice = None
        if st.session_state.suppliers_cache:
            momira_suppliers = [
                s for s in st.session_state.suppliers_cache
                if (s.get("commercialName") or s.get("legalName") or "").strip().lower().startswith("momira_")
            ]
            if not momira_suppliers:
                st.error("🚫 No suppliers starting with 'Momira_' were found in this account - can't continue.")
            else:
                supplier_options = {
                    f"{s.get('commercialName') or s.get('legalName')} — ID {s.get('id')}": s.get("id")
                    for s in momira_suppliers
                }
                selected_label = st.selectbox("Select Supplier", list(supplier_options.keys()), key="hp_supplier_select")
                supplier_id_choice = str(supplier_options[selected_label])
            if st.button("🔄 Refresh supplier list", key="hp_refresh_suppliers"):
                st.session_state.suppliers_cache = None
                st.rerun()
        else:
            st.error("Could not load the supplier list from Travel Compositor.")
            with st.expander("⚠️ Emergency manual entry"):
                st.caption("Only use this if the supplier list above failed to load - type the numeric Travel Compositor supplier ID directly.")
                supplier_id_choice = st.text_input("Supplier ID (numeric)", value="", key="hp_supplier_manual")

        provider_code_in = st.text_input(
            "Hotel code (providerCode)", value="", key="hp_provider_code",
            help="Human-assigned, unlike every other product type - e.g. CAI-H1. This is the identifier "
                 "Travel Compositor keys the whole contract off, for both create and update. Re-using an "
                 "existing code updates that hotel; a new code creates a new one."
        )

        # CONFIRMED REAL RULE (product owner): an UPDATE never asks for things the live record
        # already has - the currency in particular, since it can't be changed after creation and
        # a rate sheet only ever changes prices, not the currency a live contract is denominated
        # in (same rule already applied to ClosedTour/Ticket's ACTION_FIELDS). Hotel already has a
        # human-assigned code available at this point (unlike Transfer/Transport), so - unlike
        # those two - we CAN check existence right here, before asking currency at all, instead of
        # asking it unconditionally and only warning it'll be ignored.
        _hp_precheck_snapshot = None
        _hp_precheck_code = provider_code_in.strip()
        if supplier_id_choice and _hp_precheck_code:
            if "_hotel_exists_cache" not in st.session_state:
                st.session_state._hotel_exists_cache = {}
            _cache = st.session_state._hotel_exists_cache
            _cache_key = (str(supplier_id_choice), _hp_precheck_code)
            if _cache_key not in _cache:
                try:
                    _snap = client.get_hotel(supplier_id_choice, _hp_precheck_code)
                    _cache[_cache_key] = _snap if isinstance(_snap, dict) and "error" not in _snap else None
                except Exception:
                    _cache[_cache_key] = None
            _hp_precheck_snapshot = _cache[_cache_key]

        if _hp_precheck_snapshot:
            currency_in = _hp_precheck_snapshot.get("currency")
            st.info(f"📌 Hotel code **{_hp_precheck_code}** already exists "
                    f"(“{_hp_precheck_snapshot.get('hotelname')}”) - publishing will UPDATE it, so the "
                    f"currency it's already denominated in (**{currency_in}**) is used automatically; "
                    f"no need to ask again.")
        else:
            currency_in = st.selectbox("Currency", CURRENCY_OPTIONS, key="hp_currency")
            st.caption("Only asked for a NEW hotel. Once a hotel code you enter above is recognized as "
                      "existing, this question is skipped and the live currency is used instead.")

        release_days_in = st.number_input(
            "Release Days (days before arrival this hotel becomes bookable)",
            min_value=0, value=7, key="hp_release_days",
            help="Confirmed real field name is releaseDays - real value seen in live data = 7."
        )

        if st.button("➡️ Continue to Step 3", type="primary",
                     disabled=not (supplier_id_choice and provider_code_in.strip()), key="hp_continue1"):
            st.session_state.hp_cfg_supplier_id = supplier_id_choice
            st.session_state.hp_cfg_provider_code = provider_code_in.strip()
            st.session_state.hp_cfg_currency = currency_in
            st.session_state.hp_cfg_release_days = release_days_in
            # Carry the precheck forward so Phase 2's existence check doesn't need to repeat the
            # same GET we just made - same cache shape it already uses.
            if _hp_precheck_snapshot is not None:
                st.session_state.hp_existing_snapshot = _hp_precheck_snapshot
                st.session_state.hp_existing_checked = True
            st.session_state.hp_step1_confirmed = True
            st.rerun()
        return

    supplier_id = st.session_state.hp_cfg_supplier_id
    provider_code = st.session_state.hp_cfg_provider_code
    currency = st.session_state.hp_cfg_currency
    release_days = st.session_state.hp_cfg_release_days

    service_notes.render_standing_note_editor(supplier_id, "Hotel", key_suffix="_setup")
    cancellation_links.render_cancellation_link_editor(supplier_id, "Hotel", key_suffix="_setup")

    if "hp_phase" not in st.session_state:
        st.session_state.hp_phase = "gather"

    # ------------------------------------------------------------------
    # PHASE 1: gather source + extract
    # ------------------------------------------------------------------
    if st.session_state.hp_phase == "gather":
        st.header("Hotel — Step 3: Input Source")
        st.caption("A hotel contract normally covers ONE property: its rooms and allowed occupancy "
                  "combinations, meal plans, any offers/supplements, and the rate seasons with a price per "
                  "occupancy combination per room.")
        hp_url = st.text_input("Hotel page URL (optional)", key="hp_url")
        hp_files = st.file_uploader("Upload document(s) (optional)", type=["pdf", "docx", "xlsx"],
                                     accept_multiple_files=True, key="hp_files")
        hp_hint = st.text_input("Extraction hint (optional)", key="hp_hint")

        if not (hp_url or hp_files):
            st.info("Provide a URL and/or upload document(s) above, then click below.")
        if st.button("🔎 Extract Hotel Contract", type="primary", disabled=not (hp_url or hp_files)):
            with st.spinner("Gathering content and extracting the hotel contract..."):
                try:
                    combined_parts = []
                    if hp_url:
                        page_text, page_text_err = _fetch_url_text_safe(hp_url)
                        if page_text is not None:
                            combined_parts.append(f"--- SOURCE: WEB PAGE ({hp_url}) ---\n{page_text}")
                        else:
                            st.warning(f"⚠️ Couldn't fetch the hotel page URL: {page_text_err}.")
                    for uploaded in (hp_files or []):
                        suffix = os.path.splitext(uploaded.name)[1]
                        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                            tmp.write(uploaded.getbuffer())
                            tmp_path = tmp.name
                        _doc_text = extract_raw_text(tmp_path)
                        _scan_warning = document_reader_scanned_warning(tmp_path, _doc_text)
                        if _scan_warning:
                            st.session_state.setdefault("_scanned_doc_warnings", []).append(_scan_warning)
                        combined_parts.append(f"--- SOURCE: UPLOADED DOCUMENT ({uploaded.name}) ---\n{_doc_text}")
                        os.remove(tmp_path)

                    if not combined_parts:
                        st.error("Nothing to extract - the hotel page URL couldn't be fetched and no document(s) were provided.")
                        st.stop()

                    raw_text = "\n\n".join(combined_parts)

                    # A combined rate sheet covering several properties is rare but real - warn rather
                    # than silently merging two hotels' rooms/rates into one contract.
                    detected = detect_hotel_products(raw_text)
                    hotel_hint = None
                    if len(detected) > 1:
                        st.warning(f"⚠️ This document appears to describe {len(detected)} different hotel "
                                  f"properties: {', '.join(h.get('label', '?') for h in detected)}. Only the "
                                  f"FIRST is being extracted - run this flow again with a different hotel code "
                                  f"for each of the others.")
                        hotel_hint = detected[0].get("hotelname_hint") or detected[0].get("label")

                    st.session_state.hp_raw_text = raw_text
                    st.session_state.hp_data = extract_hotel_data(raw_text, hotel_hint=hotel_hint, human_hint=hp_hint)
                    # Only fills in when this document didn't state its own cancellation
                    # terms - see apply_cancellation_link_default's docstring. Runs once,
                    # here at extraction time, not inside the review widgets.
                    st.session_state.hp_cancellation_link_scope = cancellation_links.apply_cancellation_link_default(
                        st.session_state.hp_data, supplier_id, "Hotel")
                    st.session_state.hp_phase = "reviewing"
                    st.rerun()
                except Exception as e:
                    st.error(f"Extraction failed: {friendly_error_message(e)}")
        return

    # ------------------------------------------------------------------
    # PHASE 2: review everything, then publish
    # ------------------------------------------------------------------
    data = st.session_state.hp_data
    HP_STATE_KEYS = ["hp_phase", "hp_raw_text", "hp_data", "hp_existing_snapshot", "hp_existing_checked",
                     "hp_cancellation_link_scope"]

    st.header(f"Hotel — Step 4: Review “{data.get('hotelname') or '(unnamed)'}”")

    if st.button("🔙 Start over with a different document", key="hp_cancel"):
        for key in HP_STATE_KEYS:
            st.session_state.pop(key, None)
        # keep the supplier/hotel-code setup: the button says "a different DOCUMENT", so
        # sweeping hp_cfg_* as well (they share the "hp_" prefix) made the operator retype
        # the supplier and hotel code every time.
        _clear_batch_widget_state(["hp_"] + SHARED_WIDGET_STATE_PREFIXES,
                                  keep=["hp_cfg_supplier_id", "hp_cfg_provider_code",
                                        "hp_cfg_currency", "hp_cfg_release_days",
                                        "hp_step1_confirmed"])
        st.rerun()

    # ---- Does this hotel code already exist? (decides create vs update) ----
    if not st.session_state.get("hp_existing_checked"):
        with st.spinner(f"Checking whether hotel code {provider_code} already exists..."):
            snapshot = client.get_hotel(supplier_id, provider_code)
        if isinstance(snapshot, dict) and "error" in snapshot:
            st.session_state.hp_existing_snapshot = None
        else:
            st.session_state.hp_existing_snapshot = snapshot
        st.session_state.hp_existing_checked = True

    existing_snapshot = st.session_state.get("hp_existing_snapshot")
    if existing_snapshot:
        st.info(f"📌 Hotel code **{provider_code}** already exists in Travel Compositor "
                f"(“{existing_snapshot.get('hotelname')}”, contract {existing_snapshot.get('contractId')}). "
                f"Publishing will UPDATE it. Rooms and meal plans already there that this document doesn't "
                f"mention are preserved, not dropped.")
        # CONFIRMED REAL RULE (product owner): same "look before you update" safety bar just
        # applied to Transfer/Transport's tracked matches - a human should see what already
        # exists BEFORE editing starts, not find out only when rooms/rates get silently merged
        # at publish time. Rooms/Rates are matched by NAME (hotel_matcher.match_room_by_name /
        # match_rate_by_name) at build time - shown here purely as a heads-up list, not yet an
        # interactive picker, so the human knows which names to reuse for an update to land on
        # the right existing room/rate instead of accidentally creating a near-duplicate.
        existing_rooms = existing_snapshot.get("rooms") or []
        existing_rates = existing_snapshot.get("rates") or []
        with st.expander(f"📋 What's already there ({len(existing_rooms)} room(s), {len(existing_rates)} rate(s)) "
                         f"- reuse these exact names below to update rather than duplicate", expanded=True):
            if existing_rooms:
                st.markdown("**Existing rooms:** " + ", ".join(
                    f"`{r.get('name') or '(unnamed)'}`" for r in existing_rooms if isinstance(r, dict)))
            else:
                st.caption("No rooms on the existing record yet.")
            if existing_rates:
                st.markdown("**Existing rates:** " + ", ".join(
                    f"`{r.get('name') or '(unnamed)'}`" for r in existing_rates if isinstance(r, dict)))
            else:
                st.caption("No rates on the existing record yet.")
    else:
        st.info(f"🆕 Hotel code **{provider_code}** isn't in Travel Compositor yet - publishing will CREATE it.")
    if st.button("🔄 Re-check", key="hp_recheck"):
        st.session_state.hp_existing_checked = False
        st.rerun()

    # ---- Hotel basics ----
    st.markdown("#### Property")
    bcol1, bcol2, bcol3 = st.columns(3)
    with bcol1:
        editable_field("Hotel name", data, "hotelname")
    with bcol2:
        editable_field("Category", data, "category")
    with bcol3:
        editable_field("Chain", data, "chain")

    address = data.get("address") or {}
    data["address"] = address
    acol1, acol2, acol3 = st.columns(3)
    with acol1:
        editable_field("Street address", address, "address")
    with acol2:
        editable_field("City / location", address, "location_name")
    with acol3:
        editable_field("Postal code", address, "postal_code")
    acol4, acol5, acol6 = st.columns(3)
    with acol4:
        editable_field("Country", address, "country")
    with acol5:
        editable_field("Phone", address, "phone")
    with acol6:
        editable_field("Email", address, "email")

    editable_field("Description", data, "description", widget="text_area", height=110)

    gcol1, gcol2, gcol3, gcol4 = st.columns(4)
    with gcol1:
        data["infants_allowed"] = st.number_input("Infants allowed (max per booking)", min_value=0,
                                                    value=_safe_int(data.get("infants_allowed"), fallback=2),
                                                    key="hp_infants")
    with gcol2:
        data["min_children_age"] = st.number_input("Min children age", min_value=0,
                                                     value=_safe_int(data.get("min_children_age"), fallback=0),
                                                     key="hp_minchildage")
    with gcol3:
        data["max_children_age"] = st.number_input("Max children age", min_value=0,
                                                     value=_safe_int(data.get("max_children_age"), fallback=12),
                                                     key="hp_maxchildage")
    with gcol4:
        data["minimum_stay"] = st.number_input("Minimum stay (nights)", min_value=1,
                                                 value=_safe_int(data.get("minimum_stay"), fallback=1),
                                                 key="hp_minstay")
    st.caption("This API supports only ONE children age range (unlike the Travel Compositor admin screen's "
              "up-to-4-range widget), so infants and children share one combined band - 0-12 by default.")

    img_df = pd.DataFrame({"url": data.get("images") or [""]})

    def _hp_save_images(edited_df):
        data["images"] = [str(u).strip() for u in edited_df["url"].tolist() if str(u or "").strip()]

    editable_table("Image URLs", img_df, "hp_images", on_save=_hp_save_images)

    # ---- Rooms ----
    st.markdown("#### Rooms")
    st.caption("“Allowed distributions” uses Travel Compositor's own Adult+child shorthand, e.g. "
              "`1+0, 2+0, 2+1` means 1 adult; 2 adults; 2 adults + 1 child. Any combination totalling more "
              "than 9 people is dropped automatically (system cap).")
    rooms_df = pd.DataFrame([
        {"name": r.get("name", ""), "allowed_distributions": _hp_dist_to_str(r.get("distributions"))}
        for r in (data.get("rooms") or [{"name": "", "distributions": []}])
    ])

    # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): captured before editing, so
    # _hp_save_rooms can tell a RENAME (same row, new text) apart from a genuinely new/removed
    # room - see the rename-propagation note inside it.
    _hp_original_room_names = [r.get("name", "") for r in (data.get("rooms") or [])]

    def _hp_save_rooms(edited_df):
        rows = []
        renamed_pairs = []
        for pos, (_, row) in enumerate(edited_df.iterrows()):
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            rows.append({"name": name, "type_id": None,
                          "distributions": _hp_str_to_dist(row.get("allowed_distributions"))})
            old_name = _hp_original_room_names[pos] if pos < len(_hp_original_room_names) else None
            if old_name and old_name != name:
                renamed_pairs.append((old_name, name))
        data["rooms"] = rows
        # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): every season's room_prices and
        # every rate's stop_sales are keyed by room NAME (see build_hotel_rate_payloads), not a
        # stable id - renaming a room here used to leave those rows keyed under the OLD name,
        # which then simply doesn't match any name in the new room list and gets silently
        # dropped (see the room_prices carry-forward filter further down this screen). Propagate
        # the rename into every season/rate that referenced the old name, by row position (this
        # table's rows aren't reordered by anything else on this screen), instead of losing
        # already-entered prices/stop-sales just because a room got renamed.
        if renamed_pairs:
            rename_map = dict(renamed_pairs)
            for rate in (data.get("rates") or []):
                for season in (rate.get("seasons") or []):
                    for rp in (season.get("room_prices") or []):
                        if rp.get("room_name") in rename_map:
                            rp["room_name"] = rename_map[rp["room_name"]]
                for ss in (rate.get("stop_sales") or []):
                    if ss.get("room_name") in rename_map:
                        ss["room_name"] = rename_map[ss["room_name"]]

    editable_table("Room types", rooms_df, "hp_rooms", on_save=_hp_save_rooms)
    room_names = [r.get("name") for r in (data.get("rooms") or []) if r.get("name")]
    if not room_names:
        st.warning("⚠️ At least one room is required - Travel Compositor rejects a hotel contract with none.")
    rooms_missing_dist = [r.get("name") for r in (data.get("rooms") or []) if not r.get("distributions")]
    if rooms_missing_dist:
        st.warning(f"⚠️ These rooms have no allowed distributions and can't publish: {', '.join(rooms_missing_dist)}")

    # ---- Meal plans ----
    st.markdown("#### Meal plans")
    st.caption("Room Only is always added automatically at 0 cost - only list the paid add-ons here. "
              "Base price = the 1st adult's cost; the extra-adult/child columns are comma-separated per "
              "additional person (e.g. `0, 40` = 2nd adult free, 3rd adult +40).")
    mp_df = pd.DataFrame([
        {"meal_plan": m.get("meal_plan_hint", ""), "base_price": _safe_float(m.get("base_price")),
         "extra_adult_prices": _hp_nums_to_str(m.get("adult_prices")),
         "child_prices": _hp_nums_to_str(m.get("child_prices"))}
        for m in (data.get("meal_plans") or [{"meal_plan_hint": "", "base_price": 0.0}])
    ])

    def _hp_save_meal_plans(edited_df):
        rows = []
        for _, row in edited_df.iterrows():
            hint = str(row.get("meal_plan") or "").strip()
            if not hint:
                continue
            rows.append({"meal_plan_hint": hint, "base_price": _safe_float(row.get("base_price"), fallback=0.0),
                          "adult_prices": _hp_str_to_nums(row.get("extra_adult_prices")),
                          "child_prices": _hp_str_to_nums(row.get("child_prices"))})
        data["meal_plans"] = rows

    editable_table("Meal plans (mapped onto Room Only / B&B / Half Board / Full Board / All Inclusive)",
                   mp_df, "hp_mealplans", on_save=_hp_save_meal_plans)

    # ---- Offers ----
    st.markdown("#### Offers (discounts)")
    st.caption("Type: PERCENT (e.g. 10% off), ABSOLUTE (a fixed amount off), or STAY_TO_PAY (stay 7 pay 6 - "
              "fill Stay/Pay). Apply: LODGING, MEAL, LODGING_AND_MEAL, PER_NIGHT, PER_NIGHT_PERSON, PER_STAY "
              "or PER_STAY_PERSON. Travel window = when the guest STAYS; booking window = when they must BOOK. "
              "Leave Rooms blank to apply to every room.")
    offers_df = pd.DataFrame([
        {"name": o.get("name", ""), "type": o.get("type", "PERCENT"), "apply": o.get("apply", "LODGING"),
         "value": _safe_float(o.get("value")), "child_value": _safe_float(o.get("child_value")),
         "stay": o.get("stay"), "pay": o.get("pay"), "min_stay": o.get("minimum_stay"),
         "travel_start": _hp_first_window(o.get("travel_windows"), "start"),
         "travel_end": _hp_first_window(o.get("travel_windows"), "end"),
         "booking_start": _hp_first_window(o.get("booking_windows"), "start"),
         "booking_end": _hp_first_window(o.get("booking_windows"), "end"),
         "rooms": _hp_names_to_str(o.get("room_names"))}
        for o in (data.get("offers") or [])
    ] or [{"name": "", "type": "PERCENT", "apply": "LODGING", "value": 0.0, "child_value": 0.0,
           "stay": None, "pay": None, "min_stay": None, "travel_start": "", "travel_end": "",
           "booking_start": "", "booking_end": "", "rooms": ""}])

    def _hp_save_offers(edited_df):
        rows = []
        for _, row in edited_df.iterrows():
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            rows.append({
                "name": name,
                "type": str(row.get("type") or "PERCENT").strip().upper(),
                "apply": str(row.get("apply") or "LODGING").strip().upper(),
                "value": _safe_float(row.get("value"), fallback=0.0),
                "child_value": _safe_float(row.get("child_value"), fallback=0.0),
                "stay": None if pd.isna(row.get("stay")) else _safe_int(row.get("stay")),
                "pay": None if pd.isna(row.get("pay")) else _safe_int(row.get("pay")),
                "minimum_stay": None if pd.isna(row.get("min_stay")) else _safe_int(row.get("min_stay")),
                "travel_windows": _hp_window_list(row.get("travel_start"), row.get("travel_end")),
                "booking_windows": _hp_window_list(row.get("booking_start"), row.get("booking_end")),
                "room_names": _hp_str_to_names(row.get("rooms")),
            })
        data["offers"] = rows

    editable_table("Offers", offers_df, "hp_offers", on_save=_hp_save_offers)

    # ---- Supplements ----
    st.markdown("#### Supplements (extra charges)")
    st.caption("Same shape as Offers, but type is only PERCENT or ABSOLUTE. A hotel supplement is never "
              "optional - it is always an extra charge the client pays.")
    st.caption("⚠️ **Keep the name plain.** Travel Compositor only ever shows the client the supplement's "
              "one total price, never a per-night breakdown - so a name with a date, a night count, or "
              "\"per night\"/\"per stay\" in it reads as confusing next to that total. Write \"Resort Fee\", "
              "not \"Resort Fee (per night, 1 Dec–31 Jan)\" - the date and basis are already captured by "
              "travel_start/travel_end and apply below.")
    st.caption("⚠️ **apply must be filled in by you.** The AI leaves it blank whenever the document doesn't "
              "state the basis outright, because 'per person' can mean once for the whole stay "
              "(PER_STAY_PERSON) or once per person per night (PER_NIGHT_PERSON) - on a 7-night stay those "
              "differ sevenfold. One of: "
              + ", ".join(HOTEL_APPLY_VALUES) + ". A supplement left blank will not publish.")
    supp_df = pd.DataFrame([
        {"name": s.get("name", ""), "type": s.get("type", "ABSOLUTE"), "apply": s.get("apply", ""),
         "value": _safe_float(s.get("value")), "child_value": _safe_float(s.get("child_value")),
         "travel_start": _hp_first_window(s.get("travel_windows"), "start"),
         "travel_end": _hp_first_window(s.get("travel_windows"), "end"),
         "rooms": _hp_names_to_str(s.get("room_names"))}
        for s in (data.get("supplements") or [])
    ] or [{"name": "", "type": "ABSOLUTE", "apply": "", "value": 0.0, "child_value": 0.0,
           "travel_start": "", "travel_end": "", "rooms": ""}])

    def _hp_save_supplements(edited_df):
        rows = []
        for _, row in edited_df.iterrows():
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            rows.append({
                "name": name,
                "type": str(row.get("type") or "ABSOLUTE").strip().upper(),
                # NO DEFAULT, deliberately: an unfilled basis stays unfilled all the way to the
                # builder, which refuses to publish it. Defaulting here would reinstate exactly
                # the guess this rule exists to prevent.
                "apply": str(row.get("apply") or "").strip().upper(),
                "value": _safe_float(row.get("value"), fallback=0.0),
                "child_value": _safe_float(row.get("child_value"), fallback=0.0),
                "travel_windows": _hp_window_list(row.get("travel_start"), row.get("travel_end")),
                "room_names": _hp_str_to_names(row.get("rooms")),
            })
        data["supplements"] = rows

    editable_table("Supplements", supp_df, "hp_supplements", on_save=_hp_save_supplements)
    _missing_apply = [s.get("name") or "(unnamed)" for s in (data.get("supplements") or [])
                      if str(s.get("apply") or "").strip().upper() not in HOTEL_APPLY_VALUES]
    if _missing_apply:
        st.warning("⚠️ These supplements still have no **apply** basis and will not publish until one is "
                  "chosen: " + ", ".join(_missing_apply) + ". Pick from: " + ", ".join(HOTEL_APPLY_VALUES) + ".")

    # ---- Rates / seasons / prices ----
    st.markdown("#### Rates, seasons & prices")
    if not data.get("rates"):
        data["rates"] = [{"name": data.get("hotelname") or "Standard Rates", "minimum_stay": 1,
                          "seasons": [], "stop_sales": [], "offer_names": [], "supplement_names": []}]

    for r_idx, rate in enumerate(data["rates"]):
        with st.expander(f"Rate: {rate.get('name') or '(unnamed)'} "
                          f"({len(rate.get('seasons') or [])} season(s))", expanded=(r_idx == 0)):
            editable_field("Rate name", rate, "name", key_suffix=f"_hprate{r_idx}")

            rate["offer_names"] = _hp_str_to_names(st.text_input(
                "Offers applied to this rate (comma-separated, must match names above)",
                value=_hp_names_to_str(rate.get("offer_names")), key=f"hp_rate_offers_{r_idx}"))
            rate["supplement_names"] = _hp_str_to_names(st.text_input(
                "Supplements applied to this rate (comma-separated, must match names above)",
                value=_hp_names_to_str(rate.get("supplement_names")), key=f"hp_rate_supps_{r_idx}"))

            if not rate.get("seasons"):
                st.warning("⚠️ This rate has no seasons - add at least one with dates and prices below.")
                if st.button("➕ Add a season", key=f"hp_addseason_{r_idx}"):
                    rate.setdefault("seasons", []).append(
                        {"name": "Season 1", "date_ranges": [], "price_type": "DISTRIBUTION",
                         "minimum_stay": 1, "room_prices": [], "meal_plans": []})
                    st.rerun()

            for s_idx, season in enumerate(rate.get("seasons") or []):
                st.markdown(f"**Season {s_idx + 1}: {season.get('name') or '(unnamed)'}**")
                scol1, scol2 = st.columns([3, 1])
                with scol1:
                    editable_field("Season name", season, "name", key_suffix=f"_hpseason{r_idx}_{s_idx}")
                with scol2:
                    season["price_type"] = st.selectbox(
                        "Price type", ["DISTRIBUTION", "PAX"],
                        index=0 if (season.get("price_type") or "DISTRIBUTION").upper() != "PAX" else 1,
                        key=f"hp_pricetype_{r_idx}_{s_idx}",
                        help="DISTRIBUTION = one flat price per adults+children combination (the normal case). "
                             "PAX = a base rate plus incremental per-extra-person charges."
                    )

                dr_df = pd.DataFrame(season.get("date_ranges") or [{"start": "", "end": ""}])
                for col in ["start", "end"]:
                    if col not in dr_df.columns:
                        dr_df[col] = ""

                def _hp_save_date_ranges(edited_df, _season=season):
                    rows = []
                    for _, row in edited_df.iterrows():
                        start, end = str(row.get("start") or "").strip(), str(row.get("end") or "").strip()
                        if start and end:
                            rows.append({"start": start, "end": end})
                    _season["date_ranges"] = rows

                editable_table("Season date ranges (DD/MM/YYYY; several rows allowed for a split season)",
                               dr_df, f"hp_dr_{r_idx}_{s_idx}", on_save=_hp_save_date_ranges)

                # One priced-distribution table per room in this season.
                existing_room_prices = {rp.get("room_name"): rp for rp in (season.get("room_prices") or [])}
                for rm_name in room_names:
                    rp = existing_room_prices.get(rm_name) or {"room_name": rm_name, "units_quota": 20,
                                                                "units_on_request": 0, "distribution_prices": []}
                    existing_room_prices[rm_name] = rp

                    qcol1, qcol2 = st.columns(2)
                    with qcol1:
                        rp["units_quota"] = st.number_input(
                            f"{rm_name} — quota (rooms allotted)", min_value=0,
                            value=_safe_int(rp.get("units_quota"), fallback=20),
                            key=f"hp_quota_{r_idx}_{s_idx}_{rm_name}",
                            help="Defaults to 20 when the contract doesn't state an allotment.")
                    with qcol2:
                        rp["units_on_request"] = st.number_input(
                            f"{rm_name} — on request", min_value=0,
                            value=_safe_int(rp.get("units_on_request"), fallback=0),
                            key=f"hp_onreq_{r_idx}_{s_idx}_{rm_name}",
                            help="Defaults to 0 when the contract doesn't state one.")

                    dp_df = pd.DataFrame(rp.get("distribution_prices") or [{"adults": 1, "children": 0, "amount": 0.0}])
                    for col in ["adults", "children", "amount"]:
                        if col not in dp_df.columns:
                            dp_df[col] = None

                    def _hp_save_dist_prices(edited_df, _rp=rp):
                        rows = []
                        for _, row in edited_df.iterrows():
                            if pd.isna(row.get("adults")) or pd.isna(row.get("amount")):
                                continue
                            rows.append({
                                "adults": _safe_int(row.get("adults"), fallback=1),
                                "children": 0 if pd.isna(row.get("children")) else _safe_int(row.get("children")),
                                "amount": _safe_float(row.get("amount"), fallback=0.0),
                            })
                        _rp["distribution_prices"] = rows

                    editable_table(f"{rm_name} — price per occupancy combination", dp_df,
                                   f"hp_dp_{r_idx}_{s_idx}_{rm_name}", on_save=_hp_save_dist_prices)

                season["room_prices"] = [existing_room_prices[n] for n in room_names if n in existing_room_prices]
                st.divider()

            if st.button("➕ Add another season", key=f"hp_addseason2_{r_idx}"):
                rate.setdefault("seasons", []).append(
                    {"name": f"Season {len(rate.get('seasons') or []) + 1}", "date_ranges": [],
                     "price_type": "DISTRIBUTION", "minimum_stay": 1, "room_prices": [], "meal_plans": []})
                st.rerun()

            # ---- Stop sales ----
            st.markdown("**Stop sales (blackout dates per room)**")
            st.caption("⚠️ Submitted by room NAME only - Travel Compositor's API never exposes the numeric "
                      "room id these normally reference, so this relies on the server matching by name. Not "
                      "yet confirmed against a live upload; check the result in Travel Compositor afterwards.")
            ss_df = pd.DataFrame([
                {"room_name": s.get("room_name", ""),
                 "start": _hp_first_window(s.get("date_ranges"), "start"),
                 "end": _hp_first_window(s.get("date_ranges"), "end")}
                for s in (rate.get("stop_sales") or [])
            ] or [{"room_name": "", "start": "", "end": ""}])

            def _hp_save_stop_sales(edited_df, _rate=rate):
                rows = []
                for _, row in edited_df.iterrows():
                    rm = str(row.get("room_name") or "").strip()
                    windows = _hp_window_list(row.get("start"), row.get("end"))
                    if rm and windows:
                        rows.append({"room_name": rm, "date_ranges": windows})
                _rate["stop_sales"] = rows

            editable_table("Stop sales", ss_df, f"hp_ss_{r_idx}", on_save=_hp_save_stop_sales)

    # ---- Cancellation ----
    st.markdown("#### Cancellation policy")
    st.caption("Hotel has no structured cancellation field at all, so this text is what actually reaches "
              "Voucher Remarks - the only place the policy is visible to staff and customers.")
    if st.session_state.get("hp_cancellation_link_scope"):
        st.caption(f"ℹ️ This document didn't state its own cancellation terms - the table below "
                  f"was filled in from {st.session_state['hp_cancellation_link_scope']}. Edit or "
                  f"clear it if this hotel needs different terms.")
    render_cancellation_policy_editor(data, "hp_cancel")
    editable_field("Cancellation policy text (customer-facing summary)", data, "cancellation_policy_text",
                   widget="text_area", height=90)

    service_notes.render_notes_editor(supplier_id, "Hotel", data)

    # ------------------------------------------------------------------
    # PUBLISH - two phases, in order
    # ------------------------------------------------------------------
    st.markdown("#### Publish")
    pre_config = HotelHumanPreConfig(supplier_id=supplier_id, provider_code=provider_code,
                                      currency=currency, days_available_before_release=release_days)
    contract_result = build_hotel_contract_payload(pre_config, data, existing_hotel_snapshot=existing_snapshot)

    if contract_result.get("hotel_error"):
        st.error(f"⚠️ This hotel can't be built yet: {contract_result['hotel_error']}")
        return

    with st.expander("🔎 Preview hotel contract payload (phase 1)"):
        st.json(contract_result["hotel_payload"])

    seasons_total = sum(len(r.get("seasons") or []) for r in (data.get("rates") or []))
    priced_rooms = sum(
        1 for r in (data.get("rates") or []) for s in (r.get("seasons") or [])
        for rp in (s.get("room_prices") or []) if rp.get("distribution_prices")
    )
    st.caption(f"Ready to publish: **{len(contract_result['hotel_payload'].get('rooms') or [])}** room(s), "
              f"**{len(contract_result['hotel_payload'].get('mealPlans') or [])}** meal plan(s), "
              f"**{len(data.get('offers') or [])}** offer(s), **{len(data.get('supplements') or [])}** "
              f"supplement(s), **{len(data.get('rates') or [])}** rate(s) with **{seasons_total}** season(s).")

    rooms_ok = bool(room_names) and not rooms_missing_dist
    # CONFIRMED PRODUCT-OWNER DECISION (2026-08-19 audit): Hotel used to be the only one of the
    # five publish flows that let a record go live with zero priced rooms - just a warning, not
    # a blocked button, unlike Transfer's hard match/dates/geoloc gates. Now blocked to match.
    if not priced_rooms:
        st.error("⚠️ No room in any season has prices yet - publishing is blocked until at least "
                 "one room has a price, so nothing unsellable goes live.")

    if st.button(f"🚀 Publish — {'UPDATE' if existing_snapshot else 'CREATE'} hotel {provider_code}",
                 type="primary", key="hp_publish", disabled=not rooms_ok or not priced_rooms):
        st.session_state.hp_publish_succeeded = False
        progress = st.container()
        try:
            # ---- PHASE 1: the hotel contract itself (rooms + meal plans inline) ----
            with st.spinner("Phase 1 of 2 — publishing the hotel contract, rooms and meal plans..."):
                if existing_snapshot:
                    hotel_response = client.update_hotel(supplier_id, contract_result["hotel_payload"])
                else:
                    hotel_response = client.create_hotel(supplier_id, contract_result["hotel_payload"])

            if isinstance(hotel_response, dict) and "error" in hotel_response:
                show_publish_error(f"publish hotel **{provider_code}**", hotel_response)
                return

            progress.success("✅ Phase 1 — hotel contract, rooms and meal plans published.")

            # Travel Compositor assigns each room its providerCode here - phase 2 can't run without them.
            room_map = resolve_room_provider_codes(hotel_response.get("rooms") or [])
            unresolved = [n for n in room_names if not room_map.get(n)]
            if unresolved:
                progress.warning(f"⚠️ Travel Compositor didn't return a code for these room(s): "
                                f"{', '.join(unresolved)}. Their prices will be skipped in phase 2.")

            # ---- PHASE 2a: offers ----
            offer_map = {}
            offer_results = build_hotel_offer_payloads(data.get("offers") or [], room_map,
                                                        existing_hotel_snapshot=existing_snapshot)
            offer_failures = []
            with st.spinner("Phase 2 of 2 — publishing offers..."):
                for offer_data, res in zip(data.get("offers") or [], offer_results):
                    name = offer_data.get("name")
                    if res["action"] == "skip_duplicate":
                        offer_map[name] = res.get("matched_provider_code")
                        # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): a skipped
                        # duplicate can now carry offer_error when the document's value changed
                        # (see build_hotel_offer_payloads) - surface it instead of silently
                        # treating the skip as a no-op success.
                        if res.get("offer_error"):
                            offer_failures.append((name, res.get("offer_error")))
                        continue
                    if res.get("offer_error") or not res.get("offer_payload"):
                        offer_failures.append((name, res.get("offer_error")))
                        continue
                    resp = client.create_hotel_offer(supplier_id, provider_code, res["offer_payload"])
                    if isinstance(resp, dict) and "error" in resp:
                        offer_failures.append((name, resp.get("message")))
                    else:
                        offer_map[name] = resp.get("providerCode") if isinstance(resp, dict) else None

            # ---- PHASE 2b: supplements ----
            supplement_map = {}
            supp_results = build_hotel_supplement_payloads(data.get("supplements") or [], room_map,
                                                            existing_hotel_snapshot=existing_snapshot)
            supp_failures = []
            with st.spinner("Phase 2 of 2 — publishing supplements..."):
                for supp_data, res in zip(data.get("supplements") or [], supp_results):
                    name = supp_data.get("name")
                    if res["action"] == "skip_duplicate":
                        supplement_map[name] = res.get("matched_provider_code")
                        # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): see the matching
                        # fix for offers above.
                        if res.get("supplement_error"):
                            supp_failures.append((name, res.get("supplement_error")))
                        continue
                    if res.get("supplement_error") or not res.get("supplement_payload"):
                        supp_failures.append((name, res.get("supplement_error")))
                        continue
                    resp = client.create_hotel_supplement(supplier_id, provider_code, res["supplement_payload"])
                    if isinstance(resp, dict) and "error" in resp:
                        supp_failures.append((name, resp.get("message")))
                    else:
                        supplement_map[name] = resp.get("providerCode") if isinstance(resp, dict) else None

            # ---- PHASE 2c: rates (needs the room/offer/supplement codes resolved above) ----
            rate_results = build_hotel_rate_payloads(data.get("rates") or [], room_map, offer_map,
                                                      supplement_map, existing_hotel_snapshot=existing_snapshot)
            rate_failures = []
            with st.spinner("Phase 2 of 2 — publishing rates and seasons..."):
                for res in rate_results:
                    if res.get("rate_error") or not res.get("rate_payload"):
                        # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): this used to read
                        # res.get("rate_payload", {}).get("name") - rate_payload is present but
                        # set to None on exactly the failure path being handled here, and
                        # dict.get(key, default) only falls back to `default` when the KEY is
                        # absent, not when its value is None, so this raised an unhandled
                        # 'NoneType' object has no attribute 'get' AFTER rooms/offers/supplements
                        # were already published, with no name for the offending rate. Use the
                        # dedicated rate_name field (always present) instead.
                        rate_failures.append((res.get("rate_name"), res.get("rate_error")))
                        continue
                    if res["action"] == "update":
                        resp = client.update_hotel_rates(supplier_id, provider_code, res["rate_payload"])
                    else:
                        resp = client.create_hotel_rates(supplier_id, provider_code, res["rate_payload"])
                    if isinstance(resp, dict) and "error" in resp:
                        rate_failures.append((res["rate_payload"].get("name"), resp.get("message")))

            all_failures = offer_failures + supp_failures + rate_failures
            if all_failures:
                st.error("⚠️ The hotel contract published, but some parts failed:\n\n" + "\n".join(
                    f"- **{name or '(unnamed)'}**: {err}" for name, err in all_failures
                ) + "\n\nFix the details above and publish again - re-running is safe: rooms, rates and "
                    "seasons are matched and updated in place rather than duplicated.")
            else:
                st.balloons()
                st.success(f"🎉 Hotel **{provider_code}** published in full — contract, rooms, meal plans, "
                          f"offers, supplements and {seasons_total} season(s) of prices.")
                # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): "Start a new Hotel" used
                # to be a button nested inside `if st.button("🚀 Publish...")` - that outer
                # button's own value is only True on the EXACT render where it was clicked, so on
                # the very next rerun (the one clicking "Start a new Hotel" itself triggers), the
                # outer button is False again, this whole branch never re-executes, and the inner
                # button's click is never evaluated - a dead no-op. Fixed by persisting the
                # success into session_state instead, and rendering "Start a new Hotel" from a
                # separate, unnested check below that survives the rerun.
                st.session_state.hp_publish_succeeded = True
        except Exception as e:
            # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): an exception anywhere in
            # Phase 2 (offers/supplements/rates) used to show the exact same generic "couldn't
            # publish hotel" message as a Phase-1 failure - but by the time Phase 2 can even run,
            # the contract/rooms/meal plans (and possibly some offers/supplements/rates) are
            # ALREADY live. Say so, so the operator doesn't assume nothing happened and re-run
            # from scratch expecting a clean slate.
            show_publish_error(
                f"finish publishing hotel **{provider_code}** — note: the contract, rooms and "
                f"meal plans (Phase 1 above) may already be live even though this failed",
                str(e))

    # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): rendered here, OUTSIDE the outer
    # "🚀 Publish" button's `if` block, so it actually survives the rerun its own click causes -
    # see the note where hp_publish_succeeded is set, above.
    if st.session_state.get("hp_publish_succeeded"):
        if st.button("🆕 Start a new Hotel", key="hp_new"):
            for key in HP_STATE_KEYS:
                st.session_state.pop(key, None)
            st.session_state.hp_publish_succeeded = False
            _clear_batch_widget_state(["hp_"] + SHARED_WIDGET_STATE_PREFIXES)
            st.rerun()


# ======================================================================
# ADDING MANUAL INFORMATION
#
# Standing notes reachable on their own, as a Step 1 destination alongside the five product
# types. The task this serves - "the pickup point for every transfer from this supplier
# moved, tell every future upload about it" - has nothing to do with any particular
# document. It happens on its own, prompted by an email from a supplier, and whoever does
# the next upload may know nothing about it. Making it a first-class choice rather than a
# box buried inside a service review screen matches how the work actually arrives.
# ======================================================================
def render_manual_information_flow(client):
    st.header("Adding manual information")
    st.caption("Information a person knows that the supplier's documents don't say — a moved "
              "pickup point, revised cancellation terms, a temporary closure. Saved against a "
              "supplier and a product type, and **added automatically to the Voucher Remarks of "
              "every service of that type you upload from then on**, including uploads done by "
              "someone who never heard about the change. Notes are always *added to* what the "
              "document said; they never replace the cancellation policy or anything extracted.")

    if not platform_store.is_durable():
        st.warning("⚠️ No `DATABASE_URL` is configured, so a note saved here is lost on the next "
                   "redeploy and will not reach future uploads.")

    if st.session_state.suppliers_cache is None:
        with st.spinner("Loading supplier list from Travel Compositor..."):
            try:
                st.session_state.suppliers_cache = client.get_all_suppliers()
            except Exception as e:
                st.error(f"❌ Couldn't load the supplier list: {friendly_error_message(e)}")
                st.session_state.suppliers_cache = []

    supplier_id = None
    momira_suppliers = [
        s for s in (st.session_state.suppliers_cache or [])
        if (s.get("commercialName") or s.get("legalName") or "").strip().lower().startswith("momira_")
    ]
    if momira_suppliers:
        options = {f"{s.get('commercialName') or s.get('legalName')} — ID {s.get('id')}": s.get("id")
                   for s in momira_suppliers}
        chosen = st.selectbox("Which supplier?", list(options.keys()), key="mi_supplier_select")
        supplier_id = str(options[chosen])
        if st.button("🔄 Refresh supplier list", key="mi_refresh_suppliers"):
            st.session_state.suppliers_cache = None
            st.rerun()
    else:
        st.error("Could not load the supplier list from Travel Compositor.")
        with st.expander("⚠️ Emergency manual entry"):
            st.caption("Only use this if the supplier list above failed to load - type the numeric Travel Compositor supplier ID directly.")
            supplier_id = st.text_input("Supplier ID (numeric)", value="", key="mi_supplier_manual")

    product_type = st.radio("Which product type does this apply to?",
                            service_notes.PRODUCT_TYPES, key="mi_product_type", horizontal=True)
    st.caption("A note is scoped to one supplier AND one product type, because a change to how "
              "transfers are picked up says nothing about that supplier's hotels. To cover more "
              "than one, do it once per type.")

    if not supplier_id:
        st.info("Choose a supplier above to write a note.")
        return

    # ---- 1. What are you adding? ---------------------------------------
    # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-14): a Supplement (ClosedTour, applies to
    # every Modality; Transfer) and an Additional Service (Transfer) are useful to bulk-add
    # here too, alongside the plain-text fields this flow already handles. They're
    # structured records, not a block of text, so they get their own small form below
    # rather than the text box - see bulk_notes.STRUCTURED_TARGETS.
    structured_labels = bulk_notes.available_structured_targets(product_type)
    add_structured = False
    structured_kind = None
    item_data = {}
    if structured_labels:
        st.markdown("### 1. What are you adding?")
        add_mode = st.radio(
            "What are you adding?", ["Text into an existing field", "A new Supplement / Additional Service"],
            key="mi_add_mode", label_visibility="collapsed", horizontal=True,
        )
        add_structured = add_mode.startswith("A new")

    if add_structured:
        kind_label = st.selectbox("Which one?", structured_labels, key="mi_structured_kind_label")
        structured_kind = bulk_notes.STRUCTURED_TARGETS[product_type][kind_label]
        st.caption("This ADDS a new entry to every matching service - it never edits or replaces "
                  "anything already there. A service that already has an entry with the same name "
                  "is left alone, so sending twice can't duplicate it.")

        target = kind_label
        text = None
        mode = None
        item_data = {"name": st.text_input("Name", key="mi_s_name",
                                           placeholder="e.g. Resort Fee, Child Seat")}
        if structured_kind == "closedtour_supplement":
            st.caption("Applies to every Modality on the tour - Travel Compositor has no way to "
                      "scope a ClosedTour supplement to just one cabin.")
            c1, c2 = st.columns(2)
            with c1:
                item_data["price"] = st.number_input("Price per person", min_value=0.0, step=1.0,
                                                      key="mi_s_price")
                item_data["mandatory"] = st.checkbox("Mandatory", value=False, key="mi_s_mandatory")
            with c2:
                item_data["on_request"] = st.checkbox("On request", value=False, key="mi_s_on_request")
            item_data["single_price"] = item_data["price"]
            item_data["double_price"] = item_data["price"]
        elif structured_kind == "transfer_supplement":
            st.caption("Mandatory, automatically-applied surcharges only - an optional extra "
                      "belongs under Additional Service instead.")
            c1, c2 = st.columns(2)
            with c1:
                item_data["amount"] = st.number_input("Amount", min_value=0.0, step=1.0, key="mi_s_amount")
                item_data["type"] = st.radio("Type", ["ABSOLUTE", "PERCENT"], key="mi_s_type",
                                             horizontal=True,
                                             help="PERCENT is applied to the base price itself by "
                                                  "Travel Compositor - never pre-calculate it.")
            with c2:
                item_data["start_time"] = st.text_input("Start time (optional, HH:MM)", key="mi_s_start_time",
                                                         placeholder="22:00")
                item_data["end_time"] = st.text_input("End time (optional, HH:MM)", key="mi_s_end_time",
                                                       placeholder="08:00")
            st.caption("Dates left blank inherit each transfer's own validity window.")
        elif structured_kind == "transfer_additional_service":
            st.caption("A genuinely optional extra the client chooses to take, e.g. a child seat.")
            c1, c2 = st.columns(2)
            with c1:
                item_data["price"] = st.number_input("Price", min_value=0.0, step=1.0, key="mi_s_svc_price")
                item_data["currency"] = st.text_input("Currency (optional - defaults to the transfer's own)",
                                                       key="mi_s_svc_currency", placeholder="EUR")
            with c2:
                item_data["max_quantity"] = st.number_input("Maximum quantity", min_value=1, step=1,
                                                             value=1, key="mi_s_svc_max")
                item_data["on_request"] = st.checkbox("On request", value=False, key="mi_s_svc_on_request")

        codes = None
        if bulk_notes.needs_manual_codes(product_type):
            st.info("Travel Compositor has no endpoint that lists closed tours, so they can't be "
                    "found automatically — paste the tour codes, one per line.")
            raw_codes = st.text_area("ClosedTour codes", key="mi_codes", height=80,
                                     placeholder="ASW-CT1\nCAI-CT2")
            codes = [c.strip() for c in (raw_codes or "").splitlines() if c.strip()]
        also_future = False
    else:
        # ---- 1b. Where does the text go? -------------------------------
        st.markdown("### 1. Where should the text go?" if not structured_labels
                    else "### 2. Where should the text go?")
        targets = bulk_notes.available_targets(product_type)
        target = st.selectbox("Field", targets, key="mi_target")
        missing = bulk_notes.unavailable_targets(product_type)
        if missing:
            # Naming what ISN'T possible, and why, stops someone hunting for an option that
            # was never there - which is exactly what happened with the first Hotel note.
            st.caption("Not available on " + product_type + ": "
                       + "  ·  ".join(f"**{k}** — {v}" for k, v in missing.items()))

        # ---- 2. The text ------------------------------------------------
        st.markdown("### 2. What should it say?" if not structured_labels else "### 3. What should it say?")
        text = st.text_area(
            "Text to add to every one of this supplier's " + product_type + " services",
            key="mi_text", height=120,
            placeholder="e.g. All pickups now depart from the new terminal, not the old arrivals hall.",
        )
        mode_label = st.radio(
            "How should it be written?",
            ["Add at the bottom (keep what is already there)",
             "Replace the field completely"],
            key="mi_mode",
        )
        mode = (bulk_notes.MODE_REPLACE if mode_label.startswith("Replace") else bulk_notes.MODE_APPEND)
        if mode == bulk_notes.MODE_REPLACE:
            st.warning("⚠️ Replace deletes whatever is currently in that field — including text "
                       "extracted from the supplier's own contract. There is no undo in Travel "
                       "Compositor. Use it only when the old wording is genuinely superseded.")

        codes = None
        if bulk_notes.needs_manual_codes(product_type):
            st.info("Travel Compositor has no endpoint that lists closed tours, so they can't be "
                    "found automatically — paste the tour codes, one per line.")
            raw_codes = st.text_area("ClosedTour codes", key="mi_codes", height=80,
                                     placeholder="ASW-CT1\nCAI-CT2")
            codes = [c.strip() for c in (raw_codes or "").splitlines() if c.strip()]

        also_future = st.checkbox(
            f"Also attach this to every {product_type} I upload from now on",
            value=True, key="mi_also_future",
            help="Saved as a standing note. Note: on future uploads it is added to the Voucher "
                 "Remarks, which is the field the upload flows write notes into.",
        )

    # ---- 3. Preview, then send ----------------------------------------
    st.markdown("### 3. Check it, then send" if not structured_labels else "### 4. Check it, then send")
    st.caption("Preview reads Travel Compositor and shows exactly what would change. Nothing "
              "is written until you press Send.")

    # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-14): "either for all Service from a supplier
    # or just a selection" - the preview list below now has a checkbox per would-change
    # service so specific ones can be deselected before Send, for both text and structured
    # additions. ClosedTour keeps its pasted-codes list as the primary way to narrow the set,
    # but the checkboxes work there too if a pasted code turns out not to need this after all.
    if add_structured:
        preview_disabled = not (item_data.get("name") or "").strip()
        current_sig = (supplier_id, product_type, structured_kind,
                       tuple(sorted((item_data or {}).items())), tuple(codes or ()))
    else:
        preview_disabled = not (text or "").strip()
        current_sig = (supplier_id, product_type, target, text, mode, tuple(codes or ()))

    pcol1, pcol2 = st.columns([1, 3])
    with pcol1:
        if st.button("🔍 Preview", key="mi_preview", disabled=preview_disabled):
            bar = st.progress(0.0, text="Reading Travel Compositor...")

            def _tick(done, total, name):
                bar.progress(min(done / max(total, 1), 1.0), text=f"Checking {name} ({done}/{total})")

            try:
                if add_structured:
                    st.session_state.mi_plan = bulk_notes.plan_structured(
                        client, supplier_id, product_type, structured_kind, item_data,
                        codes=codes, progress=_tick)
                else:
                    st.session_state.mi_plan = bulk_notes.plan(
                        client, supplier_id, product_type, target, text, mode=mode,
                        codes=codes, progress=_tick)
                # The signature includes `codes`: without it, previewing tours A+B and then
                # editing the box to C left Send armed and would have written A+B.
                st.session_state.mi_plan_sig = current_sig
            except Exception as e:
                # A failed refresh must DISARM, never leave the previous plan pressable.
                for _k in ("mi_plan", "mi_plan_sig"):
                    st.session_state.pop(_k, None)
                st.error(f"Couldn't read Travel Compositor: {friendly_error_message(e)}")
            bar.empty()
            st.session_state.pop("mi_result", None)
            st.rerun()

    planned = st.session_state.get("mi_plan")
    # A plan is only valid for the exact inputs it was built from. Editing the text after
    # previewing and then pressing Send would otherwise publish the OLD text.
    if planned and st.session_state.get("mi_plan_sig") != current_sig:
        st.info("You changed something after previewing — press Preview again to see the new result.")
        planned = None

    if planned:
        if planned.get("error"):
            st.error(planned["error"])
        noun = "service(s)" if add_structured else "already have this text or have nothing to write into"
        st.markdown(f"**{planned['will_change']} service(s) would change**, "
                    f"{planned['unchanged']} {'already have this entry' if add_structured else noun}, "
                    f"{planned['failed']} couldn't be read.")
        for it in planned["items"]:
            icon = {"will_change": "✏️", "unchanged": "➖", "failed": "❌"}[it["status"]]
            with st.expander(f"{icon} {it['name']}"
                             + (f" — {it.get('reason') or it.get('detail','')}"
                                if it["status"] != "will_change" else ""),
                             expanded=False):
                if it["status"] == "will_change":
                    # Mutating `it` in place is deliberate: `planned` IS
                    # st.session_state.mi_plan, so this checkbox's value survives reruns
                    # without a separate session key to keep in sync.
                    it["_include"] = st.checkbox(
                        "Include this service", value=it.get("_include", True),
                        key=f"mi_include_{it.get('id')}")
                    for lang, (before, after) in sorted(it["changes"].items()):
                        st.caption(f"{lang} — before")
                        st.code(before or "(empty)")
                        st.caption(f"{lang} — after")
                        st.code(after)
                else:
                    st.caption(it.get("reason") or it.get("detail") or "")

        included_count = sum(1 for it in planned["items"]
                             if it["status"] == "will_change" and it.get("_include", True))
        if planned["will_change"]:
            if included_count < planned["will_change"]:
                st.caption(f"{planned['will_change'] - included_count} deselected above — "
                          f"those will be left untouched.")
            st.warning(f"This writes to **{included_count} live service(s)** for supplier "
                       f"{supplier_id}. Travel Compositor has no undo.")
            if st.button(f"🚀 Send to {included_count} service(s)", type="primary",
                         key="mi_send", disabled=not included_count):
                bar = st.progress(0.0, text="Sending...")

                def _tick2(done, total, name):
                    bar.progress(min(done / max(total, 1), 1.0), text=f"Updating {name} ({done}/{total})")

                # Deselected items are excluded by giving them a status apply() doesn't act
                # on - apply() only ever sends items still marked "will_change", and counts
                # everything else as skipped.
                to_apply = dict(planned)
                to_apply["items"] = [
                    (dict(it, status="excluded_by_user")
                     if it["status"] == "will_change" and not it.get("_include", True) else it)
                    for it in planned["items"]
                ]
                st.session_state.mi_result = bulk_notes.apply(client, supplier_id, to_apply,
                                                              progress=_tick2)
                bar.empty()
                if also_future:
                    st.session_state.mi_future_saved = service_notes.set_standing_note(
                        supplier_id, product_type, text)
                # Drop the plan: it holds pre-modified snapshots, so leaving Send on screen
                # let a second click re-PUT them and silently revert any edit made in Travel
                # Compositor in between.
                for _k in ("mi_plan", "mi_plan_sig"):
                    st.session_state.pop(_k, None)
                st.rerun()
        else:
            st.info("Nothing to send — every live service already has this "
                    + ("entry." if add_structured else "text."))
            if also_future and st.button("💾 Save it for future uploads anyway",
                                         key="mi_save_future_only"):
                if service_notes.set_standing_note(supplier_id, product_type, text):
                    st.success("Saved. It will be added to every future upload of this type.")
                else:
                    st.error("Could not save it — it will NOT apply to future uploads.")

    result = st.session_state.get("mi_result")
    if result:
        if result["updated"]:
            st.success(f"✅ Sent. {len(result['updated'])} service(s) updated.")
            for u in result["updated"]:
                st.write(f"- {u['name']} ({', '.join(u['languages'])})")
        if st.session_state.get("mi_future_saved") is False:
            st.error("⚠️ The live services were updated, but the note could NOT be saved for "
                     "future uploads — check the database banner at the top of the page.")
        if result["failed"]:
            st.error(f"❌ {len(result['failed'])} service(s) failed — nothing was changed on these:")
            for f in result["failed"]:
                st.write(f"- {f.get('name')}: {f.get('detail')}")
            st.caption("Re-running is safe: services already updated are detected and skipped.")
        if st.button("Clear this result", key="mi_clear_result"):
            for k in ("mi_result", "mi_plan", "mi_plan_sig", "mi_future_saved"):
                st.session_state.pop(k, None)
            st.rerun()

    st.markdown("---")
    st.subheader("📌 All standing notes currently in force")
    existing = service_notes.list_standing_notes()
    if not existing:
        st.caption("None yet. Anything saved above appears here, and applies to every future "
                  "upload of that type until you clear it.")
    else:
        # Every note here silently alters future uploads, so they all have to be visible and
        # removable in one place - otherwise a note written months ago keeps appending itself
        # to vouchers with nobody remembering it exists.
        name_by_id = {str(s.get("id")): (s.get("commercialName") or s.get("legalName") or "")
                      for s in (st.session_state.suppliers_cache or [])}
        for note in existing:
            cols = st.columns([6, 1])
            with cols[0]:
                who = name_by_id.get(note["supplier_id"], "")
                st.markdown(f"**{note['product_type']} · {who or 'supplier'} "
                            f"(ID {note['supplier_id']})**")
                st.info(note["text"])
                if note.get("updated_at"):
                    st.caption(f"Last updated {note['updated_at'][:16].replace('T', ' ')} UTC")
            with cols[1]:
                if st.button("🗑️", key=f"mi_clear_{note['supplier_id']}_{note['product_type']}",
                             help="Clear this note"):
                    service_notes.set_standing_note(note["supplier_id"], note["product_type"], "")
                    st.rerun()


UPDATE_REFRESH_SERVICE_TYPES = ["ClosedTour", "Hotel", "Ticket", "Transfer", "Transport"]


def render_update_refresh_flow(client):
    """Unified 'Update/Refresh existing Service' entry point (CONFIRMED PRODUCT-OWNER REDESIGN,
    2026-08-12): "the App must ask first which service... then which supplier. After human
    selected which supplier... the main part of the App is either... extracting the information
    from document and/or URL and automatically matching existing services... or... the human
    selects which exact SERVICE... will be updated." Step 1's ClosedTour/Ticket/Hotel buttons
    are now CREATE-ONLY (a brand-new product + first Modality, or a new Modality added to one
    that already exists) - every other kind of update, for any of the five product types,
    funnels through here instead: one screen instead of five different half-hidden "Update
    existing X" options buried inside each product type's own flow.

    THIS ROUND (2026-08-12) covers all five product types:
      * Transfer/Transport reuse price_refresh.py's flow - which already never creates a new
        record, already lists EXISTING Travel Compositor products as the source of truth, and
        already matches a new rate sheet's rows against them one by one for a human to accept
        or reject.
      * ClosedTour/Hotel/Ticket already carry a real human-assigned code (unlike Transfer/
        Transport, which have none) - so "which exact service" is a straightforward PICK FROM
        A LIST fetched from Travel Compositor (see get_existing_tour_names/
        get_existing_hotel_names/get_existing_ticket_codes) rather than Transfer's fuzzy
        departure/arrival matching. Recently-picked services are boosted to the top of that
        list per supplier (_recent_update_refresh_picks, Postgres-backed via platform_store
        when DATABASE_URL is set) - the "database mapping so the App could learn" the request
        asked for, for these three coded types. What's NOT built yet: genuine AI matching of a
        freshly uploaded document's content against the existing list before the human even
        picks - today the human always picks explicitly, then the app extracts/updates from
        whatever source they provide next. After the pick, the update TYPE (main info only /
        pricing-Modality only / add a new Modality) is asked exactly as answered when this was
        scoped - reusing the SAME action menus (ACTION_LABELS/TICKET_ACTION_LABELS, minus
        "create") the classic per-type flows already use, then handing off into that same
        already-proven Step 3 code with everything pre-filled, so none of the actual
        extraction/review/publish logic is duplicated here."""
    st.header("🔄 Update existing Service")
    if st.button("🔙 Back to Step 1", key="ur_back"):
        st.session_state.product_type = None
        st.rerun()

    service = st.radio("Which service do you want to update/refresh?", UPDATE_REFRESH_SERVICE_TYPES,
                       horizontal=True, key="ur_service_choice")
    st.caption("**Transfer / Transport**: matches a new rate sheet's rows against your EXISTING "
              "Travel Compositor products and updates them - nothing is ever created here. "
              "**Ticket**: either a bulk price refresh across every Ticket for a supplier (same "
              "idea as Transfer/Transport), or pick one exact Ticket to update by hand. "
              "**ClosedTour / Hotel**: pick the exact existing one from the list below, then "
              "what kind of update this is - nothing is created here either.")

    if service in (price_refresh.KIND_TRANSPORT, price_refresh.KIND_TRANSFER):
        # CONFIRMED REAL BUG (reported by product owner, with screenshot): setting
        # st.session_state.pr_kind here only changed the radio's DEFAULT selection - the radio
        # widget itself still rendered and asked "Which product type?" again, right under the
        # exact same choice already made one screen up. Passing it through explicitly instead
        # skips rendering that question entirely when it's already known.
        st.session_state.pr_kind = service
        render_price_refresh_flow(client, preselected_kind=service)
        return

    if service == price_refresh.KIND_TICKET:
        # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-25): "Could we plan this the same for
        # Tickets and closedtours... Easiest part would be starting with Ticket" - a bulk price
        # refresh across many Tickets at once, same shape as the Transfer/Transport flow above,
        # ADDED alongside (not replacing) the existing pick-one-Ticket flow below.
        ticket_mode = st.radio(
            "What kind of Ticket update is this?",
            ["Bulk price refresh from a rate sheet", "Update one Ticket by hand"],
            horizontal=True, key="ur_ticket_mode")
        if ticket_mode == "Bulk price refresh from a rate sheet":
            render_ticket_price_refresh_flow(client)
            return
        _render_update_refresh_coded_service(client, service)
        return

    if service in ("ClosedTour", "Hotel"):
        _render_update_refresh_coded_service(client, service)
        return


def _ur_pick_momira_supplier(client, key_prefix):
    """Shared 'pick a Momira_ supplier' widget for the Update/Refresh screen's ClosedTour/
    Hotel/Ticket branches - same LOCKED Momira_-only rule and emergency-manual-entry fallback
    every other flow in this app already uses, just factored out once instead of copy-pasted a
    fourth time. Returns the chosen supplier_id (str) or None."""
    if st.session_state.suppliers_cache is None:
        with st.spinner("Loading supplier list from Travel Compositor..."):
            try:
                st.session_state.suppliers_cache = client.get_all_suppliers()
            except Exception as e:
                st.error(f"❌ Couldn't load the supplier list: {friendly_error_message(e)}")
                st.session_state.suppliers_cache = []

    if not st.session_state.suppliers_cache:
        st.error("Could not load the supplier list from Travel Compositor.")
        with st.expander("⚠️ Emergency manual entry"):
            st.caption("Only use this if the supplier list above failed to load - type the numeric Travel Compositor supplier ID directly.")
            return st.text_input("Supplier ID (numeric)", value="", key=f"{key_prefix}_supplier_manual").strip() or None

    momira_suppliers = [
        s for s in st.session_state.suppliers_cache
        if (s.get("commercialName") or s.get("legalName") or "").strip().lower().startswith("momira_")
    ]
    if not momira_suppliers:
        st.error("🚫 No suppliers starting with 'Momira_' were found in this account - can't continue.")
        return None
    supplier_options = {
        f"{s.get('commercialName') or s.get('legalName')} — ID {s.get('id')}": s.get("id")
        for s in momira_suppliers
    }
    selected_label = st.selectbox("Select Supplier", list(supplier_options.keys()), key=f"{key_prefix}_supplier_select")
    if st.button("🔄 Refresh supplier list", key=f"{key_prefix}_refresh_suppliers"):
        st.session_state.suppliers_cache = None
        st.rerun()
    return str(supplier_options[selected_label])


def render_supplier_migration_flow(client):
    """Move ALL (or a chosen subset) of a supplier's Transfers to a different supplier.

    CONFIRMED REAL NEED (product owner, 2026-08-24): "If I want mass change the supplier A,
    like all Transfers from supplier must now be changed to supplier B." Travel Compositor
    has no operation that does this directly - supplierId is part of every Transfer
    endpoint's URL (GET/POST/PUT /transfer/{supplierId}), never a field on the payload itself
    (see ContractTransferVO's own docstring in schemas.py), so a Transfer's supplier is fixed
    for its whole life once created. The only way to "move" one is: fetch it whole from
    supplier A, POST an identical copy under supplier B (Travel Compositor assigns the copy a
    brand-new id - the old id can never be reused or transferred), then set the ORIGINAL under
    A to active=False so the same route can't be booked twice under two suppliers at once.
    Nothing under A is ever deleted - the Transfer API has no delete endpoint at all, only
    create/update - so the source records stay in place, just switched off.

    CONFIRMED SCOPE DECISIONS (product owner, 2026-08-24): recreate-then-auto-deactivate (not
    a dry-run / manual-deactivate-later mode), built as a standing screen for reuse on future
    supplier moves rather than a one-off script. Transfer only for now, matching what was
    asked - Transport shares the same "supplier is part of the URL, not the payload" shape
    (see ContractTransportVO) so the same approach would extend to it if that's ever needed.

    KNOWN LIMITATION, surfaced to the operator rather than silently copied: a transfer using
    ZONE-based routing (departureLocationId/arrivalLocationId, from
    client.get_transfer_zones) carries a zone id that is looked up PER SUPPLIER - the same id
    under supplier B may not exist, or may point at a completely different place. Any such
    transfer is flagged before moving so a human checks the destination supplier's zones
    rather than trusting a silently-copied id that could be silently wrong.
    """
    st.header("Move Transfers to another Supplier")
    st.caption("Recreates every selected Transfer under a different supplier, then switches the "
              "original off. Nothing is deleted - Travel Compositor has no delete endpoint for "
              "Transfers, so the originals stay in place, just inactive.")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**From (current supplier)**")
        source_id = _ur_pick_momira_supplier(client, "sm_from")
    with col2:
        st.markdown("**To (new supplier)**")
        dest_id = _ur_pick_momira_supplier(client, "sm_to")

    if st.session_state.get("sm_results"):
        st.markdown("---")
        st.subheader("Result")
        results = st.session_state.sm_results
        ok = [r for r in results if r["ok"] is True]
        partial = [r for r in results if r["ok"] == "partial"]
        failed = [r for r in results if r["ok"] is False]
        st.caption(f"{len(ok)} moved cleanly · {len(partial)} created but NOT deactivated (needs a "
                  f"look) · {len(failed)} failed outright.")
        for r in ok:
            st.success(f"✅ **{r['name']}** — now `{r['new_id']}` under the new supplier; original "
                      f"deactivated.")
        for r in partial:
            st.warning(f"⚠️ **{r['name']}** — {r['detail']}. The route now exists under BOTH "
                      f"suppliers until you deactivate the original by hand.")
        for r in failed:
            st.error(f"🚫 **{r['name']}** — failed at the {r['stage']} step: {r['detail']}")
        if st.button("↩️ Move more / start over", key="sm_reset"):
            for key in ("sm_records", "sm_selected", "sm_results", "sm_source_id", "sm_dest_id"):
                st.session_state.pop(key, None)
            st.rerun()
        return

    if not source_id or not dest_id:
        st.info("Choose both suppliers to continue.")
        return
    if source_id == dest_id:
        st.error("🚫 Source and destination are the same supplier - nothing to move.")
        return

    if st.button("📥 Load Transfers from the source supplier", key="sm_load"):
        with st.spinner("Loading transfers..."):
            try:
                data = client.get_transfers(source_id)
            except Exception as e:
                st.error(f"❌ Couldn't load transfers: {friendly_error_message(e)}")
                data = None
            if isinstance(data, dict) and "error" in data:
                st.error(f"❌ Couldn't load transfers: {data.get('message') or data.get('error')}")
                data = None
            if data is not None:
                records = data.get("transfer", []) if isinstance(data, dict) else (data or [])
                st.session_state.sm_records = [r for r in records if isinstance(r, dict)]
                st.session_state.sm_selected = {i: True for i in range(len(st.session_state.sm_records))}
                st.session_state.sm_source_id = source_id
                st.session_state.sm_dest_id = dest_id
                st.rerun()

    records = st.session_state.get("sm_records")
    if records is None:
        return
    if st.session_state.get("sm_source_id") != source_id or st.session_state.get("sm_dest_id") != dest_id:
        st.warning("⚠️ The supplier selection changed since these were loaded - click 'Load "
                  "Transfers' again to refresh the list before moving anything.")
        return
    if not records:
        st.info("This supplier has no transfers to move.")
        return

    st.subheader(f"2 — Choose which of {len(records)} transfer(s) to move")

    bcol1, bcol2 = st.columns(2)
    with bcol1:
        if st.button("Select all", key="sm_select_all"):
            st.session_state.sm_selected = {i: True for i in range(len(records))}
            st.rerun()
    with bcol2:
        if st.button("Select none", key="sm_select_none"):
            st.session_state.sm_selected = {i: False for i in range(len(records))}
            st.rerun()

    for i, record in enumerate(records):
        dep = (record.get("departure") or {}).get("name", "") if isinstance(record.get("departure"), dict) else ""
        arr = (record.get("arrival") or {}).get("name", "") if isinstance(record.get("arrival"), dict) else ""
        name = record.get("name") or f"{dep} - {arr}".strip(" -") or record.get("id") or f"Transfer #{i + 1}"
        is_zoned = bool(record.get("departureLocationId") or record.get("arrivalLocationId"))
        label = f"**{name}**  ·  {record.get('currency', '')} {record.get('basePrice', '')}  ·  id `{record.get('id')}`"
        st.session_state.sm_selected[i] = st.checkbox(
            label, value=st.session_state.sm_selected.get(i, True), key=f"sm_pick_{i}")
        if is_zoned:
            st.caption("⚠️ Zone-based routing (departureLocationId/arrivalLocationId) - this zone id "
                      "is specific to the SOURCE supplier and may not exist, or may mean something "
                      "different, under the destination. Check the destination supplier's zones in "
                      "Travel Compositor after moving this one, before trusting it live.")

    selected_indices = [i for i, v in st.session_state.sm_selected.items() if v]
    st.caption(f"{len(selected_indices)} of {len(records)} selected.")
    if not selected_indices:
        return

    st.subheader("3 — Move")
    st.warning(f"⚠️ This creates {len(selected_indices)} new transfer(s) under the destination "
              f"supplier, and switches the same number OFF (active = False) under the source "
              f"supplier. The new records get brand-new Travel Compositor ids - the old ones "
              f"cannot be reused.")

    if st.button(f"🚀 Move {len(selected_indices)} transfer(s)", key="sm_confirm", type="primary"):
        results = []
        progress_bar = st.progress(0.0)
        for n, i in enumerate(selected_indices):
            record = records[i]
            dep = (record.get("departure") or {}).get("name", "") if isinstance(record.get("departure"), dict) else ""
            arr = (record.get("arrival") or {}).get("name", "") if isinstance(record.get("arrival"), dict) else ""
            name = record.get("name") or f"{dep} - {arr}".strip(" -") or record.get("id")
            progress_bar.progress((n + 1) / len(selected_indices), text=f"Moving {name}...")

            create_payload = dict(record)
            create_payload["id"] = None
            create_payload["active"] = True
            try:
                create_res = client.create_transfer(dest_id, create_payload)
            except Exception as e:
                results.append({"name": name, "ok": False, "stage": "create",
                               "detail": friendly_error_message(e)})
                continue
            if isinstance(create_res, dict) and "error" in create_res:
                results.append({"name": name, "ok": False, "stage": "create",
                               "detail": str(create_res.get("message") or create_res.get("error"))})
                continue
            new_id = create_res.get("id") if isinstance(create_res, dict) else None
            # CONFIRMED BUG FIX (full-app audit LOW (plausible), 2026-09-01): the "error" in
            # create_res check above only catches a response Travel Compositor itself flagged as
            # an error - it doesn't guarantee `new_id` actually came back set. Deactivating the
            # ORIGINAL Transfer here was previously unconditional on the create having "worked"
            # (no "error" key), not on it having genuinely returned a usable id - so a create
            # response that was some other kind of malformed/empty dict would still deactivate
            # the original, potentially destroying the only working copy with no replacement.
            if not new_id:
                results.append({"name": name, "ok": False, "stage": "create",
                               "detail": "the create call didn't return an id for the new record - "
                                         "the original was NOT deactivated, nothing was lost."})
                continue

            deactivate_payload = dict(record)
            deactivate_payload["active"] = False
            try:
                deact_res = client.update_transfer(source_id, deactivate_payload)
            except Exception as e:
                results.append({"name": name, "ok": "partial", "stage": "deactivate", "new_id": new_id,
                               "detail": f"created as `{new_id}`, but couldn't deactivate the "
                                         f"original: {friendly_error_message(e)}"})
                continue
            if isinstance(deact_res, dict) and "error" in deact_res:
                results.append({"name": name, "ok": "partial", "stage": "deactivate", "new_id": new_id,
                               "detail": f"created as `{new_id}`, but couldn't deactivate the "
                                         f"original: {deact_res.get('message') or deact_res.get('error')}"})
                continue

            # Keep the app's own route-matching memory in sync, so a future price refresh on
            # this route finds the NEW id under the NEW supplier instead of the now-inactive one.
            try:
                if dep and arr:
                    transfer_matcher.forget_transfer_id(source_id, dep, arr)
                    if new_id:
                        transfer_matcher.remember_transfer_id(dest_id, dep, arr, new_id)
            except Exception:
                pass

            results.append({"name": name, "ok": True, "stage": "done", "new_id": new_id})

        st.session_state.sm_results = results
        st.session_state.sm_records = None
        st.session_state.sm_selected = None
        st.rerun()


def render_transport_cancellation_bulk_flow(client):
    """Bulk-change the cancellation policy on every (or a chosen subset of) one supplier's
    already-live Transports - see cancellation_bulk_transport.py's module docstring for why
    this is its own deliberate action (never a side effect of a price refresh) and why it's
    scoped to Transport only (a real structured cancellationRanges field to safely overwrite;
    Transfer has no equivalent - its cancellation terms are baked into free-text voucher
    wording with no reliable anchor to safely locate and replace).

    CONFIRMED SCOPE DECISIONS (product owner, 2026-08-28, AskUserQuestion): per-supplier only
    for now (not multi-supplier/all-at-once); the new policy defaults from that supplier's
    saved Cancellation Link (cancellation_links.py) - or the house 30-day/free default when
    none is saved - always editable before applying; EVERY live Transport is listed with its
    CURRENT policy shown, so nothing "already filled out" differently is silently skipped -
    the human sees it and decides per row; and the customer-facing description text is
    rewritten to match, not just the structured field.
    """
    st.header("Bulk-update Cancellation Policy (Transport)")
    st.caption("Applies one cancellation policy to every live Transport of one supplier at "
              "once - both the structured field Travel Compositor enforces AND the matching "
              "sentence in each one's customer-facing description.")

    supplier_id = _ur_pick_momira_supplier(client, "ctb")
    if not supplier_id:
        st.info("Choose a supplier to continue.")
        return

    if st.session_state.get("ctb_supplier_id") != supplier_id:
        # Supplier changed - drop everything loaded for the previous one so nothing from a
        # different supplier's review screen can leak into this one.
        for key in ("ctb_rows", "ctb_new_tiers", "ctb_default_scope", "ctb_selected", "ctb_results"):
            st.session_state.pop(key, None)
        st.session_state.ctb_supplier_id = supplier_id

    if st.session_state.get("ctb_results"):
        st.markdown("---")
        st.subheader("Result")
        results = st.session_state.ctb_results
        ok = [r for r in results if r["ok"]]
        failed = [r for r in results if not r["ok"]]
        st.caption(f"{len(ok)} updated · {len(failed)} failed.")
        for r in ok:
            st.success(f"✅ **{r['name']}** updated.")
        for r in failed:
            st.error(f"🚫 **{r['name']}** — {r['detail']}")
        if st.button("↩️ Run again / start over", key="ctb_reset"):
            for key in ("ctb_rows", "ctb_new_tiers", "ctb_default_scope", "ctb_selected", "ctb_results"):
                st.session_state.pop(key, None)
            st.rerun()
        return

    if st.button("📥 Load this supplier's live Transports", key="ctb_load"):
        with st.spinner("Loading..."):
            rows, err = cancellation_bulk_transport.load_supplier_transports_for_cancellation(client, supplier_id)
            if err:
                st.error(f"❌ Couldn't load Transports: {err}")
            else:
                st.session_state.ctb_rows = rows
                st.session_state.ctb_selected = {r["id"]: True for r in rows}
                st.rerun()

    rows = st.session_state.get("ctb_rows")
    if rows is None:
        return
    if not rows:
        st.info("This supplier has no live Transports.")
        return

    st.subheader(f"2 — New cancellation policy (will apply to up to {len(rows)} Transport(s))")

    if "ctb_new_tiers" not in st.session_state:
        default_tiers, scope_label = cancellation_bulk_transport.default_new_tiers(supplier_id)
        st.session_state.ctb_new_tiers = default_tiers
        st.session_state.ctb_default_scope = scope_label
    st.caption(f"Pre-filled from {st.session_state.get('ctb_default_scope', 'the house default')} — "
              f"edit below if this run needs something different. This does NOT change the "
              f"saved Cancellation Link itself, only what gets applied this run.")

    import pandas as pd
    from ui_components import editable_table, _safe_int, _safe_float

    def _ctb_tier_table(tiers):
        table_rows = [{"Days before arrival (or more)": t.get("days"), "Cancellation Fee %": t.get("fee_percentage")}
                     for t in (tiers or []) if isinstance(t, dict)]
        return pd.DataFrame(table_rows) if table_rows else pd.DataFrame(
            columns=["Days before arrival (or more)", "Cancellation Fee %"])

    def _ctb_df_to_tiers(edited_df):
        new_tiers = []
        for _, row in edited_df.iterrows():
            days_val = row.get("Days before arrival (or more)")
            if days_val is None or (isinstance(days_val, float) and pd.isna(days_val)):
                continue
            new_tiers.append({
                "days": _safe_int(days_val, fallback=0),
                "fee_percentage": max(0.0, min(100.0, _safe_float(row.get("Cancellation Fee %"), fallback=0.0))),
            })
        return new_tiers

    def _ctb_save_new_tiers(edited_df):
        st.session_state.ctb_new_tiers = _ctb_df_to_tiers(edited_df)

    ctb_col_config = {
        "Days before arrival (or more)": st.column_config.NumberColumn(min_value=0, step=1),
        "Cancellation Fee %": st.column_config.NumberColumn(min_value=0, max_value=100, step=1),
    }
    editable_table("New policy", _ctb_tier_table(st.session_state.ctb_new_tiers), "ctb_new_policy",
                   on_save=_ctb_save_new_tiers, column_config=ctb_col_config)

    def _ctb_fmt_tiers(tiers):
        if not tiers:
            return "(system default — 30 days, 0% fee)"
        return "; ".join(f"{t['days']}+ days: {t['fee_percentage']:.0f}% fee"
                         for t in sorted(tiers, key=lambda t: t["days"], reverse=True))

    proposals = cancellation_bulk_transport.build_proposals(rows, st.session_state.ctb_new_tiers)
    proposals_by_id = {p["id"]: p for p in proposals}

    st.subheader("3 — Review and choose which to update")
    bcol1, bcol2 = st.columns(2)
    with bcol1:
        if st.button("Select all", key="ctb_select_all"):
            st.session_state.ctb_selected = {p["id"]: True for p in proposals}
            st.rerun()
    with bcol2:
        if st.button("Select none", key="ctb_select_none"):
            st.session_state.ctb_selected = {p["id"]: False for p in proposals}
            st.rerun()

    for p in proposals:
        route = f"  ·  {p['departure_code']} → {p['arrival_code']}" if (p["departure_code"] or p["arrival_code"]) else ""
        label = f"**{p['name']}**{route}  ·  id `{p['id']}`"
        if p["unchanged"]:
            st.session_state.ctb_selected[p["id"]] = False
            st.checkbox(f"{label}  ·  ✅ already matches — nothing to do", value=False, disabled=True,
                       key=f"ctb_pick_{p['id']}")
        else:
            st.session_state.ctb_selected[p["id"]] = st.checkbox(
                label, value=st.session_state.ctb_selected.get(p["id"], True), key=f"ctb_pick_{p['id']}")
        with st.expander("Details", expanded=False):
            dcol1, dcol2 = st.columns(2)
            with dcol1:
                st.caption("**Current**")
                st.text(_ctb_fmt_tiers(p["current_fee_tiers"]))
                st.caption(p["current_cancellation_snippet"] or "*(no cancellation text found in the description)*")
            with dcol2:
                st.caption("**New**")
                st.text(_ctb_fmt_tiers(p["new_fee_tiers"]))
                st.caption(p["new_cancellation_text"])
            if not p["existing_paragraph_found"]:
                st.warning("⚠️ No existing cancellation sentence was found in this Transport's description — "
                          "a new one will be INSERTED rather than replacing one. Double-check the result "
                          "afterward inside Travel Compositor.")

    selected_ids = [pid for pid, v in st.session_state.ctb_selected.items() if v]
    st.caption(f"{len(selected_ids)} of {len(proposals)} selected.")
    if not selected_ids:
        return

    st.subheader("4 — Apply")
    st.warning(f"⚠️ This will PUT (update) {len(selected_ids)} Transport(s) — both the structured "
              f"cancellation field and the matching sentence in each one's description. Everything "
              f"else on each record (pricing, segments, images, dates) is left exactly as it is.")
    # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): the "New policy" table above is a
    # live-editable table (editable_table) - while it's in live-edit mode, `st.session_state.
    # ctb_new_tiers` (and therefore every `proposals` entry's "New" column shown above) still
    # reflects the LAST SAVED numbers, not whatever's currently typed into the open table. This
    # button used to stay enabled through that whole window, so clicking "Apply" while the table
    # had unsaved edits pushed the OLD policy to every selected live Transport while the screen
    # displayed the new, not-yet-saved numbers right above it. Disabled until the table is saved.
    ctb_table_being_edited = bool(st.session_state.get("_editing_table_ctb_new_policy"))
    if ctb_table_being_edited:
        st.error("🚫 The New Policy table above has unsaved edits — click its own Save button "
                 "first, or this button would apply the OLD numbers while the screen shows new ones.")
    if st.button(f"🚀 Update {len(selected_ids)} Transport(s)", key="ctb_confirm", type="primary",
                 disabled=ctb_table_being_edited):
        to_apply = [proposals_by_id[pid] for pid in selected_ids]
        with st.spinner("Updating..."):
            results = cancellation_bulk_transport.apply_proposals(client, supplier_id, to_apply)
        st.session_state.ctb_results = results
        st.rerun()


def _ur_gather_text_optional(key_prefix):
    """Optional 'paste a URL and/or upload document(s)' widget pair for the Update/Refresh
    screen's matching step - same gathering logic every other flow in this app already uses
    (URL fetch + document text extraction), just standalone here since this screen only needs
    the combined text to SUGGEST a match, not to run a full extraction yet. Returns the
    combined raw text, or "" if nothing was provided/fetchable."""
    url = st.text_input("Product page URL (optional)", key=f"{key_prefix}_url")
    files = st.file_uploader("Upload document(s) (optional)", type=["pdf", "docx", "xlsx"],
                             accept_multiple_files=True, key=f"{key_prefix}_files")
    combined_parts = []
    if url:
        page_text, page_text_err = _fetch_url_text_safe(url)
        if page_text is not None:
            combined_parts.append(page_text)
        else:
            st.warning(f"⚠️ Couldn't fetch the URL: {page_text_err}.")
    for uploaded in (files or []):
        suffix = os.path.splitext(uploaded.name)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(uploaded.getbuffer())
            tmp_path = tmp.name
        combined_parts.append(extract_raw_text(tmp_path))
        os.remove(tmp_path)
    return "\n\n".join(combined_parts)


def _suggest_coded_service_matches(raw_text, existing_items, top_n=5, min_score=0.35):
    """Free, no-AI-call name matching for the Update/Refresh screen's ClosedTour/Hotel/Ticket
    branch (CONFIRMED PRODUCT-OWNER REQUEST: 'automatically matching existing services from
    this supplier with the new given information'). Scores every existing item's name against
    every line of the pasted document/URL text with difflib, keeping each item's single best
    line match - cheap and effective for a name that appears somewhere in the source (a title,
    a heading, a repeated phrase in a price table), without spending an extra paid AI call just
    to pull out a name. The human still explicitly picks/confirms afterward - this only ranks
    candidates, same principle as transfer_matcher.py's fuzzy matching for Transfers."""
    if not raw_text or not existing_items:
        return []
    # Capped - a long rate sheet scored line-by-line against every existing item's name has no
    # real benefit past the first few hundred lines; the product name overwhelmingly appears
    # near the top (a title/heading) or repeated in the price table itself.
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()][:400]
    if not lines:
        return []
    scored = []
    for item in existing_items:
        name = (item.get("name") or "").strip()
        if not name:
            continue
        best = max(difflib.SequenceMatcher(None, name.lower(), line.lower()).ratio() for line in lines)
        scored.append({**item, "score": round(best, 3)})
    scored.sort(key=lambda c: c["score"], reverse=True)
    return [c for c in scored[:top_n] if c["score"] >= min_score]


def _render_update_refresh_coded_service(client, service):
    """ClosedTour/Hotel/Ticket branch of render_update_refresh_flow - pick supplier, pick the
    exact existing service from a real list, pick what kind of update this is (skipped for
    Hotel, which has no such distinction today), then hand off into that product type's own
    proven Step 3 with everything pre-filled, so extraction/review/publish is never
    duplicated here."""
    st.subheader(f"Update an existing {service}")
    supplier_id = _ur_pick_momira_supplier(client, "ur_coded")
    if not supplier_id:
        return

    # Stale suggestions/picks from a different service or supplier must never carry over -
    # e.g. a "German Day Tour" match suggested while looking at ClosedTours would be nonsense
    # once the human switches to Hotel or a different supplier.
    _ur_scope = f"{service}:{supplier_id}"
    if st.session_state.get("ur_coded_scope") != _ur_scope:
        st.session_state.ur_coded_scope = _ur_scope
        st.session_state.pop("ur_coded_suggestions", None)
        st.session_state.pop("ur_coded_suggested_code", None)
        st.session_state.pop("ur_coded_pick", None)

    if service == "Ticket":
        existing_items, list_error = get_existing_ticket_codes(client, supplier_id)
        kind_key, action_labels = "ticket", {k: v for k, v in TICKET_ACTION_LABELS.items() if k != "create"}
    elif service == "ClosedTour":
        existing_items, list_error = get_existing_tour_names(client, supplier_id)
        kind_key, action_labels = "tour", {k: v for k, v in ACTION_LABELS.items() if k != "create"}
    else:  # Hotel
        existing_items, list_error = get_existing_hotel_names(client, supplier_id)
        kind_key, action_labels = "hotel", None

    if list_error:
        st.warning(f"⚠️ Couldn't load the existing {service} list ({list_error}) - you can still type "
                  f"the code manually below.")

    # CONFIRMED PRODUCT-OWNER REQUEST (follow-up round): "automatically matching existing
    # services from this supplier with the new given information" - optional, since the human
    # may not have gathered a document yet at this point (that still happens on the next
    # screen either way). Suggestions only RANK candidates; the human always explicitly picks
    # below, same rule transfer_matcher.py already follows for Transfers.
    suggested_code = None
    with st.expander(f"🔎 Have a document/URL for this {service} already? Get a suggested match"):
        st.caption("This is only used to suggest which existing one this is - you'll still provide "
                  "the source again on the next screen for the actual extraction.")
        match_text = _ur_gather_text_optional("ur_coded_match")
        if st.button("Suggest matches", key="ur_coded_suggest_btn", disabled=not match_text):
            st.session_state.ur_coded_suggestions = _suggest_coded_service_matches(match_text, existing_items)
        suggestions = st.session_state.get("ur_coded_suggestions") or []
        if suggestions:
            for s in suggestions:
                scol1, scol2 = st.columns([4, 1])
                with scol1:
                    st.write(f"**{s['code']}** — {s['name']}")
                    st.caption(f"match confidence: {s['score']:.0%}")
                with scol2:
                    if st.button("✅ Use this", key=f"ur_coded_use_{s['code']}"):
                        st.session_state.ur_coded_suggested_code = s["code"]
                        # Same fixed-key staleness rule as every editable_field/editable_table
                        # in this app (see reset_stale_editable_field_widgets' docstring): the
                        # selectbox below already rendered once on a prior run with its OWN
                        # key, so a freshly-computed index= would otherwise be silently
                        # ignored - clearing its stored value forces a fresh pick next render.
                        st.session_state.pop("ur_coded_pick", None)
                        st.rerun()
            if suggested_code := st.session_state.get("ur_coded_suggested_code"):
                st.success(f"Suggested match selected: **{suggested_code}** - confirm/change it below if needed.")
        elif st.session_state.get("ur_coded_suggestions") == []:
            st.caption("No confident match found - pick manually below.")

    recents = _recent_update_refresh_picks(kind_key, supplier_id)
    recent_codes = {r["code"] for r in recents}
    ordered_items = recents + [it for it in existing_items if it.get("code") not in recent_codes]

    manual_entry = not ordered_items
    chosen_code = ""
    if not manual_entry:
        options = {f"{it['code']} — {it['name']}" + (" ⭐ recently used" if it["code"] in recent_codes else ""): it
                   for it in ordered_items}
        option_labels = list(options.keys())
        default_index = 0
        if suggested_code:
            for i, it in enumerate(ordered_items):
                if it["code"] == suggested_code:
                    default_index = i
                    break
        picked_label = st.selectbox(f"Which {service} do you want to update?", option_labels,
                                    index=default_index, key="ur_coded_pick")
        chosen = options[picked_label]
        chosen_code, chosen_name = chosen["code"], chosen["name"]
        with st.expander("Can't find it? Type the code manually instead"):
            manual_override = st.text_input("Code", value="", key="ur_coded_manual").strip()
            if manual_override:
                chosen_code, chosen_name = manual_override, ""
    else:
        st.info(f"No existing {service}s were found/loaded for this supplier - type the code directly.")
        chosen_code = st.text_input("Code", value="", key="ur_coded_manual_only").strip()
        chosen_name = ""

    action_key = None
    if action_labels:
        action_key = st.radio(
            "What kind of update is this?", list(action_labels.keys()),
            format_func=lambda k: action_labels[k], key="ur_coded_action"
        )

    ready = bool(chosen_code) and (action_labels is None or action_key is not None)
    if st.button("➡️ Continue", type="primary", disabled=not ready, key="ur_coded_continue"):
        _remember_update_refresh_pick(kind_key, supplier_id, chosen_code, chosen_name)
        if service == "Ticket":
            st.session_state.tk_cfg_action = action_key
            st.session_state.tk_cfg_supplier_id = supplier_id
            st.session_state.tk_prefill_existing_ticket_code = chosen_code
            st.session_state.tk_step1_confirmed = True
            st.session_state.tk_step2_confirmed = False
            st.session_state.product_type = "Ticket"
        elif service == "ClosedTour":
            st.session_state.cfg_action = action_key
            st.session_state.cfg_supplier_id = supplier_id
            st.session_state.prefill_existing_tour_code = chosen_code
            st.session_state.cfg_existing_tour_code = chosen_code
            st.session_state.step1_confirmed = True
            st.session_state.step2_confirmed = False
            st.session_state.product_type = "ClosedTour"
        else:  # Hotel - no action sub-choice; render_hotel_flow always updates when the code
            # already exists, so jumping straight past its own Step 2 with the picked code and
            # code's live currency/release days pre-filled is enough.
            with st.spinner("Fetching this hotel's current details..."):
                fetched = client.get_hotel(supplier_id, chosen_code)
            if not isinstance(fetched, dict) or "error" in fetched:
                st.error(f"Couldn't fetch `{chosen_code}` from Travel Compositor - check the code and try again.")
                return
            st.session_state.hp_cfg_supplier_id = supplier_id
            st.session_state.hp_cfg_provider_code = chosen_code
            st.session_state.hp_cfg_currency = fetched.get("currency") or "EUR"
            st.session_state.hp_cfg_release_days = fetched.get("releaseDays", 7)
            st.session_state.hp_step1_confirmed = True
            st.session_state.product_type = "Hotel"
        st.rerun()


def render_price_refresh_flow(client, preselected_kind=None):
    """Update the prices of transports that already exist, from a new rate sheet.

    CONFIRMED REAL DESIGN (product owner): "would the app be better if TRANSPORTS are only
    being updated instead of created... the AI can suggest which price for which Transport,
    the only interaction is that the human could click Yes if the new updated price is correct
    or NO if it is incorrect." He was right, and the reason is worth recording: the list of
    products here comes from Travel Compositor, which is a fact, instead of from an AI reading
    a document, which is a judgement - and it was that judgement that kept coming back empty.
    Nothing here resolves a location, estimates a duration, names anything, or creates
    anything. Only numbers move.

    preselected_kind: when the caller (the Update/Refresh dispatcher) already knows which
    product type this is - the human picked "Transfer" or "Transport" one screen up - pass it
    here to skip asking "Which product type?" again. CONFIRMED REAL BUG (reported by product
    owner): setting st.session_state.pr_kind before calling this used to only change the
    radio's default selection, not skip rendering the radio itself - the same question appeared
    twice in a row. Only asked fresh when this is None (no caller currently does that, but kept
    as the honest fallback rather than assuming there's always a preselection)."""
    st.header("💶 Refresh prices from a rate sheet")
    st.caption("For a rate sheet covering products that already exist. The list of routes comes "
              "from Travel Compositor, not from the document — the document is only asked what "
              "each one now costs. Nothing is created, and **nothing but prices changes** — "
              "validity dates, times, names and modality structure are left exactly as they are.")
    if preselected_kind:
        kind = preselected_kind
        st.caption(f"Product type: **{kind}** (already chosen above).")
    else:
        kind = st.radio("Which product type?", [price_refresh.KIND_TRANSPORT, price_refresh.KIND_TRANSFER],
                        horizontal=True, key="pr_kind")

    if st.session_state.suppliers_cache is None:
        with st.spinner("Loading supplier list…"):
            try:
                st.session_state.suppliers_cache = client.get_all_suppliers()
            except Exception as e:
                st.error(f"Couldn't load the supplier list: {friendly_error_message(e)}")
                st.session_state.suppliers_cache = []
    momira = [x for x in (st.session_state.suppliers_cache or [])
              if (x.get("commercialName") or x.get("legalName") or "").strip().lower().startswith("momira_")]
    supplier_id = None
    if momira:
        options = {f"{x.get('commercialName') or x.get('legalName')} — ID {x.get('id')}": str(x.get("id"))
                   for x in momira}
        supplier_id = options[st.selectbox("Supplier", list(options.keys()), key="pr_supplier")]
    else:
        st.error("Could not load the supplier list from Travel Compositor.")
        with st.expander("⚠️ Emergency manual entry"):
            st.caption("Only use this if the supplier list above failed to load - type the numeric Travel Compositor supplier ID directly.")
            supplier_id = st.text_input("Supplier ID (numeric)", key="pr_supplier_manual").strip()

    st.subheader("1 — The new rate sheet")
    url = st.text_input("Rate sheet URL (optional)", key="pr_url")
    files = st.file_uploader("Upload the rate sheet", type=["pdf", "docx", "xlsx"],
                             accept_multiple_files=True, key="pr_files")
    hint = st.text_input("Instruction (optional)", key="pr_hint",
                         placeholder="e.g. only the Hurghada section, private transfers only")

    if st.button(f"🔍 Read prices for this supplier's {kind.lower()}s", type="primary",
                 disabled=not supplier_id, key="pr_read"):
        raw_parts = []
        if url:
            page_text, page_err = _fetch_url_text_safe(url)
            if page_text is not None:
                raw_parts.append(page_text)
            else:
                st.warning(f"⚠️ Couldn't fetch that URL: {page_err}.")
        for uploaded in (files or []):
            suffix = os.path.splitext(uploaded.name)[1]
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(uploaded.getbuffer())
                tmp_path = tmp.name
            raw_parts.append(extract_raw_text(tmp_path))
            os.remove(tmp_path)
        if not raw_parts:
            st.error("No document to read — upload a rate sheet or give a URL.")
        else:
            raw_text = "\n\n".join(raw_parts)
            bar = st.progress(0.0, text=f"Reading this supplier's {kind.lower()}s from Travel Compositor…")

            def _tick(done, total, name):
                bar.progress(min(done / max(total, 1), 1.0), text=f"Reading {name} ({done}/{total})")

            routes, err = price_refresh.load_supplier_products(client, supplier_id, kind,
                                                               progress=_tick)
            bar.empty()
            if err:
                st.error(f"Couldn't read this supplier's {kind.lower()}s: {err}")
            elif not routes:
                st.warning(f"This supplier has no {kind.lower()}s yet. Create them with "
                           f"**Upload & Update Products → {kind}** first; this flow only updates "
                           f"what already exists.")
            else:
                with st.spinner(f"Looking up prices for {len(routes)} route(s) in the document…"):
                    try:
                        findings = price_refresh.lookup_prices(routes, raw_text, human_hint=hint)
                    except Exception as e:
                        st.error(f"Couldn't read the document: {friendly_error_message(e)}")
                        findings = None
                if findings is not None:
                    st.session_state.pr_routes = routes
                    st.session_state.pr_proposals = _stamp_proposal_widget_tokens(
                        price_refresh.build_proposals(routes, findings))
                    st.session_state.pr_raw_text = raw_text
                    st.session_state.pop("pr_result", None)
                    # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): this st.rerun() used
                    # to sit unconditionally after the whole button block, at the same indent as
                    # the "no document"/"couldn't read supplier's X/no X yet" branches above - so
                    # it fired even when nothing new was read, instantly wiping whichever
                    # st.error/st.warning had just been shown and leaving the PREVIOUS rate
                    # sheet's proposals on screen with no indication anything failed, looking
                    # exactly like a fresh successful read. Moved inside the one branch that
                    # actually produced a new result, so a failed read's error message stays on
                    # screen instead of being rerun away.
                    st.rerun()

    proposals = st.session_state.get("pr_proposals")
    if not proposals:
        return

    changed = [p for p in proposals if p["status"] == "changed"]
    unchanged = [p for p in proposals if p["status"] == "unchanged"]
    absent = [p for p in proposals if p["status"] == "not_in_document"]
    blocked = [p for p in proposals if p["status"] == "blocked_unreadable"]

    st.subheader("2 — Check the new prices")
    st.caption(f"{len(changed)} route(s) would change · {len(unchanged)} already match the document · "
              f"{len(absent)} not found in it."
              + (f" · {len(blocked)} could not be read" if blocked else ""))

    # CONFIRMED REAL BUG (audit, 2026-08-24): routes with an unreadable option used to be filtered
    # out of this screen silently, while their remaining options were repriced around a base
    # computed from whatever happened to load - see build_proposals. They are now named here and
    # can never be accepted, so the operator knows to re-run rather than trusting a partial result.
    if blocked:
        st.error(
            f"🚫 **{len(blocked)} route(s) could not be fully read from Travel Compositor** and have "
            f"been left untouched. This is usually a temporary API hiccup - re-run the price refresh "
            f"for this supplier and they should load. They are not repriced, because the base price "
            f"is shared across a transport's modalities: repricing the ones that did load would "
            f"silently move the price of the one that didn't."
        )
        for p in blocked:
            names = ", ".join(str(c) for c in (p.get("unreadable_options") or []) if c) or "unknown option(s)"
            st.markdown(f"- **{p['route'].get('name') or '(unnamed route)'}** — couldn't read: `{names}`")

    # Accept-all with exceptions: the product owner's own choice. Only rows that genuinely
    # CHANGED are ever ticked - a route the document never mentioned must not be swept up by a
    # single click, which is the one way "accept all" could do damage.
    acol1, acol2 = st.columns([1, 4])
    with acol1:
        if st.button("✅ Accept all", key="pr_accept_all", use_container_width=True):
            for p in proposals:
                p["accepted"] = p["status"] == "changed"
            st.rerun()
    with acol2:
        if st.button("Clear all", key="pr_clear_all"):
            for p in proposals:
                p["accepted"] = False
            st.rerun()

    def _id_suffix(route):
        # CONFIRMED REAL REQUEST (product owner): show the TRANSFER-xxxxx / TRANSPORT-xxxxx id
        # next to the route so a human can find the exact record in Travel Compositor without
        # having to search by name.
        rid = route.get("id")
        return f"  ·  `{rid}`" if rid else ""

    for p in changed:
        route = p["route"]
        finding = p["finding"]
        head = f"**{route.get('name') or route.get('id')}**{_id_suffix(route)}"
        cols = st.columns([1, 6])
        with cols[0]:
            # Token, not just the index - see _stamp_proposal_widget_tokens for the confirmed bug
            # (a second run's route #0 arriving already ticked from the previous run's route #0).
            p["accepted"] = st.checkbox("Yes", value=p["accepted"],
                                        key=f"pr_ok_{p['index']}_{p.get('widget_token', 'g0')}")
        with cols[1]:
            st.markdown(head)
            # CONFIRMED REAL GAP (product owner): the AI's read price used to be take-it-or-
            # leave-it - a low-confidence or wrong read (like a bundled route with two
            # different underlying prices) had no way to be corrected other than rejecting the
            # whole row and fixing it some other way. Each bracket's "new" price is now directly
            # editable, pre-filled with what the AI read - overwrite it by hand and that's what
            # gets applied on Publish.
            for c in p["changes"]:
                pcol1, pcol2 = st.columns([3, 2])
                with pcol2:
                    c["new"] = st.number_input(
                        f"New price ({c['min_pax']}-{c['max_pax']} pax)", min_value=0.0, step=1.0,
                        value=float(c["new"]),
                        # Token, not just index+code - see _stamp_proposal_widget_tokens: without
                        # it, a re-read's corrected price was displayed as (and published as) the
                        # old one.
                        key=f"pr_price_{p['index']}_{c['code']}_{p.get('widget_token', 'g0')}",
                        label_visibility="collapsed")
                with pcol1:
                    # CONFIRMED REAL REQUEST (product owner): red when the price to apply
                    # differs from what's already live, green when (after any hand-edit above)
                    # it now matches - a quick visual scan instead of reading every number.
                    _ccy = route.get('currency') or ''
                    if abs(c["new"] - c["old"]) < 0.005:
                        st.markdown(f"{c['min_pax']}–{c['max_pax']} pax: {c['old']} → "
                                   f":green[**{c['new']}**] {_ccy}  ·  *matches the live price*")
                    else:
                        st.markdown(f"{c['min_pax']}–{c['max_pax']} pax: {c['old']} → "
                                   f":red[**{c['new']}**] {_ccy}")
            bits = []
            if finding.get("matched_row"):
                bits.append(f"from the row *“{finding['matched_row']}”*")
            if finding.get("confidence") and finding["confidence"] != "high":
                bits.append(f"**{finding['confidence']} confidence**")
            if finding.get("note"):
                bits.append(finding["note"])
            if p.get("currency_changed"):
                bits.append(f"⚠️ the document says **{finding['currency']}** but this transport is "
                            f"**{route.get('currency')}** — the price is applied as-is, not converted")
            if bits:
                st.caption("  ·  ".join(bits))
            # CONFIRMED REAL GAP (product owner): no way to redirect the AI when it read the
            # wrong row (e.g. picked Marsa Allam's price for a bundled Port Ghalib/Marsa Allam
            # route) short of fixing the number by hand above. This re-reads ONLY this one
            # route, with the extra instruction folded in, and replaces its proposal in place -
            # every other route in the batch is untouched.
            with st.expander("🤖 Not right? Tell the AI more about this route", expanded=False):
                route_hint = st.text_input(
                    "Extra instruction for this route only",
                    key=f"pr_hint_{p['index']}_{p.get('widget_token', 'g0')}",
                    placeholder="e.g. use the Port Ghalib price, not Marsa Allam")
                if st.button("🔁 Re-read this route", key=f"pr_reread_{p['index']}",
                             disabled=not route_hint.strip()):
                    with st.spinner("Re-reading this route..."):
                        combined_hint = "\n".join(
                            x for x in [(hint or "").strip(), route_hint.strip()] if x)
                        try:
                            single_finding = price_refresh.lookup_prices(
                                [route], st.session_state.pr_raw_text, human_hint=combined_hint
                            ).get(0)
                        except Exception as e:
                            st.error(f"Couldn't re-read this route: {friendly_error_message(e)}")
                            single_finding = None
                    if single_finding is not None:
                        rebuilt = _stamp_proposal_widget_tokens(price_refresh.build_proposals(
                            st.session_state.pr_routes, {p["index"]: single_finding}))
                        st.session_state.pr_proposals[p["index"]] = rebuilt[p["index"]]
                        st.rerun()
                    elif single_finding is None and route_hint.strip():
                        st.warning("The AI didn't find this route in the document even with the extra "
                                  "instruction - the current price is left as it was.")

    if unchanged:
        with st.expander(f"➖ {len(unchanged)} already at the document's price"):
            for p in unchanged:
                route = p["route"]
                price_bits = ", ".join(
                    f"{o['min_pax']}-{o['max_pax']} pax: {o['unit_price']}"
                    for o in (route.get("options") or []) if not o.get("fetch_failed"))
                st.markdown(f"- **{route.get('name')}**{_id_suffix(route)}  ·  "
                           f":green[{price_bits}] {route.get('currency') or ''}")
    if absent:
        with st.expander(f"❓ {len(absent)} not found in the document — match by hand if you want"):
            st.caption("The document may price these under a wording nobody matched, or the supplier "
                      "may have dropped them. Pick the row's price yourself to update one anyway.")
            for p in absent:
                route = p["route"]
                mcol1, mcol2, mcol3 = st.columns([3, 2, 1])
                with mcol1:
                    st.write(f"**{route.get('name')}**{_id_suffix(route)}")
                    st.caption(", ".join(f"{o['min_pax']}–{o['max_pax']} pax now {o['unit_price']}"
                                         for o in route["options"] if not o.get("fetch_failed")))
                with mcol2:
                    typed = st.number_input(
                        "New price per person/vehicle", min_value=0.0, step=1.0, value=0.0,
                        # Token, same reason as the other pr_ widgets - a hand-typed price from a
                        # previous run must not reappear under a new run's route #0.
                        key=f"pr_manual_{p['index']}_{p.get('widget_token', 'g0')}",
                        help="The base bracket's new price. The solo bracket is recalculated "
                             "from it using the same minimum-party rule.")
                with mcol3:
                    st.write("")
                    if st.button("Use", key=f"pr_use_{p['index']}", disabled=typed <= 0):
                        widest = max((o for o in route["options"] if not o.get("fetch_failed")),
                                     key=lambda o: (o["max_pax"] - o["min_pax"], -o["min_pax"]),
                                     default=None)
                        if widest:
                            manual = {"found": True, "minimum_pax": widest["min_pax"],
                                      "confidence": "high", "note": "price entered by hand",
                                      "matched_row": "entered by hand", "currency": "",
                                      "brackets": [{"min_pax": widest["min_pax"],
                                                    "max_pax": widest["max_pax"],
                                                    "price": float(typed),
                                                    "child_price": None, "infant_price": None}]}
                            rebuilt = _stamp_proposal_widget_tokens(price_refresh.build_proposals(
                                st.session_state.pr_routes, {p["index"]: manual}))
                            st.session_state.pr_proposals[p["index"]] = rebuilt[p["index"]]
                            st.rerun()
                        else:
                            # CONFIRMED REAL BUG (audit, 2026-08-28): every option on this route
                            # failed to fetch (fetch_failed=True), so `widest` is None and the
                            # click used to do nothing at all - no rerun, no message, the operator
                            # just sees the button appear not to work. Named explicitly instead.
                            st.error("Every bracket on this route failed to load from Travel "
                                    "Compositor, so there's nothing to price by hand yet. "
                                    "Re-run the price refresh and try again.")

    st.subheader("3 — Apply")
    accepted = [p for p in proposals if p.get("accepted") and p.get("changes")]
    st.warning(f"This changes prices on **{len(accepted)} live {kind.lower()}(s)** for supplier "
               f"{supplier_id}. Nothing else is touched — validity dates stay as they are, even "
               f"where they run to 2049 or 2099.")
    if st.button(f"🚀 Update {len(accepted)} {kind.lower()}(s)", type="primary",
                 disabled=not accepted, key="pr_apply"):
        bar = st.progress(0.0, text="Updating…")

        def _tick2(done, total, name):
            bar.progress(min(done / max(total, 1), 1.0), text=f"Updating {name} ({done}/{total})")

        st.session_state.pr_result = price_refresh.apply_proposals(
            client, supplier_id, proposals, progress=_tick2)
        bar.empty()
        st.rerun()

    result = st.session_state.get("pr_result")
    if result:
        if result["updated"]:
            st.success(f"✅ {len(result['updated'])} {kind.lower()}(s) repriced.")
            for u in result["updated"]:
                st.write(f"- {u['name']}: " + ", ".join(
                    f"{c['min_pax']}–{c['max_pax']} pax {c['old']} → {c['new']}" for c in u["changes"]))
        if result["failed"]:
            st.error(f"❌ {len(result['failed'])} failed:")
            for f in result["failed"]:
                st.write(f"- **{f.get('name')}**: {f.get('detail')}")
        if st.button("🆕 Start again", key="pr_new"):
            for key in ("pr_proposals", "pr_routes", "pr_raw_text", "pr_result"):
                st.session_state.pop(key, None)
            st.rerun()


def render_ticket_price_refresh_flow(client):
    """Update the occupancy prices of Tickets that already exist, from a new rate sheet - Phase
    1 of the product-owner's request (2026-08-25): "the next developement must be done, when we
    are talking about updating Tickets or ClosedTours for the new Seasons with new prices... The
    logic we have build for transfers and transports are great... Could we plan this the same
    for Tickets and closedtours." Explicitly scoped to base/occupancy price only, per "yes,
    please start with phase 1" - a Peak Season supplement is never added here (Phase 2) and an
    existing language-choice supplement's own price is never touched here (Phase 3).

    Same review pattern as render_price_refresh_flow (Transfer/Transport) above: the list of
    Tickets/Modalities comes from Travel Compositor, the document is only asked what each known
    CODE now costs, and nothing but occupancyPrices ever changes - dates, languages, supplements
    and modality structure are left exactly as they are."""
    st.header("🎟️ Refresh Ticket prices from a rate sheet")
    st.caption("For a rate sheet covering Tickets that already exist. The list of Tickets/"
              "Modalities comes from Travel Compositor, not from the document — the document is "
              "only asked what each known CODE now costs. Nothing is created, and **only the "
              "occupancy prices change** — dates, languages, supplements and modality structure "
              "are left exactly as they are. Phase 1 only: Peak Season supplements and "
              "language-choice supplement prices are not touched by this screen yet.")

    if st.session_state.suppliers_cache is None:
        with st.spinner("Loading supplier list…"):
            try:
                st.session_state.suppliers_cache = client.get_all_suppliers()
            except Exception as e:
                st.error(f"Couldn't load the supplier list: {friendly_error_message(e)}")
                st.session_state.suppliers_cache = []
    momira = [x for x in (st.session_state.suppliers_cache or [])
              if (x.get("commercialName") or x.get("legalName") or "").strip().lower().startswith("momira_")]
    supplier_id = None
    if momira:
        options = {f"{x.get('commercialName') or x.get('legalName')} — ID {x.get('id')}": str(x.get("id"))
                   for x in momira}
        supplier_id = options[st.selectbox("Supplier", list(options.keys()), key="tpr_supplier")]
    else:
        st.error("Could not load the supplier list from Travel Compositor.")
        with st.expander("⚠️ Emergency manual entry"):
            st.caption("Only use this if the supplier list above failed to load - type the numeric Travel Compositor supplier ID directly.")
            supplier_id = st.text_input("Supplier ID (numeric)", key="tpr_supplier_manual").strip()

    st.subheader("1 — The new rate sheet")
    url = st.text_input("Rate sheet URL (optional)", key="tpr_url")
    files = st.file_uploader("Upload the rate sheet", type=["pdf", "docx", "xlsx"],
                             accept_multiple_files=True, key="tpr_files")
    hint = st.text_input("Instruction (optional)", key="tpr_hint",
                         placeholder="e.g. only the Alexandria tours section")

    if st.button("🔍 Read prices for this supplier's Tickets", type="primary",
                 disabled=not supplier_id, key="tpr_read"):
        raw_parts = []
        if url:
            page_text, page_err = _fetch_url_text_safe(url)
            if page_text is not None:
                raw_parts.append(page_text)
            else:
                st.warning(f"⚠️ Couldn't fetch that URL: {page_err}.")
        for uploaded in (files or []):
            suffix = os.path.splitext(uploaded.name)[1]
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(uploaded.getbuffer())
                tmp_path = tmp.name
            raw_parts.append(extract_raw_text(tmp_path))
            os.remove(tmp_path)
        if not raw_parts:
            st.error("No document to read — upload a rate sheet or give a URL.")
        else:
            raw_text = "\n\n".join(raw_parts)
            bar = st.progress(0.0, text="Reading this supplier's Tickets from Travel Compositor…")

            def _tick(done, total, name):
                bar.progress(min(done / max(total, 1), 1.0), text=f"Reading {name} ({done}/{total})")

            routes, err = price_refresh.load_supplier_tickets(client, supplier_id, progress=_tick)
            bar.empty()
            if err:
                st.error(f"Couldn't read this supplier's Tickets: {err}")
            elif not routes:
                st.warning("This supplier has no Tickets yet. Create them with "
                           "**Upload & Update Products → Ticket** first; this flow only updates "
                           "what already exists.")
            else:
                with st.spinner(f"Looking up prices for {len(routes)} Modality(ies) in the document…"):
                    try:
                        findings = price_refresh.lookup_ticket_prices(routes, raw_text, human_hint=hint)
                    except Exception as e:
                        st.error(f"Couldn't read the document: {friendly_error_message(e)}")
                        findings = None
                if findings is not None:
                    st.session_state.tpr_routes = routes
                    st.session_state.tpr_proposals = _stamp_proposal_widget_tokens(
                        price_refresh.build_ticket_proposals(routes, findings))
                    st.session_state.tpr_raw_text = raw_text
                    st.session_state.pop("tpr_result", None)
                    # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): see the matching fix
                    # in render_price_refresh_flow (Transfer/Transport) - this st.rerun() used to
                    # fire unconditionally after every branch, wiping the "no document"/"couldn't
                    # read"/"no Tickets yet" error or warning before the operator could read it
                    # and leaving the previous rate sheet's proposals on screen looking fresh.
                    # Moved inside the one branch that actually produced a new result.
                    st.rerun()

    proposals = st.session_state.get("tpr_proposals")
    if not proposals:
        return

    changed = [p for p in proposals if p["status"] == "changed"]
    unchanged = [p for p in proposals if p["status"] == "unchanged"]
    absent = [p for p in proposals if p["status"] == "not_in_document"]
    blocked = [p for p in proposals if p["status"] == "blocked_unreadable"]
    unsupported = [p for p in proposals if p["status"] == "unsupported_price_type"]

    st.subheader("2 — Check the new prices")
    st.caption(f"{len(changed)} Modality(ies) would change · {len(unchanged)} already match the "
              f"document · {len(absent)} not found in it."
              + (f" · {len(blocked)} could not be read" if blocked else "")
              + (f" · {len(unsupported)} not OCCUPANCY-priced (unsupported)" if unsupported else ""))

    if blocked:
        st.error(f"🚫 **{len(blocked)} Modality(ies) could not be fully read from Travel "
                f"Compositor** and have been left untouched. Re-run the price refresh and they "
                f"should load.")
        for p in blocked:
            st.markdown(f"- **{p['route'].get('name') or '(unnamed)'}**")

    if unsupported:
        with st.expander(f"⚠️ {len(unsupported)} Modality(ies) not supported yet "
                         f"(DISTRIBUTION/SERVICE pricing)"):
            st.caption("This screen only refreshes OCCUPANCY-priced Modalities (a per-headcount "
                      "table) so far. A DISTRIBUTION (flat per-adult/child) or SERVICE (one flat "
                      "total) Modality is listed here rather than guessed at — update it by hand "
                      "for now.")
            for p in unsupported:
                route = p["route"]
                st.markdown(f"- **{route.get('name') or '(unnamed)'}** — priceType "
                           f"`{route.get('price_type') or '?'}`")

    acol1, acol2 = st.columns([1, 4])
    with acol1:
        if st.button("✅ Accept all", key="tpr_accept_all", use_container_width=True):
            for p in proposals:
                p["accepted"] = p["status"] == "changed"
            st.rerun()
    with acol2:
        if st.button("Clear all", key="tpr_clear_all"):
            for p in proposals:
                p["accepted"] = False
            st.rerun()

    for p in changed:
        route = p["route"]
        finding = p["finding"]
        head = f"**{route.get('name')}**  ·  `{route.get('ticket_code')}/{route.get('modality_code')}`"
        cols = st.columns([1, 6])
        with cols[0]:
            p["accepted"] = st.checkbox("Yes", value=p["accepted"],
                                        key=f"tpr_ok_{p['index']}_{p.get('widget_token', 'g0')}")
        with cols[1]:
            st.markdown(head)
            for c in p["changes"]:
                pcol1, pcol2 = st.columns([3, 2])
                with pcol2:
                    c["new"] = st.number_input(
                        f"New adult price ({c['min_pax']} pax)", min_value=0.0, step=1.0,
                        value=float(c["new"]),
                        key=f"tpr_price_{p['index']}_{c['code']}_{p.get('widget_token', 'g0')}",
                        label_visibility="collapsed")
                with pcol1:
                    _ccy = route.get('currency') or ''
                    if abs(c["new"] - c["old"]) < 0.005:
                        st.markdown(f"{c['min_pax']} pax: {c['old']} → :green[**{c['new']}**] {_ccy}  ·  "
                                   f"*matches the live price*")
                    else:
                        st.markdown(f"{c['min_pax']} pax: {c['old']} → :red[**{c['new']}**] {_ccy}")
                    if c.get("child_new") is not None:
                        st.caption(f"child at {c['min_pax']} pax: "
                                  f"{c.get('child_old') if c.get('child_old') is not None else '?'} → "
                                  f"{c['child_new']} {_ccy} (from the document)")
                    elif c.get("child_old") is not None:
                        st.caption(f"child at {c['min_pax']} pax moves with the adult price "
                                  f"(document gave no separate child price)")
            bits = []
            if finding.get("matched_row"):
                bits.append(f"from the row *“{finding['matched_row']}”*")
            if finding.get("confidence") and finding["confidence"] != "high":
                bits.append(f"**{finding['confidence']} confidence**")
            if finding.get("note"):
                bits.append(finding["note"])
            if p.get("currency_changed"):
                bits.append(f"⚠️ the document says **{finding['currency']}** but this Ticket is "
                            f"**{route.get('currency')}** — the price is applied as-is, not converted")
            if bits:
                st.caption("  ·  ".join(bits))
            with st.expander("🤖 Not right? Tell the AI more about this Ticket", expanded=False):
                route_hint = st.text_input(
                    "Extra instruction for this Ticket only",
                    key=f"tpr_hint_{p['index']}_{p.get('widget_token', 'g0')}",
                    placeholder="e.g. use the half-day price, not the full-day one")
                if st.button("🔁 Re-read this Ticket", key=f"tpr_reread_{p['index']}",
                             disabled=not route_hint.strip()):
                    with st.spinner("Re-reading this Ticket..."):
                        combined_hint = "\n".join(
                            x for x in [(hint or "").strip(), route_hint.strip()] if x)
                        try:
                            single_finding = price_refresh.lookup_ticket_prices(
                                [route], st.session_state.tpr_raw_text, human_hint=combined_hint
                            ).get(0)
                        except Exception as e:
                            st.error(f"Couldn't re-read this Ticket: {friendly_error_message(e)}")
                            single_finding = None
                    if single_finding is not None:
                        rebuilt = _stamp_proposal_widget_tokens(price_refresh.build_ticket_proposals(
                            st.session_state.tpr_routes, {p["index"]: single_finding}))
                        st.session_state.tpr_proposals[p["index"]] = rebuilt[p["index"]]
                        st.rerun()
                    elif single_finding is None and route_hint.strip():
                        st.warning("The AI didn't find this code in the document even with the "
                                  "extra instruction - the current price is left as it was.")

    if unchanged:
        with st.expander(f"➖ {len(unchanged)} already at the document's price"):
            for p in unchanged:
                route = p["route"]
                price_bits = ", ".join(f"{o['min_pax']} pax: {o['unit_price']}"
                                       for o in (route.get("options") or []))
                st.markdown(f"- **{route.get('name')}**  ·  :green[{price_bits}] "
                           f"{route.get('currency') or ''}")
    if absent:
        # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-28): "the human shall review if the matched
        # tickets with the new documents are correct. Same as with transfers" - closing the one
        # gap between this screen and render_price_refresh_flow's (Transfer/Transport) own
        # "not found" section: a ticket the AI didn't match still gets a manual price + "Use"
        # button here, exactly like a transfer/transport route does, instead of being a
        # dead-end read-only list.
        with st.expander(f"❓ {len(absent)} not found in the document — match by hand if you want"):
            st.caption("The document may price these under a code nobody matched, or the "
                      "supplier may have dropped them. Pick the Modality's price yourself to "
                      "update one anyway.")
            for p in absent:
                route = p["route"]
                mcol1, mcol2, mcol3 = st.columns([3, 2, 1])
                with mcol1:
                    st.write(f"**{route.get('name')}**  ·  `{route.get('ticket_code')}/"
                            f"{route.get('modality_code')}`")
                    st.caption(", ".join(f"{o['min_pax']} pax now {o['unit_price']}"
                                         for o in (route.get("options") or [])))
                with mcol2:
                    typed = st.number_input(
                        "New adult price per person",
                        min_value=0.0, step=1.0, value=0.0,
                        # Token, same reason as the other tpr_ widgets - a hand-typed price from
                        # a previous run must not reappear under a new run's route #0.
                        key=f"tpr_manual_{p['index']}_{p.get('widget_token', 'g0')}",
                        help="The widest occupancy bracket's new price. The solo bracket, if "
                             "any, is recalculated from it using the same minimum-party rule.")
                with mcol3:
                    st.write("")
                    if st.button("Use", key=f"tpr_use_{p['index']}", disabled=typed <= 0):
                        widest = max(route.get("options") or [],
                                     key=lambda o: (o["max_pax"] - o["min_pax"], -o["min_pax"]),
                                     default=None)
                        if widest:
                            manual = {"found": True, "minimum_pax": widest["min_pax"],
                                      "confidence": "high", "note": "price entered by hand",
                                      "matched_row": "entered by hand", "currency": "",
                                      "brackets": [{"min_pax": widest["min_pax"],
                                                    "max_pax": widest["max_pax"],
                                                    "price": float(typed),
                                                    "child_price": None, "infant_price": None}]}
                            rebuilt = _stamp_proposal_widget_tokens(price_refresh.build_ticket_proposals(
                                st.session_state.tpr_routes, {p["index"]: manual}))
                            st.session_state.tpr_proposals[p["index"]] = rebuilt[p["index"]]
                            st.rerun()
                        else:
                            # Same defensive fix as the Transfer/Transport "Use" button above -
                            # this Modality has no readable occupancy bracket to price by hand.
                            st.error("This Modality has no occupancy bracket to price by hand. "
                                    "Re-run the price refresh and try again.")

    st.subheader("3 — Apply")
    accepted = [p for p in proposals if p.get("accepted") and p.get("changes")]
    st.warning(f"This changes prices on **{len(accepted)} live Ticket Modality(ies)** for "
               f"supplier {supplier_id}. Nothing else is touched.")
    if st.button(f"🚀 Update {len(accepted)} Modality(ies)", type="primary",
                 disabled=not accepted, key="tpr_apply"):
        bar = st.progress(0.0, text="Updating…")

        def _tick2(done, total, name):
            bar.progress(min(done / max(total, 1), 1.0), text=f"Updating {name} ({done}/{total})")

        st.session_state.tpr_result = price_refresh.apply_ticket_proposals(
            client, supplier_id, proposals, progress=_tick2)
        bar.empty()
        st.rerun()

    result = st.session_state.get("tpr_result")
    if result:
        if result["updated"]:
            st.success(f"✅ {len(result['updated'])} Modality(ies) repriced.")
            for u in result["updated"]:
                st.write(f"- {u['name']}: " + ", ".join(
                    f"{c['min_pax']} pax {c['old']} → {c['new']}" for c in u["changes"]))
        if result["failed"]:
            st.error(f"❌ {len(result['failed'])} failed:")
            for f in result["failed"]:
                st.write(f"- **{f.get('name')}**: {f.get('detail')}")
        if st.button("🆕 Start again", key="tpr_new"):
            for key in ("tpr_proposals", "tpr_routes", "tpr_raw_text", "tpr_result"):
                st.session_state.pop(key, None)
            st.rerun()


st.set_page_config(page_title="Momira Travel Platform", layout="wide")

# Slightly larger base font app-wide for readability. Streamlit's own CSS is
# built almost entirely on rem units, so scaling the ROOT font-size (rather
# than hunting down individual elements) cleanly scales text, inputs,
# buttons, tables etc. together without breaking any layout - 106% takes the
# default 16px browser base up to ~17px.
st.markdown("<style>html { font-size: 106%; }</style>", unsafe_allow_html=True)

_defaults = {
    "client": None, "extracted": None, "raw_preview": "", "payloads": None,
    "suppliers_cache": None, "step1_confirmed": False, "step2_confirmed": False,
    "cfg_action": None, "cfg_supplier_id": None, "cfg_provider_code": "",
    "cfg_min_pax": 1, "cfg_max_pax": 9, "cfg_currency": "", "cfg_modality_code": "",
    "cfg_on_request": True, "cfg_release_days": 30, "cfg_existing_tour_code": "",
}
for _k, _v in _defaults.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

if st.session_state.client is None:
    st.session_state.client = TravelCompositorAPI()
client = st.session_state.client

BUILD_VERSION = "2026-09-02-audit-medium-batch4-5-final"

# Every module delivered alongside app.py carries the same MODULE_BUILD string. Comparing them
# here catches a PARTIAL DEPLOY - one file committed and pushed, another left behind - which is
# otherwise close to undiagnosable: Streamlit renders the traceback's line numbers against the
# file currently on disk, so a stale module produces a traceback pointing at source that has
# nothing to do with the error. CONFIRMED REAL INCIDENT: an outreach crash reported line 199 of
# outreach_tool.py while quoting a line of code from a completely different function, because
# app.py had been pushed and outreach_tool.py had not.
def _module_build_mismatches():
    import glob
    import importlib

    # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): this used to be a hand-maintained
    # tuple of 23 names, which is exactly as stale-prone as the partial-deploy problem it exists
    # to catch - api_client.py (the actual publish path for every product type) and
    # trip_quote_client.py (the newest file in the repo) both carried NO MODULE_BUILD at all and
    # were silently never checked, and any future module someone forgot to add here would be
    # just as invisible. Now discovers every local .py module and checks whichever ones actually
    # declare a MODULE_BUILD - a module that's never been stamped still isn't checked (nothing to
    # compare), but a module that WAS given a stamp is picked up automatically, with no second
    # list to keep in sync.
    this_dir = os.path.dirname(os.path.abspath(__file__))
    candidate_names = sorted(
        os.path.splitext(os.path.basename(p))[0]
        for p in glob.glob(os.path.join(this_dir, "*.py"))
        if os.path.basename(p) not in ("app.py",) and not os.path.basename(p).startswith("test_")
    )
    stale = []
    import_failures = []
    for name in candidate_names:
        try:
            mod = importlib.import_module(name)
        except Exception as e:
            # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): a failed import used to be
            # silently treated as "no mismatch" (`continue`) - the exact same blind spot as never
            # checking the module at all, just reached a different way (e.g. a module with a
            # genuine syntax error or a missing dependency after a partial deploy). Surfaced as
            # its own kind of finding instead of being swallowed.
            import_failures.append((name, str(e)))
            continue
        found = getattr(mod, "MODULE_BUILD", None)
        if found is None:
            continue  # never stamped - nothing to compare, not itself a mismatch
        if found != BUILD_VERSION:
            stale.append((name, found))
    return stale, import_failures


st.title("Momira Travel Platform")
st.caption(f"Build version: {BUILD_VERSION} — bump this string whenever new code is shared, so it's always obvious whether a deploy actually took effect.")

_stale_modules, _module_import_failures = _module_build_mismatches()
if _stale_modules:
    st.error(
        "🚨 **Partial deploy — some files on the server are older than this one.** Errors from "
        "these will point at the wrong lines, because the traceback is drawn against whatever is "
        "on disk now:\n\n"
        + "\n".join(f"- `{name}.py` is from **{found}**, but app.py is **{BUILD_VERSION}**"
                    for name, found in _stale_modules)
        + "\n\nIn GitHub Desktop, check that **every** changed file is ticked before committing, "
          "then push and let the app redeploy.")
if _module_import_failures:
    # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): see _module_build_mismatches'
    # docstring - a module that fails to import can't be build-checked at all, which used to be
    # silently indistinguishable from "everything's fine."
    st.error(
        "🚨 **Some modules failed to import and could not be build-checked:**\n\n"
        + "\n".join(f"- `{name}.py`: {err}" for name, err in _module_import_failures)
        + "\n\nThis usually means a partial/broken deploy too - fix the import error above before "
          "trusting anything this module is used for.")

# A document that yielded almost no readable text - a screenshot or a scan. Said here, on every
# screen, because the symptom otherwise looks like the AI being stupid rather than the AI having
# been handed a blank page. See document_reader.scanned_document_warning.
for _scan_msg in st.session_state.get("_scanned_doc_warnings", []) or []:
    st.error("🖼️ " + _scan_msg)
st.session_state["_scanned_doc_warnings"] = []

st.caption("Every publish respects the confirmed active/inactive workflow. Human verification and final activation still happen inside Travel Compositor.")

# CONFIRMED PRODUCT-OWNER REQUEST: "the integrated AI tool will ask me once a week, if it needs
# clarification. So we can constantly improve the included databank information."
#
# Every question is derived from what the platform has actually observed in its own memory - a
# correction typed on several suppliers, or one typed many times - never invented to fill the
# slot. If it has observed nothing, it says nothing, which is what keeps the weekly prompt worth
# reading on the week it does have something.
if weekly_review.is_due():
    _review_questions = weekly_review.pending_questions()
    if not _review_questions:
        weekly_review.mark_reviewed()          # nothing to ask; quietly reset the clock
    else:
        with st.container(border=True):
            st.markdown("### 🗓️ Weekly check-in — a few things I keep needing to be told")
            st.caption("Each of these is something you have corrected more than once. Saying **Yes** "
                       "turns it into a house rule, applied to every future document of that type "
                       "for every supplier — so you stop having to repeat it.")
            for _q in _review_questions:
                st.markdown(f"**{_q['product_type']}** — {_q['text']}")
                st.caption(_q["why"])
                _c1, _c2, _c3 = st.columns([1, 1, 4])
                with _c1:
                    if st.button("✅ Yes, always", key=f"wr_yes_{_q['id']}"):
                        weekly_review.accept(_q)
                        st.rerun()
                with _c2:
                    if st.button("✖️ No", key=f"wr_no_{_q['id']}"):
                        weekly_review.dismiss(_q["id"])
                        st.rerun()
            _d1, _d2 = st.columns([1, 5])
            with _d1:
                if st.button("Not now", key="wr_snooze"):
                    weekly_review.mark_reviewed()
                    st.rerun()
            with _d2:
                st.caption("“Not now” hides this for another week. Nothing here touches Travel "
                           "Compositor — it only edits what the AI is told next time.")

# Say out loud when nothing is being remembered between runs. Without this the platform
# looks identical either way: it silently re-translates content already paid for and
# forgets route matches a human confirmed, with no symptom an operator would notice.
#
# This asks platform_store.health(), which does a real write-and-read-back against the
# database - NOT is_durable(), which only checks that a DATABASE_URL exists. The
# difference matters: a wrong password, a paused project or an IPv6-only connection
# string all satisfy is_durable() and then fail silently on every read. Those are the
# realistic misconfigurations, so they get their own loud red state rather than being
# indistinguishable from success.
_storage = platform_store.health()
if _storage["ok"] and _storage["durable"]:
    st.caption(f"💾 Memory: {_storage['detail']} — connection verified.")
elif _storage["mode"] == "postgres":
    st.error(
        "🚨 **`DATABASE_URL` is set, but the database is not answering — so nothing is being "
        "remembered.** This is the dangerous case: the setting looks correct, and the platform "
        "keeps working, but every translation will be paid for again and every confirmed route "
        "match will be lost.\n\n"
        f"The database said:\n\n`{_storage['error']}`\n\n"
        "Usual causes: a wrong database password; the `[YOUR-PASSWORD]` placeholder or its "
        "square brackets left in the string; a password containing `@ : / ? # %` that needs "
        "percent-encoding; the *Direct connection* string used instead of the *Session pooler* "
        "one (direct is IPv6-only and unreachable from here); or a paused Supabase project."
    )
else:
    st.warning(
        "⚠️ **Nothing is being remembered between restarts.** No `DATABASE_URL` is configured, "
        "so what has already been translated and which routes map to which Travel Compositor id "
        "sit in a local file this host wipes on every redeploy. In practice that means paying to "
        "translate the same content again, and re-confirming route matches. Add a `DATABASE_URL` "
        "(any hosted Postgres) to fix it."
    )
    if _storage["error"]:
        st.caption(f"Detail: {_storage['error']}")

with st.expander("💾 What the platform remembers", expanded=False):
    st.caption(f"Storage: {_storage['detail']}")
    if st.button("🔌 Test the database connection now", key="storage_health_btn"):
        _fresh = platform_store.health(force=True)
        if _fresh["ok"] and _fresh["durable"]:
            st.success(f"Connected. Wrote a row and read it back from {_fresh['detail']}.")
        elif _fresh["mode"] == "local":
            st.warning("Running on a local file — nothing here survives a redeploy.")
        else:
            st.error(f"Could not reach the database: {_fresh['error']}")
    _counts = platform_store.stats()
    if _counts:
        # Namespace names are internal; say what each one actually means to an operator.
        _labels = {
            "translation_state": "translated entities tracked",
            "transfer_matches": "confirmed transfer route matches",
            "transport_matches": "confirmed transport route matches",
            "standing_notes": "standing supplier notes",
            "hotel_matches": "confirmed hotel matches",
            "extraction_memory": "suppliers with learned corrections",
        }
        for _ns, _n in sorted(_counts.items()):
            st.write(f"- **{_n}** {_labels.get(_ns, _ns)}")
    else:
        st.caption("Nothing stored yet. This fills up as you correct documents, translate "
                   "and confirm route matches.")

    # Everything learned from corrections, with a delete button on each. A learning system
    # nobody can inspect or overrule is one you have to take on trust; this is the page that
    # makes it answerable instead.
    st.markdown("---")
    st.markdown("##### 🧠 What it has learned from your corrections")
    extraction_memory.render_memory_panel()
    _instr = extraction_memory.list_all_instructions()
    if _instr:
        st.markdown("##### 💬 What it has learned from “Tell AI what to fix”")
        st.caption("Instructions you typed while reviewing, now given to the AI before it reads "
                  "the next document from that supplier. The document always wins over these.")
        for _row in _instr:
            _c1, _c2 = st.columns([6, 1])
            with _c1:
                _times = int(_row.get("count", 0))
                st.markdown(f"**{_row['product_type']} · supplier {_row['supplier_id']}** — "
                            f"{_row['text']}" + (f"  ·  *said {_times}×*" if _times > 1 else ""))
                if _row.get("fields"):
                    st.caption("changed: " + ", ".join(f"`{f}`" for f in _row["fields"]))
            with _c2:
                if st.button("🗑️", key=f"em_fi_{_row['supplier_id']}_{_row['product_type']}_{_row['key']}",
                             help="Forget this"):
                    extraction_memory.forget_instruction(_row["supplier_id"], _row["product_type"],
                                                         _row["key"])
                    st.rerun()


# ======================================================================
# STEP 0: WHICH TOOL?
# The platform's top-level split. Everything below hangs off this one
# choice, and it's deliberately the very first thing a human sees, because
# the two halves do opposite things and confusing them wastes real work:
#
#   UPLOAD & UPDATE  - source of truth is a SUPPLIER CONTRACT (a document
#                      or web page). Reads it, extracts the product, and
#                      writes a NEW or REFRESHED product into Travel
#                      Compositor. This is where product data is born.
#
#   TRANSLATE        - source of truth is TRAVEL COMPOSITOR ITSELF. Reads
#                      products that already exist there and fills in their
#                      other-language content. Never invents or changes
#                      product data, never touches prices.
#
#   FIND SUPPLIERS   - doesn't touch Travel Compositor at all. Searches the
#                      open web for local operators worth working with, and
#                      emails the ones a human approves. This is what happens
#                      BEFORE a supplier ever has a contract to upload.
#
# A further clue that the first two differ: their entity lists don't match.
# Holiday Packages can be translated but never uploaded (they're assembled
# inside Travel Compositor from products we upload), which is why that
# entity appears on one side only.
# ======================================================================
TOOL_UPLOAD = "📤 Upload & Update Products"
TOOL_TRANSLATE = "🌐 Translate Products"
TOOL_OUTREACH = "🤝 Find & Contact Suppliers"
# Reads a supplier's stop-sale email and blocks the dates. Its own tool rather than a
# product type, because the source of truth is an EMAIL, not a contract and not Travel
# Compositor - and because it changes availability on products that are already live.
TOOL_STOPSALES = "📧 Stop Sales Email Reader"
# PROTOTYPE (2026-08-19): free-text customer trip idea -> structured search criteria. Doesn't
# touch Travel Compositor at all yet - see trip_idea_tool.py's module docstring for why.
TOOL_TRIPIDEA = "💡 AI Trip Idea (prototype)"
# PROTOTYPE (2026-08-19): human enters a Holiday Package ID, tool proposes a replacement
# departure. Read-only (real GET calls, no PUT) - see package_rollover_tool.py's module
# docstring and the "package-auto-rollover-rules" project note.
TOOL_PACKAGEROLLOVER = "🔁 Package Rollover (prototype)"

# A Step 1 destination that is not a product type. It sits in the same list because that is
# where a person looks when they have something to record about a supplier, even though
# nothing is being uploaded.
MANUAL_INFO_CHOICE = "Adding manual information"
# Update-only price refresh. A Step 1 destination rather than a product type, because the
# product list comes from Travel Compositor rather than from the document. Kept as the
# constant price_refresh.py's own code compares against internally (KIND_TRANSPORT/
# KIND_TRANSFER) - the STEP 1 button that used to say this is gone, replaced by
# UPDATE_REFRESH_CHOICE below, which folds price refresh in as one branch among five.
PRICE_REFRESH_CHOICE = "Refresh prices (update only)"
# CONFIRMED PRODUCT-OWNER REDESIGN (2026-08-12): the ONE place every kind of update/refresh
# happens now, for all five product types - see render_update_refresh_flow's docstring.
UPDATE_REFRESH_CHOICE = "Update existing Service"
# CONFIRMED REAL NEED (product owner, 2026-08-24): "mass change the supplier - all Transfers
# from supplier A must now be changed to supplier B." A Step 1 destination rather than living
# inside Update/Refresh, since it acts on a whole supplier's worth of transfers at once, not
# one already-identified record - see render_supplier_migration_flow's docstring.
MIGRATE_SUPPLIER_CHOICE = "Move Transfers to another Supplier"
# CONFIRMED REAL NEED (product owner, 2026-08-28): "can i also include/change the cancellation
# for a bulk or at least per supplier for transports?" A Step 1 destination rather than living
# inside Update/Refresh, since it acts on a whole supplier's worth of Transports at once, not
# one already-identified record - see render_transport_cancellation_bulk_flow's docstring.
TRANSPORT_CANCELLATION_BULK_CHOICE = "Bulk-update Cancellation Policy (Transport)"

if "active_tool" not in st.session_state:
    st.session_state.active_tool = None
if "product_type" not in st.session_state:
    st.session_state.product_type = None


def _reset_to_tool_chooser():
    """Full reset back to Step 0. Keeps only the cached API client and supplier list, so
    switching tools doesn't force a re-login or re-fetch, but no half-finished state from one
    tool can leak into the other."""
    for key in list(st.session_state.keys()):
        if key not in ("client", "suppliers_cache"):
            del st.session_state[key]
    st.session_state.active_tool = None
    st.session_state.product_type = None


# ---- Breadcrumb + switch, shown once a tool is chosen ----
if st.session_state.active_tool is not None:
    crumb = st.session_state.active_tool
    if st.session_state.active_tool == TOOL_UPLOAD and st.session_state.product_type:
        crumb = f"{crumb}  ›  **{st.session_state.product_type}**"
    bcol1, bcol2 = st.columns([5, 1])
    with bcol1:
        st.success(f"You are in: {crumb}")
    with bcol2:
        if st.button("🔄 Switch tool"):
            _reset_to_tool_chooser()
            st.rerun()

# ---- Step 0: the tool chooser itself ----
# Each tool is a self-contained card: heading, what it does, and its OWN button
# directly underneath. Previously the three descriptions sat above a single shared
# radio + Continue, which put the actual control a long way from the text explaining
# it and made choosing a two-step job. One click per tool now, and the button sits
# where the eye already is after reading that column. Type is deliberately small -
# this screen is read once to orient, not studied.
if st.session_state.active_tool is None:
    st.subheader("What do you want to do?")
    st.caption("The three tools sit at different points in the same lifecycle: find a supplier, "
              "load their contract, then translate what you loaded.")
    st.write("")

    _TOOL_CARDS = [
        (TOOL_OUTREACH, "tool_btn_outreach",
         "Find local operators worth working with, and contact them.",
         "Searches the web for well-reviewed suppliers, filters out articles and booking "
         "marketplaces, finds a direct email where it can, and sends an intro after you "
         "approve the list.",
         "Doesn't touch Travel Compositor."),
        (TOOL_UPLOAD, "tool_btn_upload",
         "Turn a supplier contract into a live Travel Compositor product.",
         "You give it a document or a URL; it extracts the details, you review and correct "
         "them, then it publishes — for a new product or to refresh one when new rates arrive.",
         "Closed Tours · Tickets · Transfers · Transports · Hotels"),
        (TOOL_TRANSLATE, "tool_btn_translate",
         "Fill in other-language content for products already live in Travel Compositor.",
         "It reads the English content, translates it into 19 languages, and writes it back. "
         "It never changes prices or product data.",
         "Holiday Packages · Tickets · Transfers · Transports · Hotels · Closed Tours"),
        (TOOL_STOPSALES, "tool_btn_stopsales",
         "Block dates a supplier has closed, from their email.",
         "Paste the stop-sale email; it reads the dates, finds the product, shows you what "
         "would change, and blocks them only after you confirm. Existing blocks are kept.",
         "Closed Tours · Hotels"),
    ]

    for _col, (_label, _key, _lead, _detail, _scope) in zip(st.columns(len(_TOOL_CARDS)), _TOOL_CARDS):
        with _col:
            st.markdown(f"##### {_label}")
            st.caption(f"**{_lead}**")
            st.caption(_detail)
            st.caption(f"*{_scope}*")
            st.write("")
            if st.button(_label, key=_key, type="primary", use_container_width=True):
                st.session_state.active_tool = _label
                st.rerun()

    # CONFIRMED PRODUCT-OWNER REDESIGN (2026-08-19): "Make one botton for the 2 Prototyp
    # below the find contact; Upload/Update; Translate and Stop Sale reader. As long as
    # those tools are prototypes, we do not have to shwo them immediately." Both prototypes
    # (AI Trip Idea, Package Rollover) collapse into one expandable section under the four
    # real tools above, instead of getting their own full-width cards - same expandable-menu
    # pattern as Step 1 of Upload & Update, so a prototype only takes up screen space once
    # someone actually opens it.
    st.write("")
    with st.expander("🧪 Prototypes — not part of the regular workflow yet"):
        st.caption("Early, not-yet-finished tools. Safe to try - see each one's own warning "
                  "for exactly what it does and doesn't do.")
        if st.button(TOOL_TRIPIDEA, key="tool_btn_tripidea", use_container_width=True):
            st.session_state.active_tool = TOOL_TRIPIDEA
            st.rerun()
        st.caption("Turn a customer's free-text trip idea (\"2 adults, February, city and "
                  "beach in Spain\") into structured destination/dates/party/theme fields. "
                  "Doesn't touch Travel Compositor — not a real search yet.")
        if st.button(TOOL_PACKAGEROLLOVER, key="tool_btn_packagerollover", use_container_width=True):
            st.session_state.active_tool = TOOL_PACKAGEROLLOVER
            st.rerun()
        st.caption("Look up a Holiday Package by ID and see a proposed replacement departure "
                  "(14-day trigger, ~4 months out, rating 8+, price within +3.5%). Read-only — "
                  "real GET calls, never writes anything.")
    st.stop()

# ---- Outreach tool: hand straight off, it has no product-type step ----
if st.session_state.active_tool == TOOL_OUTREACH:
    render_outreach_tool()
    st.stop()

# ---- Translate tool: hand straight off, it has no product-type step ----
if st.session_state.active_tool == TOOL_STOPSALES:
    from stop_sales_tool import render_stop_sales_tool
    render_stop_sales_tool(client)
    st.stop()

if st.session_state.active_tool == TOOL_TRANSLATE:
    render_translation_tool()
    st.stop()

# ---- AI Trip Idea prototype: hand straight off, it has no product-type step and doesn't
# need the Travel Compositor client at all ----
if st.session_state.active_tool == TOOL_TRIPIDEA:
    render_trip_idea_tool()
    st.stop()

# ---- Package Rollover prototype: hand straight off, it has no product-type step and uses
# its own Packages-API client (see package_rollover_tool.py's module docstring) ----
if st.session_state.active_tool == TOOL_PACKAGEROLLOVER:
    render_package_rollover_tool()
    st.stop()

# ======================================================================
# UPLOAD & UPDATE - Step 1: which product type?
# CONFIRMED PRODUCT-OWNER REDESIGN (2026-08-12): "In step 1 we must ask only: Choose one:
# ClosedTour; Ticket; Hotel; Adding manual Information to a service; Update Service/Information
# of a service --> Transfer and Transport are not possible to automatically Import/upload."
# Two changes from before: (1) Transfer/Transport are no longer offered as their own CREATE
# buttons here at all - a brand-new Transfer/Transport can no longer be created through this
# tool, only updated (see UPDATE_REFRESH_CHOICE below); (2) the old standalone "Refresh prices"
# button is gone too, folded into that same unified Update/Refresh entry point, which now
# covers all five product types (not just Transfer/Transport) as the ONE place any kind of
# update happens, instead of five different half-hidden "Update existing X" options buried
# inside each product type's own flow. Goal (verbatim): "make the tool less complex and more
# intuitive for humans."
# ======================================================================
if st.session_state.product_type is None:
    st.header("Step 1 — Which product are you uploading or updating?")
    st.caption("Click a section below to open it, then pick where you want to go — like "
              "Travel Compositor's own \"Contracts\" menu.")

    # CONFIRMED PRODUCT-OWNER REDESIGN (2026-08-19): "can we make the menu in the App more
    # like this example in the Travel Compositor site: When the human clicks on the according
    # step 1: then the dropdown opens with all the options within the options in the step 1."
    # Replaces the flat radio-button list with two independently expandable/collapsible
    # sections (Streamlit's st.expander keeps each section's open/closed state on its own,
    # so opening one doesn't close the other - confirmed as the wanted behaviour over an
    # accordion). Clicking an option inside a section selects it immediately and moves on,
    # the same one-click navigation as clicking a leaf item in Travel Compositor's sidebar -
    # there's no separate "Continue" button to click afterwards anymore.
    with st.expander("📦 Create a new product", expanded=False):
        st.caption("Each of these CREATES something new: either a brand-new product with its "
                  "first Modality, or a new Modality added to one that already exists.")
        if st.button("ClosedTour", key="pt_choice_closedtour", use_container_width=True):
            st.session_state.product_type = "ClosedTour"
            st.rerun()
        st.caption("Multi-day tour (itinerary, room-occupancy pricing).")
        if st.button("Ticket", key="pt_choice_ticket", use_container_width=True):
            st.session_state.product_type = "Ticket"
            st.rerun()
        st.caption("Single-destination excursion/activity, no overnight, passenger-type pricing.")
        if st.button("Hotel", key="pt_choice_hotel", use_container_width=True):
            st.session_state.product_type = "Hotel"
            st.rerun()
        st.caption("A full accommodation contract: rooms, meal plans, offers, supplements and "
                  "rate seasons.")

    with st.expander("🔧 Manage an existing product", expanded=False):
        if st.button(MANUAL_INFO_CHOICE, key="pt_choice_manual", use_container_width=True):
            st.session_state.product_type = MANUAL_INFO_CHOICE
            st.rerun()
        st.caption("No document at all: write something you know about a supplier — a moved "
                  "pickup point, changed cancellation terms — and it is attached automatically "
                  "to every future upload of that product type.")
        if st.button(UPDATE_REFRESH_CHOICE, key="pt_choice_updaterefresh", use_container_width=True):
            st.session_state.product_type = UPDATE_REFRESH_CHOICE
            st.rerun()
        st.caption("The one place for every other kind of update - new prices, changed "
                  "details, a new Modality on something that already exists - for ANY of the "
                  "five product types, including Transfer and Transport (which can no longer "
                  "be created fresh through this tool, only updated here).")
        if st.button(MIGRATE_SUPPLIER_CHOICE, key="pt_choice_migratesupplier", use_container_width=True):
            st.session_state.product_type = MIGRATE_SUPPLIER_CHOICE
            st.rerun()
        st.caption("Recreates a supplier's Transfers under a different supplier and switches the "
                  "originals off - for when a supplier relationship itself changes, not a single "
                  "product's details.")
        if st.button(TRANSPORT_CANCELLATION_BULK_CHOICE, key="pt_choice_ctbulk", use_container_width=True):
            st.session_state.product_type = TRANSPORT_CANCELLATION_BULK_CHOICE
            st.rerun()
        st.caption("Applies one cancellation policy to every (or a chosen subset of) one "
                  "supplier's live Transports at once - for when the supplier's terms "
                  "themselves changed, not a single product's details. Transport only - "
                  "Transfer has no equivalent structured field to safely bulk-overwrite.")
    st.stop()

if st.session_state.product_type == UPDATE_REFRESH_CHOICE:
    render_update_refresh_flow(client)
    st.stop()

if st.session_state.product_type == TRANSPORT_CANCELLATION_BULK_CHOICE:
    render_transport_cancellation_bulk_flow(client)
    st.stop()

if st.session_state.product_type == MANUAL_INFO_CHOICE:
    render_manual_information_flow(client)
    st.stop()

if st.session_state.product_type == MIGRATE_SUPPLIER_CHOICE:
    render_supplier_migration_flow(client)
    st.stop()

if st.session_state.product_type == "Ticket":
    render_ticket_flow(client)
    st.stop()

if st.session_state.product_type == "Hotel":
    render_hotel_flow(client)
    st.stop()


# ----------------------------------------------------------------------
# STEP 2: What do you want to do? + Supplier
# ----------------------------------------------------------------------


st.header("Step 2 — What do you want to do?")

if st.session_state.step1_confirmed:
    st.success(f"✅ Action: **{ACTION_LABELS[st.session_state.cfg_action]}** | "
               f"Supplier ID: **{st.session_state.cfg_supplier_id}**")
    if st.button("🔄 Change action / supplier"):
        st.session_state.step1_confirmed = False
        st.session_state.step2_confirmed = False
        # The Existing Tour Code box now persists via a stable widget key (see the
        # CONFIRMED BUG FIX note where it's rendered) so its typed text survives reruns -
        # correct within one action/supplier, but it must NOT leak into a different one.
        st.session_state.pop("ct_existing_tour_code_in", None)
        st.rerun()
else:
    action_key = st.radio(
        "Choose one:",
        list(ACTION_LABELS.keys()),
        format_func=lambda k: ACTION_LABELS[k],
        help="Creating makes something brand-new; Updating changes something that already exists."
    )

    if st.session_state.suppliers_cache is None:
        with st.spinner("Loading supplier list from Travel Compositor..."):
            try:
                st.session_state.suppliers_cache = client.get_all_suppliers()
            except Exception as e:
                st.error(f"❌ Couldn't load the supplier list: {friendly_error_message(e)}")
                st.session_state.suppliers_cache = []

    supplier_id_choice = None
    if st.session_state.suppliers_cache:
        # LOCKED: only "Momira_"-prefixed suppliers may be picked - forces
        # the human to explicitly choose a real Momira supplier instead of
        # any other supplier that happens to exist in the account.
        momira_suppliers = [
            s for s in st.session_state.suppliers_cache
            if (s.get("commercialName") or s.get("legalName") or "").strip().lower().startswith("momira_")
        ]
        if not momira_suppliers:
            st.error("🚫 No suppliers starting with 'Momira_' were found in this account - can't continue. "
                    "Check the supplier exists in Travel Compositor with the correct naming, or refresh below.")
        else:
            supplier_options = {
                f"{s.get('commercialName') or s.get('legalName')} — ID {s.get('id')}": s.get("id")
                for s in momira_suppliers
            }
            # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-26): carried over from the "Do something
            # else with this Code" button after a ClosedTour publish (see just_published_tour_code
            # above) - a convenience default, not a hard requirement, since the human might
            # genuinely want a different supplier for the next action. Popped once so it only
            # affects the very next Step 1 render, not every one after.
            option_labels = list(supplier_options.keys())
            prefill_supplier = st.session_state.pop("cfg_prefill_supplier_id", None)
            default_index = 0
            if prefill_supplier is not None:
                matches = [i for i, label in enumerate(option_labels)
                          if str(supplier_options[label]) == str(prefill_supplier)]
                if matches:
                    default_index = matches[0]
            selected_label = st.selectbox("Select Supplier", option_labels, index=default_index)
            supplier_id_choice = str(supplier_options[selected_label])
        if st.button("🔄 Refresh supplier list"):
            st.session_state.suppliers_cache = None
            st.rerun()
    else:
        st.error("Could not load the supplier list from Travel Compositor.")
        if st.button("🔄 Try again"):
            st.rerun()
        with st.expander("⚠️ Emergency manual entry (only if the list keeps failing to load)"):
            st.caption("Bypasses the Momira_ check above - only use this if you've already confirmed the "
                      "numeric ID belongs to a real Momira_ supplier.")
            supplier_id_choice = st.text_input("Supplier ID (numeric)", value="")

    if st.button("➡️ Continue to Step 3", type="primary", disabled=not supplier_id_choice):
        st.session_state.cfg_action = action_key
        st.session_state.cfg_supplier_id = supplier_id_choice
        st.session_state.step1_confirmed = True
        st.rerun()

    st.stop()


# ----------------------------------------------------------------------
# STEP 3: Action-specific details
# ----------------------------------------------------------------------
st.header("Step 3 — Details for this action")
action = st.session_state.cfg_action
needed = ACTION_FIELDS[action]
supplier_id = st.session_state.cfg_supplier_id

cancellation_links.render_cancellation_link_editor(supplier_id, "ClosedTour", key_suffix="_setup")

if st.session_state.step2_confirmed:
    st.success("✅ Step 3 details confirmed.")
    if st.button("🔄 Change details"):
        st.session_state.step2_confirmed = False
        st.rerun()
else:
    provider_code_in = min_pax_in = max_pax_in = currency_in = modality_code_in = existing_tour_code_in = None
    on_request_in = True
    release_days_in = 30

    if "existing_tour_code" in needed:
        # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): this widget used to be a one-shot
        # `value=prefill` with NO `key=` - Streamlit only honors `value=` on a widget's very
        # first render, so the moment the mandatory "Check what's already online" button below
        # is clicked, the rerun it triggers pops the prefill to "" (already consumed on the
        # PREVIOUS render), the widget re-renders empty with nothing to preserve the typed/
        # prefilled text (no key = no persisted state), `existing_tour_code_in` becomes "", and
        # the button - now `disabled=not existing_tour_code_in` - goes disabled on that same
        # rerun. The first click always silently no-oped. Fixed by giving the widget a stable
        # `key` (so Streamlit persists whatever's typed across reruns) and only using the
        # prefill to SEED that key once, the one time something else (a "recheck this code"
        # shortcut elsewhere) actually sets it - never on every render.
        _ct_code_key = "ct_existing_tour_code_in"
        if "prefill_existing_tour_code" in st.session_state:
            _prefill = st.session_state.pop("prefill_existing_tour_code")
            if _prefill:
                st.session_state[_ct_code_key] = _prefill
        existing_tour_code_in = st.text_input(
            "Existing Tour Code",
            key=_ct_code_key,
            placeholder="e.g. BKK-1 (your own ClosedTour/Provider Code) or CLOSEDTOUR-411099",
        ).strip()
        st.caption(
            "Try your own ClosedTour/Provider Code first (e.g. 'BKK-1') - the app will "
            "automatically also try the internal 'CLOSEDTOUR-XXXXX' format as a fallback "
            "if the first attempt doesn't work."
        )

        if st.button("🔍 Check what's already online for this code", disabled=not existing_tour_code_in):
            with st.spinner("Fetching from Travel Compositor..."):
                fetched, working_code = try_code_variants(
                    lambda c: client.get_closed_tour(supplier_id, c), existing_tour_code_in
                )
                st.session_state.fetched_tour = fetched
                st.session_state.fetched_option = None
                st.session_state.working_tour_code = working_code
                # CONFIRMED BUG FIX (audit CRITICAL #3, 2026-09-01): record which code this
                # fetch was actually for, every time - success OR failure - so
                # fetched_tour_matches_code() can tell a genuinely-fresh fetch for THIS tour
                # apart from stale data left over from a previous tour. See that function's
                # docstring for the full leak this closes.
                st.session_state.fetched_tour_for_code = existing_tour_code_in
                if isinstance(fetched, dict) and "error" not in fetched:
                    st.session_state.fetched_tour_provider_code = fetched.get("providerCode", "")
                    st.session_state.fetched_tour_min_pax = fetched.get("minPax")
                    st.session_state.fetched_tour_max_pax = fetched.get("maxPax")
                    st.session_state.fetched_tour_currency = fetched.get("currency")
                    # CONFIRMED FIX (real near-data-loss report): pre-fill the Step 5 review
                    # screen from this tour's OWN live data immediately, instead of leaving it
                    # blank until/unless a fresh document is extracted - see
                    # _map_fetched_tour_to_data()'s docstring for the full story.
                    if action == "update_tour":
                        st.session_state.extracted = _map_fetched_tour_to_data(fetched)
                        st.session_state.raw_preview = (
                            f"(No new document/URL provided - these fields were pre-filled from the "
                            f"tour's CURRENT live data on Travel Compositor, code `{existing_tour_code_in}`. "
                            f"Edit below, or provide a new source and click Extract to bring in updates - "
                            f"your existing values won't be blanked out by an incomplete new extraction.)"
                        )
                        st.session_state.payloads = None
                        st.session_state.images_text_value = ""
                        st.session_state.doc_raw_images = []
                        st.session_state.hosted_image_candidates = []

        if st.session_state.get("fetched_tour"):
            t = st.session_state.fetched_tour
            if "error" in t:
                st.error(f"Not found or error: {t.get('message', t)}")
            else:
                working_code = st.session_state.get("working_tour_code") or existing_tour_code_in
                st.success(f"Found: **{t.get('name', '(no name)')}** (using code `{working_code}`)")
                if action == "update_tour":
                    st.caption(f"Will reuse from this tour: Min Pax **{t.get('minPax')}**, "
                              f"Max Pax **{t.get('maxPax')}**, Currency **{t.get('currency')}**, "
                              f"ClosedTour Code **{t.get('providerCode')}**.")
                existing_modalities = t.get("modalityCodes", [])
                st.write(f"Existing modality codes: {existing_modalities if existing_modalities else '(none)'}")
                if existing_modalities and "modality_code" in needed:
                    check_modality = st.selectbox("Check pricing for modality:", existing_modalities, key="check_modality_pick")
                    if st.button("🔍 Fetch this modality's live pricing"):
                        with st.spinner("Fetching option..."):
                            st.session_state.fetched_option = client.get_closed_tour_option(
                                supplier_id, working_code, check_modality
                            )
                    if st.session_state.get("fetched_option"):
                        opt = st.session_state.fetched_option
                        if "error" in opt:
                            st.error(f"Could not fetch option: {opt.get('message', opt)}")
                        else:
                            with st.expander("Live pricing for this modality", expanded=True):
                                for row in opt.get("priceList", []):
                                    label = row.get("name") or ""
                                    st.write(f"**{row.get('startDate')} → {row.get('endDate')}** {label}")
                                    st.json(row.get("price", {}))

    ct_update_scope_in = "whole_tour"
    if action == "update_tour":
        # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-28), identical to Ticket's equivalent
        # radio: asked up front so a human who only needs a price fix doesn't pay for (or
        # wait through) the full name/description/cancellation extraction, and so "whole
        # tour" - which now actually publishes the pricing it extracts, see ACTION_FIELDS'
        # comment - knows to expect a Modality Code below.
        st.markdown("##### What do you want to update?")
        _ct_scope_choice = st.radio(
            "Update scope", label_visibility="collapsed",
            options=["Price only (fast, cheaper - skips re-checking name/description/etc.)",
                    "Whole tour (also re-checks name, description, cancellation policy, etc.)"],
            key="ct_update_scope_radio",
        )
        ct_update_scope_in = "price_only" if _ct_scope_choice.startswith("Price only") else "whole_tour"

    if "provider_code" in needed:
        provider_code_in = st.text_input("ClosedTour Code", value="", placeholder="e.g. ASW-1")
        render_code_availability_check(client, "tour", supplier_id, provider_code_in, "tour")
    if "min_pax" in needed:
        min_pax_in = st.selectbox("Min Pax", [1, 2])
    if "max_pax" in needed:
        max_pax_in = st.selectbox("Max Pax", list(range(2, 10)), index=7)
    if "currency" in needed:
        # CONFIRMED PRODUCT-OWNER RULE (2026-09-01, full-app audit HIGH #1 fix): "Once a
        # currency has been set, it can never be changed and all Modalities are using the
        # same Currency." "Change details" (above) resets step2_confirmed and re-renders this
        # very widget - without this lock, an operator could pick a different currency here
        # AFTER Modality 1 already has price data entered, and republishing would carry
        # Modality 1's old-currency prices forward under the new currency label (the other
        # half of the same bug render_currency_check's docstring documents - that widget is
        # now locked too, but "Change details" was the second way to re-set currency after
        # data already existed, so both had to close).
        _currency_already_set = bool(st.session_state.get("cfg_currency"))
        if _currency_already_set:
            currency_in = st.session_state.cfg_currency
            st.selectbox(
                "Currency", CURRENCY_OPTIONS,
                index=CURRENCY_OPTIONS.index(currency_in) if currency_in in CURRENCY_OPTIONS else 0,
                disabled=True,
                help="Locked - a currency, once set, cannot be changed. It applies to every "
                     "Modality of this tour.",
            )
        else:
            currency_in = st.selectbox("Currency", CURRENCY_OPTIONS)
    if "modality_code" in needed:
        # "update_tour" needs the SAME "which existing Modality" semantics as
        # "update_option" now (see ACTION_FIELDS's comment) - both are asking for an
        # ALREADY-LIVE modality's code, not a brand-new one.
        default_modality = st.session_state.get("check_modality_pick", "") if action in ("update_option", "update_tour") else ""
        label = "Modality Code to update" if action in ("update_option", "update_tour") else "Unique Modality Code"
        modality_code_in = st.text_input(label, value=default_modality or "", placeholder="e.g. Standard Cruise")
    if "on_request" in needed:
        on_request_in = st.checkbox("On Request", value=True)
    if "release_days" in needed:
        release_days_in = st.number_input(
            "Release Day (days before departure this tour becomes bookable)",
            min_value=0, value=30,
            help="Default 30 days before departure."
        )

    required_ok = True
    if "provider_code" in needed and not provider_code_in.strip():
        required_ok = False
    # CONFIRMED REAL BUG (reported: a human was able to continue past this
    # step with a ClosedTour Code that was ALREADY TAKEN - the availability
    # check above was purely informational, an st.error the human could
    # simply ignore and click through anyway). The actual publish-time
    # rejection for a duplicate code is much harder to recover from (it
    # happens after the whole batch is built), so block progression here
    # instead - reuses check_code_availability's own session-state cache
    # (already populated by render_code_availability_check above), so this
    # costs no extra API call. Only blocks on a CONFIRMED "exists" - a None
    # result (couldn't verify, e.g. Travel Compositor briefly unreachable)
    # doesn't block, matching render_code_availability_check's own display
    # logic (which also stays silent on None rather than claiming a pass).
    if "provider_code" in needed and provider_code_in.strip():
        provider_code_check = check_code_availability(client, "tour", supplier_id, provider_code_in)
        if provider_code_check and provider_code_check["exists"]:
            required_ok = False
    if "currency" in needed and not (currency_in or "").strip():
        required_ok = False
    if "modality_code" in needed and not (modality_code_in or "").strip():
        required_ok = False
    if "existing_tour_code" in needed and not existing_tour_code_in:
        required_ok = False
    # CONFIRMED BUG FIX (audit CRITICAL #3, 2026-09-01): must match THIS code, not just be
    # present - see fetched_tour_matches_code()'s docstring. Without the match check, editing
    # the code above after a previous successful check (for a different tour) silently let the
    # previous tour's stale currency/min/max/provider-code through.
    # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): "update_option" was missing from this
    # tuple - ACTION_FIELDS deliberately excludes "currency" from update_option's own needed
    # fields ("an UPDATE never asks for things the live record already has"), relying entirely
    # on it being inherited from the fetched tour (fetched_tour_currency, used further down once
    # Steps 4+ render - see that block's own fetched_tour_matches_code() check, which DOES
    # already cover update_option). But THIS gate - the only thing standing between Step 3 and
    # Step 4 - never required that fetch to have happened for update_option, so an operator
    # could click Continue having never checked what's online, and every price row would
    # publish under whatever cfg_currency last held (blank on a fresh session, which the
    # downstream builder defaults to EUR) - silently re-denominating a non-EUR tour.
    if action in ("update_tour", "add_option", "update_option") and not fetched_tour_matches_code(existing_tour_code_in):
        required_ok = False
        st.info("Click 'Check what's already online for this code' above first (or again, if you "
               "changed the code) - this fetches the existing tour's Currency (and for updates, "
               "Min/Max Pax too) so you don't have to re-enter them.")

    if st.button("➡️ Continue to Step 4", type="primary", disabled=not required_ok):
        if action == "update_tour":
            min_pax_in = st.session_state.get("fetched_tour_min_pax") or 1
            max_pax_in = st.session_state.get("fetched_tour_max_pax") or 9
            currency_in = st.session_state.get("fetched_tour_currency") or ""
        elif action == "add_option":
            currency_in = st.session_state.get("fetched_tour_currency") or ""
        st.session_state.cfg_provider_code = provider_code_in or ""
        st.session_state.cfg_min_pax = min_pax_in or 1
        st.session_state.cfg_max_pax = max_pax_in or 9
        st.session_state.cfg_currency = currency_in or ""
        st.session_state.cfg_modality_code = modality_code_in or ""
        st.session_state.cfg_on_request = on_request_in
        st.session_state.cfg_release_days = release_days_in
        st.session_state.cfg_existing_tour_code = existing_tour_code_in or ""
        st.session_state.cfg_update_scope = ct_update_scope_in
        st.session_state.step2_confirmed = True
        st.rerun()

    if not required_ok:
        if "provider_code" in needed and provider_code_in.strip() and st.session_state.get("_code_exists_cache", {}).get(("tour", supplier_id, provider_code_in.strip()), {}).get("exists"):
            st.info("Choose a different ClosedTour Code above (the one you entered is already taken) to continue.")
        else:
            st.info("Fill in all fields above to continue.")
    st.stop()


supplier_id = st.session_state.cfg_supplier_id
provider_code = st.session_state.cfg_provider_code
min_pax = st.session_state.cfg_min_pax
max_pax = st.session_state.cfg_max_pax
currency = st.session_state.cfg_currency
modality_code = st.session_state.cfg_modality_code
existing_tour_code = st.session_state.cfg_existing_tour_code

# CONFIRMED REAL RULE (product owner): "if updating a service it never has to be asked for
# the code (it is set already), never for the currency (it also is set), never for the min
# and max passenger." Step 3 no longer asks for them on an update - so take them from the
# tour that was actually fetched. Without this the update would publish the blank/default
# Step-3 values over a live tour, re-denominating its prices and resetting its capacity.
#
# CONFIRMED BUG FIX (audit CRITICAL #3, 2026-09-01): this block runs on EVERY rerun of Steps
# 4+, re-pulling fetched_tour_currency/min_pax/max_pax/provider_code fresh each time - so even
# though Step 3's own "Continue" button is now guarded (see fetched_tour_matches_code() above),
# these globals must ALSO be re-validated here against the tour actually being worked on
# (cfg_existing_tour_code). Otherwise a stale fetch left over from a previous tour (or one that
# failed silently) keeps being blended in on every single render of this tour's own screens.
if action in ("update_tour", "update_option", "add_option") and not fetched_tour_matches_code(existing_tour_code):
    st.warning("⚠️ The tour data fetched by 'Check what's already online' doesn't match this "
              "tour's code (or was never fetched / failed) - go back to Step 3 and re-check "
              "before continuing, to avoid publishing with another tour's currency, pax limits, "
              "or code.")
if action in ("update_tour", "update_option", "add_option") and fetched_tour_matches_code(existing_tour_code):
    _live_currency = st.session_state.get("fetched_tour_currency")
    _live_min = st.session_state.get("fetched_tour_min_pax")
    _live_max = st.session_state.get("fetched_tour_max_pax")
    _live_code = st.session_state.get("fetched_tour_provider_code")
    currency = _live_currency or currency
    min_pax = _live_min if _live_min not in (None, "") else min_pax
    max_pax = _live_max if _live_max not in (None, "") else max_pax
    provider_code = _live_code or provider_code
on_request = st.session_state.cfg_on_request
days_available_before_release = st.session_state.cfg_release_days
# Only meaningful for action == "update_tour" - see ACTION_FIELDS's comment and the "What do
# you want to update?" radio in Step 3. Defaults to "whole_tour" for every other action so
# nothing below has to special-case "key not set yet".
ct_update_scope = st.session_state.get("cfg_update_scope", "whole_tour")

_action_to_publish_label = {
    "create": "Create a brand-new tour (+ first option)",
    "add_option": "Add a new option to an existing tour",
    "update_tour": "Update an existing tour's details",
    "update_option": "Update an existing option",
}
publish_action = _action_to_publish_label[action]
# CONFIRMED PRODUCT-OWNER REQUEST (2026-08-28): "Price only" under action "update_tour" is
# structurally IDENTICAL to action "update_option" from here on - same cheap extraction, same
# review (pricing/schedule only, no name/description/cancellation), same publish call.
# Relabeling publish_action here, rather than adding new branches further down, is what makes
# that reuse automatic instead of duplicated.
ct_price_only_via_update_tour = action == "update_tour" and ct_update_scope == "price_only"
if ct_price_only_via_update_tour:
    publish_action = "Update an existing option"
is_option_only = action in ("add_option", "update_option") or ct_price_only_via_update_tour


# ----------------------------------------------------------------------
# STEP 4: Input source
# ----------------------------------------------------------------------
st.header("Step 4 — Input Source")
st.caption("Provide a URL, a document, or both. If you give both, information from each will be "
           "combined into one extraction (e.g. itinerary from a web page + hotel detail from a document).")

url = st.text_input("Product page URL (optional)")
uploaded_files = st.file_uploader(
    "Upload DMC document(s) (optional, multiple allowed)",
    type=["pdf", "docx", "xlsx"], accept_multiple_files=True
)
extraction_hint = st.text_input(
    "Extraction hint (optional)",
    placeholder="e.g. 'Use the German-language pricing table' or 'Focus on the Superior room category'",
    help="Short, specific guidance for the AI if the source is ambiguous (e.g. multiple languages, "
         "multiple room categories). Leave blank for normal extraction."
)

multi_modality_mode = False
if action == "add_option":
    multi_modality_mode = st.checkbox(
        "📦 I'm adding MULTIPLE modalities from this same source",
        help="The app will detect distinct pricing categories (e.g. Standard/Deluxe cabin) from one "
             "shared document/URL, and let you review + publish each one individually, one at a time."
    )

if multi_modality_mode:
    render_multi_modality_flow(client, url=url, uploaded_files=uploaded_files)
    st.stop()

# "Create" always routes through the batch-capable flow now, regardless of
# how many tour variants the source actually turns out to describe - it
# transparently handles a single variant exactly like the old single-tour
# flow did (just one row to fill in), and auto-detects/handles multiple
# variants without the human needing to pre-declare "this has several" via
# a checkbox first. This removes the old upfront single-vs-multiple choice
# per the confirmed design (always auto-detect, one unified queue-based UI
# regardless of count).
if action == "create":
    render_multi_tour_flow(client, supplier_id, currency, on_request, days_available_before_release, url, uploaded_files,
                          min_pax=min_pax, max_pax=max_pax, default_tour_code=provider_code,
                          extraction_hint=extraction_hint or None)
    st.stop()

if st.button("🔎 Extract", disabled=not (url or uploaded_files)):
    spinner_msg = "Gathering pricing/schedule content..." if is_option_only else "Gathering content and checking for multiple tour variants..."
    with st.spinner(spinner_msg):
        try:
            combined_parts = []
            doc_names = []
            if url:
                page_text, page_text_err = _fetch_url_text_safe(url)
                if page_text is not None:
                    combined_parts.append(f"--- SOURCE: WEB PAGE ({url}) ---\n{page_text}")
                else:
                    st.warning(f"⚠️ Couldn't fetch the product page URL: {page_text_err}.")
            doc_image_urls = []
            doc_raw_images = []  # [(filename, bytes), ...] - always kept as a guaranteed fallback
            seen_image_hashes = set()  # shared across all documents in this batch, so a logo repeated across files is only extracted once
            for uploaded in (uploaded_files or []):
                doc_names.append(uploaded.name)
                suffix = os.path.splitext(uploaded.name)[1]
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(uploaded.getbuffer())
                    tmp_path = tmp.name
                _doc_text = extract_raw_text(tmp_path)
                _scan_warning = document_reader_scanned_warning(tmp_path, _doc_text)
                if _scan_warning:
                    st.session_state.setdefault("_scanned_doc_warnings", []).append(_scan_warning)
                combined_parts.append(f"--- SOURCE: UPLOADED DOCUMENT ({uploaded.name}) ---\n{_doc_text}")

                remaining_budget = 12 - len(doc_raw_images)
                _doc_image_errors = []
                embedded_images = extract_images(tmp_path, max_images=remaining_budget, seen_hashes=seen_image_hashes, errors=_doc_image_errors, label=uploaded.name) if remaining_budget > 0 else []
                _warn_page_image_upload_errors(_doc_image_errors)
                if embedded_images:
                    for i, (img_bytes, ext) in enumerate(embedded_images):
                        doc_raw_images.append((f"{os.path.splitext(uploaded.name)[0]}_img{i+1}.{ext or 'jpg'}", img_bytes))
                    with st.spinner(f"Trying to auto-upload {len(embedded_images)} image(s) from {uploaded.name}..."):
                        try:
                            new_urls = upload_images_r2(embedded_images)
                            doc_image_urls.extend(new_urls)
                            if new_urls:
                                st.caption(f"✅ Auto-uploaded {len(new_urls)}/{len(embedded_images)} image(s) from {uploaded.name}.")
                            if len(new_urls) < len(embedded_images):
                                st.caption(f"ℹ️ {len(embedded_images) - len(new_urls)} image(s) will be available to download instead (see Step 5).")
                        except Exception as e:
                            st.caption(f"ℹ️ Auto-upload unavailable ({e}) - all {len(embedded_images)} image(s) from "
                                      f"{uploaded.name} will be available to download instead (see Step 5).")

                os.remove(tmp_path)

            if not combined_parts:
                st.error("Nothing to extract - the product page URL couldn't be fetched and no document(s) were provided.")
                st.stop()

            if len(doc_image_urls) >= len(doc_raw_images):
                doc_raw_images = []

            raw_text = "\n\n".join(combined_parts)

            if is_option_only:
                # Lightweight path: no variant detection needed - we're adding
                # pricing/schedule to an ALREADY-KNOWN modality, not identifying
                # which tour variant this is.
                #
                # CONFIRMED FIX: "Add a new option to an existing tour" is
                # introducing a genuinely NEW Modality - if that Modality has
                # its own supplements, they need to be captured too (supplements
                # live on the MAIN tour, not the option, so they get folded into
                # the follow-up update_closed_tour PUT below - see the publish
                # step). extract_option_only_data() deliberately excludes
                # supplements (it's shared with "update an existing option",
                # where introducing a brand-new supplement doesn't make sense),
                # so use extract_modality_data() instead specifically for
                # add_option, which extracts the exact same price_list/schedule
                # fields PLUS supplements, scoped to this one new Modality.
                if action == "add_option":
                    tour_nights = (st.session_state.get("fetched_tour") or {}).get("nights")
                    data = extract_modality_data(raw_text, human_hint=extraction_hint or None, tour_nights=tour_nights)
                else:
                    data = extract_option_only_data(raw_text, human_hint=extraction_hint or None)
                st.session_state.extracted = data
                sources_desc = " + ".join(filter(None, [url] + doc_names))
                st.session_state.raw_preview = f"Source(s): {sources_desc}\n\n{raw_text}"
                st.session_state.payloads = None
                st.session_state.doc_raw_images = doc_raw_images
                st.success("Pricing/schedule extraction complete. Review and edit below.")
            else:
                variants = detect_tour_variants(raw_text)

                if variants:
                    st.session_state.pending_variants = variants
                    st.session_state.pending_raw_text = raw_text
                    st.session_state.pending_url = url or None
                    st.session_state.pending_hint = extraction_hint or None
                    st.session_state.pending_doc_images = doc_image_urls
                    st.session_state.pending_doc_raw_images = doc_raw_images
                else:
                    data = extract_structured_data(raw_text, human_hint=extraction_hint or None)
                    # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): see the matching
                    # fix in the Ticket update flow - setting the placeholder before the merge
                    # made it always win over an update's real, already-live photos.
                    data["image_urls"] = []
                    if action == "update_tour":
                        # Merge on top of the tour's real live values (pre-filled in Step 3) rather
                        # than replacing them outright - an incomplete fresh extraction shouldn't
                        # blank out fields the new source just didn't happen to mention.
                        data = _merge_extraction_over_baseline(st.session_state.get("extracted") or {}, data)
                    if not data.get("image_urls"):
                        data["image_urls"] = [FALLBACK_IMAGE]
                    # Only fills in when this document (and, for an update, the live baseline
                    # it was just merged over) had no cancellation terms of its own - see
                    # apply_cancellation_link_default's docstring.
                    st.session_state.ct_cancellation_link_scope = cancellation_links.apply_cancellation_link_default(
                        data, supplier_id, "ClosedTour")
                    st.session_state.extracted = data
                    reset_child_age_band_widgets("ct")
                    st.session_state.images_text_value = ""
                    sources_desc = " + ".join(filter(None, [url] + doc_names))
                    st.session_state.raw_preview = f"Source(s): {sources_desc}\n\n{raw_text}"
                    st.session_state.payloads = None
                    _warn_page_image_upload_errors(_add_page_images_to_doc_pool(url, doc_raw_images, doc_image_urls))
                    st.session_state.doc_raw_images = doc_raw_images
                    st.session_state.hosted_image_candidates = list(dict.fromkeys(doc_image_urls))
                    st.success("Extraction complete. Review and edit below.")
        except Exception as e:
            st.error(f"Extraction failed: {friendly_error_message(e)}")

if st.session_state.get("pending_variants") and not is_option_only:
    variants = st.session_state.pending_variants
    st.warning(f"⚠️ This content describes {len(variants)} distinct tour variants — which one do you want to use?")
    # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01, was a "tick several to create them all
    # as a batch" option here): this block is only ever reached for action == "update_tour" -
    # "create" always routes through render_multi_tour_flow above and st.stop()s first, so the
    # "batch review" path below was UNREACHABLE dead code for its only sensible use case, and
    # made no sense for update_tour anyway (you're updating ONE existing tour, not creating
    # several new ones). Ticking multiple variants and clicking through used to write to
    # mct_queue/mct_queue_index and set mct_phase="reviewing" - a phase render_multi_tour_flow's
    # own dispatcher (see its docstring, phase list starting at app.py:667) never handles, so the
    # whole Create-ClosedTour screen rendered blank until "Switch tool" reset the state. Fixed by
    # only ever allowing ONE variant to be picked here - the working single-tour path below.
    st.caption("Only one variant can be selected here (this picker is for choosing which "
              "variant to extract, not for batch-creating several tours).")

    if "pending_variant_selection" not in st.session_state:
        st.session_state.pending_variant_selection = [
            {"label": v.get("label", f"Variant {i+1}"), "nights": v.get("nights"), "selected": False}
            for i, v in enumerate(variants)
        ]
    pv_selection = st.session_state.pending_variant_selection

    for i, sel in enumerate(pv_selection):
        nights_note = f" ({sel['nights']} nights)" if sel.get("nights") else ""
        newly_checked = st.checkbox(f"{sel['label']}{nights_note}", value=sel["selected"], key=f"pv_sel_{i}")
        if newly_checked and not sel["selected"]:
            # Enforce single-select: checking one unchecks every other (a real radio button
            # would be cleaner, but this preserves each variant's own widget key/state).
            for other in pv_selection:
                other["selected"] = False
        sel["selected"] = newly_checked

    pv_num_selected = sum(1 for s in pv_selection if s["selected"])
    if pv_num_selected > 1:
        # Guards the one release-to-release gap where two boxes can appear checked in the same
        # run (the uncheck above only takes effect next rerun) - never publish against that.
        st.error("🚫 Please tick only one variant.")

    if st.button("✅ Confirm and Extract Full Details", disabled=pv_num_selected != 1):
        with st.spinner("Extracting full details for the selected variant..."):
            try:
                chosen = next(s for s in pv_selection if s["selected"])
                chosen_label = chosen["label"]
                data = extract_structured_data(
                    st.session_state.pending_raw_text, variant_hint=chosen_label,
                    human_hint=st.session_state.get("pending_hint")
                )

                pending_url = st.session_state.get("pending_url")
                # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): see the matching fix
                # above - the placeholder must not be set before the merge.
                data["image_urls"] = []
                preview = f"(Extracted variant: {chosen_label})\n\n{st.session_state.pending_raw_text}"
                if action == "update_tour":
                    data = _merge_extraction_over_baseline(st.session_state.get("extracted") or {}, data)
                if not data.get("image_urls"):
                    data["image_urls"] = [FALLBACK_IMAGE]

                st.session_state.ct_cancellation_link_scope = cancellation_links.apply_cancellation_link_default(
                    data, supplier_id, "ClosedTour")
                st.session_state.extracted = data
                reset_child_age_band_widgets("ct")
                st.session_state.images_text_value = ""
                st.session_state.raw_preview = preview
                st.session_state.payloads = None
                pending_doc_raw_images = list(st.session_state.get("pending_doc_raw_images", []))
                pending_doc_image_urls = list(st.session_state.get("pending_doc_images", []))
                _warn_page_image_upload_errors(_add_page_images_to_doc_pool(pending_url, pending_doc_raw_images, pending_doc_image_urls))
                st.session_state.doc_raw_images = pending_doc_raw_images
                st.session_state.hosted_image_candidates = list(dict.fromkeys(pending_doc_image_urls))
                st.session_state.pending_variants = None
                st.session_state.pending_raw_text = None
                st.session_state.pending_url = None
                st.session_state.pending_variant_selection = None
                st.rerun()
            except Exception as e:
                st.error(f"Extraction failed: {friendly_error_message(e)}")


# ----------------------------------------------------------------------
# STEP 5: Side-by-side review & edit
# ----------------------------------------------------------------------
if st.session_state.extracted:
    data = st.session_state.extracted

    st.header("Step 5 — Review & Edit")
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Original Source")
        render_readonly_source(st.session_state.raw_preview, height=600)

    with col2:
        if is_option_only:
            st.subheader("Only pricing/schedule are needed for this action")
            st.caption("Tour details (name, description, hotels, itinerary, supplements) are skipped "
                      "entirely - they belong to the existing tour and aren't touched by adding/updating "
                      "a Modality. Scroll down for Departure Schedule and Pricing.")
        else:
            st.subheader("Extracted Data (click ✏️ to edit each field)")
            DEFAULT_MEETING_POINT = ("Meet your guide in the airport arrival hall or, if you are already in the "
                                     "tour's starting city, in your hotel lobby.")
            if not data.get("meeting_point"):
                data["meeting_point"] = DEFAULT_MEETING_POINT

            editable_field("Tour name", data, "tour_name", widget="text_input")
            editable_field("Description", data, "description", widget="html_text_area", height=200)
            editable_field("Hotels", data, "hotels_text", widget="text_area", height=140)
            editable_field("Included", data, "included", widget="html_list_area", height=120)
            editable_field("Excluded", data, "excluded", widget="html_list_area", height=120)
            editable_field("Meeting point", data, "meeting_point", widget="text_input")
            editable_field("Policy remarks", data, "policy_remarks", widget="text_area", height=100)
            # CONFIRMED HOUSE RULE (product owner, 2026-08-24) - see the mct_main copy above.
            editable_field("What to bring (added to voucher remarks)", data, "what_to_bring",
                           widget="text_area", height=80)
            if st.session_state.get("ct_cancellation_link_scope"):
                st.caption(f"ℹ️ This document didn't state its own cancellation terms - the table "
                          f"below was filled in from {st.session_state['ct_cancellation_link_scope']}. "
                          f"Edit or clear it if this tour needs different terms.")
            render_cancellation_policy_editor(data, "legacy_tour")
            editable_field("Nights", data, "nights", widget="number_input")

            tcol1, tcol2 = st.columns(2)
            with tcol1:
                render_optional_time_input("Start Time", data, "start_time", "ct_start_time")
            with tcol2:
                render_optional_time_input("End Time", data, "end_time", "ct_end_time", default_time_str="18:00:00")

            render_child_age_band(data, "ct")

            dest_rows = [{"#": i + 1, "Destination": d} for i, d in enumerate(data.get("itinerary_destinations", []))]
            dest_df = pd.DataFrame(dest_rows) if dest_rows else pd.DataFrame(columns=["#", "Destination"])

            def _save_destinations(edited_df):
                data["itinerary_destinations"] = [
                    str(row.get("Destination") or "").strip() for _, row in edited_df.iterrows()
                    if _safe_cell_str(row.get("Destination")).strip()
                ]

            editable_table(
                "Itinerary destinations (in visit order)", dest_df, "destinations",
                on_save=_save_destinations,
                column_config={"#": st.column_config.NumberColumn(disabled=True)}
            )

            if "images_text_value" not in st.session_state:
                st.session_state.images_text_value = "\n".join(data.get("image_urls", []))
            if st.session_state.get("_pending_images_update") is not None:
                st.session_state.images_text_value = st.session_state._pending_images_update
                st.session_state._pending_images_update = None

            images_text = st.text_area(
                "Image URLs (one per line - documents need these added manually)",
                key="images_text_value",
                height=80
            )
            data["image_urls"] = [u.strip() for u in images_text.split("\n") if u.strip()] or [FALLBACK_IMAGE]
            if data["image_urls"] == [FALLBACK_IMAGE]:
                st.caption(f"⚠️ No real images provided - using placeholder ({FALLBACK_IMAGE}).")

            def _ct_add_url_images():
                selected = render_url_image_picker(st.session_state.hosted_image_candidates, "found_images")
                if selected:
                    current = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                    new_list = current + selected
                    data["image_urls"] = new_list
                    st.session_state._pending_images_update = "\n".join(new_list)
                    return len(selected)
                return 0

            render_closable_image_section(
                bool(st.session_state.get("hosted_image_candidates")),
                f"🖼️ Images found ({len(st.session_state.get('hosted_image_candidates') or [])}) - from the page/document",
                "found_images_closed", _ct_add_url_images
            )

            def _ct_add_doc_image():
                added = render_doc_image_picker(st.session_state.doc_raw_images, "doc_images")
                if added:
                    current = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                    new_list = current + [added]
                    data["image_urls"] = new_list
                    st.session_state._pending_images_update = "\n".join(new_list)
                    return 1
                return 0

            render_closable_image_section(
                bool(st.session_state.get("doc_raw_images")),
                f"📥 Images extracted from your document(s) ({len(st.session_state.get('doc_raw_images') or [])}) - need hosting",
                "doc_images_closed", _ct_add_doc_image
            )

            default_img_query = data.get("tour_name", "") or (data.get("itinerary_destinations")[0] if data.get("itinerary_destinations") else "")

            def _ct_add_pexels():
                selected = render_stock_photo_picker("Pexels", search_images, default_img_query, "pexels")
                if selected:
                    current = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                    new_list = current + selected
                    data["image_urls"] = new_list
                    st.session_state._pending_images_update = "\n".join(new_list)
                    return len(selected)
                return 0

            render_closable_image_section(True, "🖼️ Or search free stock photos (Pexels)", "pexels_closed", _ct_add_pexels)

            def _ct_add_pixabay():
                selected = render_stock_photo_picker("Pixabay", search_images_pixabay, default_img_query, "pixabay")
                if selected:
                    current = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                    new_list = current + selected
                    data["image_urls"] = new_list
                    st.session_state._pending_images_update = "\n".join(new_list)
                    return len(selected)
                return 0

            render_closable_image_section(True, "🖼️ Or search free stock photos (Pixabay)", "pixabay_closed", _ct_add_pixabay)

    st.subheader("Departure Schedule")
    if data.get("schedule_notes"):
        st.info(f"🔎 AI detected this note about departure timing in the source: \"{data['schedule_notes']}\" "
                f"— use this to help set Operational Days and Stop Sales below correctly. "
                f"This is NOT applied automatically - please verify and set the fields yourself.")

    data["operational_days"] = st.multiselect(
        "Operational Days (which weekdays this tour can depart on)",
        ALL_WEEKDAYS,
        default=data.get("operational_days", ALL_WEEKDAYS)
    )

    render_stop_sales_editor(
        data, "ct_single",
        help_text="For tours that ONLY depart on specific dates (e.g. once a month), set Operational Days "
                  "above to the relevant weekday, then add Stop Sales rows here to block every date EXCEPT "
                  "the ones you want to allow."
    )

    # Clear at-a-glance summary of what kind of schedule this actually is.
    num_days = len(data.get("operational_days", []))
    num_stop_sales = len(data.get("stop_sales", []))
    if num_days == 0:
        schedule_summary = ("⚠️ No Operational Days selected", "#f8d7da", "#721c24")
    elif num_days == 7 and num_stop_sales == 0:
        schedule_summary = ("🟢 DAILY departure - runs every day", "#d4edda", "#155724")
    elif num_stop_sales > 0:
        schedule_summary = (
            f"🟠 SPECIFIC DATE departure - runs on {num_days} weekday(s) MINUS {num_stop_sales} "
            f"blocked date range(s) (irregular/custom schedule)", "#fff3cd", "#856404"
        )
    else:
        schedule_summary = (
            f"🔵 WEEKLY departure - runs every {', '.join(data.get('operational_days', []))}",
            "#d1ecf1", "#0c5460"
        )
    label, bg, fg = schedule_summary
    st.markdown(
        f"<div style='background-color:{bg}; color:{fg}; padding:10px 14px; border-radius:4px; "
        f"font-weight:bold; margin-bottom:10px;'>{label}</div>",
        unsafe_allow_html=True
    )

    st.subheader("Pricing (required by Travel Compositor to publish)")
    if data.get("pricing_notes"):
        st.warning(f"⚠️ **Pricing had to be approximated to fit the 4-slot Distribution schema:**\n\n"
                  f"{data['pricing_notes']}\n\n"
                  f"Review the priceList below carefully - some information may have been "
                  f"simplified or dropped.")

    default_price_list = sorted(
        coerce_price_list_shape(data.get("price_list"), currency)[0] or [{
            "name": "Example row - edit or delete",
            "startDate": "2027-01-01",
            "endDate": "2027-12-31",
            "price": {
                "singlePrice": {"amount": 0, "currency": currency},
                "doublePrice": {"amount": 0, "currency": currency}
            }
        }],
        key=lambda entry: entry.get("startDate", "")   # SORT ON ISO, never the display form: "03/12" would sort before "28/01"
    )
    data["price_list"] = default_price_list

    price_df_rows = []
    for entry in default_price_list:
        price = entry.get("price") if isinstance(entry.get("price"), dict) else {}
        def _amt(key, price=price):
            block = price.get(key)
            if isinstance(block, dict):
                block = block.get("amount")
            try:
                return float(block) if block not in (None, "") else None
            except (TypeError, ValueError):
                return None
        price_df_rows.append({
            "Name": entry.get("name", ""),
            "Start Date": _disp(entry.get("startDate", "")),
            "End Date": _disp(entry.get("endDate", "")),
            "Single": _amt("singlePrice"),
            "Double": _amt("doublePrice"),
            "Triple": _amt("triplePrice"),
            "Quadruple": _amt("quadruplePrice"),
        })

    st.caption(f"Prices below are in **{currency or '(set Currency in Step 3)'}**. "
              f"Leave a price blank if that occupancy isn't offered. Add/remove rows freely.")
    def _row_to_price_entry(row):
        price = {}
        for col, key in [("Single", "singlePrice"), ("Double", "doublePrice"),
                         ("Triple", "triplePrice"), ("Quadruple", "quadruplePrice")]:
            val = row.get(col)
            if val is not None and not pd.isna(val):
                price[key] = {"amount": float(val), "currency": currency}
        entry = {
            "startDate": _iso(_safe_cell_str(row.get("Start Date"))),
            "endDate": _iso(_safe_cell_str(row.get("End Date"))),
            "price": price
        }
        name = _safe_cell_str(row.get("Name")).strip()
        if name:
            entry["name"] = name
        return entry

    def _save_price_list(edited_df):
        data["price_list"] = sorted(
            [
                _row_to_price_entry(row) for _, row in edited_df.iterrows()
                if _iso(_safe_cell_str(row.get("Start Date"))) and _iso(_safe_cell_str(row.get("End Date")))
            ],
            key=lambda entry: entry.get("startDate", "")   # SORT ON ISO, never the display form: "03/12" would sort before "28/01"
        )

    price_df = pd.DataFrame(price_df_rows)
    editable_table("Pricing table", price_df, "pricing", on_save=_save_price_list)
    render_extra_child_notice(data, "ct_single")
    render_child_discount_editor(data, "ct_single", currency)

    price_list_valid = len(data["price_list"]) > 0
    if not price_list_valid:
        st.error("Add at least one price row with both a Start Date and End Date.")

    # Detect overlapping date ranges - Travel Compositor ADDS prices together
    # for any rows with overlapping dates within one option, silently inflating
    # the total. Catch this here regardless of whether it came from AI
    # extraction or a manual edit to the table.
    def _dates_overlap(a_start, a_end, b_start, b_end):
        return a_start <= b_end and b_start <= a_end

    overlaps_found = []
    for i in range(len(data["price_list"])):
        for j in range(i + 1, len(data["price_list"])):
            r1, r2 = data["price_list"][i], data["price_list"][j]
            if _dates_overlap(r1.get("startDate", ""), r1.get("endDate", ""), r2.get("startDate", ""), r2.get("endDate", "")):
                overlaps_found.append((i, j))

    if overlaps_found:
        price_list_valid = False
        st.error(
            f"🚫 **Overlapping date ranges detected in {len(overlaps_found)} row pair(s) of the pricing "
            f"table above.** Travel Compositor ADDS TOGETHER prices from rows with overlapping dates "
            f"within one Modality - this would silently create a wrong, inflated price. Each date range "
            f"in the table should be unique/non-overlapping. If you meant to set different prices for "
            f"different occupancy (single/double/triple/quadruple) in the SAME period, that all belongs "
            f"in ONE row, not separate rows."
        )

    with st.expander("🔧 Advanced: view raw priceList JSON (for reference/copying)"):
        st.json(data["price_list"])

    if action == "create":
        st.subheader("➕ Add more Modalities to create right away (optional)")
        st.caption("Add more room/cabin/product types now - all get created together with a SINGLE "
                  "deactivation at the end, so you don't need to manually reactivate the tour in Travel "
                  "Compositor between each one.")
        if "extra_modalities" not in st.session_state:
            st.session_state.extra_modalities = []

        for i, mod in enumerate(st.session_state.extra_modalities):
            st.markdown(f"**Modality {i + 2}**")
            mcol1, mcol2, mcol3 = st.columns([2, 2, 1])
            with mcol1:
                mod["code"] = st.text_input("Modality Code", value=mod["code"], key=f"extramod_code_{i}")
            with mcol2:
                mod["hint"] = st.text_input("Focus Hint (e.g. 'Deluxe Cabin')", value=mod["hint"], key=f"extramod_hint_{i}")
            with mcol3:
                st.write("")
                if st.button("🗑️ Remove", key=f"extramod_remove_{i}"):
                    st.session_state.extra_modalities.pop(i)
                    # CONFIRMED REAL BUG (internal audit): every widget here is
                    # keyed off this positional slot i (e.g. f"extramod_code_{i}")
                    # - removing one shifts every later extra modality down one
                    # slot, so the item now AT that slot would otherwise inherit
                    # the removed item's stale typed Code/Hint/prices (Streamlit
                    # widgets with a fixed key ignore value= after first render).
                    # Also sweep SHARED_WIDGET_STATE_PREFIXES since each modality's
                    # pricing below is a render_seasonal_price_editor -> editable_table,
                    # whose own internal open/closed-edit-mode state is keyed off
                    # those generic prefixes, not "extramod_" itself.
                    _clear_batch_widget_state(["extramod_"] + SHARED_WIDGET_STATE_PREFIXES)
                    st.rerun()

            if st.button(f"🔎 Extract pricing focused on '{mod['hint'] or mod['code'] or 'this modality'}'", key=f"extramod_extract_{i}", disabled=not mod["code"]):
                with st.spinner("Extracting..."):
                    mod["data"] = extract_option_only_data(st.session_state.raw_preview, human_hint=mod["hint"])
                    st.rerun()

            if mod["data"]:
                render_seasonal_price_editor(f"Pricing - {mod['code'] or f'Modality {i + 2}'}", mod["data"], f"extramod_pricing_{i}", currency)
            else:
                st.info("Click 'Extract pricing' above to get started for this modality.")
            st.divider()

        if st.button("➕ Add another Modality"):
            st.session_state.extra_modalities.append({"code": "", "hint": "", "data": None})
            st.rerun()

    if not is_option_only:
        st.subheader("Optional Add-ons / Upgrades / Excursions (Supplements)")
        st.caption("TRUE optional extras the customer only pays for if they choose them - e.g. a hotel/room "
                  "upgrade, a meal upgrade, or an optional excursion day. Leave empty if this tour has none. "
                  "For a genuinely different core product (different cabin/route with its own full pricing), "
                  "use a separate Modality instead (Publish Action 2).")
        st.caption("Every row needs a clear Name. Special Travel Date is optional - only set it if this "
                  "supplement only applies during a specific date range (e.g. a seasonal excursion).")
        st.caption("⚠️ **Check Mandatory and On Request on every row before publishing.** A ClosedTour "
                  "supplement is often genuinely optional, so these two boxes are the difference between "
                  "an add-on the client chooses and a charge they cannot avoid - the AI's guess is a "
                  "starting point, not a decision. House rule: ClosedTour supplements are never "
                  "refundable, and the app always publishes them that way.")

        default_supplements = data.get("supplements") or []
        supp_df_rows = [
            {
                "Name": s.get("name", ""),
                "Price (per person)": s.get("price", 0),
                "Per Pax": s.get("per_pax", True),
                "Mandatory": s.get("mandatory", False),
                "On Request": s.get("on_request", False),
                "Special Travel Start Date": _disp(s.get("travel_start_date", "")),
                "Special Travel End Date": _disp(s.get("travel_end_date", "")),
            }
            for s in default_supplements
        ]
        supp_df = pd.DataFrame(supp_df_rows) if supp_df_rows else pd.DataFrame(
            columns=["Name", "Price (per person)", "Per Pax", "Mandatory", "On Request",
                     "Special Travel Start Date", "Special Travel End Date"]
        )

        def _save_supplements(edited_df):
            missing_name = False
            new_supplements = []
            for _, row in edited_df.iterrows():
                name = _safe_cell_str(row.get("Name")).strip()
                price_given = row.get("Price (per person)", 0)
                price_given_is_blank = price_given is None or (isinstance(price_given, float) and pd.isna(price_given))
                has_any_data = name or (not price_given_is_blank and price_given not in (0, ""))
                if not name and has_any_data:
                    missing_name = True
                    continue
                if not name:
                    continue
                new_supplements.append({
                    "name": name,
                    "price": _safe_float(price_given),
                    "per_pax": bool(row.get("Per Pax", True)),
                    "mandatory": bool(row.get("Mandatory", False)),
                    "on_request": bool(row.get("On Request", False)),
                    "travel_start_date": _iso(_safe_cell_str(row.get("Special Travel Start Date"))),
                    "travel_end_date": _iso(_safe_cell_str(row.get("Special Travel End Date"))),
                })
            data["supplements"] = new_supplements
            st.session_state._supplements_missing_name = missing_name

        editable_table("Supplements", supp_df, "supplements", on_save=_save_supplements)
        if st.session_state.get("_supplements_missing_name"):
            st.warning("⚠️ A supplement row has a price but no Name - it was skipped. Every supplement needs a clear Name.")

    # ----------------------------------------------------------------------
    # STEP 6: Build payloads (destination resolution happens here)
    # ----------------------------------------------------------------------
    st.subheader("🤖 Tell AI what to fix or clarify (optional)")
    st.caption("Ask a question, or tell it to fix something (e.g. 'the end date of season 1 should be "
              "Sept 30, not Oct 10'). It applies real changes when you ask for them - always shows exactly "
              "what changed so you can double-check.")
    clarify_question = st.text_input("Your message", key="clarify_question_input",
                                     placeholder="e.g. 'Fix season 1's end date to Sept 30' or 'Does this include the Junior Suite?'")
    if render_house_rule_shortcut(clarify_question, "ClosedTour", "single_ct"):
        pass
    elif not clarify_question.strip():
        st.caption(f"Type a message above first — Send stays disabled until there's something to send. "
                  f"Start with \"{HOUSE_RULE_CODEWORD}\" to save a standing rule for every ClosedTour "
                  f"supplier instead of a one-off fix.")
    if not clarify_question.strip().upper().startswith(HOUSE_RULE_CODEWORD.upper()) and st.button(
            "Send", disabled=not clarify_question.strip(), key="clarify_question_input_send"):
        with st.spinner("Thinking..."):
            result = apply_clarification(st.session_state.raw_preview, data, clarify_question)
            remember_clarification(clarify_supplier_id(), "ClosedTour", clarify_question, result)
            st.session_state.clarify_result = result
            if result.get("changes"):
                apply_clarify_changes(data, result, currency)
                # Force any affected table out of edit mode so it re-renders
                # fresh from the new data, rather than potentially showing a
                # stale cached data_editor state from before the AI change.
                field_to_table_key = {
                    "supplements": "_editing_table_supplements",
                    "price_list": "_editing_table_pricing",
                    "itinerary_destinations": "_editing_table_destinations",
                    "stop_sales": "_editing_table_ct_single_stop_sales",
                }
                for field_name in result["changes"]:
                    table_key = field_to_table_key.get(field_name)
                    if table_key:
                        st.session_state[table_key] = False
                # Plain text/number fields (Tour name, Hotels, Included, Excluded, Meeting
                # point, Policy remarks, Nights) - see reset_stale_editable_field_widgets'
                # docstring for why these need the same treatment as table fields.
                reset_stale_editable_field_widgets(result["changes"])
            st.rerun()
    if st.session_state.get("clarify_result"):
        r = st.session_state.clarify_result
        render_clarify_result(r)
    remember_memory_panel(clarify_supplier_id(), "ClosedTour", "legacy")

    if st.button("🔎 Check Locations & Continue",
                disabled=not price_list_valid):
        # CONFIRMED BUG FIX (audit CRITICAL #3, 2026-09-01): used to fall back to a fresh,
        # UN-validated read of st.session_state.fetched_tour_provider_code here - if that global
        # was stale (left over from checking a different tour), it could win over an empty
        # `provider_code` and silently publish under the wrong tour's code. `provider_code`
        # (module-level, above) already carries the fetched_tour_matches_code()-validated value
        # when one applies - nothing else should be trusted here.
        with st.spinner("Resolving destinations against Travel Compositor..."):
            try:
                # HumanPreConfig() itself used to be constructed OUTSIDE this
                # try block - if provider_code didn't match the required
                # "XXX-Number" format, its pydantic validation raised
                # unguarded and crashed the whole app instead of showing a
                # contained error here. Moved inside the try so that failure
                # mode is caught too, not just failures inside
                # build_closed_tour_payloads.
                pre_config = HumanPreConfig(
                    supplier_id=supplier_id,
                    provider_code=provider_code or "XXX-1",
                    min_pax=min_pax, max_pax=max_pax, currency=currency,
                    modality_code=modality_code, on_request=on_request,
                    days_available_before_release=days_available_before_release
                )
                st.session_state.payloads = build_closed_tour_payloads(pre_config, data, client)
                st.session_state.pre_config = pre_config
                st.session_state.payloads_data_fingerprint = _data_fingerprint(data)
            except Exception as e:
                # This used to be able to crash the whole app on a bad
                # destination/network hiccup instead of showing a contained
                # error - build_closed_tour_payloads itself now guards its
                # main construction, but keep this as a last-resort net for
                # anything upstream (e.g. the destination-resolution API
                # calls themselves).
                show_publish_error("resolve destinations / build the payload", str(e), flow="tour_legacy")

    # CONFIRMED REAL BUG (internal audit) - see _data_fingerprint's docstring:
    # the price/supplements/stop-sales/itinerary tables above stay editable
    # after a payload was already built, and an edit there used to publish
    # silently using the STALE pre-edit payload. Discard it here the moment
    # `data` no longer matches what it was built from, forcing an explicit
    # rebuild instead of letting a stale payload reach Step 6/7 below.
    if st.session_state.payloads and _data_fingerprint(data) != st.session_state.get("payloads_data_fingerprint"):
        st.session_state.payloads = None
        st.warning("✏️ You edited the data above after building the payload - click "
                  "**🔎 Check Locations & Continue** again to refresh it before publishing.")

    if st.session_state.payloads:
        payloads = st.session_state.payloads

        st.header("Step 6 — Destination Resolution & Payload Preview")

        render_modalities_review(
            "tour", modality_code, "Base Modality", data,
            st.session_state.get("extra_modalities", []), currency
        )

        st.subheader("Destination Check — verify these against Travel Compositor before publishing")
        for res in payloads["itinerary_resolution"]:
            if res["valid"]:
                st.markdown(
                    f"<div style='background-color:#d4edda; color:#155724; padding:6px 12px; "
                    f"border-radius:4px; margin-bottom:4px;'>✅ <b>{res['input']}</b> → "
                    f"<code>{res['destination']}</code> ({res.get('resolved_name', '')})</div>",
                    unsafe_allow_html=True
                )
            else:
                st.markdown(
                    f"<div style='background-color:#f8d7da; color:#721c24; padding:6px 12px; "
                    f"border-radius:4px; margin-bottom:4px;'>❌ <b>{res['input']}</b> → NOT FOUND "
                    f"in Travel Compositor</div>",
                    unsafe_allow_html=True
                )

        if payloads.get("is_indonesia"):
            st.info(f"🇮🇩 Indonesia detected in this itinerary — Vesak Day and Nyepi are automatically "
                    f"blocked as stop-sale dates, no excursion/tour may start on either day. "
                    f"{payloads.get('indonesia_holiday_note', '')}")

        if payloads.get("is_vietnam") and payloads.get("tet_overlap"):
            _ct_tet = payloads["tet_overlap"]
            st.warning(f"🇻🇳 This ClosedTour's price list overlaps **Tet Holiday {_ct_tet['year']}** "
                      f"({_ct_tet['start']} to {_ct_tet['end']}) — check whether the source document/"
                      f"contract needs a Tet surcharge added as a seasonal price row. "
                      f"{payloads.get('tet_holiday_note', '')}")

        if payloads.get("release_days_overridden"):
            st.info(f"📅 The document mentions its own booking/release deadline, so the release period "
                    f"being used is **{payloads['effective_release_days']} days** instead of your default - "
                    f"if the source mentioned more than one deadline, the longer (safer) one was used.")

        if payloads["unresolved_destinations"]:
            st.error(
                f"🚫 **{len(payloads['unresolved_destinations'])} destination(s) could NOT be matched "
                f"to a real Travel Compositor location:** {', '.join(payloads['unresolved_destinations'])}\n\n"
                f"This means Travel Compositor doesn't recognize this place by that name - publishing "
                f"would fail or create a wrong/broken itinerary stop. **To fix:** go back up to Step 5's "
                f"'Itinerary destinations' box and either correct the spelling/name, or replace it with "
                f"the exact name Travel Compositor uses, then click 'Check Locations & Continue' again."
            )

        tour_update_blocks_publish = False
        if publish_action in ("Update an existing tour's details", "Update an existing option"):
            tour_update_blocks_publish = render_tour_update_comparison(
                publish_action, data, payloads, client, payloads["supplier_id"],
                existing_tour_code, st.session_state.get("working_tour_code"), modality_code
            )

        col3, col4 = st.columns(2)
        with col3:
            if publish_action == "Create a brand-new tour (+ first option)":
                title = "Main Tour Payload (POST - Call 1)"
            elif publish_action == "Update an existing tour's details":
                title = "Main Tour Payload (PUT - update)"
            else:
                title = "Main Tour Payload (not sent this time)"
            if payloads.get("main_tour_error"):
                show_publish_error("build the main tour payload", payloads["main_tour_error"], flow="tour_legacy")
            else:
                with st.expander(f"🔧 {title}", expanded=False):
                    if publish_action not in ("Create a brand-new tour (+ first option)", "Update an existing tour's details"):
                        st.caption(f"Shown for reference only — '{publish_action}' doesn't touch the main tour.")
                    st.json(payloads["main_tour_payload"])
        with col4:
            if publish_action in ("Create a brand-new tour (+ first option)", "Add a new option to an existing tour"):
                title = "Tour Option Payload (POST)"
            elif publish_action in ("Update an existing option", "Update an existing tour's details"):
                # "Update an existing tour's details" now also PUTs the option (see the
                # publish button handler below) - see ACTION_FIELDS's comment on why "whole
                # tour" was changed to actually publish the pricing it extracts.
                title = "Tour Option Payload (PUT - update)"
            else:
                title = "Tour Option Payload (not sent this time)"
            if payloads["tour_option_error"]:
                show_publish_error("build the tour option payload", payloads["tour_option_error"], flow="tour_legacy")
            else:
                with st.expander(f"🔧 {title}", expanded=False):
                    st.json(payloads["tour_option_payload"])

        # ----------------------------------------------------------------------
        # STEP 7: Publish
        # ----------------------------------------------------------------------
        st.header("Step 7 — Publish")

        creating_new_tour = publish_action == "Create a brand-new tour (+ first option)"
        target_tour_code = payloads["main_tour_code"] if creating_new_tour else existing_tour_code
        missing_existing_code = not creating_new_tour and not existing_tour_code
        # CONFIRMED BUG FIX (audit CRITICAL #3, 2026-09-01): must match the tour actually being
        # published, not just be present - see fetched_tour_matches_code()'s docstring.
        missing_provider_code_for_update = (
            publish_action == "Update an existing tour's details"
            and not fetched_tour_matches_code(existing_tour_code)
        )
        if missing_provider_code_for_update:
            st.warning("⚠️ Go back to Step 3 and click 'Check what's already online for this code' first — "
                      "without it, this update could overwrite the tour's real ClosedTour Code with a placeholder.")

        can_publish = (
            not payloads["unresolved_destinations"]
            and not payloads.get("main_tour_error")
            and not payloads["tour_option_error"]
            and not missing_existing_code
            and not missing_provider_code_for_update
            and not tour_update_blocks_publish
        )

        if tour_update_blocks_publish:
            st.info("Publishing is blocked until you either switch to 'Create a brand-new tour' (see the "
                   "message above) or fix the source so the night count matches what's currently live.")
        if missing_existing_code:
            st.info("Existing Tour Code is missing - go back to Step 3.")
        elif not can_publish:
            st.info("Resolve all destinations and fix pricing above before publishing.")

        action_descriptions = {
            "Create a brand-new tour (+ first option)": "Will POST a new tour, then POST a new option.",
            "Add a new option to an existing tour": f"Will POST a new option under existing tour `{target_tour_code}`. Main tour is untouched.",
            "Update an existing tour's details": f"Will PUT (update) tour `{target_tour_code}`'s details, then PUT (update) Modality `{modality_code}`'s pricing/schedule.",
            "Update an existing option": f"Will PUT (update) the option under tour `{target_tour_code}`.",
        }
        st.caption(action_descriptions[publish_action])

        if creating_new_tour:
            dup_warning = check_duplicate_tour_name(client, payloads["supplier_id"], data.get("tour_name"))
            if dup_warning:
                col_dup1, col_dup2 = st.columns([5, 1])
                with col_dup1:
                    st.warning(dup_warning)
                with col_dup2:
                    if st.button("🔄 Re-check", key="recheck_dup_tour_name"):
                        st.session_state._existing_tours_cache.pop(payloads["supplier_id"], None)
                        st.rerun()

        ct_publish_as_active = True
        if creating_new_tour:
            ct_activation_choice = st.radio(
                "After publishing, should this Tour be Active or Inactive (draft)?",
                ["Inactive (draft) - recommended, review inside Travel Compositor before it goes live",
                 "Active - live immediately"],
                index=0, key="ct_activation_choice"
            )
            ct_publish_as_active = ct_activation_choice.startswith("Active")

        if st.button("🚀 Publish to Travel Compositor", disabled=not can_publish, type="primary"):
            with st.spinner("Sending to Travel Compositor..."):

                try:
                    if publish_action == "Create a brand-new tour (+ first option)":
                        creation_payload = dict(payloads["main_tour_payload"])
                        creation_payload["active"] = True
                        # CONFIRMED FIX (real production failure, KNO-1): same issue as the restructured
                        # create flow - build_closed_tour_payloads() only declares the BASE Modality's
                        # code in modalityCodes, but supplements tagged (via applies_to) to any of the
                        # OTHER queued Modalities reference codes not yet in that list, and Travel
                        # Compositor rejects the whole tour creation for it ("Modality code X not found
                        # in contract modalities"). Declare every queued Modality's code upfront.
                        _extra_mod_codes = [m.get("code") for m in st.session_state.get("extra_modalities", []) if m.get("code")]
                        creation_payload["modalityCodes"] = list(dict.fromkeys(
                            creation_payload.get("modalityCodes", []) + _extra_mod_codes
                        ))

                        result = client.create_closed_tour(payloads["supplier_id"], creation_payload)
                        if "error" in result:
                            show_publish_error("create the main tour", result, flow="tour_legacy")
                        else:
                            real_code = result.get('code', payloads['main_tour_code'])
                            # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): see
                            # mark_code_as_taken's docstring.
                            mark_code_as_taken("tour", payloads["supplier_id"], payloads["main_tour_code"], result.get("name"))
                            if real_code and real_code != payloads["main_tour_code"]:
                                mark_code_as_taken("tour", payloads["supplier_id"], real_code, result.get("name"))
                            st.success(f"✅ Main tour created (active) with real Code: **{real_code}** "
                                      f"— save this exact value, you'll need it for any future lookups, "
                                      f"updates, or adding more modalities to this tour.")

                            # Try the human-chosen ClosedTour/Provider Code first (confirmed working
                            # via direct API test), falling back to the internal 'code' if that fails -
                            # we've seen conflicting evidence about which one Travel Compositor's
                            # lookup actually uses, so don't bet everything on just one. (Each attempt
                            # below is itself already retried up to 6x internally by api_client.py's
                            # _request() - this loop is for trying the two different CODES, not retries.)
                            option_result = None
                            used_code = None
                            for candidate_code in [provider_code, real_code]:
                                option_result = client.create_closed_tour_option(
                                    payloads["supplier_id"], candidate_code, payloads["tour_option_payload"]
                                )
                                if "error" not in option_result:
                                    used_code = candidate_code
                                    break

                            if "error" not in option_result:
                                st.caption(f"(Option succeeded using code: `{used_code}`)")

                            if "error" in option_result:
                                show_publish_error(f"create the tour option after trying both `{provider_code}` and `{real_code}`", option_result, flow="tour_legacy")
                                st.info(f"💡 Adjustments to a ClosedTour require it to be ACTIVE - inactive tours "
                                       f"aren't visible via the API. The tour was created with active:true, but if "
                                       f"this keeps failing, check inside Travel Compositor whether `{real_code}` "
                                       f"shows as active, and try 'Add a new option to an existing tour' manually once confirmed.")
                            else:
                                st.success("✅ Tour option created.")

                                extra_modalities = st.session_state.get("extra_modalities", [])
                                if extra_modalities:
                                    st.markdown("**Creating additional modalities...**")
                                    for mod in extra_modalities:
                                        # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): this only ever
                                        # checked that `data` was present at all - it was always present, complete
                                        # with a fabricated "Example row" placeholder price row nobody had actually
                                        # entered (see render_seasonal_price_editor's own fix, ui_components.py, for
                                        # why that placeholder used to get written into the live data too). This
                                        # extra-modality path never separately validated real pricing before
                                        # publish, unlike the base modality's own "Add at least one price row"
                                        # button-disable check - a ClosedTour Modality could publish bookable for
                                        # all of 2027 at 0.00. `target_data["price_list"]` is now only ever real,
                                        # operator-saved rows (never the placeholder), so this check is trustworthy.
                                        if not mod.get("code") or not mod.get("data") or not (mod.get("data") or {}).get("price_list"):
                                            st.warning(f"⚠️ Skipped modality '{mod.get('code') or '(no code)'}' - "
                                                      f"missing code or at least one real (saved) price row.")
                                            continue
                                        with st.spinner(f"Creating modality '{mod['code']}'..."):
                                            try:
                                                mod_pre_config = HumanPreConfig(
                                                    supplier_id=payloads["supplier_id"], provider_code=provider_code or "XXX-1",
                                                    min_pax=min_pax, max_pax=max_pax, currency=currency,
                                                    modality_code=mod["code"], on_request=on_request,
                                                    days_available_before_release=days_available_before_release
                                                )
                                                mod_payloads = build_closed_tour_payloads(mod_pre_config, mod["data"], client)
                                                if mod_payloads["tour_option_error"]:
                                                    show_publish_error(f"prepare modality '{mod['code']}'", mod_payloads["tour_option_error"], flow="tour_legacy")
                                                    continue
                                                mod_result, mod_used_code = try_code_variants(
                                                    lambda c: client.create_closed_tour_option(payloads["supplier_id"], c, mod_payloads["tour_option_payload"]),
                                                    [provider_code, real_code]
                                                )
                                                if "error" in mod_result:
                                                    show_publish_error(f"create modality '{mod['code']}'", mod_result, flow="tour_legacy")
                                                else:
                                                    st.success(f"✅ Modality '{mod['code']}' created.")
                                            except Exception as e:
                                                show_publish_error(f"create modality '{mod['code']}' (unexpected error - skipped, rest continues)", str(e), flow="tour_legacy")
                                                continue

                                if ct_publish_as_active:
                                    st.success(f"✅ Tour `{real_code}` left ACTIVE, as chosen above - it's live now.")
                                    st.session_state.just_published_tour_code = real_code
                                    st.session_state.just_published_supplier_id = payloads["supplier_id"]
                                    st.session_state.just_published_is_inactive = False
                                    st.session_state.extra_modalities = []
                                else:
                                    deactivate_payload = dict(creation_payload)
                                    deactivate_payload["active"] = False
                                    deactivate_payload["code"] = real_code
                                    deactivate_result = client.update_closed_tour(payloads["supplier_id"], deactivate_payload)
                                    if "error" in deactivate_result:
                                        st.warning(f"⚠️ Tour and option were created successfully, but switching "
                                                  f"the tour back to inactive/draft failed: {deactivate_result}. "
                                                  f"You may need to deactivate it manually inside Travel Compositor.")
                                    else:
                                        st.success(f"✅ Tour `{real_code}` switched back to inactive/draft. "
                                                  f"Ready for human review — activate it inside Travel Compositor when ready to go live.")
                                        st.session_state.just_published_tour_code = real_code
                                        st.session_state.just_published_supplier_id = payloads["supplier_id"]
                                        st.session_state.just_published_is_inactive = True
                                        st.session_state.extra_modalities = []

                    elif publish_action == "Add a new option to an existing tour":
                        option_result, used_code = try_code_variants(
                            lambda c: client.create_closed_tour_option(payloads["supplier_id"], c, payloads["tour_option_payload"]),
                            target_tour_code
                        )
                        if "error" in option_result:
                            show_publish_error(f"add the option (tried both `{target_tour_code}` and its CLOSEDTOUR- variant)", option_result, flow="tour_legacy")
                            st.info(f"💡 Adjustments to a ClosedTour require it to be ACTIVE - inactive tours "
                                   f"aren't visible via the API. Activate `{target_tour_code}` inside Travel "
                                   f"Compositor first, then retry (you can switch it back to inactive/draft afterward).")
                        else:
                            st.success(f"✅ New option added to existing tour using code `{used_code}`. Verify inside Travel Compositor.")
                            st.session_state.just_published_tour_code = target_tour_code
                            st.session_state.just_published_supplier_id = payloads["supplier_id"]
                            st.session_state.just_published_is_inactive = False

                            # CONFIRMED FIX: supplements live on the MAIN tour
                            # (ContractClosedTourVO), NOT the option just created
                            # above - if this new Modality has its own supplements,
                            # they can only be attached via a follow-up PUT to the
                            # tour, now that the option genuinely exists (Travel
                            # Compositor validates a supplement's modalityCodes
                            # against Modalities that already exist as real options -
                            # same rule that drove the "not found in contract
                            # modalities" fix for brand-new tours). The PUT payload
                            # is built from the tour's OWN current live GET data
                            # (not a fresh extraction) so every other field stays
                            # exactly as it is - only supplements/modalityCodes change.
                            new_supplements = data.get("supplements") or []
                            if new_supplements:
                                with st.spinner(f"Adding '{modality_code}''s supplements to the tour..."):
                                    old_tour = st.session_state.get("fetched_tour")
                                    if not isinstance(old_tour, dict) or "error" in old_tour:
                                        st.warning(
                                            f"⚠️ '{modality_code}' was created, but its {len(new_supplements)} "
                                            f"supplement(s) were NOT added - couldn't find the tour's current "
                                            f"live data. Go back to Step 3, click 'Check what's already online "
                                            f"for this code', then use 'Update an existing tour's details' to "
                                            f"add the supplements separately."
                                        )
                                    else:
                                        # CONFIRMED PRODUCT-OWNER CORRECTION: a ClosedTour
                                        # supplement applies to EVERY Modality, so it is added to
                                        # the tour unscoped. Scoping it to the Modality being
                                        # added would have made it unbuyable for everyone already
                                        # booked on the tour's other Modalities.
                                        new_vos = [v.dict() for v in build_supplement_vos(new_supplements)]
                                        update_payload = dict(old_tour)
                                        update_payload["supplements"] = (old_tour.get("supplements") or []) + new_vos
                                        update_payload["modalityCodes"] = list(dict.fromkeys(
                                            (old_tour.get("modalityCodes") or []) + [modality_code]
                                        ))
                                        supp_result, supp_used_code = try_code_variants(
                                            lambda c: client.update_closed_tour(payloads["supplier_id"], {**update_payload, "code": c}),
                                            target_tour_code
                                        )
                                        if "error" in supp_result:
                                            show_publish_error(f"add '{modality_code}''s supplements to the tour", supp_result, flow="tour_legacy")
                                            st.info(f"'{modality_code}' itself was created successfully above - "
                                                   f"only its supplements failed to attach. Retry via 'Update "
                                                   f"an existing tour's details' once the issue above is fixed.")
                                        else:
                                            st.success(f"✅ Added {len(new_supplements)} supplement(s) for "
                                                      f"'{modality_code}' to the tour (code `{supp_used_code}`).")

                    elif publish_action == "Update an existing tour's details":
                        # This branch only runs for action=="update_tour" with scope
                        # "whole_tour" (the "price_only" scope relabels publish_action to
                        # "Update an existing option" above, routing through that branch
                        # instead - see ct_price_only_via_update_tour). CONFIRMED FIX
                        # (product owner, 2026-08-28): "whole tour" already extracts and
                        # builds a full tour_option_payload via build_closed_tour_payloads,
                        # but historically only ever published the main tour details,
                        # silently discarding the pricing/schedule it just asked the human
                        # to review. Now it publishes both.
                        update_payload = dict(payloads["main_tour_payload"])
                        update_payload["code"] = target_tour_code
                        # CONFIRMED BUG FIX (audit CRITICAL #2, same root cause as the Ticket twin
                        # above, 2026-09-01): build_closed_tour_payloads always sets active=False
                        # ("LOCKED default" for a brand-new tour, which must land as a draft), but
                        # this same payload is reused verbatim for UPDATE - sent as-is, every
                        # "update this tour's details" silently took a live, active tour off sale.
                        # The live record's own active state (fetched_tour, from "Check what's
                        # already online") must win here instead.
                        _ct_live_for_active = st.session_state.get("fetched_tour") or {}
                        if isinstance(_ct_live_for_active, dict) and "error" not in _ct_live_for_active \
                                and _ct_live_for_active.get("active") is not None:
                            update_payload["active"] = _ct_live_for_active["active"]
                        result, used_code = try_code_variants(
                            lambda c: client.update_closed_tour(payloads["supplier_id"], {**update_payload, "code": c}),
                            target_tour_code
                        )
                        if "error" in result:
                            show_publish_error(f"update the tour (tried both `{target_tour_code}` and its CLOSEDTOUR- variant)", result, flow="tour_legacy")
                            st.info(f"💡 Adjustments to a ClosedTour require it to be ACTIVE - inactive tours "
                                   f"aren't visible via the API. Activate `{target_tour_code}` inside Travel Compositor first, then retry.")
                        else:
                            st.success(f"✅ Tour updated using code `{used_code}`.")

                            update_option_payload = dict(payloads["tour_option_payload"])
                            update_option_payload["code"] = modality_code
                            option_result, option_used_code = try_code_variants(
                                lambda c: client.update_closed_tour_option(payloads["supplier_id"], c, update_option_payload),
                                target_tour_code
                            )
                            if "error" in option_result:
                                show_publish_error(f"update the tour's pricing/modality (tried both `{target_tour_code}` and its CLOSEDTOUR- variant)", option_result, flow="tour_legacy")
                                st.info(f"💡 The tour's own details ARE saved. Only the Modality `{modality_code}`'s "
                                       f"pricing/schedule failed - fix and retry with **'Update existing ClosedTour "
                                       f"Modality'** against `{target_tour_code}` / `{modality_code}`, no need to "
                                       f"redo the tour details.")
                                # CONFIRMED FIX (2026-08-30 audit): just_published_tour_code must NOT be set
                                # here - setting it unconditionally (as this used to) made the green "✅
                                # ClosedTour published - what would you like to do next?" banner render right
                                # below this failure message, which could lead an operator to trust the banner,
                                # click "Start a new ClosedTour", and lose the only on-screen pointer to the
                                # Modality that still needs a retry - matching every other failure branch in
                                # this handler (e.g. "Add a new option to an existing tour"), none of which set
                                # just_published_tour_code on a sub-step failure either.
                            else:
                                st.success(f"✅ Modality `{modality_code}` pricing/schedule updated using code `{option_used_code}`.")
                                st.session_state.just_published_tour_code = target_tour_code
                                st.session_state.just_published_supplier_id = payloads["supplier_id"]
                                st.session_state.just_published_is_inactive = False

                    elif publish_action == "Update an existing option":
                        update_option_payload = dict(payloads["tour_option_payload"])
                        update_option_payload["code"] = modality_code
                        option_result, used_code = try_code_variants(
                            lambda c: client.update_closed_tour_option(payloads["supplier_id"], c, update_option_payload),
                            target_tour_code
                        )
                        if "error" in option_result:
                            show_publish_error(f"update the option (tried both `{target_tour_code}` and its CLOSEDTOUR- variant)", option_result, flow="tour_legacy")
                            st.info(f"💡 Adjustments to a ClosedTour require it to be ACTIVE - inactive tours "
                                   f"aren't visible via the API. Activate `{target_tour_code}` inside Travel Compositor first, then retry.")
                        else:
                            st.success(f"✅ Option `{modality_code}` under tour (code `{used_code}`) updated.")
                            st.session_state.just_published_tour_code = target_tour_code
                            st.session_state.just_published_supplier_id = payloads["supplier_id"]
                            st.session_state.just_published_is_inactive = False
                except Exception as e:
                    # This used to be able to crash the whole app on any
                    # unhandled exception (network error, unexpected API
                    # response shape, etc.) partway through publishing -
                    # now it shows a contained error instead.
                    show_publish_error("publish the tour (unexpected error)", str(e), flow="tour_legacy")

# ----------------------------------------------------------------------
# Post-publish follow-up: what next?
# ----------------------------------------------------------------------
if st.session_state.get("just_published_tour_code"):
    st.divider()
    st.subheader("✅ ClosedTour published — what would you like to do next?")
    st.write(f"Just published: **{st.session_state.just_published_tour_code}** "
            f"(Supplier {st.session_state.just_published_supplier_id})")

    if st.session_state.get("just_published_is_inactive"):
        st.warning("⚠️ **This ClosedTour is now INACTIVE.** It was created, given its first Modality, then "
                  "switched back to draft/inactive for your review — this is expected. To add more "
                  "Modalities or make further changes, first **activate it manually inside Travel "
                  "Compositor**, then come back and use 'Add new Modality to existing ClosedTour'.")
        if st.button("🆕 Start a new ClosedTour", type="primary"):
            keep_client = st.session_state.client
            keep_suppliers = st.session_state.suppliers_cache
            keep_product_type = st.session_state.product_type
            keep_tool = st.session_state["active_tool"] if "active_tool" in st.session_state else None
            st.session_state.clear()
            st.session_state.client = keep_client
            st.session_state.suppliers_cache = keep_suppliers
            st.session_state.product_type = keep_product_type
            st.session_state.active_tool = keep_tool
            st.rerun()
    else:
        fcol1, fcol2 = st.columns(2)
        with fcol1:
            if st.button("🆕 Start a new ClosedTour", type="primary"):
                keep_client = st.session_state.client
                keep_suppliers = st.session_state.suppliers_cache
                keep_product_type = st.session_state.product_type
                keep_tool = st.session_state["active_tool"] if "active_tool" in st.session_state else None
                st.session_state.clear()
                st.session_state.client = keep_client
                st.session_state.suppliers_cache = keep_suppliers
                st.session_state.product_type = keep_product_type
                st.session_state.active_tool = keep_tool
                st.rerun()
        with fcol2:
            if st.button("➕ Add another Modality to this same ClosedTour"):
                prefill_tour_code = st.session_state.just_published_tour_code
                prefill_supplier_id = st.session_state.just_published_supplier_id
                keep_client = st.session_state.client
                keep_suppliers = st.session_state.suppliers_cache
                keep_product_type = st.session_state.product_type
                keep_tool = st.session_state["active_tool"] if "active_tool" in st.session_state else None
                st.session_state.clear()
                st.session_state.client = keep_client
                st.session_state.suppliers_cache = keep_suppliers
                st.session_state.product_type = keep_product_type
                st.session_state.active_tool = keep_tool
                st.session_state.cfg_action = "add_option"
                st.session_state.cfg_supplier_id = prefill_supplier_id
                st.session_state.cfg_existing_tour_code = prefill_tour_code
                st.session_state.prefill_existing_tour_code = prefill_tour_code
                st.session_state.step1_confirmed = True
                st.rerun()


# ============================================================================
# THE PAGE FOOTER - reference material, deliberately last.
# Every review screen that has memory worth showing queues it via
# remember_memory_panel(); it is rendered here, once, at the bottom, so it never
# sits between the AI's answer and the buttons a person is trying to reach.
# ============================================================================
render_memory_panel_footer()
