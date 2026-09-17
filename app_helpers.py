"""
Shared helper functions and constants, split out of app.py (Phase 1 restructure module 13,
zero behaviour change). Every name below used to live at app.py's own top level; app.py now
does `from app_helpers import (...)` so every existing `from app import X` line in the already
-split flows/*.py files keeps resolving unchanged, without those files needing any edits.

Verified via free-variable analysis (2026-09-16): none of these 96 functions or 22 constants
reference anything defined elsewhere in app.py (no page-script globals like `client` or
`BUILD_VERSION`, and no dependency on `_module_build_mismatches`, which stays in app.py). Every
free variable is either another name in this same set, a builtin, or an import - so no
`from app import (...)` circular-import line is needed here at all, unlike every flows/*.py
module split out before it.

Two functions (render_update_refresh_flow, render_cancellation_bulk_flow) are the exception:
they call into flows.price_refresh/flows.cancellation, which each do `from app import (...)`
for names now defined in this file. A module-top-level import of those two flows modules here
would deadlock the 3-way circular import (app -> app_helpers -> flows.X -> app, needing names
app_helpers hasn't finished defining while app.py's own `from app_helpers import (...)` is
still mid-execution) - so those two imports are deferred to call time inside the functions that
use them instead, same fix pattern as the dispatcher-stays-in-app.py lesson from module 10.
"""
import os
import re
import json
import difflib
import tempfile
from datetime import datetime

import pandas as pd
import requests
import streamlit as st

import widget_state
import masterdata_store
import masterdata_matcher
import hotel_automap
import bulk_notes
import platform_store
import supplier_images
import extraction_memory
import price_refresh
import ai_extractor as ai_extractor_module

from builder import (transport_company_name as builder_transport_company_name,
                     transport_description as builder_transport_description,
                     start_date_or_today as builder_start_date_or_today)
from builder import LANGUAGE_CODE_NAMES
from builder import coerce_price_list_shape, coerce_ticket_occupancy_prices_shape
from date_format import to_iso_date as _iso, to_display_date as _disp
from document_reader import extract_raw_text
from ui_components import is_active_supplier, _safe_float, _safe_int
from web_extractor import get_page_text, short_page_text_warning
from r2_client import stale_image_warning
from geocoding_client import build_place_query
from ai_extractor import friendly_error_message, min_pax_guaranteed_departure_note
from price_audit import run_hotel_price_audit, compare_price_audit_to_extraction, summarize_findings
from translation_tool import DEFAULT_TARGET_LANGUAGES
from image_dimensions import FALLBACK_IMAGE


def _dmy_date_field(label, key, value_iso="", placeholder=None):
    """A date field with BOTH ways in at once: a typeable text input (DD/MM/YYYY or
    DD.MM.YYYY, house format) and a small calendar picker next to it. CONFIRMED REAL NEED
    (product owner, 2026-09-10): "when adding a date to the App, it would be nice if we can
    include a calendar for the human, so he could either click on the calendar or he can type
    it in." Picking a date in the calendar fills the text field (so it stays the single source
    of truth and every existing _iso()/_disp() call site around a date keeps working
    unchanged); typing directly still works exactly as before. Returns the ISO date string (or
    "" if the field is empty/unparseable) - a drop-in replacement for the previous bare
    `_iso(st.text_input(...))` pattern.
    """
    # CONFIRMED REAL BUG (2026-09-16, reported: "creating new tickets" -> StreamlitWidgetAlready
    # InstantiatedError on this exact field): the popover below used to write directly into
    # st.session_state[key] AFTER the text_input(key=key) widget above had already been
    # instantiated in this same run. Streamlit refuses that outright - a widget's own key can
    # only be set BEFORE that widget is created (its next rerun), never after, and this
    # function was doing it in the same script pass every time. Every existing call site
    # (14 of them, every product flow) hit this the moment a human used the calendar picker at
    # all, not something new to multi-ticket - it just happened to be reported there first.
    # FIX: the picked value is queued into a separate "<key>__pending" slot instead, and
    # consumed here, BEFORE the text_input widget for `key` exists yet, on the rerun that
    # follows - the standard safe pattern for updating an already-instantiated widget's value.
    pending_key = f"{key}__pending"
    if pending_key in st.session_state:
        st.session_state[key] = st.session_state.pop(pending_key)

    tcol, ccol = st.columns([5, 1])
    with tcol:
        typed = st.text_input(label, value=_disp(value_iso), key=key, placeholder=placeholder)
    iso_value = _iso(typed)
    with ccol:
        st.write("")  # spacer so the button lines up with the input box, not the label above it
        with st.popover("📅", use_container_width=True):
            picked_default = None
            if iso_value:
                try:
                    picked_default = datetime.strptime(iso_value, "%Y-%m-%d").date()
                except ValueError:
                    picked_default = None
            picked = st.date_input("Pick a date", value=picked_default, key=f"{key}_cal",
                                   format="DD/MM/YYYY")
            if picked and picked.strftime("%d/%m/%Y") != typed:
                st.session_state[pending_key] = picked.strftime("%d/%m/%Y")
                st.rerun()
    return iso_value


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


def _warn_stale_images(urls):
    """Surfaces r2_client.stale_image_warning right before a publish button, on every product's
    review screen. CONFIRMED PARTIAL FIX, now completed (full-app audit, Batch 5 / 2026-09-03):
    stale_image_urls/stale_image_warning were built and unit-tested to catch R2's ~2-day image-
    expiry lifecycle rule biting a document image that was uploaded during a multi-day review and
    hadn't been published yet, but the capability was never actually called from any of app.py's
    5 publish screens - built, tested, and silently unused. Thin wrapper, same shape as
    _warn_page_image_upload_errors right above: does nothing if r2_client sees nothing stale,
    otherwise a single st.warning with its own complete, self-explanatory message."""
    message = stale_image_warning(urls)
    if not message:
        return
    st.warning(message)


def _apply_min_pax_guaranteed_departure_note(remarks_data, remarks_fields, min_pax, label=None):
    """CONFIRMED PRODUCT-OWNER RULE (2026-09-03): "add to remarks, if there is a minimum pax
    number needed for guaranteed departure. If Ticket or Closedtour has minimum of 3 pax or
    higher, we must set the ticket or closedtour on request." This handles the "add to remarks"
    half - folding min_pax_guaranteed_departure_note()'s plain-English note into every remarks
    field named in remarks_fields that's actually present on remarks_data (Ticket: Condition +
    Voucher Remarks, both per the product owner's answer; ClosedTour: Policy remarks, its only
    one). Safe to call on every rerun/re-extraction: skips the append when that exact note is
    already present, so it never duplicates. `label` (a Modality code) is included when the note
    is being folded into a field SHARED across several Modalities (ClosedTour's tour-level Policy
    remarks) so a human reading it can tell which Modality the minimum applies to; omit it for
    Ticket, where each Ticket item already has its own dedicated data/remarks scoped to just the
    one Modality being created. See min_pax_forces_on_request() (ai_extractor.py) for the other
    half - forcing On Request at publish time - applied separately, at each publish call site."""
    note = min_pax_guaranteed_departure_note(min_pax)
    if not note:
        return
    if label:
        note = f"{label}: {note}"
    for field in remarks_fields:
        if field not in remarks_data:
            continue
        existing = (remarks_data.get(field) or "").strip()
        if note in existing:
            continue
        remarks_data[field] = f"{existing}\n\n{note}".strip() if existing else note


SHARED_WIDGET_STATE_PREFIXES = ["_editing_", "_widgetval_", "pencil_", "save_", "editor_",
                                # "sn_" = service_notes widgets, also keyed per queue item -
                                # without this a one-off note typed on one service reappears
                                # on the next one and gets published to the wrong voucher.
                                "sn_"]


def _geo_search_default(client, place_name):
    """Builds the "City, Country" default search query for the Ticket geolocation boxes.

    CONFIRMED PRODUCT-OWNER RULE (2026-09-03): "when searching for Coordinates, use the name of
    the City and then the Country. Example: Phuket, Thailand." Travel Compositor's own
    destination list (the same one get_destination_country() already serves for the Indonesia/
    Vietnam holiday rules in builder.py) is the authoritative source when it has a record for
    this place; a miss (or any lookup error) just falls back to the bare place name, same as
    before this fix - never blocks the search."""
    if not place_name:
        return place_name
    try:
        country = client.get_destination_country(place_name)
    except Exception:
        country = None
    return build_place_query(place_name, country)


CURRENCY_OPTIONS = [
    "EUR", "USD", "GBP", "AUD", "CAD", "CHF", "CNY", "IDR", "INR", "JPY",
    "MXN", "NZD", "SEK", "SGD", "THB", "TRY", "VND", "ZAR",
]


TICKET_ACTION_LABELS = {
    "create": "1: Create new Ticket + 1 Modality",
    "add_option": "2: Add new Modality to existing Ticket",
    "update_ticket": "3: Update an existing Ticket",
    "update_option": "4: Update existing Ticket Modality",
    # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-08): "yes please fix it first, that's the reason
    # i reached out to you first." The single-ticket "3: Update an existing Ticket" flow only
    # ever updates ONE ticket at a time - there was no equivalent of the batch-CREATE flow
    # (render_multi_ticket_flow) for updating MANY EXISTING tickets from one supplier document
    # (e.g. a whole new price-list covering 20+ existing excursions). Routes to
    # render_multi_ticket_update_flow, the same phase/state-machine pattern as
    # render_multi_ticket_flow, but matching each detected excursion to an EXISTING ticket code
    # instead of creating a new one.
    "update_tickets_batch": "5: Update multiple existing Tickets from one document",
}


