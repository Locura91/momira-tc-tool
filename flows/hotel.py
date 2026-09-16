"""
Hotel product flow, split out of app.py (Phase 1 restructure, zero behaviour change).

render_hotel_flow moved here verbatim. Everything it references that is defined at app.py's
own top level (constants, small helpers, the Hotel-specific `_hp_*`/`_render_hotel_*`
helpers) is imported back from app via the same late-binding pattern used by flows/ticket.py:
app.py imports this module only after all of those names are already defined in its own
namespace, so `from app import ...` resolves correctly despite the circular import shape.
"""
import os
import re
import tempfile
import pandas as pd
import streamlit as st

from schemas import HotelHumanPreConfig
from builder import (
    build_hotel_contract_payload, resolve_room_provider_codes, build_hotel_offer_payloads,
    build_hotel_supplement_payloads, build_hotel_rate_payloads,
    _APPLY_TYPE_VALUES as HOTEL_APPLY_VALUES,
    GEOLOCATION_SOURCE_CONFIRMED_MASTER,
)
from document_reader import extract_raw_text, extract_images
from document_reader import scanned_document_warning as document_reader_scanned_warning
from ai_extractor import detect_hotel_products, extract_hotel_data, friendly_error_message
from pexels_client import search_images
from pixabay_client import search_images as search_images_pixabay
from r2_client import upload_images_with_errors as upload_images_r2_with_errors
from geocoding_client import geocode_search, parse_google_maps_url
import cancellation_links
import hotel_automap
import service_notes
from image_dimensions import FALLBACK_IMAGE
from ui_components import (
    editable_table, editable_field, render_cancellation_policy_editor,
    render_closable_image_section, render_url_image_picker, render_doc_image_picker,
    render_stock_photo_picker, is_active_supplier,
    _safe_float, _safe_int, _add_page_images_to_doc_pool,
)

from app import (
    CURRENCY_OPTIONS, SHARED_WIDGET_STATE_PREFIXES,
    _clear_batch_widget_state, _extract_error_message_detail, _extract_rejected_image_url,
    _fetch_url_text_safe, _hp_dist_to_str, _hp_first_window, _hp_names_to_str, _hp_nums_to_str,
    _hp_str_to_dist, _hp_str_to_names, _hp_str_to_nums, _hp_window_list,
    _render_hotel_masterdata_step, _render_hotel_price_audit_section,
    _warn_page_image_upload_errors, _warn_stale_images, show_publish_error,
    get_existing_hotel_names,
)


