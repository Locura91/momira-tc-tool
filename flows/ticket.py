"""
Ticket product flow, split out of app.py (Phase 1 restructure, zero behaviour change).

render_ticket_flow moved here verbatim. Everything it references that is defined at
app.py's own top level (constants, small helpers, other render_* functions not yet
split out) is imported back from app via the late-binding pattern below: app.py imports
this module only after all of those names are already defined in its own namespace, so
`from app import ...` resolves correctly despite the circular import shape.
"""
import os
import tempfile
import pandas as pd
import streamlit as st

from schemas import TicketHumanPreConfig
from builder import build_ticket_payloads, build_ticket_voucher_remarks_only_update
from ai_extractor import (
    extract_ticket_data, extract_ticket_option_only_data, detect_ticket_variants,
    friendly_error_message, apply_clarification, check_ticket_content_drift,
)
from document_reader import extract_raw_text, extract_images
from document_reader import scanned_document_warning as document_reader_scanned_warning
from pexels_client import search_images
from pixabay_client import search_images as search_images_pixabay
from r2_client import upload_images_with_errors as upload_images_r2_with_errors
from geocoding_client import geocode_search, parse_google_maps_url
import price_validity
import cancellation_links
import draft_autosave
from image_dimensions import FALLBACK_IMAGE
from ui_components import (
    editable_table, editable_field, merge_what_to_bring_into_voucher_remarks,
    render_stop_sales_editor, render_cancellation_policy_editor,
    render_ticket_modality_supplements_editor, render_ticket_pricing_editor,
    render_readonly_source, render_closable_image_section, render_url_image_picker,
    render_doc_image_picker, render_stock_photo_picker, render_child_age_band,
    render_duration_editor, is_active_supplier,
    _clean_time_table_rows, _safe_cell_str, _safe_float, _add_page_images_to_doc_pool,
)