TICKET_CREATE_ACTION_KEYS = ("create", "add_option")


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
    # NOTE: deliberately does NOT include "existing_ticket_code" or "modality_code" - the new
    # batch-update flow (render_multi_ticket_update_flow) collects a target existing Ticket Code
    # (and which of ITS live Modality Codes to update) per detected excursion, right in its own
    # matching step - see that function's PHASE "match". Also does NOT include "currency" or
    # min/max passengers - each matched ticket's OWN live currency/passenger limits win (same
    # "an UPDATE never asks for things the live record already has" rule as every other update
    # action here), fetched fresh per item rather than asked once for the whole batch.
    # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-08 follow-up): "we do not need to ask the human
    # again for release date, this is already set and wont change" - also does NOT include
    # "release_days" for this same reason: render_multi_ticket_update_flow now reads each
    # matched ticket's OWN live daysAvailableBeforeRelease instead of asking once for the whole
    # batch (same "an UPDATE never asks for things the live record already has" rule as above).
    "update_tickets_batch": [],
}


ACTION_LABELS = {
    "create": "1: Create new ClosedTour + 1 Modality",
    "add_option": "2: Add new Modality to existing ClosedTour",
    "update_tour": "3: Update an existing ClosedTour",
    "update_option": "4: Update existing ClosedTour Modality",
}


CLOSEDTOUR_CREATE_ACTION_KEYS = ("create", "add_option")


ACTION_FIELDS = {
    # NOTE: "create" deliberately does NOT include "modality_code" - Step 4's
    # queue-based flow (render_multi_tour_flow / the "Set up this tour" screen)
    # collects the Modality Code per tour there instead, right next to the Tour
    # Code, so it's asked exactly once instead of twice (previously a human had
    # to type it here in Step 3 AND again in Step 4 - pure double work, since
    # Step 3's value was never even used).
    "create": ["provider_code", "min_pax", "max_pax", "currency", "on_request", "release_days"],
    # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-17): "If we select the supplier and if we select
    # the ClosedTour Code, we just want to add a new Modality, regardless what is already
    # online." Used to rely on "Check what's already online for this code" (a live GET fetch) to
    # supply currency, the same "an UPDATE never asks for things the live record already has"
    # rule update_option/update_tour use - but the product owner reversed that specifically for
    # add_option: adding a Modality shouldn't depend on a successful fetch of the CURRENT tour
    # state at all. "currency" is asked directly here instead (same as "create"), and Step 3/4's
    # "must have fetched the live tour first" gate no longer applies to this action - see app.py's
    # own comments at the Step 3 Continue-button gate and the Step 4+ currency/min/max override
    # block for the matching removal.
    "add_option": ["existing_tour_code", "modality_code", "currency", "on_request"],
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
    # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-03): "if I start a new batch for creating a new
    # service, please allow to change the currency as this can be always vary" - same fix as
    # Ticket's "Start a new batch". cfg_currency was left set from whichever ClosedTour was just
    # published, so Step 3's lock (see "Locked - a currency, once set, cannot be changed" there)
    # kept applying to the NEXT ClosedTour too, even from a different supplier/rate sheet.
    # Reopening Step 3 and clearing the stored currency here gives a genuinely new ClosedTour a
    # fresh, editable currency choice, while an in-progress tour's own Modalities are still
    # protected by the same lock as before.
    st.session_state.step2_confirmed = False
    st.session_state.pop("cfg_currency", None)


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


_PUBLISH_ERROR_PATTERNS = [
    ("not found in contract modalities", "modality",
     "the Modality Code - it needs to exactly match (spelling and case) an option that actually exists for this tour/ticket"),
    ("closed tour not found", "code", "the Tour Code - it doesn't match an existing ClosedTour on Travel Compositor"),
    ("ticket not found", "code", "the Ticket Code - it doesn't match an existing Ticket on Travel Compositor"),
    # CONFIRMED REAL BUG (reported 2026-09-05, first hotel with 3 brand-new rooms in one go):
    # a real "Room null already exists for contract HRG-H1" error used to fall through to the
    # generic "already exists" pattern below, which told the human to go change the "Tour/Ticket
    # Code" - nonsensical for a Hotel publish, which has no such field at all. Matched here,
    # earlier in the list so it wins, with guidance that's actually about rooms. This exact error
    # should now be rare in practice (see the "add extra new rooms one at a time" fix in the
    # hotel publish button above), but a genuine room-name collision could still surface it.
    ("already exists for contract", "rooms",
     "the hotel's Rooms section - Travel Compositor reported a room-level conflict for this "
     "contract, not a Tour/Ticket code issue; check whether a room with this name already "
     "exists for this hotel, or try publishing again (rooms are now added one at a time)"),
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


def _extract_error_message_detail(raw_error):
    """Shared with show_publish_error below - pulls the human-readable detail text out of
    Travel Compositor's own nested error shape ({'error': 400, 'message': '{"error": [...]}'})
    or a raw Pydantic validation error string. Factored out so retry logic (see
    _extract_rejected_image_url) can inspect the same text show_publish_error would display,
    without duplicating this parsing."""
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
    return extracted_detail


_REJECTED_IMAGE_URL_RE = re.compile(r"Image:\s*'([^']+)'")


def _extract_rejected_image_url(raw_error):
    """Returns the image URL Travel Compositor's own error message named as rejected (either
    the 500x400 minimum-size message or the "Not valid image" message - both end in the same
    `Image: '<url>'` shape), or None if this error wasn't about a specific image."""
    detail = _extract_error_message_detail(raw_error)
    if not detail:
        return None
    m = _REJECTED_IMAGE_URL_RE.search(detail)
    return m.group(1) if m else None


def show_publish_error(context_label, raw_error, flow=None):
    """
    Shows a simple, human-readable error summary by default - extracted from
    Travel Compositor's own nested error message when possible - with the
    full raw technical detail available in an expander for anyone who needs
    to see or report the exact API response. Also shows an actionable
    "go back and check ___" hint (see _publish_error_guidance) so a human
    isn't just left staring at a rejected error with no idea what to change.
    """
    extracted_detail = _extract_error_message_detail(raw_error)

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


TICKET_LANGUAGE_OPTIONS = ["EN"] + DEFAULT_TARGET_LANGUAGES


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
    kept distinct from a language that costs EXTRA, which is entered as a priced supplement
    instead - see "Supplements by dates" below).

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
             "option here - enter it as a row under \"Supplements by dates\" below instead.",
    )
    data["languages"] = chosen or ["EN"]


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
    """Two bulk buttons - "Select all" / "Select none" - above a long candidate list.

    WHY: a real supplier rate sheet prices TWO services on every row - a Shuttle and a
    Private version of the same route - so a forty-route document detects as ~80 separate
    products, all pre-ticked. A human who only wants a handful was left un-ticking dozens of
    boxes by hand (or, the other way round, ticking dozens by hand after clearing them), which
    is both tedious and easy to get wrong by one.

    SIMPLIFIED (product owner, 2026-09-16): "we must keep it simple. No text to be added, just
    select all or unselect all." The original version of this also had a filter text box and a
    third button that ticked every row matching a typed term. Retired - two buttons only.

    CONFIRMED REAL BUG (product owner, 2026-09-16, live on a 116-row document): "Select none"
    correctly updated the ticked-count caption to "0 of 116 ticked" but every checkbox on screen
    still showed checked. Popping the "{key_prefix}_sel_" keys from session_state (via
    _clear_batch_widget_state, the pattern this whole codebase otherwise relies on for
    text/number widgets) relies on Streamlit treating an absent key as "uninitialized" and
    falling back to the `value=` argument on the next render - which held up in this repo's own
    test harness, but not on whatever Streamlit build is actually deployed with 116 real
    checkboxes in a live browser. Fixed by not relying on that fallback at all: `_apply` now
    WRITES the exact target boolean straight into st.session_state[f"{key_prefix}_sel_{i}"] for
    every row before the rerun, which every Streamlit version honors unconditionally (a keyed
    widget always displays st.session_state[key] once that key is set, full stop - no
    "was this ever rendered before" ambiguity for popped-vs-never-set keys to get wrong)."""
    if len(candidates) < 2:
        return
    total = len(candidates)
    chosen = sum(1 for c in candidates if c.get("selected"))

    def _apply(new_value):
        for i, cand in enumerate(candidates):
            cand["selected"] = new_value
            st.session_state[f"{key_prefix}_sel_{i}"] = new_value
        st.rerun()

    bcol1, bcol2 = st.columns(2)
    with bcol1:
        if st.button("Select all", key=f"{key_prefix}_all", use_container_width=True):
            _apply(True)
    with bcol2:
        if st.button("Select none", key=f"{key_prefix}_none", use_container_width=True):
            _apply(False)

    # Travel Compositor stores a route in ONE direction, and a "per way" rate sheet lists it
    # once - so selling the return leg means a second product per route. Sixteen routes is
    # sixteen more rows to type by hand, which is exactly the kind of work this screen exists
    # to remove. Added as candidates rather than silently doubling the queue, so the return
    # legs sit in the list and can be unticked or renamed like any other.
    #
    # CONFIRMED REAL BUG (product owner, 2026-09-16): "the button 'add the return direction for
    # tickets' is absolutely useless. It would be more useful for transfers and transports, but
    # not for tickets." A Ticket/Modality candidate has no departure_hint/arrival_hint at all -
    # the button rendered anyway (it only checked whether anything was ticked, not whether any
    # ticked row actually had a route to mirror) and, once clicked, did nothing every time,
    # since the loop below already skipped every row lacking both hints. Fixed by gating the
    # button itself on the same condition, so it simply doesn't appear on a screen where it
    # could never add anything - Ticket/Modality batch screens never show it, Transfer/Transport
    # screens are unaffected.
    ticked = [c for c in candidates if c.get("selected")]
    routes_ticked = [c for c in ticked if str(c.get("departure_hint") or "").strip()
                     and str(c.get("arrival_hint") or "").strip()]
    if routes_ticked and st.button(f"↔️ Add the return direction for the {len(routes_ticked)} ticked route(s)",
                            key=f"{key_prefix}_returns", use_container_width=True,
                            help="Creates a mirrored candidate for each ticked route, with the "
                                 "departure and arrival swapped. Prices are read from the "
                                 "document again for each one, so a return leg priced "
                                 "differently is still read correctly."):
        existing = {(str(c.get("departure_hint") or "").strip().lower(),
                     str(c.get("arrival_hint") or "").strip().lower(),
                     str(c.get("service_name") or "").strip().lower()) for c in candidates}
        added = 0
        for cand in routes_ticked:
            dep = str(cand.get("departure_hint") or "").strip()
            arr = str(cand.get("arrival_hint") or "").strip()
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