# CONFIRMED PRODUCT-OWNER REQUEST (2026-09-16): folded into the extraction hint once the human
# answers the contract-purpose question, BEFORE the document is read - see the "CONTRACT PURPOSE"
# comment at its call site (Step 3) for the full history of why this moved up from a review-
# screen-only, extraction-blind toggle. Kept as a module-level dict (not inline) so the wording
# for each purpose lives in exactly one place alongside the radio's own labels.
_HP_CONTRACT_PURPOSE_EXTRACTION_HINTS = {
    "new_period": "This document describes a NEW price period for this hotel - focus on accurately "
                  "extracting the new season's dates, rates, and any new rooms/meal plans/offers/"
                  "supplements it introduces.",
    "check_current": "This document is being used to CHECK/VERIFY the CURRENTLY live period for this "
                     "hotel - extract the current season's rooms/rates/meal plans/offers/supplements as "
                     "thoroughly and accurately as possible so they can be compared against what's "
                     "already published.",
    "mixture": "This document is a MIX for this hotel - it may describe both a NEW price period AND "
              "details for the CURRENTLY live period in the same document. Extract everything present; "
              "do not assume it is only one or the other.",
}


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
                if (s.get("commercialName") or s.get("legalName") or "").strip().lower().startswith("momira_") and is_active_supplier(s)
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

        # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-16): "We shall provide the human with all
        # Hotel code possible from the supplier we have selected - otherwise the human makes a
        # mistake and writes the code wrong." The code below is free-typed either way (it's how
        # both a brand-new hotel and an update to an existing one are entered), but showing every
        # code this supplier already has on file - right above the box - gives a human something
        # to check against/copy instead of guessing or mistyping a code from memory. Reuses
        # get_existing_hotel_names, the same lookup the Update/Refresh screen's Hotel picker
        # already relies on (app_helpers.py) - no new API call shape needed.
        if supplier_id_choice:
            _hp_existing_names, _hp_existing_names_error = get_existing_hotel_names(client, supplier_id_choice)
            if _hp_existing_names:
                with st.expander(f"📋 Existing hotel codes for this supplier ({len(_hp_existing_names)})",
                                 expanded=True):
                    st.caption("Already on file in Travel Compositor for this supplier. Pick one below and "
                              "click Use to fill the code field (this UPDATES that hotel), or just type a new "
                              "code yourself below to CREATE one.")
                    st.dataframe(
                        pd.DataFrame([{"Hotel code": i["code"], "Name": i["name"]} for i in _hp_existing_names]),
                        use_container_width=True, hide_index=True,
                    )
                    # CONFIRMED REAL BUG (product owner, 2026-09-16, right after this list was
                    # first added): "the Hotel code selection, after i chose the supplier is not
                    # working. i still have to add the Hotel code manually" - the table above was
                    # read-only, nothing to click actually filled the text field below it. This
                    # selectbox + button pair sets st.session_state["hp_provider_code"] - the
                    # text_input's own key - BEFORE that widget is instantiated further down, so
                    # it picks the chosen code up as its value on the rerun triggered by the
                    # button (same set-then-rerun pattern app_helpers.py's "suggested match" /
                    # "Use this" buttons already use elsewhere in this app).
                    _hp_code_pick_options = {f"{i['code']} — {i['name']}": i["code"] for i in _hp_existing_names}
                    _hp_code_pick_label = st.selectbox(
                        "Pick an existing code", list(_hp_code_pick_options.keys()), key="hp_code_pick",
                        label_visibility="collapsed",
                    )
                    if st.button("✅ Use this code", key="hp_code_pick_use"):
                        st.session_state["hp_provider_code"] = _hp_code_pick_options[_hp_code_pick_label]
                        st.rerun()
            elif _hp_existing_names_error:
                st.caption(f"ℹ️ Couldn't load this supplier's existing hotel codes ({_hp_existing_names_error}) "
                          f"- type the code manually below.")

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

    # ---- Does this hotel code already exist? (decides create vs update) ----
    # Hoisted up from Phase 2 (2026-09-06, master-data step): the "use Travel Compositor master
    # data?" prompt below only makes sense for a genuinely NEW hotel - an existing hotel already
    # has its own live images/description in Travel Compositor - so this needs to be known before
    # Phase 1 renders, not just before Phase 2's review screen.
    # Also hoisted ahead of the standing-note editor below (2026-09-12, product owner): standing
    # notes are a supplier-wide maintenance job for services that already exist on the platform -
    # for a brand-new hotel there is nothing yet to attach a note to, and the product owner asked
    # for that management to live in the dedicated standing-notes tool instead, not on the
    # create-a-new-hotel path.
    if not st.session_state.get("hp_existing_checked"):
        with st.spinner(f"Checking whether hotel code {provider_code} already exists..."):
            snapshot = client.get_hotel(supplier_id, provider_code)
        if isinstance(snapshot, dict) and "error" in snapshot:
            st.session_state.hp_existing_snapshot = None
        else:
            st.session_state.hp_existing_snapshot = snapshot
        st.session_state.hp_existing_checked = True

    existing_snapshot = st.session_state.get("hp_existing_snapshot")

    # 2026-09-16 (product owner, "No need to include Standing note — applies to EVERY Hotel from
    # this supplier"): removed both here and from the Manual notes block further down (see the
    # show_standing_note=False review-screen call) - a supplier's already-saved standing note
    # still gets folded into the voucher automatically either way (service_notes.compose_manual_
    # notes), this only drops the editor UI for setting/changing one from inside Hotel's flow.
    cancellation_links.render_cancellation_link_editor(supplier_id, "Hotel", key_suffix="_setup")

    if "hp_phase" not in st.session_state:
        st.session_state.hp_phase = "gather"

    # ------------------------------------------------------------------
    # PHASE 1: gather source + extract
    # ------------------------------------------------------------------
    if st.session_state.hp_phase == "gather":
        # ---- New-hotel-only: offer Travel Compositor's own master hotel data first ----
        # Product owner request (2026-09-06), mirroring Travel Compositor's own manual "add
        # hotel" screen, which offers exactly this choice before falling back to asking a human
        # to hunt for photos. See masterdata_store.py/masterdata_matcher.py for why this has to
        # be a locally-synced index rather than a live search (no such endpoint exists).
        if not existing_snapshot and not st.session_state.get("hp_masterdata_decided"):
            _render_hotel_masterdata_step(client)
            return

        # ------------------------------------------------------------------
        # CONTRACT PURPOSE - moved here, BEFORE the document is read (product owner, 2026-09-16):
        # "would it not be smarter to ask before the AI reads the document, if the document is a:
        # checking current period b: Add a new period c: mixture of both." Originally (2026-09-12)
        # this was only asked on the review screen, AFTER extraction had already run - purely to
        # decide where the Price Audit tool appears, never actually informing the extraction
        # itself. Now it's asked up front and its answer is folded into the extraction hint below
        # (see _HP_CONTRACT_PURPOSE_EXTRACTION_HINTS), and a third "mixture" option covers a
        # document that does both at once (e.g. a rate sheet that restates the current season
        # while also adding the next one). Still only asked for an EXISTING hotel, same reasoning
        # as before - a brand-new hotel has nothing live yet to "check" or "add a period to".
        # hp_contract_purpose itself is read again, unchanged, further down (Price Audit
        # placement, Standing/Manual notes) - only WHERE it's asked moved, not how it's used.
        # ------------------------------------------------------------------
        if existing_snapshot:
            st.session_state.hp_contract_purpose = st.radio(
                "What is this document for?",
                ["new_period", "check_current", "mixture"],
                format_func=lambda v: {
                    "new_period": "📈 A NEW price period - add/extend rates, offers or rooms for a season not yet live",
                    "check_current": "🔍 CHECKING the CURRENT period - verify what's already live against this contract",
                    "mixture": "🔀 A MIX of both - some current-period verification AND a new period in the same document",
                }[v],
                index=["new_period", "check_current", "mixture"].index(
                    st.session_state.get("hp_contract_purpose") or "new_period"),
                key="hp_contract_purpose_radio",
            )
            st.caption("Asked before the document is read so the extraction can focus on the right thing.")

        st.header("Hotel — Step 3: Input Source")
        st.caption("A hotel contract normally covers ONE property: its rooms and allowed occupancy "
                  "combinations, meal plans, any offers/supplements, and the rate seasons with a price per "
                  "occupancy combination per room.")
        hp_url = st.text_input("Hotel page URL (optional)", key="hp_url")
        hp_files = st.file_uploader("Upload document(s) (optional)", type=["pdf", "docx", "xlsx", "pptx", "csv"],
                                     accept_multiple_files=True, key="hp_files")
        hp_hint = st.text_input("Extraction hint (optional)", key="hp_hint")

        if not (hp_url or hp_files):
            st.info("Provide a URL and/or upload document(s) above, then click below.")
        if st.button("🔎 Extract Hotel Contract", type="primary", disabled=not (hp_url or hp_files)):
            with st.spinner("Gathering content and extracting the hotel contract..."):
                try:
                    combined_parts = []
                    # CONFIRMED BUG FIX (reported 2026-09-02: "no image has been extracted from
                    # the url" - createHotel failed with "contract.images: Size must be between 1
                    # and 2147483647 ([])"): Hotel's Input Source step fetched the page URL's TEXT
                    # for extraction but never looked for IMAGES on that page or inside an uploaded
                    # document, unlike every other product-type flow (Ticket/ClosedTour/Transfer/
                    # Transport all pull embedded-document images via extract_images() and page
                    # images via _add_page_images_to_doc_pool()). Hotel's "Image URLs" field was a
                    # bare manual-paste table with nothing to paste FROM - so a real hotel contract
                    # with real property photos on its website reached Publish with an empty images
                    # list every time, and Travel Compositor's API (which requires at least 1 image
                    # per hotel, unlike this app's own schema default of []) rejected it with a raw
                    # technical error instead of a clear "add an image" message. Same
                    # doc_raw_images/doc_image_urls pipeline as Ticket now runs here too - see the
                    # picker section below (Step 4) for where these surface for the human to pick.
                    doc_raw_images = []
                    doc_image_urls = []
                    seen_image_hashes = set()
                    # Travel Compositor master-data images (2026-09-06) - already hosted on
                    # Travel Compositor's own CDN, so they go straight into doc_image_urls
                    # (no R2 upload needed, unlike document-extracted images) and surface in
                    # the same image picker at Step 4 as any other found image.
                    _hp_md_seed = st.session_state.get("hp_masterdata_seed")
                    if _hp_md_seed:
                        doc_image_urls.extend(_hp_md_seed.get("image_urls") or [])
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
                        st.error("Nothing to extract - the hotel page URL couldn't be fetched and no document(s) were provided.")
                        st.stop()

                    # Master-data text (name/address/phone/chain/description/facilities) folds in
                    # as just another source for the same extraction pass, rather than being
                    # hand-mapped field by field - deliberately doesn't count toward the
                    # "something to extract" check above, since it never substitutes for the
                    # actual rate contract (master data has no rooms/rates/prices at all).
                    if _hp_md_seed and _hp_md_seed.get("text_block"):
                        combined_parts.append(_hp_md_seed["text_block"])

                    # Same page-URL image scrape every other flow already does (server-side
                    # download, not a raw hotlink - see _add_page_images_to_doc_pool's own
                    # docstring for why a direct <img src=originalsite> breaks for real suppliers).
                    _warn_page_image_upload_errors(_add_page_images_to_doc_pool(hp_url, doc_raw_images, doc_image_urls))
                    if len(doc_image_urls) >= len(doc_raw_images):
                        doc_raw_images = []

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

                    # Fold the contract-purpose answer (asked above, before this document was
                    # read) into the extraction hint, so the AI itself is told what it's looking
                    # at rather than that only shaping the review screen afterward - see
                    # _HP_CONTRACT_PURPOSE_EXTRACTION_HINTS's own comment for the full reasoning.
                    _hp_purpose_hint = _HP_CONTRACT_PURPOSE_EXTRACTION_HINTS.get(
                        st.session_state.get("hp_contract_purpose")) if existing_snapshot else None
                    combined_hint = "\n\n".join(p for p in [_hp_purpose_hint, hp_hint] if p) or None

                    st.session_state.hp_raw_text = raw_text
                    st.session_state.hp_data = extract_hotel_data(raw_text, hotel_hint=hotel_hint, human_hint=combined_hint)
                    # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-16): "why must humans confirm the
                    # geolocation ... this information is coming from the hotel information
                    # already." When this hotel was seeded from a human-CONFIRMED Travel
                    # Compositor master-data record (the destination-confirmed search above),
                    # that record's own coordinates are Travel Compositor's own data - stashed
                    # under master_latitude/master_longitude (never manual_latitude, so an
                    # explicit document-stated coordinate still wins - see
                    # build_hotel_contract_payload's own priority order in builder.py).
                    _hp_md_geo = (_hp_md_seed or {}).get("geolocation") or {}
                    if _hp_md_geo.get("latitude") is not None and _hp_md_geo.get("longitude") is not None:
                        st.session_state.hp_data["master_latitude"] = _hp_md_geo["latitude"]
                        st.session_state.hp_data["master_longitude"] = _hp_md_geo["longitude"]
                    # Only fills in when this document didn't state its own cancellation
                    # terms - see apply_cancellation_link_default's docstring. Runs once,
                    # here at extraction time, not inside the review widgets.
                    st.session_state.hp_cancellation_link_scope = cancellation_links.apply_cancellation_link_default(
                        st.session_state.hp_data, supplier_id, "Hotel")
                    st.session_state.hp_doc_raw_images = doc_raw_images
                    # CONFIRMED REAL REQUEST (product owner, 2026-09-11, follow-up after seeing
                    # the first fix in a screenshot): "make sure that all those images are
                    # selected by default if they are coming from masterdata." The first version
                    # of this fix folded master-data images into hp_data["images"] directly, but
                    # LEFT THEM ALSO sitting in hp_hosted_image_candidates - so the "Images
                    # found" picker still showed all of them as unchecked checkboxes, which
                    # (reasonably) read as "these aren't selected" even though they already were.
                    # Fix: master-data images are excluded from the generic "Images found"
                    # candidate list entirely, since they don't need a manual pick at all - they
                    # already live in hp_data["images"] (see below) and show up in the editable
                    # "Image URLs" table above this picker. Only genuinely still-needs-a-decision
                    # images (from the page/uploaded document) remain in this candidate list.
                    _hp_md_image_urls_list = (_hp_md_seed or {}).get("image_urls") or []
                    _hp_md_image_urls = set(_hp_md_image_urls_list)
                    st.session_state.hp_hosted_image_candidates = [
                        u for u in dict.fromkeys(doc_image_urls) if u not in _hp_md_image_urls]
                    # CONFIRMED REAL REQUEST (product owner, 2026-09-11): "We need to use the
                    # provided images from the masterdata automatically. Please make sure that
                    # all images are automatically selected when creating a hotel from
                    # masterdata." Every OTHER image source (page scrape, uploaded document,
                    # stock photos) is deliberately left as a candidate the human must pick from
                    # (extract_hotel_data always starts "images": [] - see its own default) -
                    # but a master-data match has already been confirmed by a human one step
                    # earlier (Step 3's candidate-confirmation list, see
                    # _render_hotel_masterdata_step's own docstring), so requiring a SECOND,
                    # redundant manual pick here just to avoid the "no image added yet" publish
                    # warning added no safety, only friction. Extend rather than replace, in the
                    # unlikely case extract_hotel_data itself ever populates "images" from the
                    # document text.
                    if _hp_md_image_urls_list:
                        st.session_state.hp_data["images"] = list(dict.fromkeys(
                            (st.session_state.hp_data.get("images") or []) + _hp_md_image_urls_list))
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
                     "hp_cancellation_link_scope", "hp_price_audit_facts", "hp_contract_purpose"]

    st.header(f"Hotel — Step 4: Review “{data.get('hotelname') or '(unnamed)'}”")

    _hp_md_seed_used = st.session_state.get("hp_masterdata_seed")
    if _hp_md_seed_used:
        _hp_md_img_count = len(_hp_md_seed_used.get("image_urls") or [])
        st.info(f"📚 Seeded from Travel Compositor master data: **{_hp_md_seed_used.get('name') or '(unnamed)'}** "
                f"— its description was folded into extraction below, and its "
                f"**{_hp_md_img_count} image(s) were already added** to Image URLs below (no manual "
                f"selection needed) — double-check they're right for this property before publishing.")
        # Flagged here as well as after publishing, because this is the last screen where someone
        # can still change their mind about which master record this is - and the mapping itself
        # can only be done by hand in Travel Compositor afterwards (see hotel_automap.py).
        # Reads hp_existing_snapshot from session state rather than the `existing_snapshot` local,
        # which isn't bound until further down this function.
        if _hp_md_seed_used.get("accommodation_id") and not st.session_state.get("hp_existing_snapshot"):
            st.caption(f"🔗 After publishing, this still needs **Automap with master** set by hand in "
                       f"Travel Compositor (accommodation id **{_hp_md_seed_used['accommodation_id']}**"
                       + (f", GIATA {_hp_md_seed_used['giata_id']}" if _hp_md_seed_used.get("giata_id") else "")
                       + ") — the API has no field for it. It'll be added to the automap checklist "
                         "automatically so it isn't forgotten.")

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
    # Check itself now runs earlier, at the top of Phase 1 (2026-09-06, master-data step) - this
    # just reads the same result, kept here since `data`/the rest of Phase 2 already expects
    # `existing_snapshot` as a local name.
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

    # ------------------------------------------------------------------
    # CONTRACT PURPOSE (product owner, originally 2026-09-12): "once a product is uploaded and
    # prices must be checked or new prices will be added... the app can also ask the human to
    # make it clear if the contract is for a new period or a current period to check." Only set
    # for an EXISTING hotel - a brand-new hotel has nothing live yet to "check", so there's
    # nothing to disambiguate there; this drives where the Price Audit tool appears below, same
    # as before - product owner: "we must structure it simple and not on both ends."
    #
    # MOVED (product owner, 2026-09-16): the actual question is now asked at Step 3, BEFORE the
    # document is read (see _HP_CONTRACT_PURPOSE_EXTRACTION_HINTS and its call site) - this is
    # just re-reading the answer already given, not re-asking it a second time on this screen.
    # ------------------------------------------------------------------
    hp_contract_purpose = st.session_state.get("hp_contract_purpose") if existing_snapshot else None

    # ------------------------------------------------------------------
    # PRICE AUDIT (product owner, 2026-09-12) - only offered when managing an EXISTING hotel (see
    # the contract-purpose question above) - a brand-new hotel has no prior numbers to have gotten
    # wrong yet, so there is nothing this tool would be checking. Deliberately kept separate from
    # the main extraction below, focused ONLY on the numbers (room prices, meal plan supplements,
    # offers/early-birds, other supplements) - see price_audit.py's own docstring for the full
    # reasoning. Prices are what actually cost Momira Travel money if wrong, unlike a mis-
    # extracted hotel description; this exists to catch that specific class of mistake while the
    # app is still learning how different suppliers structure their contracts, and is meant to be
    # removed again once the main extraction reliably gets prices right without a second opinion.
    # Placed right here (before the editable review fields) when the human is specifically
    # CHECKING the current period, since that's the primary thing they came here to do; when
    # they're instead adding a new period, this same tool is still available lower down (right
    # before Publish) as a secondary sanity check rather than the main event.
    # ------------------------------------------------------------------
    if existing_snapshot and hp_contract_purpose == "check_current":
        _render_hotel_price_audit_section(data, primary=True)

    # ---- Hotel basics ----
    # 2026-09-16 (product owner): "the information already provided by Travel C is great and no
    # rewrite needed... no need to add additional images, no need to review the Description - we
    # are only focusing on price, supplement, meal type, offer, stop sale... We must keep the app
    # simple." Once a hotel already exists (existing_snapshot), builder.py's own "existing wins on
    # update" priority (see build_hotel_contract_payload's _basic_info_on_update) already means
    # none of this can be overwritten by the document any more - so showing the full edit UI for
    # it here was asking for a review that could no longer change anything, just noise in the way
    # of the fields that matter. A brand-new hotel (no existing_snapshot yet) still needs all of
    # it, since there's nothing else for this information to come from.
    address = data.get("address") or {}
    data["address"] = address
    if not existing_snapshot:
        st.markdown("#### Property")
        bcol1, bcol2, bcol3 = st.columns(3)
        with bcol1:
            editable_field("Hotel name", data, "hotelname")
        with bcol2:
            editable_field("Category", data, "category")
        with bcol3:
            editable_field("Chain", data, "chain")

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
    else:
        st.caption(f"ℹ️ Using **{existing_snapshot.get('hotelname') or data.get('hotelname') or provider_code}**'s "
                   f"existing name, address, category, chain, description and images from Travel "
                   f"Compositor unchanged - only the pricing and inventory below come from this document.")

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

    if not existing_snapshot:
        st.markdown("#### Images")
        st.caption("Travel Compositor requires at least one image to publish a hotel - paste a URL below, or "
                  "use one of the pickers to add a photo found on the hotel's page/document or a free stock photo.")
        img_df = pd.DataFrame({"url": data.get("images") or [""]})

        def _hp_save_images(edited_df):
            data["images"] = [str(u).strip() for u in edited_df["url"].tolist() if str(u or "").strip()]

        editable_table("Image URLs", img_df, "hp_images", on_save=_hp_save_images)

        default_hp_img_query = data.get("hotelname", "")

        def _hp_add_pexels():
            selected = render_stock_photo_picker("Pexels", search_images, default_hp_img_query, "hp_pexels")
            if selected:
                data["images"] = (data.get("images") or []) + selected
                return len(selected)
            return 0

        render_closable_image_section(True, "🖼️ Search free stock photos (Pexels)", "hp_pexels_closed", _hp_add_pexels)

        def _hp_add_pixabay():
            selected = render_stock_photo_picker("Pixabay", search_images_pixabay, default_hp_img_query, "hp_pixabay")
            if selected:
                data["images"] = (data.get("images") or []) + selected
                return len(selected)
            return 0

        render_closable_image_section(True, "🖼️ Search free stock photos (Pixabay)", "hp_pixabay_closed", _hp_add_pixabay)

        def _hp_add_url_images():
            selected = render_url_image_picker(st.session_state.get("hp_hosted_image_candidates"), "hp_found_images")
            if selected:
                data["images"] = (data.get("images") or []) + selected
                return len(selected)
            return 0

        render_closable_image_section(
            bool(st.session_state.get("hp_hosted_image_candidates")),
            f"🖼️ Images found ({len(st.session_state.get('hp_hosted_image_candidates') or [])}) - from the page/document",
            "hp_found_images_closed", _hp_add_url_images
        )

        def _hp_add_doc_image():
            added = render_doc_image_picker(st.session_state.get("hp_doc_raw_images"), "hp_doc_images")
            if added:
                data["images"] = (data.get("images") or []) + [added]
                return 1
            return 0

        render_closable_image_section(
            bool(st.session_state.get("hp_doc_raw_images")),
            f"📥 Images extracted from your document(s) ({len(st.session_state.get('hp_doc_raw_images') or [])}) - need hosting",
            "hp_doc_images_closed", _hp_add_doc_image
        )

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

    if existing_snapshot:
        service_notes.render_notes_editor(supplier_id, "Hotel", data, show_standing_note=False)

    pre_config = HotelHumanPreConfig(supplier_id=supplier_id, provider_code=provider_code,
                                      currency=currency, days_available_before_release=release_days)
    contract_result = build_hotel_contract_payload(pre_config, data, existing_hotel_snapshot=existing_snapshot)

    if contract_result.get("hotel_error"):
        st.error(f"⚠️ This hotel can't be built yet: {contract_result['hotel_error']}")
        return

    # ---- Geolocation (2026-09-06): Travel Compositor rejects a hotel whose coordinates fall
    # outside any known destination - search for a better match, paste a Google Maps link, or
    # type coordinates directly, then confirm before publish is allowed.
    hp_geo = contract_result.get("geolocation") or {}

    # 2026-09-16: an existing hotel's own coordinates already published successfully once, so
    # nothing new to check - one-line confirmation, not the full search/checkbox UI below. Falls
    # through to that full UI if the existing record is somehow missing coordinates.
    if existing_snapshot and hp_geo.get("valid"):
        st.session_state.hp_geo_confirmed = True
        hp_lat, hp_lng = hp_geo["latitude"], hp_geo["longitude"]
        st.caption(f"📍 Location **{hp_lat:.6f}, {hp_lng:.6f}** — already confirmed when this "
                   f"hotel was created in Travel Compositor.")
    else:
        st.markdown("#### Geolocation")
        if hp_geo.get("valid"):
            hp_lat, hp_lng = hp_geo["latitude"], hp_geo["longitude"]
            hp_maps_link = f"https://www.google.com/maps?q={hp_lat},{hp_lng}"
            st.markdown(
                f"<div style='background-color:#d4edda; color:#155724; padding:8px 12px; "
                f"border-radius:4px;'>📍 Resolved: <strong>{data.get('hotelname') or provider_code}</strong>"
                f"<br>Coordinates: {hp_lat:.6f}, {hp_lng:.6f} (source: {hp_geo.get('source')}) — "
                f"<a href='{hp_maps_link}' target='_blank'>Open in Google Maps to verify</a></div>",
                unsafe_allow_html=True
            )
            if hp_geo.get("source") not in ("manual override", "document", "existing hotel record",
                                             GEOLOCATION_SOURCE_CONFIRMED_MASTER, None):
                st.caption("Geocoding data © OpenStreetMap contributors")
            if hp_geo.get("source") == GEOLOCATION_SOURCE_CONFIRMED_MASTER:
                st.caption("✅ Auto-confirmed — Travel Compositor's own master-data coordinates, not a "
                           "geocoder guess. Search below if this looks wrong.")
        else:
            st.markdown(
                "<div style='background-color:#f8d7da; color:#721c24; padding:6px 12px; "
                "border-radius:4px;'>❌ Geolocation NOT resolved - Travel Compositor will reject this hotel "
                "without valid coordinates. Search below or enter coordinates manually.</div>",
                unsafe_allow_html=True
            )

        with st.expander("🔍 Search for a better match / fix this location", expanded=not hp_geo.get("valid")):
            if st.session_state.get("hp_geo_link_note"):
                st.info(st.session_state.hp_geo_link_note)
                st.session_state.hp_geo_link_note = None
            hp_geo_default_query = (data.get("address") or {}).get("location_name") or data.get("hotelname") or ""
            hp_geo_query = st.text_input("Search for a location", value=hp_geo_default_query, key="hp_geo_query")
            if st.button("🔎 Search", key="hp_geo_search_btn"):
                with st.spinner("Searching..."):
                    st.session_state.hp_geo_search_results = geocode_search(hp_geo_query, limit=5)
            if st.session_state.get("hp_geo_search_results"):
                for gi, candidate in enumerate(st.session_state.hp_geo_search_results):
                    hgcol1, hgcol2 = st.columns([4, 1])
                    with hgcol1:
                        st.write(f"**{candidate['display_name']}**")
                        st.caption(f"{candidate['latitude']:.6f}, {candidate['longitude']:.6f} ({candidate.get('type', '')})")
                    with hgcol2:
                        if st.button("Use this", key=f"hp_geo_pick_{gi}"):
                            data["manual_latitude"] = candidate["latitude"]
                            data["manual_longitude"] = candidate["longitude"]
                            st.session_state.hp_geo_confirmed = False
                            st.session_state.hp_geo_search_results = None
                            st.rerun()

            st.markdown("**Or paste a Google Maps link:**")
            st.caption("Find the place in Google Maps, hit Share (or copy the address-bar URL), and paste "
                      "it here - coordinates are read out automatically, or geocoded from the place "
                      "name if the link itself has none (common for a mobile Share link).")
            hp_maps_url = st.text_input("Google Maps link", key="hp_geo_maps_url", placeholder="https://maps.google.com/...")
            if st.button("🔗 Use this link's coordinates", key="hp_geo_maps_url_btn", disabled=not hp_maps_url.strip()):
                with st.spinner("Reading coordinates from the link..."):
                    hp_url_geo = parse_google_maps_url(hp_maps_url)
                if hp_url_geo["valid"]:
                    data["manual_latitude"] = hp_url_geo["latitude"]
                    data["manual_longitude"] = hp_url_geo["longitude"]
                    st.session_state.hp_geo_confirmed = False
                    # A message set here would be wiped by the rerun below before it's ever seen -
                    # stash it in session_state instead, shown once at the top of this expander.
                    st.session_state.hp_geo_link_note = (
                        f"ℹ️ That link had no coordinates of its own - geocoded its place name instead "
                        f"({hp_url_geo.get('name') or 'match found'}). Double-check the pin above looks "
                        f"right before confirming."
                    ) if hp_url_geo.get("source") == "geocoded from link" else None
                    st.rerun()
                else:
                    st.error(hp_url_geo["error"])

        # 2026-09-16: master-data-sourced coordinates need no human check (see builder.py's
        # "MASTER-DATA COORDINATES" comment) - pre-ticks below, once per resolution, without
        # overriding a deliberate uncheck on a later rerun (hp_geo_auto_confirmed_for tracks that).
        if hp_geo.get("source") == GEOLOCATION_SOURCE_CONFIRMED_MASTER:
            if st.session_state.get("hp_geo_auto_confirmed_for") != GEOLOCATION_SOURCE_CONFIRMED_MASTER:
                st.session_state.hp_geo_confirmed = True
                st.session_state.hp_geo_auto_confirmed_for = GEOLOCATION_SOURCE_CONFIRMED_MASTER
        else:
            st.session_state.hp_geo_auto_confirmed_for = None

        # CONFIRMED REAL BUG (reported 2026-09-06): this used to pass BOTH `key="hp_geo_confirmed"`
        # AND `value=...` to the checkbox - unlike Ticket's own, already-proven tk_geo_confirmed
        # checkbox (which never combines a widget key with an explicit value=), that combination
        # can leave the checkbox stuck showing its stale/disabled state on some Streamlit versions.
        # Matching Ticket's exact pattern: no key on the widget itself, read/write the confirmed
        # flag through session_state explicitly instead.
        st.session_state.hp_geo_confirmed = st.checkbox(
            "✅ I've checked this location on the map and it's correct for this hotel",
            value=st.session_state.get("hp_geo_confirmed", False),
            disabled=not hp_geo.get("valid"),
        )
    hp_geo_confirmed = st.session_state.get("hp_geo_confirmed", False)

    # PRICE AUDIT (product owner, 2026-09-12) - secondary placement. When the human is CHECKING
    # the current period (see the contract-purpose question above), this same tool was already
    # shown earlier, as the primary thing to do - see _render_hotel_price_audit_section's own
    # docstring for why it isn't rendered twice. When they're instead adding a NEW price period
    # (or this is a brand-new hotel, where the question above never even appears), it's offered
    # here instead, right before Publish, as a secondary sanity check rather than the main event.
    if existing_snapshot and hp_contract_purpose != "check_current":
        _render_hotel_price_audit_section(data, primary=False)

    # ------------------------------------------------------------------
    # PUBLISH - two phases, in order
    # ------------------------------------------------------------------
    st.markdown("#### Publish")

    with st.expander("🔎 Preview hotel contract payload (phase 1)"):
        st.json(contract_result["hotel_payload"])

    seasons_total = sum(len(r.get("seasons") or []) for r in (data.get("rates") or []))
    priced_rooms = sum(
        1 for r in (data.get("rates") or []) for s in (r.get("seasons") or [])
        for rp in (s.get("room_prices") or []) if rp.get("distribution_prices")
    )
    images_ok = bool(contract_result["hotel_payload"].get("images"))
    st.caption(f"Ready to publish: **{len(contract_result['hotel_payload'].get('rooms') or [])}** room(s), "
              f"**{len(contract_result['hotel_payload'].get('mealPlans') or [])}** meal plan(s), "
              f"**{len(data.get('offers') or [])}** offer(s), **{len(data.get('supplements') or [])}** "
              f"supplement(s), **{len(data.get('rates') or [])}** rate(s) with **{seasons_total}** season(s), "
              f"**{len(contract_result['hotel_payload'].get('images') or [])}** image(s).")

    rooms_ok = bool(room_names) and not rooms_missing_dist
    # CONFIRMED PRODUCT-OWNER DECISION (2026-08-19 audit): Hotel used to be the only one of the
    # five publish flows that let a record go live with zero priced rooms - just a warning, not
    # a blocked button, unlike Transfer's hard match/dates/geoloc gates. Now blocked to match.
    if not priced_rooms:
        st.error("⚠️ No room in any season has prices yet - publishing is blocked until at least "
                 "one room has a price, so nothing unsellable goes live.")

    # CONFIRMED BUG FIX (reported 2026-09-02): Travel Compositor's createHotel/updateHotel
    # rejects an empty images list outright - "contract.images: Size must be between 1 and
    # 2147483647 ([])" - a raw technical error surfacing only AFTER the publish call, by which
    # point everything else about the hotel was already validated and ready. Same "block before
    # the API call, not after" pattern as the priced_rooms gate right above: caught here with a
    # clear, actionable message instead of letting it reach Travel Compositor at all.
    if not images_ok:
        st.error("⚠️ No image has been added yet - Travel Compositor requires at least one image to "
                 "publish a hotel. Use the Images section above (search stock photos, or pick one found "
                 "on the hotel's page/document) before publishing.")
    else:
        _warn_stale_images(data.get("images"))
        # CONFIRMED REAL PUBLISH FAILURE (reported 2026-09-06, HRG-T1): a picked image measuring
        # under Travel Compositor's hard 500x400 minimum used to reject the ENTIRE publish with a
        # raw "Minimum size of 500x400 required, WxH found" error. It's now dropped automatically
        # before publish (falling back to a placeholder if nothing else is left) - this just lets
        # the human know it happened, since the image list they see above still shows the
        # original pick.
        _hp_dropped_images = contract_result.get("images_dropped_too_small") or []
        if _hp_dropped_images:
            st.warning(f"⚠️ {len(_hp_dropped_images)} image(s) were skipped for being smaller than "
                      f"Travel Compositor's required 500x400 minimum, so publishing isn't blocked: "
                      f"{', '.join(_hp_dropped_images)}")

    geo_ok = bool(hp_geo.get("valid")) and hp_geo_confirmed
    if not geo_ok:
        st.error("⚠️ Geolocation isn't confirmed yet - Travel Compositor rejects a hotel whose "
                 "coordinates don't fall inside any of its known destinations, so a human must "
                 "check the map above and tick the confirmation box before publishing.")

    if st.button(f"🚀 Publish — {'UPDATE' if existing_snapshot else 'CREATE'} hotel {provider_code}",
                 type="primary", key="hp_publish", disabled=not rooms_ok or not priced_rooms or not images_ok or not geo_ok):
        st.session_state.hp_publish_succeeded = False
        progress = st.container()
        try:
            # ---- PHASE 1: the hotel contract itself (rooms + meal plans inline) ----
            # CONFIRMED REAL BUG, part 2 (reported 2026-09-06, HRG-H1 - Steigenberger Golf Resort
            # El Gouna, a 100%-brand-new hotel with zero pre-existing rooms): the 2026-09-05 fix
            # assumed submitting AT MOST ONE brand-new room (providerCode still None) inline in the
            # main create/update call was safe, based on the only real precedent this tool had
            # (CAI-H1, Four Seasons Cairo, which came back with providerCode
            # "AUTO_jr9fFXzBSX1YlVmTLVOw8PuP"). That precedent turned out to be a GET of an
            # already-populated hotel record - never an observed create-time success - so it never
            # actually proved a null-providerCode room inline was accepted. Real production errors
            # (2026-09-06, then again 2026-09-11 on the same hotel) proved the opposite: a room
            # with providerCode left null is rejected outright -
            #     "Bean Validation constraint(s) violated on callback event:'prePersist'.
            #      Errors: HotelContractRoom.providerCode:must not be null ( Id: null)"
            # - AND (2026-09-11, confirmed via the zero-rooms attempt's own separate error)
            # ContractHotelVO.rooms genuinely does require at least 1 item -
            #     "createHotel.contract.rooms: Size must be between 1 and 2147483647 ([])"
            # - a real deadlock for a 100%-new hotel: no combination of "zero new rooms" or "one
            # null-providerCode room inline" can satisfy both constraints at once.
            #
            # NEW APPROACH (2026-09-11, re-reading the real Swagger for POST/PUT /hotel/{supplierId}
            # with the product owner): the room providerCode field in that request body's own
            # schema is a plain, normal string - not marked read-only, and identical in shape to the
            # response schema. Nothing in the Swagger says it must be server-assigned; the earlier
            # "never invent one ourselves" rule was inferred only from GET examples showing
            # "AUTO_..." codes, never actually tested against a client-supplied value. Since the
            # hotel's OWN providerCode is already confirmed human-assigned (e.g. "HRG-H1"), it's a
            # reasonable bet that a ROOM's providerCode can be too. So the new default first attempt
            # generates a simple, deterministic placeholder code for every not-yet-coded room
            # (derived from the hotel's own code + the room name) and sends the FULL rooms[] array -
            # every room type in the contract - inline in ONE call, instead of the old empty-list-
            # then-add-one-at-a-time dance. If Travel Compositor keeps our placeholder or replaces
            # it with its own, either is fine - resolve_room_provider_codes() below reads whatever
            # the RESPONSE actually says, never what we sent.
            #
            # The two previously-known shapes are kept as fallbacks, tried in order, only if this
            # new attempt is itself rejected for a room-shaped reason (e.g. Travel Compositor
            # rejects a client-supplied room code specifically) - so a brand-new hotel is no worse
            # off than before if this hypothesis turns out wrong, and updates to an EXISTING hotel
            # (which never hit the zero-rooms deadlock to begin with) get the same one-call
            # simplification as a bonus when it works.
            all_rooms = contract_result["hotel_payload"].get("rooms") or []
            rooms_with_code = [r for r in all_rooms if r.get("providerCode")]
            new_rooms = [r for r in all_rooms if not r.get("providerCode")]

            def _hp_placeholder_room_code(room, index):
                slug = re.sub(r"[^A-Za-z0-9]+", "", (room.get("name") or "")).upper()[:16] or "ROOM"
                return f"{provider_code}-{slug}-{index + 1}"

            all_rooms_with_placeholder_codes = list(rooms_with_code)
            for _i, _r in enumerate(new_rooms):
                _r2 = dict(_r)
                _r2["providerCode"] = _hp_placeholder_room_code(_r, _i)
                all_rooms_with_placeholder_codes.append(_r2)

            # Three room shapes to try, in order:
            #  1. NEW default - every room, new ones given a generated placeholder code.
            #  2. Old zero-new-rooms shape - only rooms that already carry a REAL code.
            #  3. Old one-new-room-inline shape (providerCode left null) - last resort, previously
            #     confirmed broken, kept only because it's the only other combination ever tried.
            _hp_room_candidates = [all_rooms_with_placeholder_codes, rooms_with_code, rooms_with_code + new_rooms[:1]]
            _hp_room_candidate_idx = 0
            phase1_payload = dict(contract_result["hotel_payload"])
            phase1_payload["rooms"] = _hp_room_candidates[_hp_room_candidate_idx]

            # DEBUG CAPTURE (added 2026-09-11, HRG-H1 real publish failure): every phase-1 attempt
            # (empty-rooms, then the one-new-room-inline fallback if that's tried) is recorded here
            # - which rooms[] shape was actually sent and the exact raw response - so a real
            # failure can be diagnosed from the true, UNMERGED text of each attempt instead of
            # guessing from whichever error happened to reach show_publish_error last. Same pattern
            # as price_refresh.py's per-route request/response debug capture (2026-09-11).
            _hp_phase1_attempts = []

            # CONFIRMED REAL BUG (reported 2026-09-06, HRG-H1): the in-tool 500x400 size check
            # above (image_dimensions.py) doesn't catch every way Travel Compositor can reject a
            # picked image - a later attempt was rejected outright with "Not valid image" for an
            # image that measured fine locally. Rather than block on an image issue this tool
            # can't fully predict client-side, retry with that ONE image removed (falling back to
            # the shared placeholder if it was the last one) - up to once per image in the list,
            # so a handful of bad picks can't loop forever.
            _hp_image_retries_left = len(phase1_payload.get("images") or [])
            while True:
                with st.spinner("Phase 1 of 2 — publishing the hotel contract, rooms and meal plans..."):
                    if existing_snapshot:
                        hotel_response = client.update_hotel(supplier_id, phase1_payload)
                    else:
                        hotel_response = client.create_hotel(supplier_id, phase1_payload)

                _hp_phase1_attempts.append({
                    "candidate_idx": _hp_room_candidate_idx,
                    "rooms_sent": [{"name": r.get("name"), "providerCode": r.get("providerCode")}
                                   for r in phase1_payload.get("rooms") or []],
                    "response": hotel_response,
                })

                if not (isinstance(hotel_response, dict) and "error" in hotel_response):
                    break

                _hp_error_text = str(_extract_error_message_detail(hotel_response) or "")
                _hp_bad_image = _extract_rejected_image_url(hotel_response)
                _hp_current_images = phase1_payload.get("images") or []
                if _hp_image_retries_left > 0 and _hp_bad_image and _hp_bad_image in _hp_current_images:
                    _hp_new_images = [u for u in _hp_current_images if u != _hp_bad_image] or [FALLBACK_IMAGE]
                    if _hp_new_images == _hp_current_images:
                        show_publish_error(f"publish hotel **{provider_code}**", hotel_response)
                        return
                    phase1_payload["images"] = _hp_new_images
                    _hp_image_retries_left -= 1
                    progress.warning(f"⚠️ Travel Compositor rejected this image at publish time, so it's "
                                     f"being skipped and publishing retried: {_hp_bad_image}")
                    continue

                if (_hp_room_candidate_idx < len(_hp_room_candidates) - 1
                        and "room" in _hp_error_text.lower()):
                    # Escalate to the next room shape on any room-related complaint - each
                    # remaining candidate is strictly more conservative (fewer/no new rooms) than
                    # the one just rejected, so stepping forward can't make things worse.
                    _hp_room_candidate_idx += 1
                    phase1_payload["rooms"] = _hp_room_candidates[_hp_room_candidate_idx]
                    if _hp_room_candidate_idx == 1:
                        progress.warning("⚠️ Travel Compositor rejected the attempt that included "
                                         "every room type with a generated code, so publishing is "
                                         "being retried with no new rooms attached yet (only rooms "
                                         "that already have a real Travel Compositor code).")
                    else:
                        progress.warning("⚠️ Travel Compositor rejected the hotel with no new rooms "
                                         "attached yet, so publishing is being retried with one new "
                                         "room included. Note: that fallback room still has no "
                                         "providerCode either (Travel Compositor only assigns one "
                                         "after a room is created), so if it fails too, that's a "
                                         "known dead end this app cannot currently work around alone - "
                                         "see the debug expander below.")
                    # 2026-09-08: surface the rejected attempt's raw error (was discarded).
                    with progress.expander(f"Technical details — attempt {_hp_room_candidate_idx} rejected"):
                        st.code(_hp_error_text or "(no detail)")
                    continue

                with progress.expander("🔍 Raw request/response per attempt (debug)"):
                    for _i, _att in enumerate(_hp_phase1_attempts, start=1):
                        st.markdown(f"**Attempt {_i}** — rooms sent: `{_att['rooms_sent']}`")
                        st.code(str(_att["response"]))
                show_publish_error(f"publish hotel **{provider_code}**", hotel_response)
                return

            progress.success("✅ Phase 1 — hotel contract, rooms and meal plans published.")

            # AUTOMAP REMINDER (product owner, 2026-09-13: "we must make sure that Automap with
            # master is also set, so the hotel is not a duplicate in the travel compositor
            # surface"). Only for a genuinely NEW hotel - an existing one was already mapped (or
            # deliberately not) when it was first created, and re-raising it on every price update
            # would train people to ignore this notice, which is the one thing it cannot survive.
            #
            # This is a REMINDER rather than an action because the automap genuinely cannot be set
            # through the API: neither ContractHotelDetailedVO nor the read-only "Web content -
            # Accommodations" section exposes it (confirmed against the real Swagger - see
            # hotel_automap.py's own docstring for the full field-by-field check). So the honest
            # thing is to hand over the two ids and say plainly that a human has to finish it.
            if not existing_snapshot:
                _hp_md_seed_final = st.session_state.get("hp_masterdata_seed") or {}
                _hp_accommodation_id = _hp_md_seed_final.get("accommodation_id")
                _hp_giata_id = _hp_md_seed_final.get("giata_id")
                hotel_automap.record_pending(
                    supplier_id=supplier_id,
                    provider_code=provider_code,
                    hotel_name=phase1_payload.get("hotelname") or "",
                    accommodation_id=_hp_accommodation_id,
                    giata_id=_hp_giata_id,
                    master_name=_hp_md_seed_final.get("master_name") or _hp_md_seed_final.get("name"),
                    skip_reason=st.session_state.get("hp_masterdata_skip_reason"),
                )
                if _hp_accommodation_id:
                    progress.warning(
                        f"🔗 **One manual step left in Travel Compositor.** This hotel was seeded "
                        f"from master data, but Travel Compositor's API has no way to set "
                        f"**Automap with master** — it can only be done in the back office. Until "
                        f"it is, this contract can show up as a duplicate property.\n\n"
                        f"- Hotel: **{provider_code}** — {phase1_payload.get('hotelname') or ''}\n"
                        f"- Map it to accommodation id: **{_hp_accommodation_id}**"
                        + (f"\n- GIATA code: **{_hp_giata_id}**" if _hp_giata_id else "")
                        + (f"\n- Master record name: {_hp_md_seed_final.get('master_name') or _hp_md_seed_final.get('name')}"
                           if (_hp_md_seed_final.get("master_name") or _hp_md_seed_final.get("name")) else "")
                        + "\n\nIt's saved under **Hotels awaiting automap** so it isn't lost if you "
                          "can't do it right now."
                    )
                else:
                    progress.warning(
                        f"🔗 **Check this one in Travel Compositor.** No master record was linked "
                        f"to **{provider_code}**"
                        + (f" (reason given: _{st.session_state.get('hp_masterdata_skip_reason')}_)"
                           if st.session_state.get("hp_masterdata_skip_reason") else "")
                        + ", so there's nothing to automap it to. Worth confirming the property "
                          "really isn't already in Travel Compositor's master data, since that's "
                          "what creates a duplicate. Saved under **Hotels awaiting automap**."
                    )
            if len(_hp_phase1_attempts) > 1:
                with progress.expander("🔍 Raw request/response per attempt (debug)"):
                    for _i, _att in enumerate(_hp_phase1_attempts, start=1):
                        st.markdown(f"**Attempt {_i}** — rooms sent: `{_att['rooms_sent']}`")
                        st.code(str(_att["response"]))

            # Every brand-new room NOT included inline in whichever shape actually succeeded above
            # is added here afterward, one at a time - normally NONE, now that the default first
            # attempt (candidate 0) already inlines every room type with a generated code; this
            # loop only does real work if that attempt was rejected and candidate 1 or 2 (which
            # hold back some/all new rooms) is what actually succeeded. Each response is merged
            # into the same room list resolve_room_provider_codes reads below, so phase 2
            # (offers/supplements/rates) sees every room regardless of which call actually created it.
            _hp_inline_new_room_count = len(_hp_room_candidates[_hp_room_candidate_idx]) - len(rooms_with_code)
            extra_new_rooms = new_rooms[_hp_inline_new_room_count:]
            all_room_responses = list(hotel_response.get("rooms") or [])
            if extra_new_rooms:
                room_add_failures = []
                with st.spinner(f"Adding {len(extra_new_rooms)} more room(s) one at a time..."):
                    for room_payload in extra_new_rooms:
                        room_resp = client.create_hotel_room(supplier_id, provider_code, room_payload)
                        if isinstance(room_resp, dict) and "error" in room_resp:
                            room_add_failures.append((room_payload.get("name") or "(unnamed)", room_resp.get("message")))
                        elif isinstance(room_resp, dict):
                            all_room_responses.append(room_resp)
                added_ok = len(extra_new_rooms) - len(room_add_failures)
                if added_ok:
                    progress.success(f"✅ Added {added_ok} more room(s).")
                for name, msg in room_add_failures:
                    progress.error(f"⚠️ Couldn't add room **{name}**: {msg}")

            # Travel Compositor assigns each room its providerCode here - phase 2 can't run without them.
            room_map = resolve_room_provider_codes(all_room_responses)
            unresolved = [n for n in room_names if not room_map.get(n)]
            if unresolved:
                progress.warning(f"⚠️ Travel Compositor didn't return a code for these room(s): "
                                f"{', '.join(unresolved)}. Their prices will be skipped in phase 2.")

            # CONFIRMED REAL BUG (2026-09-11, HRG-H1): an offer/supplement that names no specific
            # room or meal plan ("applies to all rooms"/"all meal plans", per ai_extractor.py's
            # own documented convention) used to be sent with an EMPTY providerRoomCodes/mealPlans
            # array - Travel Compositor requires both non-empty, so every hotel-wide offer/
            # supplement (Early Bird discounts, compulsory Gala Dinners, Half Board/Club Package
            # supplements - the common case) failed outright. build_hotel_offer_payloads/
            # build_hotel_supplement_payloads now resolve "applies to everything" into the hotel's
            # actual current meal-plan list, read straight from the Phase 1 payload that was just
            # published (so it reflects every meal plan really on the hotel, new or preserved).
            hotel_meal_plan_types = [mp.get("mealPlan") for mp in (phase1_payload.get("mealPlans") or [])
                                      if mp.get("mealPlan")]

            # ---- PHASE 2a: offers ----
            offer_map = {}
            offer_results = build_hotel_offer_payloads(data.get("offers") or [], room_map,
                                                        existing_hotel_snapshot=existing_snapshot,
                                                        hotel_meal_plan_types=hotel_meal_plan_types,
                                                        hotel_provider_code=provider_code)
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
                                                            existing_hotel_snapshot=existing_snapshot,
                                                            hotel_meal_plan_types=hotel_meal_plan_types,
                                                            hotel_provider_code=provider_code)
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
            # room_name_to_distributions feeds the missing-distribution-price safety net (see
            # builder._fill_missing_distribution_prices, 2026-09-11 HRG-H1 fix) - every room's own
            # allowed occupancy list, straight from what extraction gave for "rooms" (same data
            # that already went into Phase 1's room payloads).
            room_name_to_distributions = {
                (r or {}).get("name"): (r or {}).get("distributions") or []
                for r in data.get("rooms") or [] if (r or {}).get("name")
            }
            rate_results = build_hotel_rate_payloads(data.get("rates") or [], room_map, offer_map,
                                                      supplement_map, existing_hotel_snapshot=existing_snapshot,
                                                      room_name_to_distributions=room_name_to_distributions)
            rate_failures = []
            rate_warnings_all = []
            rate_unchanged_names = []
            with st.spinner("Phase 2 of 2 — publishing rates and seasons..."):
                for res in rate_results:
                    rate_warnings_all.extend(res.get("rate_warnings") or [])
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
                    # CONFIRMED PRODUCT-OWNER RULE (2026-09-16): "when rechecking the current
                    # price data, we only must upload/change the information that really was
                    # detected as change. Not everything needs a complete update." - see
                    # builder._hotel_rate_payload_unchanged. Nothing to send, so nothing is sent -
                    # not even a no-op PUT.
                    if res["action"] == "unchanged":
                        rate_unchanged_names.append(res.get("rate_name"))
                        continue
                    if res["action"] == "update":
                        resp = client.update_hotel_rates(supplier_id, provider_code, res["rate_payload"])
                    else:
                        resp = client.create_hotel_rates(supplier_id, provider_code, res["rate_payload"])
                    if isinstance(resp, dict) and "error" in resp:
                        rate_failures.append((res["rate_payload"].get("name"), resp.get("message")))

            if rate_warnings_all:
                # Non-blocking - a missing distribution price was safely filled by reusing the
                # price already given for the same total occupancy (see
                # builder._fill_missing_distribution_prices, 2026-09-11 HRG-H1 fix). Surfaced so
                # a human can double-check the filled figure is actually right for that combo.
                progress.info("ℹ️ Filled in some missing room prices by reusing the price already "
                              "given for the same number of guests:\n\n" +
                              "\n".join(f"- {note}" for note in rate_warnings_all))

            # CONFIRMED REAL BUG (2026-09-12, HRG-H1): a republish within the SAME session (e.g.
            # "fix these and publish again", exactly what the message below invites) reused the
            # `existing_snapshot` fetched at the START of this Step 4 visit - stale the moment
            # ANY offer/supplement/room actually got created just now. Since offers/supplements
            # use a deterministic placeholder providerCode derived from name+index (see
            # _hotel_offer_supplement_placeholder_code), the SAME offer regenerates the SAME code
            # on the next attempt - the server correctly rejects it as already existing, but our
            # own dedup (hotel_matcher.match_offer_or_supplement_by_name, which is what's supposed
            # to catch exactly this) never even ran against it, because it was still comparing
            # against the pre-publish snapshot. Re-fetching now means the NEXT publish attempt in
            # this same session (no page reload needed) correctly recognizes everything that just
            # went live and skips re-creating it, instead of colliding on its own placeholder code.
            try:
                _hp_refreshed_snapshot = client.get_hotel(supplier_id, provider_code)
                if isinstance(_hp_refreshed_snapshot, dict) and "error" not in _hp_refreshed_snapshot:
                    st.session_state.hp_existing_snapshot = _hp_refreshed_snapshot
            except Exception:
                pass  # best-effort - a failed refresh just means the OLD snapshot is used next time,
                      # same as before this fix existed; never let this block the result being shown.

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
                if rate_unchanged_names:
                    st.caption(f"ℹ️ {len(rate_unchanged_names)} rate(s) matched exactly what was already "
                              f"live and were left alone - nothing to change, nothing sent: " +
                              ", ".join(f"**{n}**" for n in rate_unchanged_names))
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