from app import (
    ALL_WEEKDAYS, CURRENCY_OPTIONS, HOUSE_RULE_CODEWORD, TICKET_ACTION_FIELDS,
    TICKET_ACTION_LABELS, TICKET_CREATE_ACTION_KEYS,
    _clean_modality_code, _data_fingerprint, _dmy_date_field, _fetch_url_text_safe,
    _geo_search_default, _map_fetched_ticket_to_data, _merge_extraction_over_baseline,
    _tk_clear_geo_confirmation, _warn_page_image_upload_errors, _warn_stale_images,
    apply_clarify_changes, bump_widget_generation, check_code_availability,
    check_modality_code_availability, clarify_supplier_id, flow_widget_key,
    floor_start_date_for_new_data, mark_code_as_taken, remember_clarification,
    remember_memory_panel, render_candidate_filter, render_clarify_result,
    render_code_availability_check, render_house_rule_shortcut, render_modalities_review,
    render_modality_code_availability_check, render_multi_ticket_flow,
    render_multi_ticket_update_flow, render_publish_blockers, render_supplement_zero_price_notes,
    render_ticket_language_options, render_ticket_update_comparison,
    reset_stale_editable_field_widgets, show_publish_error, widget_generation,
    with_learned_guidance,
)


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
            # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-03): "if I start a new batch for creating
            # a new service, please allow to change the currency as this can be always vary" -
            # tk_cfg_currency's lock (below, in Step 3) is meant to stop the currency changing
            # mid-way through ONE ticket's Modalities, not to survive into an unrelated new
            # supplier/action picked here. Clearing it lets Step 3 offer a fresh, editable
            # currency choice for whatever comes next.
            st.session_state.pop("tk_cfg_currency", None)
            st.rerun()
    else:
        # Create-only here (product owner, 2026-09-10) - this screen is ONLY reached via
        # "Create a new product -> Ticket". Updating an existing Ticket (single or batch) now
        # lives exclusively under "Price update to existing Products" - see TICKET_CREATE_ACTION_KEYS.
        action_key = st.radio(
            "Choose one:", list(TICKET_CREATE_ACTION_KEYS),
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
        # Never actually reaches the publish_action-branching logic further below - this action
        # returns straight into render_multi_ticket_update_flow (see Step 4 routing) - but the
        # dict lookup above happens unconditionally, so a real label is still needed here.
        "update_tickets_batch": "Batch-update existing tickets",
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
    tk_files = st.file_uploader("Upload document(s) (optional)", type=["pdf", "docx", "xlsx", "pptx", "csv"],
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

    # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-08): batch-update MANY existing tickets from one
    # document (e.g. a full new price-list covering 20+ excursions already live) - see
    # render_multi_ticket_update_flow's own docstring.
    if action == "update_tickets_batch":
        render_multi_ticket_update_flow(client, supplier_id, on_request, release_days, tk_url, tk_files,
                                       max_passengers=max_passengers)
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
                    # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-08): "once we receive new prices...
                    # we must exchange the code with the correct date" AND "the AI must review the
                    # current ticket information and check if that is matching with the new ticket
                    # information... sometimes small changes are done for a new season". This is
                    # the FAST/price-only path, which never re-extracts name/description/etc - so
                    # neither of those can rely on data this call already gathered. Both instead
                    # compare against the LIVE ticket (already fetched via "Check what's already
                    # online" before this flow reaches Step 4) directly:
                    _tk_live_for_pv = st.session_state.get("tk_fetched_ticket") or {}
                    if isinstance(_tk_live_for_pv, dict) and "error" not in _tk_live_for_pv:
                        _tk_live_datasheet = ((_tk_live_for_pv.get("datasheets") or {}).get("EN")) or {}
                        _tk_live_valid_until = price_validity.extract_price_validity_date(
                            _tk_live_datasheet.get("voucherRemarks"))
                        if _tk_live_valid_until and not (data.get("price_valid_until_date") or "").strip():
                            data["price_valid_until_date"] = _tk_live_valid_until.isoformat()
                        try:
                            st.session_state.tk_content_drift = check_ticket_content_drift(
                                raw_text, _tk_live_datasheet, human_hint=tk_hint or None)
                        except Exception as e:
                            # Best-effort - a failed drift check must never block the (already
                            # successful) price extraction above from being usable.
                            st.session_state.tk_content_drift = {"error": friendly_error_message(e)}
                    else:
                        st.session_state.tk_content_drift = None
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
                        floor_start_date_for_new_data(data)
                        # Only fills in when this document (and, for an update, the live
                        # baseline it was just merged over) had no cancellation terms of its
                        # own - see apply_cancellation_link_default's docstring.
                        st.session_state.tk_cancellation_link_scope = cancellation_links.apply_cancellation_link_default(
                            data, supplier_id, "Ticket")
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
                        # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-23, verbatim): "...I cannot
                        # automatically use the images... at least the images are being
                        # detected... but i cannot automatically use them for my closedtours and
                        # neither for my tickets." Same fix as the ClosedTour flow (app.py) -
                        # every URL in tk_hosted_image_candidates is already a verified,
                        # R2-hosted image (uploaded AND public-URL-verified inside
                        # _add_page_images_to_doc_pool/upload_images_with_errors before it ever
                        # reaches this list), so fold it straight into image_urls instead of
                        # waiting for a manual tick-and-"Add selected" click.
                        auto_images = list(dict.fromkeys(
                            [u for u in data.get("image_urls", []) if u] + st.session_state.tk_hosted_image_candidates))
                        data["image_urls"] = auto_images or [FALLBACK_IMAGE]
                        st.session_state.tk_extracted = data
                        st.success("Extraction complete. Review and edit below.")
            except Exception as e:
                st.error(f"Extraction failed: {friendly_error_message(e)}")

    if st.session_state.get("tk_pending_variants"):
        excursions = st.session_state.tk_pending_variants
        st.warning(f"⚠️ This content describes {len(excursions)} distinct excursions — which one(s) do you want to add?")
        st.caption("Tick just one to continue in the normal single-Ticket flow below, or tick several to create "
                  "them all as separate Tickets in one batch (you'll assign each its own Code next).")

        if "tk_pending_variant_selection" not in st.session_state:
            # CONFIRMED PRODUCT-OWNER RULE (2026-09-03): same rule as render_multi_ticket_flow's
            # PHASE 1 candidates - when the document doesn't assign this excursion its own supplier
            # code, the Modality Code defaults to the excursion's own name (already fully known
            # here, unlike that flow's async single-fallback case) instead of the generic
            # "Standard"/"Standard Private". modality_name (client-facing) is unaffected.
            # CONFIRMED PRODUCT-OWNER RULE (2026-09-15): "When multiple Service been detected by
            # the app, we must give the option 'select all' 'select none', on default select
            # all." - was "selected": False here, the one detected-list default that didn't
            # match every other batch/candidate screen in the app.
            st.session_state.tk_pending_variant_selection = [
                {"label": e.get("label", f"Excursion {i+1}"), "selected": True,
                 "ticket_code": "",
                 "modality_code": (
                     f"{('Standard Private' if e.get('is_private') else 'Standard').upper().replace(' ', '_')}_{str(e.get('supplier_code') or '').strip()}"
                     if str(e.get("supplier_code") or "").strip()
                     # CONFIRMED BUG FIX (product owner, 2026-09-03): same real API rejection
                     # ("Modality Code cannot contain '/' or '\\'") as render_multi_ticket_flow's
                     # sibling default above - run the excursion label through the same
                     # _clean_modality_code sanitizer before using it as the default here too.
                     else (_clean_modality_code(str(e.get("label") or "").strip())
                           or ("Standard Private" if e.get("is_private") else "Standard"))
                 )}
                for i, e in enumerate(excursions)
            ]
        tkpv_selection = st.session_state.tk_pending_variant_selection

        render_candidate_filter(tkpv_selection, "tkpv", "excursion")

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

                        floor_start_date_for_new_data(data)
                        st.session_state.tk_cancellation_link_scope = cancellation_links.apply_cancellation_link_default(
                            data, supplier_id, "Ticket")
                        bump_widget_generation("tk")  # see the sibling extraction path above
                        st.session_state.tk_raw_preview = f"(Extracted excursion: {chosen_label})\n\n{st.session_state.tk_pending_raw_text}"
                        st.session_state.tk_payloads = None
                        _tk_clear_geo_confirmation()
                        pending_doc_raw_images = list(st.session_state.get("tk_pending_doc_raw_images", []))
                        pending_doc_image_urls = list(st.session_state.get("tk_pending_doc_images", []))
                        _warn_page_image_upload_errors(_add_page_images_to_doc_pool(tk_pending_url, pending_doc_raw_images, pending_doc_image_urls))
                        st.session_state.tk_doc_raw_images = pending_doc_raw_images
                        st.session_state.tk_hosted_image_candidates = list(dict.fromkeys(pending_doc_image_urls))
                        # Same auto-fold as the direct-extraction path above (2026-09-23
                        # product-owner request) - every tk_hosted_image_candidates URL here is
                        # already verified-hosted.
                        auto_images = list(dict.fromkeys(
                            [u for u in data.get("image_urls", []) if u] + st.session_state.tk_hosted_image_candidates))
                        data["image_urls"] = auto_images or [FALLBACK_IMAGE]
                        st.session_state.tk_extracted = data
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
                # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-08) - see the matching comment at the
                # Extract button above for the full rationale.
                _tk_drift = st.session_state.get("tk_content_drift")
                if isinstance(_tk_drift, dict):
                    if _tk_drift.get("error"):
                        st.caption(f"(Couldn't run the AI content check against the new document: "
                                  f"{_tk_drift['error']})")
                    elif _tk_drift.get("has_changes"):
                        st.warning("⚠️ The new document may describe more than a price change - "
                                  "double-check before publishing:")
                        for _c in _tk_drift.get("changes") or []:
                            st.markdown(f"- {_c}")
                    else:
                        st.caption("✅ AI check: the new document doesn't appear to describe any "
                                  "content change beyond pricing.")
                editable_field("Prices confirmed valid until (optional - the app adds the "
                               "\"(YYYYMMDD)\" marker to Voucher Remarks automatically)", data,
                               "price_valid_until_date", widget="text_input")
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
                # Price-validity code (product owner, 2026-09-08) - see price_validity.py's own
                # docstring and this file's other call site for the fuller comment.
                editable_field("Prices confirmed valid until (optional - the app adds the "
                               "\"(YYYYMMDD)\" marker to Voucher Remarks automatically)", data,
                               "price_valid_until_date", widget="text_input")
                # CONFIRMED PRODUCT-OWNER RULE (2026-08-12): Manual Notes removed here too, same
                # reasoning as the batch flow above - every field is directly editable now.

                if data.get("is_private") and "private" not in (modality_code or "").lower():
                    st.info(f"💡 This excursion is described as **PRIVATE** in the source - a genuine "
                           f"selling point. Your current Modality Code is `{modality_code}` - consider "
                           f"going back to Step 3 (Details) and adding \"Private\" to it if you'd like "
                           f"this reflected there.")

                # CONFIRMED FIX (2026-09-03, product owner): "estimated duration must be seen
                # within the app if used days, minutes or hours" - see render_duration_editor's
                # own docstring; never requires a value.
                render_duration_editor(data, "legacy_ticket")

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

                images_text = st.text_area(
                    "Image URLs (one per line - images found on the page/URL or in your "
                    "document(s) are added automatically; edit or delete a line to change what's used)",
                    key=_tk_images_key)
                data["image_urls"] = [u.strip() for u in images_text.split("\n") if u.strip()] or [FALLBACK_IMAGE]
                if data["image_urls"] == [FALLBACK_IMAGE]:
                    st.caption(f"⚠️ No real images provided - using placeholder ({FALLBACK_IMAGE}).")
                elif st.session_state.get("tk_hosted_image_candidates"):
                    # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-23) - see the extraction-time
                    # merge above: this used to be a manual tick-and-"Add selected" picker
                    # (render_url_image_picker); now it's a plain confirmation, since the URLs
                    # are already in the text area above.
                    st.caption(f"✅ {len(st.session_state.tk_hosted_image_candidates)} image(s) found on the page/URL/document "
                              f"were added automatically above.")

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
            data["start_date"] = _dmy_date_field("Valid From (DD/MM/YYYY)", flow_widget_key("tk", "start_date", value_iso=data.get("start_date", "")))
        with dcol2:
            data["end_date"] = _dmy_date_field("Valid Until (DD/MM/YYYY)", flow_widget_key("tk", "end_date", value_iso=data.get("end_date", "")))

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
                    "add them afterward via **Price update to existing Products -> Ticket -> \"2: Add "
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
            if st.session_state.get("tk_geo_link_note"):
                st.info(st.session_state.tk_geo_link_note)
                st.session_state.tk_geo_link_note = None

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
                        tk_geo_search_query = st.text_input("Search for a location", value=_geo_search_default(client, data.get("city", "")), key=flow_widget_key("tk", "geo_search_query"))
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

                        st.markdown("**Or paste a Google Maps link:**")
                        st.caption("Find the place in Google Maps, hit Share (or copy the address-bar URL), "
                                  "and paste it here - the coordinates are read out of the link automatically.")
                        tk_maps_url = st.text_input("Google Maps link", key="tk_geo_maps_url", placeholder="https://maps.google.com/...")
                        if st.button("🔗 Use this link's coordinates", key="tk_geo_maps_url_btn", disabled=not tk_maps_url.strip()):
                            with st.spinner("Reading coordinates from the link..."):
                                tk_url_geo = parse_google_maps_url(tk_maps_url)
                            if tk_url_geo["valid"]:
                                data["manual_latitude"] = tk_url_geo["latitude"]
                                data["manual_longitude"] = tk_url_geo["longitude"]
                                pre_config = TicketHumanPreConfig(
                                    supplier_id=supplier_id, ticket_code=ticket_code or existing_ticket_code or "XXX",
                                    currency=currency, modality_code=modality_code, on_request=on_request,
                                    days_available_before_release=release_days, min_passengers=min_passengers, max_passengers=max_passengers
                                )
                                st.session_state.tk_payloads = build_ticket_payloads(pre_config, data, client)
                                st.session_state.tk_payloads_data_fingerprint = _data_fingerprint(data)
                                _tk_clear_geo_confirmation()
                                st.session_state.tk_geo_link_note = (
                                    f"ℹ️ That link had no coordinates of its own - geocoded its place "
                                    f"name instead ({tk_url_geo.get('name') or 'match found'}). "
                                    f"Double-check it above before confirming."
                                ) if tk_url_geo.get("source") == "geocoded from link" else None
                                st.rerun()
                            else:
                                st.error(tk_url_geo["error"])

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
                    st.caption("Search for the correct location below, or paste a Google Maps link - "
                              "manual coordinate entry isn't needed any more, the link (or its place "
                              "name, if the link itself has none) covers that.")

                    tk_geo_search_query2 = st.text_input("Search for a location", value=_geo_search_default(client, data.get("city", "")), key="tk_geo_search_query2")
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

                    st.markdown("**Or paste a Google Maps link:**")
                    st.caption("Find the place in Google Maps, hit Share (or copy the address-bar URL), and "
                              "paste it here - the coordinates are read out of the link automatically.")
                    tk_maps_url2 = st.text_input("Google Maps link", key="tk_geo_maps_url2", placeholder="https://maps.google.com/...")
                    if st.button("🔗 Use this link's coordinates", key="tk_geo_maps_url_btn2", disabled=not tk_maps_url2.strip()):
                        with st.spinner("Reading coordinates from the link..."):
                            tk_url_geo2 = parse_google_maps_url(tk_maps_url2)
                        if tk_url_geo2["valid"]:
                            data["manual_latitude"] = tk_url_geo2["latitude"]
                            data["manual_longitude"] = tk_url_geo2["longitude"]
                            pre_config = TicketHumanPreConfig(
                                supplier_id=supplier_id, ticket_code=ticket_code or existing_ticket_code or "XXX",
                                currency=currency, modality_code=modality_code, on_request=on_request,
                                days_available_before_release=release_days, min_passengers=min_passengers, max_passengers=max_passengers
                            )
                            st.session_state.tk_payloads = build_ticket_payloads(pre_config, data, client)
                            st.session_state.tk_payloads_data_fingerprint = _data_fingerprint(data)
                            _tk_clear_geo_confirmation()
                            st.session_state.tk_geo_link_note = (
                                f"ℹ️ That link had no coordinates of its own - geocoded its place "
                                f"name instead ({tk_url_geo2.get('name') or 'match found'}). "
                                f"Double-check it above before confirming."
                            ) if tk_url_geo2.get("source") == "geocoded from link" else None
                            st.rerun()
                        else:
                            st.error(tk_url_geo2["error"])

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
            # CONFIRMED ABSOLUTE HOUSE RULE (product owner, 2026-09-18): "a supplement can never
            # be 0 Euro" - a non-blocking note (not a can_publish gate, unlike the blockers
            # above), see render_supplement_zero_price_notes' own docstring.
            render_supplement_zero_price_notes(payloads)
            if not tk_is_option_only:
                can_publish = can_publish and payloads.get("geolocation_resolved") and st.session_state.get("tk_geo_confirmed", False)
                if payloads.get("geolocation_resolved") and not st.session_state.get("tk_geo_confirmed", False):
                    st.warning("⚠️ Confirm the location above (checkbox in Step 6) before you can publish.")

            _warn_stale_images(data.get("image_urls"))

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
                                                    render_supplement_zero_price_notes(mod_payloads)
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
                                # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-08): "we must exchange
                                # the code with the correct date" - this is the price-only path
                                # (see build_ticket_voucher_remarks_only_update's own docstring for
                                # why it can't reuse the "whole ticket" payload builder). Only
                                # calls update_ticket at all when the encoded code actually
                                # changes - a human who left the field alone (or it was already
                                # correctly prefilled from the live ticket) shouldn't trigger a
                                # second API call for nothing.
                                _tk_live_for_voucher = st.session_state.get("tk_fetched_ticket") or {}
                                if isinstance(_tk_live_for_voucher, dict) and "error" not in _tk_live_for_voucher:
                                    _tk_live_voucher_text = (
                                        ((_tk_live_for_voucher.get("datasheets") or {}).get("EN")) or {}
                                    ).get("voucherRemarks", "")
                                    _tk_old_pv_code = price_validity.encode_price_validity_code(
                                        price_validity.extract_price_validity_date(_tk_live_voucher_text))
                                    _tk_new_pv_code = price_validity.encode_price_validity_code(
                                        data.get("price_valid_until_date"))
                                    if _tk_new_pv_code != _tk_old_pv_code:
                                        _tk_voucher_payload = build_ticket_voucher_remarks_only_update(
                                            _tk_live_for_voucher, data.get("price_valid_until_date"))
                                        _tk_voucher_result = client.update_ticket(supplier_id, _tk_voucher_payload)
                                        if "error" in _tk_voucher_result:
                                            st.warning(f"⚠️ Pricing was updated, but the price-validity "
                                                      f"code in Voucher Remarks couldn't be saved: "
                                                      f"{_tk_voucher_result.get('message', _tk_voucher_result)}")
                                        else:
                                            st.caption("✅ Price-validity code in Voucher Remarks updated too.")
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
            # This tab's saved draft (see draft_autosave.py) exists purely to protect UNFINISHED
            # work against a reload - a ticket that has already published successfully has
            # nothing left to protect. Safe to call on every render of this success screen.
            draft_autosave.clear_on_publish_success()
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