def _mtu_fetch_live_ticket(client, supplier_id, code):
    """Fetches+caches a Ticket's full live GET response for render_multi_ticket_update_flow -
    shared by the "match" phase (which only needs modalityCodes, to offer as a default) and the
    "reviewing" phase (which needs the whole record as the merge baseline, and for the
    content-drift check). Cached per (supplier_id, code) in session_state so re-rendering the
    same item doesn't re-fetch every rerun - same pattern as check_code_availability's own
    cache above. Returns whatever client.get_ticket() returns (an error dict on failure)."""
    clean = (code or "").strip()
    if not clean:
        return None
    cache = st.session_state.setdefault("mtu_live_ticket_cache", {})
    cache_key = (supplier_id, clean.lower())
    if cache_key in cache:
        return cache[cache_key]
    try:
        result = client.get_ticket(supplier_id, clean)
    except Exception as e:
        result = {"error": True, "message": str(e)}
    cache[cache_key] = result
    return result


def _mtu_resolve_modality_name(client, supplier_id, ticket_code, modality_code, fallback_label):
    """The Modality's real client-facing NAME for render_multi_ticket_update_flow - NOT the
    same thing as its code.

    CONFIRMED PRODUCT-OWNER RULE (2026-09-08 follow-up): "If updating bulk ticket, the modality
    is the same as the one existing or it is the same name as Excursion (max 40 signs)." Before
    this, the batch-update flow never passed modality_name to TicketHumanPreConfig at all, which
    (per that schema's own default_modality_name_before_code_is_truncated validator) silently
    defaulted the published name to the MODALITY CODE - e.g. an existing Modality genuinely
    named "Standard Private Tour" would get overwritten to just "Standard" on every batch
    update, a real (if quiet) data loss. Fixed by resolving the ALREADY-LIVE Modality's own name
    via GET first; only when that can't be read (e.g. a brand-new Modality Code, or the GET
    fails) does it fall back to the excursion's own label, capped to 40 characters - the field's
    real Travel Compositor length limit, same class of constraint as MODALITY_CODE_MAX_LENGTH.
    Cached per (supplier_id, ticket_code, modality_code) so re-rendering the same item doesn't
    re-fetch every rerun - same pattern as _mtu_fetch_live_ticket's own cache."""
    cache = st.session_state.setdefault("mtu_modality_name_cache", {})
    cache_key = (supplier_id, (ticket_code or "").strip().lower(), (modality_code or "").strip().lower())
    if cache_key not in cache:
        live_name = None
        if ticket_code and modality_code:
            try:
                opt = client.get_ticket_option(supplier_id, ticket_code, modality_code)
            except Exception:
                opt = None
            if isinstance(opt, dict) and "error" not in opt:
                live_name = (opt.get("name") or "").strip() or None
        cache[cache_key] = live_name
    live_name = cache[cache_key]
    if live_name:
        return live_name
    fallback = (fallback_label or modality_code or "").strip()
    return fallback[:40] or (modality_code or "")


def _mtu_clear_geo_confirmation(current, idx):
    """Twin of _mt_clear_geo_confirmation for this ("mtu_") flow's own separate geo-confirm
    state/checkbox - see that function's docstring for the full bug this pattern closes."""
    current["geo_confirmed"] = False
    st.session_state.pop(f"mtu_geo_confirm_{idx}", None)


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


