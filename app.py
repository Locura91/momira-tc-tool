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
    - document_reader.py  (PDF/Word/Excel/PowerPoint -> raw text)
    - ai_extractor.py     (raw text -> structured English data)
    - web_extractor.py    (URL -> structured data, incl. destination scanning)
"""
# CONFIRMED REAL PRODUCTION BUG (2026-09-16, Streamlit Cloud deploy): Streamlit's script runner
# executes this file as the top-level script (registered in sys.modules under its own runner
# name, e.g. "__main__"), NOT as an importable module named "app". Every flows/*.py file split
# out by the Phase 1 refactor does `from app import (...)` to pull back names still defined here
# - under a plain `python -c "import app"` that's harmless (Python's import machinery registers
# sys.modules["app"] BEFORE executing this file's body, precisely to support this exact
# circular-import shape). Under Streamlit, no such "app" entry exists yet, so the FIRST
# `from app import (...)` anywhere in flows/*.py instead triggers a brand-new, independent
# import of this entire file under the name "app" - which starts re-executing app.py from the
# top while the original run is still mid-way through, and that second run re-enters the same
# `from flows.<module> import ...` line whose module is already (from the first run) stuck
# partway through its own `from app import (...)` - raising exactly "ImportError: cannot import
# name '<flow_function>' from partially initialized module 'flows.<module>' (most likely due to
# a circular import)". Fix: alias "app" to whatever module this file is ACTUALLY running as, so
# every `from app import (...)` elsewhere resolves against the SAME live namespace instead of
# kicking off a second execution. Must run before any of this file's own `from app_helpers
# import (...)` or `from flows.<module> import ...` lines - hence right at the top.
import sys as _sys
if __name__ in _sys.modules and "app" not in _sys.modules:
    _sys.modules["app"] = _sys.modules[__name__]

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
from builder import build_ticket_voucher_remarks_only_update
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
import draft_autosave


from app_helpers import (
    _dmy_date_field,
    ALL_WEEKDAYS,
    _warn_page_image_upload_errors,
    _warn_stale_images,
    _apply_min_pax_guaranteed_departure_note,
    SHARED_WIDGET_STATE_PREFIXES,
    _geo_search_default,
    CURRENCY_OPTIONS,
    TICKET_ACTION_LABELS,
    TICKET_CREATE_ACTION_KEYS,
    TICKET_ACTION_FIELDS,
    ACTION_LABELS,
    CLOSEDTOUR_CREATE_ACTION_KEYS,
    ACTION_FIELDS,
    _data_fingerprint,
    _fetch_url_text_safe,
    _clean_modality_code,
    _modality_code_suspicious,
    _reset_mct_state,
    _new_mct_tour,
    _PUBLISH_ERROR_PATTERNS,
    _LEGACY_TOUR_STEP_NAMES,
    _LEGACY_TICKET_STEP_NAMES,
    _publish_error_guidance,
    _VALIDATION_SECTION_LABELS,
    _VALIDATION_FIELD_LABELS,
    _PYDANTIC_PATH_RE,
    _describe_validation_path,
    humanise_validation_error,
    _extract_error_message_detail,
    _REJECTED_IMAGE_URL_RE,
    _extract_rejected_image_url,
    show_publish_error,
    remember_memory_panel,
    render_memory_panel_footer,
    render_house_rules,
    render_learned_instructions,
    reset_stale_editable_field_widgets,
    new_widget_token,
    bump_widget_generation,
    widget_generation,
    _tk_clear_geo_confirmation,
    _mt_clear_geo_confirmation,
    flow_widget_key,
    _stamp_proposal_widget_tokens,
    render_publish_blockers,
    render_supplement_zero_price_notes,
    reset_child_age_band_widgets,
    floor_start_date_for_new_data,
    apply_clarify_changes,
    render_clarify_result,
    TICKET_LANGUAGE_OPTIONS,
    render_ticket_language_options,
    get_existing_tour_names,
    get_existing_ticket_codes,
    get_existing_ticket_modality_codes,
    check_modality_code_availability,
    render_modality_code_availability_check,
    get_existing_hotel_names,
    _UPDATE_REFRESH_RECENTS_NAMESPACE,
    _UPDATE_REFRESH_RECENTS_MAX,
    _remember_update_refresh_pick,
    _recent_update_refresh_picks,
    check_duplicate_tour_name,
    check_code_availability,
    mark_code_as_taken,
    render_code_availability_check,
    _diff_tour_price_list,
    _map_fetched_supplements,
    _map_fetched_tour_to_data,
    _map_fetched_ticket_to_data,
    _merge_extraction_over_baseline,
    render_tour_update_comparison,
    _diff_ticket_option_pricing,
    render_ticket_update_comparison,
    _summarize_modality_pricing,
    render_modalities_review,
    _clear_batch_widget_state,
    FORCE_ALL_ROUTES_HINT,
    render_detection_diagnosis,
    render_empty_detection_retry,
    _swapped_label,
    ensure_return_candidates,
    render_candidate_filter,
    with_learned_guidance,
    clarify_supplier_id,
    remember_clarification,
    HOUSE_RULE_CODEWORD,
    render_house_rule_shortcut,
    seed_transport_from_candidate,
    render_batch_bulk_controls,
    render_skip_item_button,
    fetched_tour_matches_code,
    try_code_variants,
    _mtu_fetch_live_ticket,
    _mtu_resolve_modality_name,
    _mtu_clear_geo_confirmation,
    render_direction_image_section,
    _hp_dist_to_str,
    _hp_str_to_dist,
    _hp_nums_to_str,
    _hp_str_to_nums,
    _hp_names_to_str,
    _hp_str_to_names,
    _hp_first_window,
    _hp_window_list,
    _render_hotel_masterdata_step,
    render_hotel_automap_review,
    _render_hotel_price_audit_section,
    UPDATE_REFRESH_SERVICE_TYPES,
    render_update_refresh_flow,
    _ur_pick_momira_supplier,
    render_cancellation_bulk_flow,
    _ur_gather_text_optional,
    _suggest_coded_service_matches,
    _render_update_refresh_coded_service,
    render_transport_manual_adjustment_flow,
    render_transport_price_consistency_flow,
    _reset_to_tool_chooser,
)


# Widget-key generations - the defence against a widget showing a PREVIOUSLY-reviewed item's
# value. See widget_state.py's module docstring for the bug class and why it replaces sweeping.
import widget_state
from builder import (build_hotel_contract_payload, resolve_room_provider_codes, build_hotel_offer_payloads,
                     build_hotel_supplement_payloads, build_hotel_rate_payloads)
from document_reader import extract_raw_text, extract_images
from document_reader import scanned_document_warning as document_reader_scanned_warning
from price_audit import run_hotel_price_audit, compare_price_audit_to_extraction, summarize_findings
from ai_extractor import extract_structured_data, extract_option_only_data, extract_modality_data, detect_tour_variants, detect_multiple_modalities, apply_clarification, extract_ticket_data, extract_ticket_option_only_data, detect_ticket_variants, friendly_error_message, detect_transfer_products, extract_transfer_data, extract_ticket_main_info, extract_ticket_modality_data, detect_ticket_modalities
from ai_extractor import detect_transport_products, extract_transport_data, detect_hotel_products, extract_hotel_data
# Deterministic (non-AI) bulk importer for FTS's own "TRANSFER MATRIX" CSV format - see
# fts_transfer_matrix.py's module docstring for why this bypasses the AI pipeline entirely
# (271 routes/file overflowed AI extraction's token limit) and is scoped to this one supplier.
from fts_transfer_matrix import (build_fts_matrix_candidates, classify_fts_matrix_file,
                                 match_fts_candidates_to_existing, publish_fts_candidate)
from ai_extractor import check_ticket_content_drift
from ai_extractor import min_pax_guaranteed_departure_note, min_pax_forces_on_request
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
    render_child_discount_editor, render_duration_editor, is_active_supplier,
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
from r2_client import stale_image_warning
from geocoding_client import geocode_search, geocode, parse_google_maps_url, build_place_query
import transfer_matcher
import supplier_migration
import masterdata_store
import hotel_automap
import masterdata_matcher
import price_validity
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

# CONFIRMED (2026-09-06): moved into image_dimensions.py as the single source of truth, so the
# too-small-image fallback (see builder.py's ensure_images_meet_minimum_size) and this app's own
# "no real image picked" fallback can never drift apart into two different placeholder URLs.
from image_dimensions import FALLBACK_IMAGE








# Session-state key prefixes used ONLY by the shared editable_field/
# editable_table widget helpers - never by any flow's own phase/queue
# control state (mct_phase, mm_queue, tk_..., etc. never start with any of
# these). Safe to sweep-clear in bulk: see _clear_batch_widget_state's
# docstring for why this is needed (positional-index widget keys getting
# reused by a different queue item after a skip or a fresh batch start).



# Currency dropdown options - EUR/USD first as the ones actually used in
# practice, then the rest of the common ISO codes a DMC document might quote
# in, alphabetically. A dropdown (instead of free-text) prevents typos like
# "EURO" or "Eur" that Travel Compositor's API would otherwise reject or
# silently mishandle.

# CONFIRMED PRODUCT-OWNER REDESIGN (2026-09-10): "in Step 1, When creating a new product the
# app shall only allow 'Create new SERVICE + 1 Modality' and 'Add new Modality to existing
# SERVICE'. Nr. 3 and 4 and 5 must be removed from this points, as this is more Updating
# existing product. We must define between create new service and Price update to existing Products."
# render_ticket_flow is ONLY reachable via "📦 Create a new product -> Ticket" - its own Step 2
# radio must offer just these two create-only actions; the update actions (3/4/5) stay defined
# in TICKET_ACTION_LABELS above (still needed for the summary label after a pre-set action, and
# by "Price update to existing Products", which reaches update_ticket/update_option/update_tickets_batch
# through its own entry points instead - see _render_update_refresh_coded_service and
# render_update_refresh_flow's Ticket branch).

# Same create/update split as TICKET_CREATE_ACTION_KEYS above, same product-owner request -
# the generic Step 2 radio below (reached ONLY via "Create a new product -> ClosedTour") must
# offer just these two; update_tour/update_option stay reachable via "Price update to existing Products"
# (_render_update_refresh_coded_service already excludes "create" from ACTION_LABELS there).





# editable_table / editable_field / render_stop_sales_editor / render_cancellation_policy_editor /
# render_seasonal_price_editor / render_readonly_source / render_optional_time_input /
# _clean_time_table_rows / _safe_cell_str / _safe_float / _safe_int / the HTML<->plain-text
# converters - all moved to ui_components.py (imported at the top of this file) so every one of
# the five product-type flows shares exactly one implementation. See ui_components.py's
# module docstring for why.















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

# Real numbered-step labels, ONLY for the two flows that actually have them
# (the legacy single-tour and single-ticket flows - "Step 1" through "Step
# 7"). The newer queue-based flows (mct/mt/tk multi-item flows) don't have
# numbered steps at all, so they fall back to flow-agnostic phrasing below
# rather than a made-up step number.




# Pydantic validation errors name the SCHEMA's own field path, e.g.
# "priceList.0.price.triplePrice.amount". That is exactly the information a human
# needs - which row, which column - but written in a language nobody in this office
# speaks. CONFIRMED REAL COMPLAINT (product owner, ASW-6): the screen said "review/edit
# that field" without ever naming the field. The two maps below turn the path back into
# the words that appear on the actual editing screen.








# CONFIRMED REAL BUG (reported 2026-09-06, HRG-H1): even after the in-tool 500x400 size check
# (image_dimensions.py), Travel Compositor still rejected a DIFFERENT picked image outright with
# "Not valid image. Image: '<url>'" - a rejection this tool has no way to predict client-side
# (the image measured fine locally; whatever Travel Compositor's own fetch/validation didn't like
# about it isn't something a pixel-dimension check can catch in advance). Both this and the
# 500x400 message end with the exact rejected URL in the same `Image: '...'` shape, so rather
# than trying to guess every possible server-side image rule in advance, the Hotel publish button
# below now reads this pattern out of a rejection and retries with that one image removed - see
# the retry loop around client.create_hotel/update_hotel.














# SUPPLEMENT_COLUMNS / render_closedtour_supplements / render_child_age_band - moved to
# ui_components.py, same reason as the blocks above.




























# EN first (the base language every ticket has by default), then the same 19 codes the
# Translation tool already offers, so there's one shared list of language codes across the app
# rather than two that could drift apart.
# LANGUAGE_CODE_NAMES now lives in builder.py (imported above) - it's also the source for the
# "You can choose between X-speaking Guide or Y-speaking Guide" Includes line build_ticket_
# payloads writes for a multi-language Modality, so there's exactly one code->name mapping
# instead of two that could quietly drift apart.




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
# RETIRED (2026-09-15, CONFIRMED PRODUCT-OWNER DECISION): "When Ticket creation and Supplement
# says: Needs own Modality, we can ignore that information - we want to make the app simple and
# handy for humans in the future." The "Needs own Modality?" checkbox (and the 2026-08-25
# exclude-from-publish behaviour it drove) is gone - the table is the one place to enter every
# priced extra, and every row published onto this Modality's price, no exceptions.














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




































# When detection comes back empty, this is the instruction the "detect again" button sends.
# Deliberately blunt: the operator has looked at the document and said these ARE transports,
# so the model's own Transfer-vs-Transport judgement is the thing being overruled.






































from flows.multi_modality import render_multi_modality_flow


from flows.multi_ticket import render_multi_ticket_flow, render_multi_ticket_update_flow


from flows.multi_tour import render_multi_tour_flow


from flows.ticket import render_ticket_flow


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





from flows.multi_transfer import render_multi_transfer_flow

# CONFIRMED PRODUCT-OWNER REQUEST (2026-09-17) - see TRANSFER_DUPLICATE_AND_CREATE_CHOICE's own
# comment further down for the full reasoning: ONE combined Step 1 destination for both the
# automated missing-reverse-direction scan and the manual duplicate-by-id flow, sharing a single
# supplier picker (flows/transfer_duplicate_and_create.py). render_duplicate_transfer_flow and
# render_missing_transfers_flow themselves are kept importable for their own test suites even
# though app.py no longer wires them as separate Step 1 destinations.
from flows.transfer_duplicate_and_create import render_transfer_duplicate_and_create_flow

# CONFIRMED PRODUCT-OWNER REQUEST (2026-09-22) - see DUPLICATE_TRANSPORT_CHOICE's own comment
# further down for the full reasoning: the same combined "scan for missing reverse-direction
# routes + manual duplicate-by-id" Step 1 destination Transfer already has
# (flows/transport_duplicate_and_create.py). render_duplicate_transport_flow and
# render_missing_transports_flow themselves are kept importable for their own test suites even
# though app.py no longer wires them as separate Step 1 destinations.
from flows.transport_duplicate_and_create import render_transport_duplicate_and_create_flow


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
from flows.fts_matrix import render_fts_matrix_import_flow




from flows.multi_transport import render_multi_transport_flow


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






















from flows.hotel import render_hotel_flow


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
from flows.manual_information import render_manual_information_flow








from flows.supplier_migration import render_supplier_migration_flow







from flows.cancellation import render_transport_cancellation_bulk_flow, render_generic_cancellation_bulk_flow












from flows.price_refresh import render_price_refresh_flow, render_ticket_price_refresh_flow


st.set_page_config(page_title="Momira Travel Platform", layout="wide")

# Slightly larger base font app-wide for readability. Streamlit's own CSS is
# built almost entirely on rem units, so scaling the ROOT font-size (rather
# than hunting down individual elements) cleanly scales text, inputs,
# buttons, tables etc. together without breaking any layout - 106% takes the
# default 16px browser base up to ~17px.
st.markdown("<style>html { font-size: 106%; }</style>", unsafe_allow_html=True)

# CONFIRMED REAL PRODUCT-OWNER REQUEST (2026-09-17): "if the human reloads the page all
# information is gone ... general issue" across every flow, not just one. Runs BEFORE any
# flow-specific state is touched below, so a returning tab either gets its unfinished work back
# (Restore/Discard banner, then st.stop()s until the human picks) or, on a clean run, quietly
# keeps re-saving the current session_state as it goes - see draft_autosave.py's own docstring.
draft_autosave.restore_and_autosave()

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

BUILD_VERSION = "2026-09-23-images-auto-used-closedtour-and-ticket"

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

# CONFIRMED PRODUCT-OWNER REQUEST (2026-09-08): a "(YYYYMMDD)" code in a Ticket/Transfer/
# Transport's Voucher Remarks (Transport: Description) states how long a supplier's prices are
# confirmed valid for - see price_validity.py's own docstring. Once a week, scan every live
# service for that code and flag anything expired or expiring within 60 days, both as an in-app
# banner here and as an email (product owner confirmed both channels, 2026-09-08). Same "no real
# background cron on Streamlit Cloud" solution as weekly_review.py just above - checked
# opportunistically on page load rather than on an actual schedule.
if price_validity.is_due():
    with st.spinner("Checking price-validity dates on live services..."):
        if st.session_state.suppliers_cache is None:
            try:
                st.session_state.suppliers_cache = client.get_all_suppliers()
            except Exception:
                st.session_state.suppliers_cache = []
        _pv_suppliers = [
            s for s in (st.session_state.suppliers_cache or [])
            if (s.get("commercialName") or s.get("legalName") or "").strip().lower().startswith("momira_") and is_active_supplier(s)
        ]
        _pv_flagged = price_validity.scan_expiring_services(client, _pv_suppliers)
        _pv_email_result = price_validity.send_alert_email(_pv_flagged)
        price_validity.mark_reviewed(flagged_count=len(_pv_flagged))

    if _pv_flagged:
        with st.container(border=True):
            st.markdown("### ⏰ Weekly price-validity check")
            st.caption("Each of these services has a \"(YYYYMMDD)\" price-validity code (in Voucher "
                      "Remarks, or Description for Transport) that's already past or due within 60 "
                      "days - go back to the supplier for confirmed pricing and update the service.")
            for _f in _pv_flagged:
                _status = (f"⚠️ expired {abs(_f['days_remaining'])} day(s) ago" if _f["days_remaining"] < 0
                           else f"expires in {_f['days_remaining']} day(s)")
                st.markdown(f"- **[{_f['product_type']}] {_f['supplier_name']} / {_f['code']}** "
                           f"“{_f['label']}” — valid until {_f['valid_until'].strftime('%d/%m/%Y')} ({_status})")
            if not _pv_email_result["ok"]:
                st.caption(f"(Email digest to {price_validity.alert_recipient()} failed to send: "
                          f"{_pv_email_result['error']} — the list above is still accurate.)")
            else:
                st.caption(f"Also emailed to {price_validity.alert_recipient()}.")

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
TOOL_UPLOAD = "📤 Create & Update Products"
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
# Follow-up checklist rather than a tool: hotels published from here that still need "Automap
# with master" set by hand in Travel Compositor's back office. Deliberately NOT given a permanent
# card on the home screen - it only appears when there is actually something on it (see the
# conditional block further down), because a checklist that shows "0 items" every day is one
# people stop reading. See hotel_automap.py for why this can't be automated away.
TOOL_HOTEL_AUTOMAP = "🔗 Hotels awaiting automap"

# A Step 1 destination that is not a product type. It sits in the same list because that is
# where a person looks when they have something to record about a supplier, even though
# nothing is being uploaded.
MANUAL_INFO_CHOICE = "Adding manual information to Product"
# Update-only price refresh. A Step 1 destination rather than a product type, because the
# product list comes from Travel Compositor rather than from the document. Kept as the
# constant price_refresh.py's own code compares against internally (KIND_TRANSPORT/
# KIND_TRANSFER) - the STEP 1 button that used to say this is gone, replaced by
# UPDATE_REFRESH_CHOICE below, which folds price refresh in as one branch among five.
PRICE_REFRESH_CHOICE = "Refresh prices (update only)"
# CONFIRMED PRODUCT-OWNER REDESIGN (2026-08-12): the ONE place every kind of update/refresh
# happens now, for all five product types - see render_update_refresh_flow's docstring.
UPDATE_REFRESH_CHOICE = "Price update to existing Products"
# CONFIRMED REAL NEED (product owner, 2026-08-24, Transfer only; extended to all 5 product
# types 2026-09-10): "mass change the supplier - all Transfers from supplier A must now be
# changed to supplier B." ... "this is not only for the transfer section, it must work for all
# services." A Step 1 destination rather than living inside Update/Refresh, since it acts on a
# whole supplier's worth of one product type at once, not one already-identified record - see
# render_supplier_migration_flow's docstring.
MIGRATE_SUPPLIER_CHOICE = "Move a Supplier's Services to another Supplier"
# CONFIRMED REAL NEED (product owner, 2026-08-28; extended to all 5 product types 2026-09-10):
# "can i also include/change the cancellation for a bulk or at least per supplier for
# transports?" ... "bulk update cancellation policy --> this must be usable for all Services:
# Hotel; Transfer, Transport, Ticket and ClosedTour." A Step 1 destination rather than living
# inside Update/Refresh, since it acts on a whole supplier's worth of one product type at once,
# not one already-identified record - see render_cancellation_bulk_flow's docstring.
CANCELLATION_BULK_CHOICE = "Bulk-update Cancellation Policy"
# CONFIRMED PRODUCT-OWNER REQUEST (2026-09-16, combined 2026-09-17): "when human create a new
# transfer or transport, could the app simple copy the product and just swap the destinations?"
# 2026-08-12's redesign removed AI-document creation for Transfer/Transport entirely ("Transfer
# and Transport are not possible to automatically Import/upload"), leaving no way at all in this
# app to create a brand-new Transfer - only price_refresh.py's update-existing-only flow
# remained. The natural next step, once that duplicate-by-id path was proven out: "Goal with the
# duplicate must be, that humans create one way transfers, then the app must be controlled by
# human and human adds the supplier as usually, the app checks is there are missing transfers
# and then provides a list with all possible missing transfers." And then, 2026-09-17, combining
# the two: "the transfer duplicate section shall be combined with transfer find & create. We
# must put them together. Long term Goal for this section is, that human selects the correct
# supplier, the app checks all transfers and identifies missing duplicates in a list for
# example. Then the human reviews all possible duplicates and can say 'select all' or 'select
# none' for auto creation." ONE Step 1 "Create a new product" destination now covers both: pick
# a supplier once, the app scans and lists every missing reverse-direction route with select-
# all/select-none batch creation (the primary path), with the manual "duplicate one specific
# Transfer by id" flow kept underneath for a record the automatic scan doesn't catch - see
# flows/transfer_duplicate_and_create.py's own docstring.
TRANSFER_DUPLICATE_AND_CREATE_CHOICE = "Transfer (duplicate & create missing reverse-direction transfers)"
# CONFIRMED PRODUCT-OWNER REQUEST (2026-09-16), same day as the Transfer duplicate flow above:
# "can we do the same for Transport. Changing the Destination of the original Transport ID,
# adopting the Name and adopting the Description." Transport has the same missing-create-path
# problem as Transfer did - see builder.build_transport_swap_payload's own docstring for the
# swap logic (and why it needs an extra api_client lookup Transfer's version doesn't).
# CONFIRMED PRODUCT-OWNER REQUEST (2026-09-22, verbatim): "when duplicating transfer, I can
# select the supplier and then direct I can scan this supplier for missing transfers - that
# should be exactly the same for transport. currently I can only dubilicate one transpport at
# the time, but that is not practical." Transport now gets the exact same combined shape Transfer
# already has - pick a supplier once, scan for missing reverse-direction routes with select-all/
# select-none batch creation (the primary path), with the manual duplicate-by-id flow kept
# underneath for a record the automatic scan doesn't catch - see
# flows/transport_duplicate_and_create.py's own docstring.
DUPLICATE_TRANSPORT_CHOICE = "Transport (duplicate & create missing reverse-direction transports)"

if "active_tool" not in st.session_state:
    st.session_state.active_tool = None
if "product_type" not in st.session_state:
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
    # REMOVED (product owner, 2026-09-16): "this information is useless now as we map the hotels
    # differently. The hint can be deleted." Since hotels are now created directly in Travel
    # Compositor via "New hotel using master data" (setting automap correctly at creation - see
    # claude/hotel-automap-no-retroactive-mapping-confirmed-2026-09-16.md) and this app only adds
    # pricing/inventory to the already-existing record afterward (see builder.py's "UPDATE
    # PRIORITY FLIP"), the create-here-then-remember-to-automap-later failure mode this checklist
    # existed for no longer happens in normal use. hotel_automap.py itself, and the review screen
    # it feeds (TOOL_HOTEL_AUTOMAP), are left in place rather than deleted - any pending/dismissed
    # entries already on record stay visible if that screen is ever reopened another way - only
    # this home-screen banner+button entry point is gone, so it no longer competes for attention
    # in the normal day-to-day workflow.
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

# ---- Hotels awaiting automap: a follow-up checklist, not a product flow. Reads and writes only
# this app's own durable store - never Travel Compositor (the automap it tracks cannot be set
# through the API at all; see hotel_automap.py). ----
if st.session_state.active_tool == TOOL_HOTEL_AUTOMAP:
    render_hotel_automap_review(client)
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
        if st.button(TRANSFER_DUPLICATE_AND_CREATE_CHOICE, key="pt_choice_transfer_duplicate_and_create", use_container_width=True):
            st.session_state.product_type = TRANSFER_DUPLICATE_AND_CREATE_CHOICE
            st.rerun()
        st.caption("For a brand-new Transfer that's really the SAME route in the other "
                  "direction (e.g. Hotel → Airport once Airport → Hotel already exists). Pick a "
                  "supplier: the app scans every live Transfer and lists every route with no "
                  "reverse-direction pair yet, so you can Select all/Select none and create "
                  "them as a batch - or duplicate one specific Transfer by id instead.")
        if st.button(DUPLICATE_TRANSPORT_CHOICE, key="pt_choice_duplicate_transport", use_container_width=True):
            st.session_state.product_type = DUPLICATE_TRANSPORT_CHOICE
            st.rerun()
        st.caption("Same idea, for Transport. Pick a supplier: the app scans every live "
                  "Transport and lists every route with no reverse-direction pair yet, so you "
                  "can Select all/Select none and create them as a batch (parent record AND "
                  "every occupancy bracket, route swapped) - or duplicate one specific "
                  "Transport by id instead.")

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
        st.caption("Recreates a supplier's services under a different supplier and retires the "
                  "originals - for when a supplier relationship itself changes, not a single "
                  "product's details. ClosedTour · Ticket · Transfer · Transport · Hotel.")
        if st.button(CANCELLATION_BULK_CHOICE, key="pt_choice_ctbulk", use_container_width=True):
            st.session_state.product_type = CANCELLATION_BULK_CHOICE
            st.rerun()
        st.caption("Applies one cancellation policy to every (or a chosen subset of) one "
                  "supplier's live services of one type at once - for when the supplier's "
                  "terms themselves changed, not a single product's details. "
                  "ClosedTour · Ticket · Transfer · Transport · Hotel.")
    st.stop()

if st.session_state.product_type == UPDATE_REFRESH_CHOICE:
    render_update_refresh_flow(client)
    st.stop()

if st.session_state.product_type == CANCELLATION_BULK_CHOICE:
    render_cancellation_bulk_flow(client)
    st.stop()

if st.session_state.product_type == MANUAL_INFO_CHOICE:
    render_manual_information_flow(client)
    st.stop()

if st.session_state.product_type == MIGRATE_SUPPLIER_CHOICE:
    render_supplier_migration_flow(client)
    st.stop()

if st.session_state.product_type == TRANSFER_DUPLICATE_AND_CREATE_CHOICE:
    render_transfer_duplicate_and_create_flow(client)
    st.stop()

if st.session_state.product_type == DUPLICATE_TRANSPORT_CHOICE:
    render_transport_duplicate_and_create_flow(client)
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
        # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-03): "if I start a new batch for creating a
        # new service, please allow to change the currency as this can be always vary" - a
        # different supplier/action picked here can easily mean a different currency, so the
        # Step 3 lock (see "Locked - a currency, once set, cannot be changed" below) must not
        # carry over from whatever was last confirmed.
        st.session_state.pop("cfg_currency", None)
        st.rerun()
else:
    # Create-only here (product owner, 2026-09-10) - this screen is ONLY reached via
    # "Create a new product -> ClosedTour" (every other product_type value st.stop()s before
    # this point). Updating an existing ClosedTour now lives exclusively under "Update existing
    # Service" - see CLOSEDTOUR_CREATE_ACTION_KEYS.
    action_key = st.radio(
        "Choose one:",
        list(CLOSEDTOUR_CREATE_ACTION_KEYS),
        format_func=lambda k: ACTION_LABELS[k],
        help="Creating makes something brand-new; adding a Modality extends one that already "
             "exists. To update anything else, use \"Price update to existing Products\" instead.",
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
            if (s.get("commercialName") or s.get("legalName") or "").strip().lower().startswith("momira_") and is_active_supplier(s)
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

    # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-17): "If we select the supplier and if we select
    # the ClosedTour Code, we just want to add a new Modality, regardless what is already
    # online." add_option no longer fetches/compares against the tour's current live state at
    # all - Currency is asked directly above (see ACTION_FIELDS's own comment), and the new
    # Modality simply gets added under whatever code was typed. The "Check what's already
    # online" button/results below (existing modality codes, live pricing lookup) stay available
    # for update_tour/update_option, which genuinely need to inherit live data.
    if "existing_tour_code" in needed and action != "add_option":
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
    # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-17): "add_option" removed from this gate - it no
    # longer requires (or even offers) the "Check what's already online" fetch at all, see
    # ACTION_FIELDS's own comment. Still gated by "existing_tour_code"/"currency" being filled in
    # (the generic required_ok checks above already cover both, now that "currency" is in
    # add_option's own ACTION_FIELDS list).
    if action in ("update_tour", "update_option") and not fetched_tour_matches_code(existing_tour_code_in):
        required_ok = False
        st.info("Click 'Check what's already online for this code' above first (or again, if you "
               "changed the code) - this fetches the existing tour's Currency (and for updates, "
               "Min/Max Pax too) so you don't have to re-enter them.")

    if st.button("➡️ Continue to Step 4", type="primary", disabled=not required_ok):
        if action == "update_tour":
            min_pax_in = st.session_state.get("fetched_tour_min_pax") or 1
            max_pax_in = st.session_state.get("fetched_tour_max_pax") or 9
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
# CONFIRMED PRODUCT-OWNER REQUEST (2026-09-17): "add_option" removed from both branches below -
# it no longer fetches the live tour at all (see ACTION_FIELDS's own comment), so there is
# nothing to warn about or blend in here; its own currency comes straight from cfg_currency
# (the Step 3 selectbox), already set above.
if action in ("update_tour", "update_option") and not fetched_tour_matches_code(existing_tour_code):
    st.warning("⚠️ The tour data fetched by 'Check what's already online' doesn't match this "
              "tour's code (or was never fetched / failed) - go back to Step 3 and re-check "
              "before continuing, to avoid publishing with another tour's currency, pax limits, "
              "or code.")
if action in ("update_tour", "update_option") and fetched_tour_matches_code(existing_tour_code):
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
    type=["pdf", "docx", "xlsx", "pptx", "csv"], accept_multiple_files=True
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
                    # Only fills in when this document (and, for an update, the live baseline
                    # it was just merged over) had no cancellation terms of its own - see
                    # apply_cancellation_link_default's docstring.
                    st.session_state.ct_cancellation_link_scope = cancellation_links.apply_cancellation_link_default(
                        data, supplier_id, "ClosedTour")
                    reset_child_age_band_widgets("ct")
                    sources_desc = " + ".join(filter(None, [url] + doc_names))
                    st.session_state.raw_preview = f"Source(s): {sources_desc}\n\n{raw_text}"
                    st.session_state.payloads = None
                    _warn_page_image_upload_errors(_add_page_images_to_doc_pool(url, doc_raw_images, doc_image_urls))
                    st.session_state.doc_raw_images = doc_raw_images
                    st.session_state.hosted_image_candidates = list(dict.fromkeys(doc_image_urls))
                    # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-23, verbatim): "...I cannot
                    # automatically use the images... at least the images are being detected...
                    # but i cannot automatically use them for my closedtours and neither for my
                    # tickets." Every URL in hosted_image_candidates is already a verified,
                    # R2-hosted image (uploaded AND public-URL-verified inside
                    # _add_page_images_to_doc_pool/upload_images_with_errors before it ever
                    # reaches this list - see r2_client.verify_public_url) - nothing left for a
                    # human to confirm, so fold it straight into image_urls (and the text area
                    # that drives it) instead of waiting for a manual tick-and-"Add selected"
                    # click. The "Images found" section below now only confirms what was
                    # auto-added, it no longer gates on a click.
                    auto_images = list(dict.fromkeys(
                        [u for u in data.get("image_urls", []) if u] + st.session_state.hosted_image_candidates))
                    data["image_urls"] = auto_images or [FALLBACK_IMAGE]
                    st.session_state.images_text_value = "\n".join(auto_images)
                    st.session_state.extracted = data
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

                st.session_state.ct_cancellation_link_scope = cancellation_links.apply_cancellation_link_default(
                    data, supplier_id, "ClosedTour")
                reset_child_age_band_widgets("ct")
                st.session_state.raw_preview = preview
                st.session_state.payloads = None
                pending_doc_raw_images = list(st.session_state.get("pending_doc_raw_images", []))
                pending_doc_image_urls = list(st.session_state.get("pending_doc_images", []))
                _warn_page_image_upload_errors(_add_page_images_to_doc_pool(pending_url, pending_doc_raw_images, pending_doc_image_urls))
                st.session_state.doc_raw_images = pending_doc_raw_images
                st.session_state.hosted_image_candidates = list(dict.fromkeys(pending_doc_image_urls))
                # Same auto-fold as the direct-extraction path above (2026-09-23 product-owner
                # request) - every hosted_image_candidates URL here is already verified-hosted.
                auto_images = list(dict.fromkeys(
                    [u for u in data.get("image_urls", []) if u] + st.session_state.hosted_image_candidates))
                data["image_urls"] = auto_images or [FALLBACK_IMAGE]
                st.session_state.images_text_value = "\n".join(auto_images)
                st.session_state.extracted = data
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
                "Image URLs (one per line - images found on the page/URL or in your document(s) "
                "are added automatically; edit or delete a line to change what's used)",
                key="images_text_value",
                height=80
            )
            data["image_urls"] = [u.strip() for u in images_text.split("\n") if u.strip()] or [FALLBACK_IMAGE]
            if data["image_urls"] == [FALLBACK_IMAGE]:
                st.caption(f"⚠️ No real images provided - using placeholder ({FALLBACK_IMAGE}).")
            elif st.session_state.get("hosted_image_candidates"):
                # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-23) - see the docstring above where
                # hosted_image_candidates is merged into image_urls: this used to be a manual
                # tick-and-"Add selected" picker (render_url_image_picker); now it's a plain
                # confirmation, since the URLs are already in the text area above.
                st.caption(f"✅ {len(st.session_state.hosted_image_candidates)} image(s) found on the page/URL/document "
                          f"were added automatically above.")

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

        _warn_stale_images(data.get("image_urls"))

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
                                    # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-17): "add_option" no
                                    # longer requires the human to click "Check what's already
                                    # online" in Step 3 first (see ACTION_FIELDS's own comment) -
                                    # so st.session_state.fetched_tour is typically empty now.
                                    # Merging a new supplement into the tour's existing list still
                                    # genuinely needs the tour's CURRENT live data (to avoid wiping
                                    # out supplements that already belong to other Modalities) - so
                                    # fetch it here, automatically, only in this one case where a
                                    # fetch is actually needed, rather than making the human do it
                                    # up front for every add_option run (most of which have no new
                                    # supplements at all and never needed this data).
                                    old_tour = st.session_state.get("fetched_tour")
                                    if not isinstance(old_tour, dict) or "error" in old_tour:
                                        old_tour, _fresh_code = try_code_variants(
                                            lambda c: client.get_closed_tour(payloads["supplier_id"], c),
                                            target_tour_code
                                        )
                                    if not isinstance(old_tour, dict) or "error" in old_tour:
                                        st.warning(
                                            f"⚠️ '{modality_code}' was created, but its {len(new_supplements)} "
                                            f"supplement(s) were NOT added - couldn't fetch the tour's current "
                                            f"live data ({old_tour.get('message', old_tour) if isinstance(old_tour, dict) else old_tour}). "
                                            f"Use 'Update an existing tour's details' to add the supplements "
                                            f"separately."
                                        )
                                    else:
                                        # CONFIRMED PRODUCT-OWNER CORRECTION: a ClosedTour
                                        # supplement applies to EVERY Modality, so it is added to
                                        # the tour unscoped. Scoping it to the Modality being
                                        # added would have made it unbuyable for everyone already
                                        # booked on the tour's other Modalities.
                                        # CONFIRMED ABSOLUTE HOUSE RULE (product owner, 2026-09-18):
                                        # "a supplement can never be 0 Euro" - dropped, with a
                                        # note, by build_supplement_vos itself (see its own
                                        # docstring); surfaced here too since this legacy
                                        # add-Modality path has no other review screen for it.
                                        _new_supplement_notes = []
                                        new_vos = [v.dict() for v in build_supplement_vos(
                                            new_supplements, notes=_new_supplement_notes)]
                                        render_supplement_zero_price_notes(
                                            {"supplement_zero_price_notes": _new_supplement_notes})
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
    # This tab's saved draft (see draft_autosave.py) exists purely to protect UNFINISHED work
    # against a reload - a tour that has already published successfully has nothing left to
    # protect, and leaving the draft behind would just get offered back as "unfinished work" the
    # next time this tab/URL is reopened. Safe to call on every render of this success screen.
    draft_autosave.clear_on_publish_success()
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