def _render_hotel_masterdata_step(client):
    """
    "Use Travel Compositor master data for this hotel?" - shown once per new hotel, right after
    Step 2 is confirmed and before Step 3's Input Source (product owner request, 2026-09-06,
    mirroring Travel Compositor's own manual "add hotel" screen). Only reachable for a genuinely
    NEW hotel code (see render_hotel_flow's caller) - an existing hotel already has its own live
    content in Travel Compositor.

    Confirmed via feasibility investigation (2026-09-06, real Swagger + real API responses):
    Travel Compositor's master hotel database (361,942 records) has NO live name-search endpoint
    at all - only bulk pagination (GET /accommodations) and a curated "preferred hotels" list that
    turned out too narrow to even cover the real hotel that prompted this investigation. So this
    searches a LOCAL COPY (masterdata_store.py), synced from Travel Compositor on demand, using
    name + optional country + optional geolocation matching (masterdata_matcher.py) - never an
    exact/silent match: every candidate is shown to a human to confirm, per product owner decision
    the same day, since fuzzy name matching alone can and will occasionally surface the wrong
    property.

    Sets st.session_state.hp_masterdata_decided=True and hp_masterdata_seed (a dict from
    masterdata_matcher.datasheet_to_masterdata_seed(), or None if skipped) once the human is done
    here - render_hotel_flow only calls this again if those get cleared (e.g. "Start over").

    UPDATED 2026-09-11 (product owner, showing Travel Compositor's own "New hotel using master
    data" screen - Destination + Name search - as the model to match): added a Destination field
    and a GIATA code field (exact-id lookup, see masterdata_matcher.find_by_giata_id) - either
    one an alternative path into the same candidate-confirmation list below, never a silent
    auto-match.

    UPDATED AGAIN 2026-09-16 (product owner, same screen, red-circled): "Human must first select
    the destination, which must be confirmed by Travel C." The free-text Destination field above
    (only ever geocoded via OpenStreetMap as a soft boost) is now a required confirmation step
    against Travel Compositor's OWN destination list (client.find_destination_candidates - a
    real GET /destination/{micrositeId}) - the Hotel name field isn't even shown until one is
    confirmed. The Country code field is gone entirely: the confirmed destination's own country
    is used instead, as a hard filter this time (masterdata_matcher.find_candidates'
    strict_country=True) rather than the soft boost/fallback the plain-text version used. GIATA
    code is unchanged - still skips destination confirmation entirely.
    """
    st.header("Hotel — Step 3: Use Travel Compositor master data?")
    st.caption(
        "Travel Compositor keeps its own master database of hotel content (images, description, "
        "facilities) for hotels worldwide - the same one it offers when a human manually adds a "
        "hotel in its own back office. If this property is in there, its images and description "
        "can seed this contract instead of asking someone to go find photos."
    )

    meta = masterdata_store.index_meta()
    if not masterdata_store.index_is_usable():
        if meta and not meta.get("complete"):
            st.warning("⚠️ A previous sync of Travel Compositor's master data didn't finish - the local copy isn't usable yet.")
        else:
            st.info("No local copy of Travel Compositor's master hotel data has been synced yet - this is a one-time setup step (then an occasional refresh).")
        if st.button("🔄 Sync master data now (one-time, several minutes)", key="hp_md_sync_btn"):
            progress_bar = st.progress(0.0)
            status_line = st.empty()

            def _hp_md_progress(done, total):
                status_line.caption(f"Synced {done:,} / {total:,} accommodations so far...")
                if total:
                    progress_bar.progress(min(1.0, done / total))

            with st.spinner("Syncing Travel Compositor's master hotel data - this can take several minutes..."):
                sync_result = masterdata_store.sync_accommodation_index(client, progress_callback=_hp_md_progress)
            if sync_result["ok"]:
                st.success(f"✅ Synced {sync_result['total_records']:,} accommodations.")
                st.session_state.pop("hp_md_index_cache", None)
                st.rerun()
            else:
                st.error(f"❌ Sync failed: {sync_result['error']}")
        # Skipping here means creating a hotel without ever having CHECKED master data - the
        # local index isn't usable, so no search can run at all. That is the single most likely
        # way to end up with a duplicate property in Travel Compositor, so it needs a stated
        # reason rather than one click (product owner, 2026-09-13, choosing "block until a reason
        # is given" over a warning: "we must make sure"). The reason is stored and shown later in
        # the automap review screen, so a decision made in a hurry is still reviewable.
        st.markdown("---")
        st.warning(
            "⚠️ Master data can't be searched until the sync above has run, so this hotel would be "
            "created **without checking whether Travel Compositor already has it**. That's how a "
            "duplicate property appears on the Travel Compositor surface."
        )
        no_index_reason = st.text_input(
            "If you still want to continue, say why (required)",
            value="", key="hp_md_skip_reason_no_index",
            placeholder="e.g. brand-new property, confirmed with the supplier it isn't listed anywhere yet",
        )
        if st.button("Continue without checking master data", key="hp_md_skip_no_index",
                     disabled=not no_index_reason.strip()):
            st.session_state.hp_masterdata_decided = True
            st.session_state.hp_masterdata_seed = None
            st.session_state.hp_masterdata_skip_reason = no_index_reason.strip()
            st.rerun()
        return

    synced_at = meta.get("synced_at")
    synced_caption = f"{meta.get('total_records', 0):,} accommodations"
    if synced_at:
        synced_caption += f", synced {datetime.fromtimestamp(synced_at).strftime('%Y-%m-%d %H:%M')}"
    st.caption(f"Local master-data copy: {synced_caption}.")
    if st.button("🔄 Refresh master data", key="hp_md_resync_btn"):
        st.session_state.hp_md_resync_requested = True
    if st.session_state.get("hp_md_resync_requested"):
        progress_bar = st.progress(0.0)
        status_line = st.empty()

        def _hp_md_progress(done, total):
            status_line.caption(f"Synced {done:,} / {total:,} accommodations so far...")
            if total:
                progress_bar.progress(min(1.0, done / total))

        with st.spinner("Re-syncing Travel Compositor's master hotel data..."):
            sync_result = masterdata_store.sync_accommodation_index(client, progress_callback=_hp_md_progress)
        st.session_state.hp_md_resync_requested = False
        if sync_result["ok"]:
            st.success(f"✅ Synced {sync_result['total_records']:,} accommodations.")
            st.session_state.pop("hp_md_index_cache", None)
        else:
            st.error(f"❌ Sync failed: {sync_result['error']}")

    # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-16, screenshots of this exact screen and Travel
    # Compositor's own "Search Accommodation" screen, red-circled): "Human must first select the
    # destination, which must be confirmed by Travel C. Then we add the name of the hotel we are
    # creation. The country is not needed, as this information is provided by the destination
    # code from travel compositor: So after checking, travel c first GET the Destination
    # information and only when human confirms the destination the human can add the name of the
    # hotel he is searching." Replaces the old free-text Destination field (only ever geocoded
    # via OpenStreetMap - a soft, never-confirmed boost) and the 2-letter Country code field with
    # a required destination-confirmation step against Travel Compositor's OWN destination list
    # (api_client.find_destination_candidates - a real GET /destination/{micrositeId}, not a
    # free-text guess): type a destination, see every real Travel Compositor destination that
    # matches, confirm the right one. Only then does the Hotel name field become usable, scoped
    # to the confirmed destination's own country as a HARD filter (product owner's own explicit
    # choice, see masterdata_matcher.find_candidates' strict_country parameter) and its own
    # coordinates for the geo-boost (no separate OpenStreetMap call needed - Travel Compositor's
    # destination record already carries them when available).
    #
    # GIATA code stays exactly as it was: an authoritative id lookup that skips destination
    # confirmation entirely (product owner's own "search it with Giatacodes OR just by manually
    # adding the name" framing from 2026-09-11 - a known id needs no destination check at all).
    giata_query = st.text_input(
        "GIATA code (optional, exact match — skips destination confirmation entirely)",
        value="", key="hp_md_search_giata",
        help="If you already know this hotel's GIATA id, this is the fastest and most reliable "
             "way to find it - an exact id match, not a name guess.")

    confirmed_destination = st.session_state.get("hp_md_confirmed_destination")

    search_name = ""
    if not giata_query.strip():
        if confirmed_destination:
            st.success(
                f"✅ Destination confirmed: **{confirmed_destination.get('name')}** "
                f"(code `{confirmed_destination.get('code')}`"
                + (f", country {confirmed_destination.get('country')}"
                   if confirmed_destination.get("country") else "") + ")")
            if st.button("↩️ Change destination", key="hp_md_dest_change"):
                for k in ("hp_md_confirmed_destination", "hp_md_dest_candidates", "hp_md_candidates"):
                    st.session_state.pop(k, None)
                st.rerun()
            search_name = st.text_input(
                "Hotel name to search for", value="", key="hp_md_search_name",
                help=f"Searched within {confirmed_destination.get('name')} "
                     f"({confirmed_destination.get('country') or 'country unknown'}).")
        else:
            st.markdown("##### Step 1 — confirm the destination")
            st.caption("Checked against Travel Compositor's own real destination list - not a "
                      "free-text guess. The hotel name search only becomes available once one "
                      "of these is confirmed.")
            dest_query = st.text_input(
                "Destination, Country (e.g. \"El Gouna, Egypt\")", value="", key="hp_md_dest_query",
                help="Including the country narrows the search to that country and avoids "
                     "matching a same-named destination in the wrong place - e.g. \"Cairo, "
                     "Egypt\". The country is optional; a bare city name still works.")
            if st.button("🔎 Find destination", key="hp_md_dest_search_btn",
                         disabled=not dest_query.strip()):
                with st.spinner(f"Looking up \"{dest_query.strip()}\" in Travel Compositor..."):
                    st.session_state.hp_md_dest_candidates = client.find_destination_candidates(
                        dest_query.strip())
                if not st.session_state.hp_md_dest_candidates:
                    st.warning(f"No destination matching \"{dest_query.strip()}\" was found in "
                               f"Travel Compositor. Try a different spelling.")

            dest_candidates = st.session_state.get("hp_md_dest_candidates")
            if dest_candidates:
                st.write(f"Found {len(dest_candidates)} destination(s) — confirm the right one:")
                for i, dest in enumerate(dest_candidates):
                    with st.container(border=True):
                        dcols = st.columns([4, 1])
                        with dcols[0]:
                            st.markdown(
                                f"**{dest.get('name')}** — code `{dest.get('code')}`"
                                + (f" · country {dest.get('country')}" if dest.get("country") else ""))
                        with dcols[1]:
                            if st.button("Confirm", key=f"hp_md_dest_confirm_{i}"):
                                st.session_state.hp_md_confirmed_destination = dest
                                st.session_state.pop("hp_md_dest_candidates", None)
                                st.session_state.pop("hp_md_candidates", None)
                                st.rerun()

    if st.button("🔎 Search master data", key="hp_md_search_btn",
                 disabled=not (giata_query.strip() or (confirmed_destination and search_name.strip()))):
        if "hp_md_index_cache" not in st.session_state:
            with st.spinner("Loading local master-data index..."):
                st.session_state.hp_md_index_cache = masterdata_store.load_index()
        if giata_query.strip():
            # Authoritative id lookup - skips destination confirmation entirely, per the product
            # owner's own framing ("search it with Giatacodes if we enter it OR just by
            # manually adding the name" - an either/or, not a combined filter).
            st.session_state.hp_md_candidates = masterdata_matcher.find_by_giata_id(
                giata_query, st.session_state.hp_md_index_cache)
            if not st.session_state.hp_md_candidates:
                st.warning(f"No hotel with GIATA code **{giata_query.strip()}** found in the "
                           f"local master data. Search by name instead, or refresh the sync if "
                           f"this hotel might be very new.")
        else:
            st.session_state.hp_md_candidates = masterdata_matcher.find_candidates(
                search_name, st.session_state.hp_md_index_cache,
                country_code=confirmed_destination.get("country"),
                lat=confirmed_destination.get("latitude"), lon=confirmed_destination.get("longitude"),
                strict_country=True)

    with st.expander("🔍 Not finding a hotel you know is in Travel Compositor? Check the raw local copy"):
        st.caption("Bypasses country/score matching entirely - a plain text search over every "
                   "name in the local copy, so you can see directly whether the hotel is in "
                   "there at all, and if so, exactly what Travel Compositor's own record says "
                   "for its name/country - rather than trusting the ranked results above.")
        raw_query = st.text_input("Text to search for (part of the name is enough)", value="",
                                  key="hp_md_raw_search")
        if raw_query.strip():
            if "hp_md_index_cache" not in st.session_state:
                with st.spinner("Loading local master-data index..."):
                    st.session_state.hp_md_index_cache = masterdata_store.load_index()
            raw_hits = masterdata_matcher.find_by_raw_substring(raw_query, st.session_state.hp_md_index_cache)
            if not raw_hits:
                st.error(f"Not in the local copy at all - {len(st.session_state.hp_md_index_cache):,} "
                         f"accommodations checked, none with \"{raw_query.strip()}\" in the name. "
                         f"Try **🔄 Refresh master data** above (this hotel may have been added to "
                         f"Travel Compositor after the last sync), then search again.")
            else:
                st.success(f"Found {len(raw_hits)} record(s) with \"{raw_query.strip()}\" in the name:")
                for r in raw_hits:
                    st.markdown(f"- **{r.get('name') or '(unnamed)'}** — id `{r.get('id')}` · "
                               f"country `{r.get('countryCode') or '(blank)'}` · "
                               f"GIATA `{r.get('giataId') or '—'}`")

    candidates = st.session_state.get("hp_md_candidates")
    if candidates is not None:
        if not candidates:
            st.warning("No close matches found in the local master data. Adjust the search above, "
                       "refresh the sync if this hotel might be very new, or skip below.")
        else:
            st.write(f"Found {len(candidates)} possible match(es) — confirm one, or skip if none are right:")
            for i, cand in enumerate(candidates):
                with st.container(border=True):
                    cols = st.columns([4, 1])
                    with cols[0]:
                        geo_note = f" · {cand['geo_km']} km from the location you provided" if cand.get("geo_km") is not None else ""
                        giata_note = f" · GIATA {cand['giataId']}" if cand.get("giataId") else ""
                        confidence = "exact GIATA match" if cand.get("name_score") is None \
                            else f"{cand['score']*100:.0f}%"
                        st.markdown(f"**{cand.get('name') or '(unnamed)'}**  \n"
                                    f"Country: {cand.get('countryCode') or '—'}{giata_note} · "
                                    f"Match confidence: {confidence}{geo_note}")
                        if cand.get("country_mismatch"):
                            st.caption("⚠️ Outside the country you searched for - shown because no "
                                      "strong match was found inside it. Travel Compositor's own "
                                      "record for this property may have the wrong/blank country "
                                      "code - check the name/location before using it.")
                    with cols[1]:
                        if st.button("Use this hotel", key=f"hp_md_pick_{i}"):
                            with st.spinner("Fetching this hotel's content from Travel Compositor..."):
                                datasheet = client.get_accommodation_datasheet(cand["id"])
                            if isinstance(datasheet, dict) and "error" not in datasheet:
                                _hp_seed = masterdata_matcher.datasheet_to_masterdata_seed(datasheet)
                                # The datasheet is the authority on its own ids, but fall back to
                                # the index row this candidate came from if the datasheet omits
                                # either - both carry id/giataId, and losing them here is what
                                # would leave a human with no way to complete the back-office
                                # automap (see hotel_automap.py's own docstring for why that step
                                # can't be done through the API at all).
                                _hp_seed["accommodation_id"] = _hp_seed.get("accommodation_id") or (
                                    str(cand["id"]).strip() if cand.get("id") else None)
                                _hp_seed["giata_id"] = _hp_seed.get("giata_id") or (
                                    str(cand["giataId"]).strip() if cand.get("giataId") else None)
                                _hp_seed["master_name"] = _hp_seed.get("name") or cand.get("name")
                                st.session_state.hp_masterdata_seed = _hp_seed
                                st.session_state.hp_masterdata_decided = True
                                st.session_state.hp_masterdata_skip_reason = None
                                st.rerun()
                            else:
                                st.error(f"❌ Couldn't fetch this hotel's content: "
                                          f"{datasheet.get('message') if isinstance(datasheet, dict) else datasheet}")

    # CONFIRMED PRODUCT-OWNER RULE (2026-09-13): moving past this step without picking a master
    # record must be a deliberate, stated decision, not one click - "we must make sure that
    # Automap with master is also set, so the hotel is not a duplicate". The strictness is graded
    # by what the human has actually been shown, because a blanket "always demand a reason" would
    # make people type filler text to get past a screen that had nothing to offer them:
    #
    #   * candidates found      -> reason REQUIRED. This is the real risk: the app showed matches
    #                              and a human decided none of them is this hotel. If that call is
    #                              wrong, a duplicate is created, and the reason is what makes the
    #                              call reviewable afterward.
    #   * searched, zero found  -> allowed, reason recorded automatically. Nothing was on offer to
    #                              reject, so there is no judgement call to explain.
    #   * never searched        -> reason REQUIRED. Same as the no-index case above: skipping
    #                              without looking is how duplicates happen.
    st.markdown("---")
    _hp_md_searched = candidates is not None
    _hp_md_had_candidates = bool(candidates)

    if _hp_md_had_candidates:
        st.warning(
            f"⚠️ {len(candidates)} possible match(es) are listed above. If one of them IS this "
            f"hotel, use it — that's what lets this contract be mapped to Travel Compositor's "
            f"existing property instead of appearing as a duplicate."
        )
        _hp_md_reason_needed = True
    elif not _hp_md_searched:
        st.warning(
            "⚠️ No master-data search has been run yet for this hotel. Creating it without "
            "checking is how a duplicate property appears on the Travel Compositor surface."
        )
        _hp_md_reason_needed = True
    else:
        st.info(
            "The master-data search found nothing matching this hotel, so there's nothing to map "
            "it to. Continuing is fine — this will be noted on the automap review screen so it "
            "can be double-checked in Travel Compositor later."
        )
        _hp_md_reason_needed = False

    if _hp_md_reason_needed:
        _hp_md_reason = st.text_input(
            "None of these is the right hotel? Say why (required)",
            value="", key="hp_md_skip_reason",
            placeholder="e.g. all candidates are in a different resort; this property opened this year",
        )
        _hp_md_can_skip = bool(_hp_md_reason.strip())
        _hp_md_stored_reason = _hp_md_reason.strip()
    else:
        _hp_md_can_skip = True
        _hp_md_stored_reason = "Master-data search ran and returned no candidates."

    if st.button("Continue without master data — I'll provide photos manually",
                 key="hp_md_skip", disabled=not _hp_md_can_skip):
        st.session_state.hp_masterdata_decided = True
        st.session_state.hp_masterdata_seed = None
        st.session_state.hp_masterdata_skip_reason = _hp_md_stored_reason
        st.rerun()


def render_hotel_automap_review(client):
    """"Hotels awaiting automap" - the follow-up checklist for the one step of hotel creation that
    Travel Compositor's API cannot perform (product owner, 2026-09-13: "we must make sure that
    Automap with master is also set, so the hotel is not a duplicate in the travel compositor
    surface"). Also offers a standalone manual search (see the expander at the bottom) so this can
    be done for any hotel, published or not, at any time - not just the moment a brand-new hotel
    is first created (see that section's own comment for the confirmed bug this fixes, 2026-09-16).

    This screen exists because of a confirmed API limitation, not a missing feature on our side:
    neither `ContractHotelDetailedVO` (POST/PUT /hotel/{supplierId}) nor the read-only "Web content
    - Accommodations" section exposes the automap in any form - see hotel_automap.py's own
    docstring for the field-by-field check. So the app's job stops at handing a human the exact
    ids and making the outstanding work visible until someone says it's done.

    Nothing here writes to Travel Compositor. Ticking an entry off records that a HUMAN did the
    mapping in the back office; it cannot verify it, and deliberately doesn't pretend to."""
    st.header("🔗 Hotels awaiting automap")
    st.caption(
        "Travel Compositor's API can't set \"Automap with master\" - it's only available in the "
        "back office. These hotels were created here and still need that step, or they may show "
        "up as duplicate properties. Tick one off once you've done it in Travel Compositor."
    )

    pending = hotel_automap.list_pending()
    if not pending:
        st.success("✅ Nothing outstanding — every hotel created here has been mapped or checked.")
    else:
        st.warning(f"{len(pending)} hotel(s) still need attention.")

    for entry in pending:
        with st.container(border=True):
            cols = st.columns([4, 1])
            with cols[0]:
                recorded = entry.get("recorded_at")
                when = datetime.fromtimestamp(recorded).strftime("%Y-%m-%d %H:%M") if recorded else "—"
                st.markdown(f"**{entry.get('provider_code')}** — {entry.get('hotel_name') or '(no name)'}  \n"
                            f"Supplier {entry.get('supplier_id')} · published {when}")
                if entry.get("status") == hotel_automap.STATUS_LINKED:
                    st.markdown(
                        f"Map this to master accommodation **{entry.get('accommodation_id')}**"
                        + (f" · GIATA **{entry.get('giata_id')}**" if entry.get("giata_id") else "")
                        + (f"  \nMaster record: {entry.get('master_name')}" if entry.get("master_name") else "")
                    )
                else:
                    # No id to map to - so the useful thing to show is the judgement call that was
                    # made at the time, which is the thing most worth a second look.
                    st.markdown(
                        "⚠️ **No master record was linked when this was created.** Worth confirming "
                        "the property really isn't already in Travel Compositor's master data."
                        + (f"  \nReason given at the time: _{entry.get('skip_reason')}_"
                           if entry.get("skip_reason") else "")
                    )
            with cols[1]:
                if st.button("✅ Mark as done", key=f"automap_done_{entry.get('supplier_id')}_{entry.get('provider_code')}"):
                    hotel_automap.mark_mapped(entry.get("supplier_id"), entry.get("provider_code"))
                    st.rerun()
                # CONFIRMED REAL CASE (product owner, 2026-09-16): published a test hotel, then
                # deleted it directly in Travel Compositor because "the prices were wrong and the
                # matches not included - therefore it was useless data" - but the app kept
                # showing "1 hotel(s) still need attention" for it regardless, since nothing here
                # can see a deletion that happened entirely on Travel Compositor's side. This is
                # a DIFFERENT resolution than "Mark as done" (mark_mapped) - the hotel was never
                # actually mapped, it stopped existing - so it needs its own action and its own
                # timestamp (hotel_automap.dismiss), not a reuse of "mapped".
                with st.popover("🗑️ No longer applicable"):
                    st.caption("For a hotel that was deleted in Travel Compositor (or otherwise "
                              "no longer exists) rather than mapped - keeps this off the pending "
                              "list without falsely recording it as mapped.")
                    dismiss_reason = st.text_input(
                        "Why (optional)", value="",
                        key=f"automap_dismiss_reason_{entry.get('supplier_id')}_{entry.get('provider_code')}",
                        placeholder="e.g. deleted in Travel Compositor - prices were wrong")
                    if st.button("Confirm — dismiss",
                                 key=f"automap_dismiss_{entry.get('supplier_id')}_{entry.get('provider_code')}"):
                        hotel_automap.dismiss(entry.get("supplier_id"), entry.get("provider_code"),
                                              reason=dismiss_reason)
                        st.rerun()

    mapped = hotel_automap.list_mapped()
    if mapped:
        with st.expander(f"Already dealt with ({len(mapped)})"):
            st.caption("Kept as a record rather than deleted - if a duplicate ever does turn up, "
                       "this is what says whether that hotel was mapped, and when.")
            for entry in mapped:
                done = entry.get("mapped_at")
                when = datetime.fromtimestamp(done).strftime("%Y-%m-%d %H:%M") if done else "—"
                st.markdown(f"- **{entry.get('provider_code')}** — {entry.get('hotel_name') or ''} "
                            f"(marked done {when})")

    dismissed = hotel_automap.list_dismissed()
    if dismissed:
        with st.expander(f"Dismissed — no longer applicable ({len(dismissed)})"):
            st.caption("Not mapped - these were deleted in Travel Compositor or otherwise stopped "
                       "needing a mapping. Kept as a record of what was checked and why.")
            for entry in dismissed:
                done = entry.get("dismissed_at")
                when = datetime.fromtimestamp(done).strftime("%Y-%m-%d %H:%M") if done else "—"
                reason_bit = f" — _{entry.get('dismiss_reason')}_" if entry.get("dismiss_reason") else ""
                st.markdown(f"- **{entry.get('provider_code')}** — {entry.get('hotel_name') or ''} "
                            f"(dismissed {when}){reason_bit}")

    # CONFIRMED BUG FIX (product owner, 2026-09-16): "the Hotel review for automap can't be done
    # after the hotel has been published. If we cannot do it from the beginning, the button is
    # unable." Before this, the ONLY way a hotel ever landed on the list above was going through
    # the masterdata step at the moment of a brand-new hotel's creation (flows/hotel.py gates that
    # step on `not existing_snapshot`) - an already-published hotel, or one created before this
    # feature existed, or whose masterdata step was skipped, had no way back in at all. This
    # section is a standalone entry point into the exact same search (masterdata_store /
    # masterdata_matcher), reachable for ANY hotel at ANY time, that records straight into
    # hotel_automap - independent of the create/update flow, so "do it later" is always possible.
    st.markdown("---")
    with st.expander("🔎 Search master data for a hotel (published or not)"):
        st.caption(
            "Check or set the automap for any hotel - already published, created before this "
            "reminder existed, or whose masterdata step was skipped at the time. This never "
            "writes to Travel Compositor - it only searches the local master-data copy and "
            "records the result here as the reminder for the back-office step."
        )
        supplier_id = _ur_pick_momira_supplier(client, "ham_supplier")
        if not supplier_id:
            return
        if st.button("📥 Load hotels from this supplier", key="ham_load_hotels"):
            with st.spinner("Loading hotels..."):
                records, err = bulk_notes.list_services(client, supplier_id, "Hotel")
            if err and not records:
                st.error(f"❌ Couldn't load hotels: {err}")
            else:
                if err:
                    st.warning(f"⚠️ Some couldn't be loaded: {err}")
                st.session_state.ham_hotels = records
                st.session_state.ham_hotels_supplier = supplier_id
                st.rerun()

        hotels = st.session_state.get("ham_hotels")
        if hotels and st.session_state.get("ham_hotels_supplier") == supplier_id:
            if not hotels:
                st.info("This supplier has no hotels.")
                return
            hotel_options = {
                f"{h.get('hotelname') or '(unnamed)'} — {h.get('providerCode')}": h for h in hotels
            }
            picked_label = st.selectbox("Which hotel?", list(hotel_options.keys()), key="ham_hotel_pick")
            picked = hotel_options[picked_label]
            provider_code = picked.get("providerCode")
            hotel_name = picked.get("hotelname") or ""

            existing_entry = hotel_automap.get(supplier_id, provider_code)
            if existing_entry:
                status_word = "already marked mapped" if existing_entry.get("mapped_at") else "already on the pending list above"
                st.info(f"This hotel is {status_word}.")

            _render_hotel_automap_manual_search(client, supplier_id, provider_code, hotel_name)


def _render_hotel_automap_manual_search(client, supplier_id, provider_code, hotel_name):
    """The search/confirm widget behind the manual automap-review entry point above - same
    underlying search (masterdata_store/masterdata_matcher) as _render_hotel_masterdata_step, but
    standalone: its own session-state keys (never touches hp_masterdata_*, which belong to the
    create-a-new-hotel flow and would corrupt an in-progress creation if reused here), and it
    writes straight to hotel_automap.record_pending on a decision instead of feeding an
    in-progress contract build."""
    meta = masterdata_store.index_meta()
    if not masterdata_store.index_is_usable():
        st.warning("⚠️ No usable local copy of Travel Compositor's master data yet - sync it "
                   "from the hotel creation screen first (Step 3 - Use Travel Compositor master "
                   "data?), then come back here.")
        return

    giata_query = st.text_input(
        "GIATA code (optional, exact match)", value="", key=f"ham_md_giata_{provider_code}")
    col_a, col_b = st.columns(2)
    with col_a:
        search_name = st.text_input("Hotel name to search for", value=hotel_name, key=f"ham_md_name_{provider_code}")
    with col_b:
        search_country = st.text_input("Country code (optional, e.g. EG)", value="", key=f"ham_md_country_{provider_code}", max_chars=2)

    if st.button("🔎 Search master data", key=f"ham_md_search_{provider_code}",
                 disabled=not (giata_query.strip() or search_name.strip())):
        if "hp_md_index_cache" not in st.session_state:
            with st.spinner("Loading local master-data index..."):
                st.session_state.hp_md_index_cache = masterdata_store.load_index()
        if giata_query.strip():
            st.session_state[f"ham_md_candidates_{provider_code}"] = masterdata_matcher.find_by_giata_id(
                giata_query, st.session_state.hp_md_index_cache)
        else:
            st.session_state[f"ham_md_candidates_{provider_code}"] = masterdata_matcher.find_candidates(
                search_name, st.session_state.hp_md_index_cache, country_code=search_country or None)

    candidates = st.session_state.get(f"ham_md_candidates_{provider_code}")
    if candidates is not None:
        if not candidates:
            st.warning("No close matches found. Adjust the search above, or record this as "
                       "checked-and-not-found below.")
        else:
            st.write(f"Found {len(candidates)} possible match(es):")
            for i, cand in enumerate(candidates):
                with st.container(border=True):
                    cols = st.columns([4, 1])
                    with cols[0]:
                        giata_note = f" · GIATA {cand['giataId']}" if cand.get("giataId") else ""
                        confidence = "exact GIATA match" if cand.get("name_score") is None \
                            else f"{cand['score']*100:.0f}%"
                        st.markdown(f"**{cand.get('name') or '(unnamed)'}**  \n"
                                    f"Country: {cand.get('countryCode') or '—'}{giata_note} · "
                                    f"Match confidence: {confidence}")
                        if cand.get("country_mismatch"):
                            st.caption("⚠️ Outside the country you searched for - shown because no "
                                      "strong match was found inside it. Travel Compositor's own "
                                      "record for this property may have the wrong/blank country "
                                      "code - check the name/location before using it.")
                    with cols[1]:
                        if st.button("Use this", key=f"ham_md_pick_{provider_code}_{i}"):
                            hotel_automap.record_pending(
                                supplier_id, provider_code, hotel_name=hotel_name,
                                accommodation_id=cand.get("id"), giata_id=cand.get("giataId"),
                                master_name=cand.get("name"))
                            st.success(f"✅ Recorded — map this to accommodation `{cand.get('id')}` "
                                      f"in Travel Compositor's back office.")
                            st.rerun()

    reason = st.text_input(
        "Or: record as checked, nothing matches (say why)", value="",
        key=f"ham_md_reason_{provider_code}",
        placeholder="e.g. confirmed with the supplier this property isn't listed anywhere yet")
    if st.button("Record as checked — no master record found", key=f"ham_md_skip_{provider_code}",
                 disabled=not reason.strip()):
        hotel_automap.record_pending(
            supplier_id, provider_code, hotel_name=hotel_name, skip_reason=reason.strip())
        st.success("✅ Recorded.")
        st.rerun()


def _render_hotel_price_audit_section(data, primary):
    """Hotel Price Audit UI (product owner, 2026-09-12) - a deliberately TEMPORARY second check,
    kept separate from the main extraction (`data`), focused ONLY on the numbers (room prices,
    meal plan supplements, offers/early-birds, other supplements) - see price_audit.py's own
    docstring for the full reasoning. Prices are what actually cost Momira Travel money if wrong,
    unlike a mis-extracted hotel description; this exists to catch that specific class of mistake
    while the app is still learning how different suppliers structure their contracts, and is
    meant to be removed again once the main extraction reliably gets prices right without a
    second opinion. Runs on demand (not automatically) since it's a second full AI call over the
    same document and costs real API spend every time it's used.

    Called from exactly ONE of two places per render (never both - see each call site's own
    comment): near the top of Step 4, as the PRIMARY action, when the human said this contract is
    for CHECKING the current period; or lower down, right before Publish, as a SECONDARY sanity
    check, when they're instead adding a NEW period (or for a brand-new hotel, which never even
    reaches this function - see render_hotel_flow's contract-purpose question, only asked for an
    EXISTING hotel). `primary` only changes the heading/copy, not the underlying behaviour."""
    if primary:
        st.markdown("#### 🔍 Price audit — check this contract against what's already live")
        st.caption("You said this contract is for CHECKING the current period. Runs a SECOND, independent "
                   "read of the contract focused only on prices - room rates, meal plan supplements, "
                   "offers/early-birds, other supplements - and flags anything that doesn't match the "
                   "extraction below. Review this before deciding whether anything needs fixing.")
    else:
        st.markdown("#### 💰 Price audit (temporary — cross-checks numbers against the contract)")
        st.caption("Runs a SECOND, independent read of the contract focused only on prices - room rates, meal "
                   "plan supplements, offers/early-birds, other supplements - and flags anything that doesn't "
                   "match what's above. This is a temporary safety net while the app is still learning how "
                   "different suppliers structure their contracts; it costs one extra AI call per run.")
    if st.button("🔍 Run price audit", key="hp_price_audit_run"):
        with st.spinner("Re-reading the contract for prices only..."):
            try:
                _hp_audit_result = run_hotel_price_audit(st.session_state.get("hp_raw_text") or "")
                st.session_state.hp_price_audit_facts = _hp_audit_result.get("price_facts") or []
            except Exception as e:
                st.session_state.hp_price_audit_facts = None
                st.error(f"Price audit failed: {e}")

    _hp_audit_facts = st.session_state.get("hp_price_audit_facts")
    if _hp_audit_facts is not None:
        _hp_audit_findings = compare_price_audit_to_extraction(_hp_audit_facts, data)
        _hp_audit_summary = summarize_findings(_hp_audit_findings)
        if _hp_audit_summary["mismatch"] or _hp_audit_summary["not_found"]:
            st.warning(f"⚠️ Price audit found **{_hp_audit_summary['mismatch']} mismatch(es)** and "
                       f"**{_hp_audit_summary['not_found']} item(s) not found** in the extraction above "
                       f"(**{_hp_audit_summary['match']}** verified OK). Review before publishing.")
        elif _hp_audit_findings:
            st.success(f"✅ Price audit: all **{_hp_audit_summary['match']}** priced item(s) it found in "
                       f"the contract match what's above.")
        else:
            st.info("Price audit ran but found no price-bearing numbers to check.")

        if _hp_audit_findings:
            with st.expander(f"🔍 Price audit details ({len(_hp_audit_findings)} item(s) checked)",
                              expanded=bool(_hp_audit_summary["mismatch"] or _hp_audit_summary["not_found"])):
                _status_icon = {"match": "✅", "mismatch": "❌", "not_found": "❓"}
                # Worst-first ordering so a human scanning the list sees the money-affecting
                # problems (mismatch) before the merely-unmatched ones, and both before the OK's.
                _status_order = {"mismatch": 0, "not_found": 1, "match": 2}
                for _finding in sorted(_hp_audit_findings, key=lambda f: _status_order.get(f.get("status"), 3)):
                    st.markdown(f"{_status_icon.get(_finding.get('status'), '•')} {_finding.get('message')}")
                    if _finding.get("quote"):
                        st.caption(f"Contract: “{_finding['quote']}”")


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
    # Deferred import (Phase 1 module 13, 2026-09-16): a module-top-level import here would
    # create a 3-way circular import (app -> app_helpers -> flows.price_refresh -> app, the
    # last leg needing names this module hasn't finished defining yet while app.py's own
    # `from app_helpers import (...)` is still executing). Resolving it at call time instead -
    # by which point both app.py and every flows/*.py module are fully loaded - is zero
    # behaviour change, same pattern as the dispatcher-stays-in-app.py fix from module 10.
    from flows.price_refresh import render_price_refresh_flow, render_ticket_price_refresh_flow
    st.header("🔄 Price update to existing Products")
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
        #
        # CONFIRMED PRODUCT-OWNER REDESIGN (2026-09-10): "Update multiple existing Tickets from
        # one document" (content, not just price - titles/included/excluded) used to be reachable
        # ONLY via "Create a new product -> Ticket"'s own action 5, despite being an update, not a
        # create - that's the exact confusion this redesign removes. It's a THIRD mode here now,
        # alongside the other two, since (like bulk price refresh) it operates across many Tickets
        # at once and doesn't fit "you already picked ticket X" the way
        # _render_update_refresh_coded_service's per-code flow does.
        ticket_mode = st.radio(
            "What kind of Ticket update is this?",
            ["Bulk price refresh from a rate sheet",
             "Bulk update multiple Tickets' content from one document (titles/included/excluded/prices)",
             "Update one Ticket by hand"],
            horizontal=True, key="ur_ticket_mode")
        if ticket_mode == "Bulk price refresh from a rate sheet":
            render_ticket_price_refresh_flow(client)
            return
        if ticket_mode.startswith("Bulk update multiple Tickets"):
            st.caption("Detects which excursions this document describes, matches each to an "
                      "EXISTING live Ticket code, and lets you review before publishing - "
                      "nothing is created, only existing Tickets are updated.")
            supplier_id = _ur_pick_momira_supplier(client, "ur_ticket_batch")
            if not supplier_id:
                return
            if st.button("➡️ Continue", type="primary", key="ur_ticket_batch_continue"):
                # Hands off into render_ticket_flow's own already-proven Step 3/4 for this
                # action - TICKET_ACTION_FIELDS["update_tickets_batch"] == [] so Step 3 asks
                # nothing extra, and Step 4 routes straight into render_multi_ticket_update_flow
                # (see its own "if action == 'update_tickets_batch':" branch). Same mechanism
                # _render_update_refresh_coded_service already uses for update_ticket/
                # update_option, just without a per-code pick this action doesn't need.
                st.session_state.tk_cfg_action = "update_tickets_batch"
                st.session_state.tk_cfg_supplier_id = supplier_id
                st.session_state.tk_step1_confirmed = True
                st.session_state.tk_step2_confirmed = False
                st.session_state.product_type = "Ticket"
                st.rerun()
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
        if (s.get("commercialName") or s.get("legalName") or "").strip().lower().startswith("momira_") and is_active_supplier(s)
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


def render_cancellation_bulk_flow(client):
    """Bulk-update Cancellation Policy — top-level dispatcher across all 5 product types.

    CONFIRMED PRODUCT-OWNER REQUEST (2026-09-10): "bulk update cancellation policy --> this
    must be usable for all Services: Hotel; Transfer, Transport, Ticket and ClosedTour - so
    far it looks like only Transport can do it." Transport already had a proven, in-production
    tool (cancellation_bulk_transport.py / render_transport_cancellation_bulk_flow) built
    2026-08-28 - that flow is reused UNCHANGED here, not touched or duplicated. The other 4
    product types are new, backed by cancellation_bulk.py (see its own module docstring for
    the structured-field/shape differences between ClosedTour, Ticket, Transfer and Hotel).
    """
    # Deferred import - same circular-import reason as render_update_refresh_flow above.
    from flows.cancellation import (
        render_transport_cancellation_bulk_flow, render_generic_cancellation_bulk_flow,
    )
    st.header("Bulk-update Cancellation Policy")
    st.caption("Applies one cancellation policy to every (or a chosen subset of) one "
              "supplier's live services of one type at once - both the structured field "
              "Travel Compositor enforces (where one exists) AND the matching sentence in "
              "each service's customer-facing text.")

    product_type = st.radio(
        "Which product type?", ["Transport", "ClosedTour", "Ticket", "Transfer", "Hotel"],
        key="cb_product_type", horizontal=True)

    if st.session_state.get("cb_active_product_type") != product_type:
        # Product type changed - drop everything loaded for the previous one.
        for key in list(st.session_state.keys()):
            if key.startswith("cb_") and key not in ("cb_product_type", "cb_active_product_type"):
                del st.session_state[key]
        st.session_state.cb_active_product_type = product_type

    st.markdown("---")

    if product_type == "Transport":
        render_transport_cancellation_bulk_flow(client)
        return

    render_generic_cancellation_bulk_flow(client, product_type)


def _ur_gather_text_optional(key_prefix):
    """Optional 'paste a URL and/or upload document(s)' widget pair for the Update/Refresh
    screen's matching step - same gathering logic every other flow in this app already uses
    (URL fetch + document text extraction), just standalone here since this screen only needs
    the combined text to SUGGEST a match, not to run a full extraction yet. Returns the
    combined raw text, or "" if nothing was provided/fetchable."""
    url = st.text_input("Product page URL (optional)", key=f"{key_prefix}_url")
    files = st.file_uploader("Upload document(s) (optional)", type=["pdf", "docx", "xlsx", "pptx", "csv"],
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
        # "update_tickets_batch" excluded too - this screen is for picking ONE already-chosen
        # existing Ticket and updating it; the batch flow does its own excursion detection and
        # existing-ticket matching across possibly many tickets, which doesn't fit "you already
        # picked ticket X" here. It's its own THIRD mode one level up instead - see
        # render_update_refresh_flow's Ticket branch (product owner, 2026-09-10: it used to be
        # reachable only via the Ticket wizard's own Create-only Step 2, which is exactly the
        # create/update mix-up that redesign removed).
        kind_key, action_labels = "ticket", {k: v for k, v in TICKET_ACTION_LABELS.items() if k not in ("create", "update_tickets_batch")}
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


def render_transport_manual_adjustment_flow(client, supplier_id):
    """CONFIRMED REAL PRODUCT DECISION (product owner, 2026-09-11 - see
    price_refresh.build_manual_adjustment_proposals' own docstring for the full transcript):
    a simpler alternative to the rate-sheet-reading flow, for when there is no document at all -
    just "raise everything by 10%" or "add 12 to every vehicle price". No rate sheet, no AI, no
    modality/option read or write at all - only the parent Transport record's own shared
    vehiclePrice/baseAdultPrice field moves, the same field a "vehicle" round in the document flow
    touches. Reuses price_refresh.load_supplier_products (kind=Transport) for the route list, so
    the list of transports is exactly the same live-from-Travel-Compositor fact the document flow
    already trusts."""
    st.subheader("1 — Load this supplier's transports")
    if st.button("🔍 Load transports", type="primary", key="pma_load"):
        bar = st.progress(0.0, text="Loading transports from Travel Compositor…")

        def _tick(done, total, name):
            bar.progress(min(done / max(total, 1), 1.0), text=f"Reading {name} ({done}/{total})")

        routes, err = price_refresh.load_supplier_products(
            client, supplier_id, price_refresh.KIND_TRANSPORT, progress=_tick)
        bar.empty()
        if err:
            st.error(f"Couldn't read this supplier's transports: {err}")
        elif not routes:
            st.warning("This supplier has no transports yet.")
        else:
            st.session_state.pma_routes = routes
            st.session_state.pop("pma_result", None)
            st.rerun()

    routes = st.session_state.get("pma_routes")
    if not routes:
        return

    st.success(f"{len(routes)} transport(s) loaded for supplier {supplier_id}.")
    st.subheader("2 — Pick the adjustment")
    mode_label = st.radio("Adjustment type", ["Percentage (%)", "Absolute amount"],
                          horizontal=True, key="pma_mode_pick")
    mode = "percentage" if mode_label == "Percentage (%)" else "absolute"
    value = st.number_input(
        "Value (positive to raise, negative to lower)" + (" — e.g. 10 = +10%" if mode == "percentage"
                                                           else " — e.g. 12 = +12, -5 = -5"),
        value=0.0, step=1.0, format="%.2f", key="pma_value")
    _names = [r.get("name") or r.get("id") for r in routes]
    _chosen_names = st.multiselect("Which transports does this apply to?", _names,
                                   default=_names, key="pma_scope")
    scoped_routes = [r for r, n in zip(routes, _names) if n in _chosen_names]

    if not scoped_routes or abs(value) < 0.005:
        st.caption("Pick at least one transport and a non-zero value to see a preview.")
        return

    proposals = price_refresh.build_manual_adjustment_proposals(scoped_routes, mode, value)
    st.session_state.pma_proposals = proposals

    accepted = [p for p in proposals if p.get("accepted")]
    blocked = [p for p in proposals if p.get("blocked")]
    st.caption(f"{len(accepted)} route(s) would change · "
              f"{len(proposals) - len(accepted) - len(blocked)} unaffected (already zero-value change) · "
              f"{len(blocked)} blocked.")
    if blocked:
        st.error(f"❌ {len(blocked)} blocked:")
        for p in blocked:
            st.write(f"- **{p['name']}**: {p['blocked']}")
    if accepted:
        with st.expander(f"✅ {len(accepted)} route(s) — preview old → new", expanded=True):
            for p in accepted:
                st.write(f"- **{p['name']}**: {p['old']} → {p['new']}")

    st.subheader("3 — Apply")
    st.warning(f"This changes the vehicle/base price on **{len(accepted)} live transport(s)** for "
              f"supplier {supplier_id}. Nothing else is touched — no option, no supplement, no "
              f"modality, no validity date.")
    if st.button(f"🚀 Update {len(accepted)} transport(s)", type="primary",
                 disabled=not accepted, key="pma_apply"):
        bar = st.progress(0.0, text="Updating…")

        def _tick2(done, total, name):
            bar.progress(min(done / max(total, 1), 1.0), text=f"Updating {name} ({done}/{total})")

        st.session_state.pma_result = price_refresh.apply_manual_adjustments(
            client, supplier_id, proposals, progress=_tick2)
        bar.empty()
        st.rerun()

    result = st.session_state.get("pma_result")
    if result:
        if result["updated"]:
            st.success(f"✅ {len(result['updated'])} transport(s) repriced.")
            for u in result["updated"]:
                st.write(f"- {u['name']}: {u['old']} → {u['new']}")
                _dbg = u.get("debug")
                if _dbg:
                    with st.expander(f"🔍 Raw request/response for {u['name']} (debug)"):
                        st.caption("Transport (parent) request body:")
                        st.json(_dbg["transport_request"])
                        st.caption("Transport (parent) response:")
                        st.json(_dbg["transport_response"])
        if result["failed"]:
            st.error(f"❌ {len(result['failed'])} failed:")
            for f in result["failed"]:
                st.write(f"- **{f.get('name')}**: {f.get('detail')}")
                if f.get("debug"):
                    with st.expander(f"🔍 Raw request/response for {f.get('name')} (debug)"):
                        st.json(f["debug"])
        if st.button("🆕 Start again", key="pma_new"):
            for key in ("pma_routes", "pma_proposals", "pma_result"):
                st.session_state.pop(key, None)
            st.rerun()


def render_transport_price_consistency_flow(client, supplier_id):
    """CONFIRMED REAL REQUEST (product owner, 2026-09-11 - see
    price_refresh.transport_price_consistency_report's own docstring for the full transcript):
    "can the app help then, to identify price errors at least for contract transport... once a
    year we must review the new prices and we must identify price differences for the transport
    modalities... No upload from the app, but the app must be able to help and to calculate what
    the modalities usually must be." Deliberately READ-ONLY - there is no accept/apply step here
    at all, unlike every other Transport flow in this app. It only loads this supplier's live
    transports and ranks routes by how far their own modality supplement sits from what this
    SAME supplier's other routes suggest is normal, so a human can go fix anything that looks
    wrong directly in Travel Compositor (or with the manual adjustment / rate-sheet flows above)."""
    st.caption("Loads this supplier's live transports and compares each modality's price "
              "supplement against what this SAME supplier's OTHER routes usually charge for that "
              "same passenger bracket. Nothing is written — this only helps you spot a route "
              "whose numbers look out of line before your yearly review.")
    if st.button("🔍 Load transports", type="primary", key="pcc_load"):
        bar = st.progress(0.0, text="Loading transports from Travel Compositor…")

        def _tick(done, total, name):
            bar.progress(min(done / max(total, 1), 1.0), text=f"Reading {name} ({done}/{total})")

        routes, err = price_refresh.load_supplier_products(
            client, supplier_id, price_refresh.KIND_TRANSPORT, progress=_tick)
        bar.empty()
        if err:
            st.error(f"Couldn't read this supplier's transports: {err}")
        elif not routes:
            st.warning("This supplier has no transports yet.")
        else:
            st.session_state.pcc_routes = routes
            st.rerun()

    routes = st.session_state.get("pcc_routes")
    if not routes:
        return
    st.success(f"{len(routes)} transport(s) loaded for supplier {supplier_id}.")

    report = price_refresh.transport_price_consistency_report(routes)
    if not report:
        st.info("Nothing to compare — either every transport here has only one passenger "
                "bracket, or no bracket signature (e.g. \"1–8 pax\") appears on more than one "
                "route for this supplier.")
        return

    df = pd.DataFrame([{
        "Route": r["route_name"], "Modality": r["option_name"] or r["option_code"],
        "Pax": f"{r['bracket'][0]}-{r['bracket'][1]}",
        "Vehicle price": r["vehicle_price"], "Modality price": r["modality_price"],
        "This route's supplement": r["supplement"],
        "Supplier's typical supplement": r["typical_supplement"],
        "Deviation": r["deviation"],
        "Deviation %": r["deviation_pct"] if r["deviation_pct"] is not None else "",
        "Sample size": r["sample_size"],
    } for r in report])
    st.caption(f"{len(df)} route/modality combination(s) had another route on the same "
              f"passenger bracket to compare against — ranked by |deviation|, biggest first. "
              f"No hard cutoff: a year of real distance/fuel variation is normal, so use judgement.")
    st.dataframe(df, use_container_width=True, hide_index=True)

    if st.button("🆕 Start again", key="pcc_new"):
        st.session_state.pop("pcc_routes", None)
        st.rerun()


def _reset_to_tool_chooser():
    """Full reset back to Step 0. Keeps only the cached API client and supplier list, so
    switching tools doesn't force a re-login or re-fetch, but no half-finished state from one
    tool can leak into the other."""
    for key in list(st.session_state.keys()):
        if key not in ("client", "suppliers_cache"):
            del st.session_state[key]
    st.session_state.active_tool = None
    st.session_state.product_type = None
