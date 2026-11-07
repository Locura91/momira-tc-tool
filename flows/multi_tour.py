"""
Multi-tour (ClosedTour) product flow, split out of app.py (Phase 1 restructure).

REDESIGNED 2026-11: now supports creating MULTIPLE ClosedTours from one document upload,
modeled on the multi-ticket batch flow (flows/multi_ticket.py). Previously forced the user
to pick ONE tour per run; now detects multiple, lets the user select which ones to create
(each with its own Tour Code), reviews each tour sequentially (main info → modalities →
per-modality pricing → final review), then publishes all confirmed tours one by one.

Everything it references that is defined at app.py's own top level is imported back from app
via the same late-binding pattern used by flows/ticket.py, flows/hotel.py and
flows/multi_ticket.py: app.py imports this module only after all of those names are already
defined in its own namespace, so `from app import ...` resolves correctly despite the circular
import shape.
"""
import os
import copy
import tempfile
import pandas as pd
import streamlit as st

from schemas import HumanPreConfig
from builder import (build_closed_tour_payloads, coerce_price_list_shape,
                     fix_touching_season_boundaries, split_nested_price_list_seasons)
from document_reader import extract_raw_text, extract_images
from document_reader import scanned_document_warning as document_reader_scanned_warning
from ai_extractor import (
    apply_clarification, detect_multiple_modalities, detect_tour_variants,
    extract_modality_data, extract_structured_data, friendly_error_message,
    min_pax_forces_on_request, min_pax_guaranteed_departure_note,
)
from pexels_client import search_images
from pixabay_client import search_images as search_images_pixabay
from r2_client import upload_images_with_errors as upload_images_r2_with_errors
import cancellation_links
from date_format import to_iso_date as _iso, to_display_date as _disp
from image_dimensions import FALLBACK_IMAGE
from app_helpers import reset_session_keep_core
from ui_components import (
    editable_table, editable_field, render_cancellation_policy_editor,
    render_closable_image_section, render_url_image_picker, render_doc_image_picker,
    render_auto_added_image_review,
    render_stock_photo_picker, render_child_age_band, render_child_discount_editor,
    render_closedtour_supplements, render_currency_check, render_extra_child_notice,
    render_optional_time_input, render_stop_sales_editor,
    _add_page_images_to_doc_pool, _safe_cell_str,
)

from app_helpers import (
    ALL_WEEKDAYS, CURRENCY_OPTIONS, HOUSE_RULE_CODEWORD, SHARED_WIDGET_STATE_PREFIXES,
    _apply_min_pax_guaranteed_departure_note, _clean_modality_code, _clear_batch_widget_state,
    _fetch_url_text_safe, gather_source_content, _modality_code_suspicious, _new_mct_tour, _reset_mct_state,
    _warn_page_image_upload_errors, _warn_stale_images, apply_clarify_changes,
    bump_widget_generation, check_code_availability, check_duplicate_tour_name,
    clarify_supplier_id, flow_widget_key, mark_code_as_taken, remember_clarification,
    remember_memory_panel, render_candidate_filter, render_clarify_result,
    render_house_rule_shortcut, render_skip_item_button, render_supplement_zero_price_notes,
    reset_child_age_band_widgets,
    reset_stale_editable_field_widgets, show_publish_error, try_code_variants, with_learned_guidance,
)


def _mct_generate_split_modality_code(parent_code, nested_row, existing_codes):
    """A unique Modality Code for a season auto-split out of `parent_code` by
    split_nested_price_list_seasons (see its docstring in builder.py, and the CONFIRMED
    PRODUCT-OWNER RULE - 2026-09-18 - it implements). Built from the nested season's own name
    when it has one (e.g. "Peak Season" -> "CABIN-PEAKSEASON"), falling back to its date range
    when it doesn't, run through the same _clean_modality_code sanitizing every other
    AI-suggested code in this app already goes through so it can't fail the same way a stray "."
    or "/" would. A numeric suffix is appended only if that exact code is somehow already taken
    (belt-and-braces - collisions should be rare given the season name is normally unique per
    Modality) so this never silently reuses another Modality's code."""
    season_label = (nested_row or {}).get("name") or (
        f"{(nested_row or {}).get('startDate', '')}-{(nested_row or {}).get('endDate', '')}")
    base = _clean_modality_code(f"{parent_code}{season_label}".replace(" ", ""))[:40] or f"{parent_code}SPLIT"
    existing = set(existing_codes or [])
    if base not in existing:
        return base
    n = 2
    while f"{base}{n}" in existing:
        n += 1
    return f"{base}{n}"


def render_multi_tour_flow(client, supplier_id, currency, on_request, release_days, url, uploaded_files,
                          min_pax=1, max_pax=9, default_tour_code="", extraction_hint=None):
    """
    Multi-ClosedTour batch create flow. Detects multiple distinct tours
    in one document upload and lets the user select which ones to create,
    each with its own Tour Code. Each selected tour goes through:
      reviewing_main → select_modalities → reviewing_modality → final_review
    then all confirmed tours are published sequentially.

    For a single-tour document, behaviour is identical to before - no queue
    UI, just one tour flowing through the same phases.
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
                    combined_parts, doc_raw_images, doc_image_urls, seen_image_hashes = \
                        gather_source_content(url, uploaded_files)

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
    # PHASE 2: select which ClosedTour(s) to create. Single-tour documents
    # auto-proceed (queue of 1, no selection UI). Multi-tour documents show
    # checkboxes + per-tour Tour Code fields, like multi_ticket's prepare_queue.
    # ------------------------------------------------------------------
    if st.session_state.mct_phase == "select_tour":
        candidates = st.session_state.mct_candidates

        if len(candidates) <= 1:
            chosen = candidates[0] if candidates else {"label": "", "nights": None, "is_genuine_variant": False}
            st.session_state.mct_queue = [_new_mct_tour(chosen, default_tour_code)]
            st.session_state.mct_queue_index = 0
            st.session_state.mct_phase = "reviewing_main"
            st.rerun()
            return

        st.subheader(f"{len(candidates)} possible ClosedTours detected - choose which ones to create")
        st.caption("This document seems to describe more than one distinct tour product. Tick the ones "
                  "you want to create and assign each its own Tour Code. Modalities are set up in the "
                  "next steps, after the main tour info for each is confirmed.")

        distinct_nights = {c.get("nights") for c in candidates if c.get("nights") is not None}
        if len(distinct_nights) <= 1:
            st.warning(
                "🤔 These all report the same length - that often means this is really ONE tour with "
                "different Modalities (e.g. 'Standard' vs 'Superior' pricing/accommodation for the same "
                "itinerary), not genuinely different tour products. If so, just tick one and add the "
                "others as Modalities in the next steps."
            )

        render_candidate_filter(candidates, "mct_tour", "tour")

        for i, cand in enumerate(candidates):
            cand.setdefault("selected", True)
            cand.setdefault("tour_code", default_tour_code if i == 0 else "")
            c1, c2, c3 = st.columns([1, 4, 3])
            with c1:
                cand["selected"] = st.checkbox("Include", value=cand["selected"], key=f"mct_sel_{i}")
            with c2:
                nights_note = f" ({cand['nights']} nights)" if cand.get("nights") else ""
                cand["label"] = st.text_input(
                    "Tour Name", value=cand.get("label", ""), key=f"mct_label_{i}",
                    help=f"AI-detected tour name{nights_note}. Edit if needed."
                )
            with c3:
                cand["tour_code"] = st.text_input(
                    "Tour Code", value=cand.get("tour_code", ""), key=f"mct_code_{i}",
                    placeholder="e.g. BKK-1",
                    help="Your own reference code for THIS tour - make it up yourself."
                )

        if st.button("➕ Add another tour manually"):
            candidates.append({"label": "", "nights": None, "is_genuine_variant": False,
                              "selected": True, "tour_code": ""})
            st.rerun()

        missing_codes = []
        new_queue = []
        seen_codes = {}
        for cand in candidates:
            if not cand["selected"]:
                continue
            code = (cand.get("tour_code") or "").strip()
            if not code:
                missing_codes.append(cand.get("label") or "(unnamed tour)")
                continue
            seen_codes.setdefault(code, []).append(cand.get("label") or "(unnamed tour)")
            chosen_candidate = dict(cand)
            if len(distinct_nights) <= 1:
                chosen_candidate["is_genuine_variant"] = False
            new_queue.append(_new_mct_tour(chosen_candidate, code))

        duplicate_codes = {code: labels for code, labels in seen_codes.items() if len(labels) > 1}

        if missing_codes:
            st.error(f"🚫 These selected tours are missing a Tour Code: {missing_codes}")
        if duplicate_codes:
            for code, labels in duplicate_codes.items():
                st.error(f"🚫 Tour Code `{code}` is used by more than one selected tour ({', '.join(labels)}) "
                        f"- each tour needs its own unique code.")

        for q in new_queue:
            existing_check = check_code_availability(client, "tour", supplier_id, q["tour_code"])
            if existing_check and existing_check["exists"]:
                st.error(f"🚫 Tour Code `{q['tour_code']}` ({q['label'] or '(unnamed)'}) is ALREADY TAKEN "
                        f"by an existing tour (\"{existing_check.get('name') or '(unnamed)'}\") - choose a "
                        f"different code before continuing.")

        ready = new_queue and not missing_codes and not duplicate_codes
        st.caption(f"**{len(new_queue)}** tour(s) ready to review." if ready else
                  "Fix the issues above before continuing.")

        if st.button("➡️ Start Reviewing", type="primary", disabled=not ready):
            st.session_state.mct_queue = new_queue
            st.session_state.mct_queue_index = 0
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
        queue = st.session_state.mct_queue
        qidx = st.session_state.mct_queue_index
        tour = queue[qidx]
        _ks = f"_t{qidx}"  # per-tour widget key suffix to avoid stale state across tours

        if len(queue) > 1:
            st.progress(qidx / len(queue))
            st.subheader(f"Tour {qidx + 1} of {len(queue)}: {tour['label'] or tour['tour_code'] or '(new tour)'}")
        else:
            st.subheader(f"Tour details: {tour['label'] or tour['tour_code'] or '(new tour)'}")

        with st.expander("Not what you wanted?"):
            if st.button("🔙 Start over", key=f"mct_cancel_main{_ks}"):
                _reset_mct_state()
                st.rerun()

        if len(queue) > 1:
            render_skip_item_button(
                tour['label'] or tour['tour_code'], queue, qidx,
                "mct_queue", "mct_queue_index",
                ["mct_phase", "mct_raw_text", "mct_candidates", "mct_queue", "mct_queue_index",
                 "mct_doc_raw_images", "mct_hosted_image_candidates"],
                button_key=f"mct_skip{_ks}",
                widget_state_prefixes=["mct_"] + SHARED_WIDGET_STATE_PREFIXES
            )

        if tour["main_data"] is None:
            variant_hint = tour["label"] if tour.get("is_genuine_variant") else None
            with st.spinner(f"Extracting tour details{f' focused on ' + repr(tour['label']) if variant_hint else ''}..."):
                try:
                    tour["main_data"] = extract_structured_data(
                        st.session_state.mct_raw_text, variant_hint=variant_hint,
                        human_hint=with_learned_guidance(supplier_id, "ClosedTour", extraction_hint),
                        max_occupancy_hint=max_pax if max_pax and max_pax < 9 else None,
                    )
                    auto_images = list(dict.fromkeys(st.session_state.get("mct_hosted_image_candidates") or []))
                    tour["main_data"]["image_urls"] = auto_images or [FALLBACK_IMAGE]
                    reset_child_age_band_widgets(f"mct_main{_ks}")
                    tour["_cancellation_link_scope"] = cancellation_links.apply_cancellation_link_default(
                        tour["main_data"], supplier_id, "ClosedTour")
                except Exception as e:
                    st.error(f"⚠️ Couldn't extract tour details: {friendly_error_message(e)}")
                    if st.button("🔄 Retry extraction", key=f"mct_retry_main{_ks}"):
                        st.rerun()
                    return

        data = tour["main_data"]
        if not data.get("meeting_point"):
            data["meeting_point"] = ("Meet your guide in the airport arrival hall or, if you are already in "
                                     "the tour's starting city, in your hotel lobby.")

        tour["tour_code"] = st.text_input(
            "Tour Code", value=tour["tour_code"], key=f"mct_tour_code{_ks}",
            help="Your own reference code for this tour, e.g. 'BKK-1' - edit here if you want to change it."
        )
        _mct_code_check = check_code_availability(client, "tour", supplier_id, tour["tour_code"])
        if _mct_code_check and _mct_code_check["exists"]:
            st.error(f"🚫 Tour Code `{tour['tour_code']}` is ALREADY TAKEN by an existing tour "
                     f"(\"{_mct_code_check.get('name') or '(unnamed)'}\") - change it above before "
                     f"publishing, or this will fail at the very last step.")

        editable_field("Tour name", data, "tour_name", widget="text_input", key_suffix=f"_main{_ks}")
        editable_field("Description", data, "description", widget="html_text_area", height=150, key_suffix=f"_main{_ks}")
        editable_field("Hotels", data, "hotels_text", widget="html_text_area", height=100, key_suffix=f"_main{_ks}")
        editable_field("Included", data, "included", widget="html_list_area", height=100, key_suffix=f"_main{_ks}")
        editable_field("Excluded", data, "excluded", widget="html_list_area", height=100, key_suffix=f"_main{_ks}")
        editable_field("Meeting point", data, "meeting_point", widget="text_input", key_suffix=f"_main{_ks}")
        editable_field("Policy remarks", data, "policy_remarks", widget="text_area", height=80, key_suffix=f"_main{_ks}")
        editable_field("What to bring (added to voucher remarks)", data, "what_to_bring",
                       widget="text_area", height=80, key_suffix=f"_main{_ks}")
        if tour.get("_cancellation_link_scope"):
            st.caption(f"ℹ️ This document didn't state its own cancellation terms - the table below "
                      f"was filled in from {tour['_cancellation_link_scope']}. Edit or clear it if "
                      f"this tour needs different terms.")
        render_cancellation_policy_editor(data, f"mct_main{_ks}")
        editable_field("Nights", data, "nights", widget="number_input", key_suffix=f"_main{_ks}")

        tcol1, tcol2 = st.columns(2)
        with tcol1:
            render_optional_time_input("Start Time", data, "start_time", f"mct_start_time_main{_ks}")
        with tcol2:
            render_optional_time_input("End Time", data, "end_time", f"mct_end_time_main{_ks}", default_time_str="18:00:00")

        render_child_age_band(data, f"mct_main{_ks}")

        dest_rows = [{"#": i + 1, "Destination": d} for i, d in enumerate(data.get("itinerary_destinations", []))]
        dest_df = pd.DataFrame(dest_rows) if dest_rows else pd.DataFrame(columns=["#", "Destination"])

        def _save_mct_destinations(edited_df, data=data):
            data["itinerary_destinations"] = [
                str(row.get("Destination") or "").strip() for _, row in edited_df.iterrows()
                if _safe_cell_str(row.get("Destination")).strip()
            ]
        editable_table(
            "Itinerary destinations (in visit order)", dest_df, f"mct_destinations_main{_ks}",
            on_save=_save_mct_destinations,
            column_config={"#": st.column_config.NumberColumn(disabled=True, default=0)}
        )

        st.markdown("**Images**")
        if data.get("image_urls") == [FALLBACK_IMAGE] or not data.get("image_urls"):
            st.caption("⚠️ No real image picked yet - using a generic placeholder. Pick at least one real image below.")
        elif st.session_state.get("mct_hosted_image_candidates"):
            _mct_reviewed = render_auto_added_image_review(st.session_state.mct_hosted_image_candidates, f"mct_auto_img{_ks}")
            _mct_unchecked = [u for u in st.session_state.mct_hosted_image_candidates if u not in _mct_reviewed]
            if _mct_unchecked:
                data["image_urls"] = [u for u in data.get("image_urls", []) if u not in _mct_unchecked] or [FALLBACK_IMAGE]
        else:
            st.caption(f"{len([u for u in data.get('image_urls', []) if u != FALLBACK_IMAGE])} image(s) selected.")

        def _mct_add_doc_image():
            added = render_doc_image_picker(st.session_state.mct_doc_raw_images, f"mct_doc_main{_ks}")
            if added:
                current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                data["image_urls"] = current_imgs + [added]
                return 1
            return 0

        render_closable_image_section(
            bool(st.session_state.get("mct_doc_raw_images")),
            f"📥 Images needing hosting ({len(st.session_state.get('mct_doc_raw_images') or [])})",
            f"mct_doc_main{_ks}_closed", _mct_add_doc_image
        )

        mct_default_query = tour["label"] or data.get("tour_name", "") or (data.get("itinerary_destinations", [""])[0] if data.get("itinerary_destinations") else "")

        def _mct_add_pexels():
            selected = render_stock_photo_picker("Pexels", search_images, mct_default_query, f"mct_pexels_main{_ks}")
            if selected:
                current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                data["image_urls"] = current_imgs + selected
                return len(selected)
            return 0

        render_closable_image_section(True, "🖼️ Search free stock photos (Pexels)", f"mct_pexels_main{_ks}_closed", _mct_add_pexels)

        def _mct_add_pixabay():
            selected = render_stock_photo_picker("Pixabay", search_images_pixabay, mct_default_query, f"mct_pixabay_main{_ks}")
            if selected:
                current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                data["image_urls"] = current_imgs + selected
                return len(selected)
            return 0

        render_closable_image_section(True, "🖼️ Search free stock photos (Pixabay)", f"mct_pixabay_main{_ks}_closed", _mct_add_pixabay)

        render_closedtour_supplements(data, f"mct_main{_ks}")

        st.markdown("**🤖 Tell AI what to fix**")
        mct_clarify_q = st.text_input("Your message", key=f"mct_clarify_input_main{_ks}")
        if render_house_rule_shortcut(mct_clarify_q, "ClosedTour", f"mct_main{_ks}"):
            pass
        elif not mct_clarify_q.strip():
            st.caption(f"Type a message above first — Send stays disabled until there's something to send. "
                      f"Start with \"{HOUSE_RULE_CODEWORD}\" to save a standing rule for every ClosedTour "
                      f"supplier instead of a one-off fix.")
        if not mct_clarify_q.strip().upper().startswith(HOUSE_RULE_CODEWORD.upper()) and st.button(
                "Send", disabled=not mct_clarify_q.strip(), key=f"mct_clarify_send_main{_ks}"):
            with st.spinner("Thinking..."):
                result = apply_clarification(st.session_state.mct_raw_text, data, mct_clarify_q)
                st.session_state[f"mct_clarify_result_main{_ks}"] = result
                remember_clarification(clarify_supplier_id(supplier_id), "ClosedTour", mct_clarify_q, result)
                if result.get("changes"):
                    apply_clarify_changes(data, result, currency)
                    reset_stale_editable_field_widgets(result["changes"], key_suffix=f"_main{_ks}")
                    if "supplements" in result["changes"]:
                        st.session_state[f"_editing_table_mct_main{_ks}_supplements"] = False
                    if "itinerary_destinations" in result["changes"]:
                        st.session_state[f"_editing_table_mct_destinations_main{_ks}"] = False
                st.rerun()
        if st.session_state.get(f"mct_clarify_result_main{_ks}"):
            r = st.session_state[f"mct_clarify_result_main{_ks}"]
            render_clarify_result(r)
        remember_memory_panel(clarify_supplier_id(supplier_id), "ClosedTour", f"mctmain{_ks}")

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
        queue = st.session_state.mct_queue
        qidx = st.session_state.mct_queue_index
        tour = queue[qidx]
        _ks = f"_t{qidx}"
        main_name = (tour["main_data"] or {}).get("tour_name") or tour["tour_code"]
        st.subheader(f"Modalities for: {main_name}")

        if tour["modality_candidates"] is None:
            with st.spinner("Detecting pricing categories (Modalities)..."):
                try:
                    detected = detect_multiple_modalities(st.session_state.mct_raw_text)
                except Exception:
                    detected = []
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
        st.caption(
            "**One Modality = one version of the service.** A second Modality is only correct when the "
            "customer receives a genuinely different service — a different language (English vs German), "
            "a different cabin/room class (Standard vs Deluxe), a different inclusions tier, etc. "
            "Different prices at different times of year (High Season, Low Season, Peak, Shoulder) are "
            "NOT separate Modalities — they are price rows within ONE Modality. "
            "Untick any auto-detected candidates that are actually just seasonal pricing, edit codes/hints "
            "as needed, or add a second Modality manually only when the service truly differs."
        )

        suspicious_codes = [c["code"] for c in candidates if c["selected"] and _modality_code_suspicious(c["code"])]
        if suspicious_codes:
            st.warning(
                "🤔 These Modality Codes look unusually long/descriptive for a real code, which has "
                "caused real publish failures before (Travel Compositor rejects anything that isn't "
                "the short category name itself, e.g. 'Standard' not 'Standard English min. 2 people') "
                "- please shorten them to just the core category name: " + ", ".join(f"'{c}'" for c in suspicious_codes)
            )

        render_candidate_filter(candidates, f"mct_modcand{_ks}", "modality")

        for i, cand in enumerate(candidates):
            c1, c2, c3, c4 = st.columns([1, 2, 3, 1])
            with c1:
                cand["selected"] = st.checkbox("Include", value=cand["selected"], key=f"mct_modcand_sel{_ks}_{i}")
            with c2:
                cand["code"] = st.text_input(
                    "Modality Code", value=cand["code"], key=f"mct_modcand_code{_ks}_{i}",
                    help="Just the short category name, e.g. 'Standard' or 'Deluxe' - should NOT include "
                         "descriptive text like the language or a minimum-pax note."
                )
            with c3:
                cand["hint"] = st.text_input("AI focus hint (optional)", value=cand["hint"], key=f"mct_modcand_hint{_ks}_{i}")
            with c4:
                st.write("")
                if st.button("🗑️", key=f"mct_modcand_remove{_ks}_{i}", help="Remove this Modality"):
                    candidates.pop(i)
                    _clear_batch_widget_state(["mct_modcand_"])
                    st.rerun()

        if st.button("➕ Add another Modality manually", key=f"mct_add_mod{_ks}"):
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
        if st.button("➡️ Start Reviewing Modalities", type="primary", disabled=not ready, key=f"mct_start_review_mod{_ks}"):
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
                if st.button("🔙 Back to main tour info", key=f"mct_back_to_main{_ks}"):
                    st.session_state.mct_phase = "reviewing_main"
                    st.rerun()
            with wcol2:
                if st.button("🔙 Start over", key=f"mct_cancel_modsel{_ks}"):
                    _reset_mct_state()
                    st.rerun()
        return

    # ------------------------------------------------------------------
    # PHASE 5: review EACH Modality individually, one at a time
    # ------------------------------------------------------------------
    if st.session_state.mct_phase == "reviewing_modality":
        queue = st.session_state.mct_queue
        qidx = st.session_state.mct_queue_index
        tour = queue[qidx]
        _ks = f"_t{qidx}"
        modalities = tour["modalities"]
        midx = tour["modality_index"]
        mod = modalities[midx]

        if len(queue) > 1:
            st.caption(f"Tour {qidx + 1} of {len(queue)}: **{tour['label'] or tour['tour_code']}**")
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
            value=mod.get("hint", ""), key=f"mct_mod_hint{_ks}_{midx}", height=90,
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
                            mod["hint"] or mod["code"]),
                        # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-18) - see the matching
                        # extract_structured_data call above for the full quote and reasoning.
                        max_occupancy_hint=max_pax if max_pax and max_pax < 9 else None,
                    )
                    # CONFIRMED PRODUCT-OWNER RULE (2026-09-03): "add to remarks, if there is a
                    # minimum pax number needed for guaranteed departure." ClosedTour's only
                    # remarks field (Policy remarks) is shared across every Modality on the tour,
                    # so the note is prefixed with this Modality's code so a human reading it can
                    # tell which one it applies to.
                    _apply_min_pax_guaranteed_departure_note(
                        tour["main_data"], ("policy_remarks",),
                        mod["data"].get("min_pax_guaranteed_departure"), label=mod["code"])

                    # CONFIRMED PRODUCT-OWNER RULE (2026-09-18, verbatim, from a real live
                    # Travel Compositor screenshot of a published Modality's Prices tab): "End
                    # date must be one day before next season start date. If are two modalities
                    # in the same time, travel c gives an error, an extra modality has to be
                    # build." Two fixes, applied once here right after extraction - see
                    # fix_touching_season_boundaries and split_nested_price_list_seasons's own
                    # docstrings in builder.py for the full reasoning and their deliberate
                    # limits:
                    #   1. Two seasons sharing an exact boundary date are silently pulled one
                    #      day apart - pure date housekeeping, no note needed.
                    #   2. A season nested entirely inside another becomes its own new
                    #      Modality (appended to `modalities`, so it gets its own turn in this
                    #      same one-Modality-at-a-time review wizard), with the containing
                    #      season's price_list cut to leave a gap - Travel Compositor cannot
                    #      publish two overlapping price windows on one Modality.
                    mod["data"]["price_list"] = fix_touching_season_boundaries(mod["data"].get("price_list"))
                    # CONFIRMED PRODUCT-OWNER RULE (2026-10-05): DO NOT auto-create new Modalities
                    # from overlapping price_list seasons. The previous auto-split created modalities
                    # without human consent (user selected ONE modality, app produced two or more),
                    # broke stop sales (TC creates per-Modality stop sales which fail when there are
                    # extra auto-created codes the human didn't choose), and left date gaps in the
                    # remaining modality (Jul-Aug disappearing from the price table). Fix: detect the
                    # overlap and WARN, but keep the original price list intact so the human can edit
                    # the date ranges in the table below. If the periods genuinely need separate
                    # Modalities, the human can add one manually in the Modality selection step.
                    _remaining_price_list, _nested_seasons, _unhandled_nesting_notes = \
                        split_nested_price_list_seasons(mod["data"]["price_list"])
                    # Keep the ORIGINAL price list (pre-split) — don't update to _remaining_price_list.
                    # The overlapping rows stay visible so the human can fix dates manually.
                    for _nesting_note in _unhandled_nesting_notes:
                        st.warning(f"⚠️ {_nesting_note}")
                    for _nested_row in _nested_seasons:
                        st.warning(
                            f"⚠️ **'{_nested_row.get('name') or 'A season'}'** "
                            f"({_nested_row.get('startDate')} – {_nested_row.get('endDate')}) "
                            f"overlaps with another season in **'{mod['code']}'**'s price list. "
                            f"Travel Compositor cannot publish two overlapping price windows on one "
                            f"Modality. Please fix the date ranges in the Pricing table below so the "
                            f"periods don't overlap (adjust a start or end date on one of the rows). "
                            f"If these are genuinely two separate pricing categories available at the "
                            f"same time (e.g. Standard vs Deluxe), go back to Modality selection and "
                            f"add a second Modality code manually — do not rely on auto-split."
                        )
                    # (removed: the for-loop that appended auto-split Modalities to `modalities`)
                except Exception as e:
                    st.error(f"⚠️ Couldn't extract pricing for '{mod['code']}': {friendly_error_message(e)}")
                    if st.button("🔄 Retry extraction", key=f"mct_mod_retry{_ks}_{midx}"):
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

                # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-18): "the child discount must be
                # added to all modality fields" - each Modality is extracted independently by
                # its own AI call against the SAME source document, and doesn't always
                # re-detect a document-wide child_discount_percentage on every single Modality
                # even when the source states it once for the whole tour - the exact same class
                # of gap just fixed for supplements above. Falls back to Modality 1's own value
                # ONLY when THIS Modality's own extraction came back with none at all - a
                # Modality that DID detect its own (possibly genuinely different) discount keeps
                # it; nothing here can silently overwrite a real per-Modality value. Still fully
                # editable/removable per Modality below via render_child_discount_editor.
                if (midx > 0 and modalities[0]["data"]
                        and mod["data"].get("child_discount_percentage") is None):
                    mod["data"]["child_discount_percentage"] = \
                        modalities[0]["data"].get("child_discount_percentage")

        if st.button("🔄 Re-extract with updated hint", key=f"mct_mod_reextract{_ks}_{midx}"):
            mod["data"] = None
            bump_widget_generation(f"mct_mod{_ks}_{midx}")
            st.rerun()

        data = mod["data"]

        if data.get("schedule_notes"):
            st.info(f"🔎 {data['schedule_notes']}")

        if min_pax_forces_on_request(data.get("min_pax_guaranteed_departure")):
            st.warning(f"🔒 {min_pax_guaranteed_departure_note(data.get('min_pax_guaranteed_departure'))} "
                      f"This Modality will be published **On Request** regardless of the On Request "
                      f"setting above - a note was also added to Policy remarks.")

        data["operational_days"] = st.multiselect(
            "Operational Days", ALL_WEEKDAYS, default=data.get("operational_days", ALL_WEEKDAYS),
            key=flow_widget_key(f"mct_mod{_ks}_{midx}", "days")
        )
        render_stop_sales_editor(data, f"mct_mod{_ks}_{midx}")

        # CONFIRMED (product owner, 2026-08-19): "display the Currency within the modalities...
        # in case the human selected a wrong currency, so he could still change it... an extra
        # check." Only for a genuine create (this loop covers every Modality of the new tour) -
        # updates keep the existing tour's currency locked, per the rule above.
        currency = render_currency_check(currency, CURRENCY_OPTIONS, "cfg_currency", f"mct_mod_currency{_ks}_{midx}")

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
            # CONFIRMED PRODUCT-OWNER RULE (2026-09-18) - see the extraction-time wiring above
            # for the full quote/reasoning. Applied here too so a human manually editing this
            # table into a touching boundary gets the same silent fix, not just AI-extracted
            # data. Nested/overlapping seasons introduced by a manual edit are NOT auto-split
            # here (this callback only has this one Modality's data in scope, not the tour's
            # full Modality list needed to append a new one) - build_closed_tour_payloads has
            # its own belt-and-braces check for that case at publish time instead.
            data["price_list"] = fix_touching_season_boundaries(sorted(
                [_row_to_entry(r) for _, r in edited_df.iterrows() if _iso(_safe_cell_str(r.get("Start Date"))) and _iso(_safe_cell_str(r.get("End Date")))],
                key=lambda e: e.get("startDate", "")
            ))
        editable_table(f"Pricing - {mod['code']}", price_df, f"mct_mod_pricing{_ks}_{midx}", on_save=_save_mct_price_list)
        render_extra_child_notice(data, f"mct_mod{_ks}_{midx}")
        render_child_discount_editor(data, flow_widget_key(f"mct_mod{_ks}_{midx}", "cde"), currency)

        # CONFIRMED PRODUCT-OWNER CORRECTION: supplements belong to the TOUR, not to a
        # Modality - see render_closedtour_supplements. Edited once on the main tour screen.
        st.caption("💡 **Supplements are not set here.** A ClosedTour's supplements are set once "
                  "for the whole tour and apply to every Modality - they are on the main tour "
                  "screen, before the Modality list.")
        _tour_supplements = (tour.get("main_data") or {}).get("supplements") or []
        if _tour_supplements:
            st.caption("In force for this tour: " +
                       ", ".join(x.get("name", "(unnamed)") for x in _tour_supplements if isinstance(x, dict)))

        st.markdown(f"**🤖 Tell AI what to fix - {mod['code']}**")
        mct_mod_clarify_q = st.text_input("Your message", key=f"mct_mod_clarify_input{_ks}_{midx}")
        if render_house_rule_shortcut(mct_mod_clarify_q, "ClosedTour", f"mct_mod{_ks}_{midx}"):
            pass
        elif not mct_mod_clarify_q.strip():
            st.caption(f"Type a message above first — Send stays disabled until there's something to send. "
                      f"Start with \"{HOUSE_RULE_CODEWORD}\" to save a standing rule for every ClosedTour "
                      f"supplier instead of a one-off fix.")
        if not mct_mod_clarify_q.strip().upper().startswith(HOUSE_RULE_CODEWORD.upper()) and st.button(
                "Send", disabled=not mct_mod_clarify_q.strip(), key=f"mct_mod_clarify_send{_ks}_{midx}"):
            with st.spinner("Thinking..."):
                result = apply_clarification(st.session_state.mct_raw_text, data, mct_mod_clarify_q)
                st.session_state[f"mct_mod_clarify_result{_ks}_{midx}"] = result
                remember_clarification(clarify_supplier_id(supplier_id), "ClosedTour", mct_mod_clarify_q, result)
                if result.get("changes"):
                    apply_clarify_changes(data, result, currency)
                    reset_stale_editable_field_widgets(result["changes"], key_suffix=f"{_ks}_{midx}")
                    if "price_list" in result["changes"]:
                        st.session_state[f"_editing_table_mct_mod_pricing{_ks}_{midx}"] = False
                    if "supplements" in result["changes"]:
                        st.session_state[f"_editing_table_mct_mod_supplements{_ks}_{midx}"] = False
                    if "operational_days" in result["changes"]:
                        st.session_state.pop(f"mct_mod{_ks}_{midx}_days", None)
                    if "stop_sales" in result["changes"]:
                        st.session_state[f"_editing_table_mct_mod{_ks}_{midx}_stop_sales"] = False
                st.rerun()
        if st.session_state.get(f"mct_mod_clarify_result{_ks}_{midx}"):
            r = st.session_state[f"mct_mod_clarify_result{_ks}_{midx}"]
            render_clarify_result(r)
        remember_memory_panel(clarify_supplier_id(supplier_id), "ClosedTour", f"mctmod{_ks}")

        is_last = midx == len(modalities) - 1
        btn_label = "✅ Confirm this Modality & Finish Modalities" if is_last else "✅ Confirm this Modality & Continue →"
        if st.button(btn_label, type="primary", disabled=not data.get("price_list"), key=f"mct_mod_confirm{_ks}_{midx}"):
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
                if midx > 0 and st.button("⬅️ Previous Modality", key=f"mct_mod_prev{_ks}_{midx}"):
                    tour["modality_index"] -= 1
                    st.rerun()
            with ncol2:
                if st.button("🔙 Back to Modality selection", key=f"mct_mod_back{_ks}_{midx}"):
                    st.session_state.mct_phase = "select_modalities"
                    st.rerun()
            with ncol3:
                if st.button("🔙 Start over", key=f"mct_mod_cancel{_ks}_{midx}"):
                    _reset_mct_state()
                    st.rerun()
        return

    # ------------------------------------------------------------------
    # PHASE 6: final recap of the tour + all Modalities, still editable
    # ------------------------------------------------------------------
    if st.session_state.mct_phase == "final_review":
        queue = st.session_state.mct_queue
        qidx = st.session_state.mct_queue_index
        tour = queue[qidx]
        _ks = f"_t{qidx}"
        main_data = tour["main_data"]

        if len(queue) > 1:
            st.progress(qidx / len(queue))
            st.subheader(f"Final review (Tour {qidx + 1} of {len(queue)}): {main_data.get('tour_name') or tour['tour_code']}")
        else:
            st.subheader(f"Final review: {main_data.get('tour_name') or tour['tour_code']}")
        st.caption("Review everything below before publishing - click 'Edit' on any section to go back "
                  "and adjust it, your other progress is kept.")

        with st.expander("📋 Main tour info", expanded=False):
            st.write(f"**Tour Code:** {tour['tour_code']}")
            st.write(f"**Name:** {main_data.get('tour_name')}")
            st.write(f"**Nights:** {main_data.get('nights')}")
            st.write(f"**Itinerary:** {', '.join(main_data.get('itinerary_destinations', [])) or '(none)'}")
            st.write(f"**Images:** {len([u for u in main_data.get('image_urls', []) if u != FALLBACK_IMAGE])} selected")
        if st.button("✏️ Edit main tour info", key=f"mct_final_edit_main{_ks}"):
            st.session_state.mct_phase = "reviewing_main"
            st.rerun()

        for i, mod in enumerate(tour["modalities"]):
            mdata = mod["data"] or {}
            with st.expander(f"📋 Modality: {mod['code']}", expanded=False):
                st.write(f"**Price rows:** {len(mdata.get('price_list', []))}")
                st.write(f"**Supplements:** {len(mdata.get('supplements', []))}")
                st.write(f"**Operational Days:** {', '.join(mdata.get('operational_days', []))}")
            if st.button(f"✏️ Edit Modality '{mod['code']}'", key=f"mct_final_edit_mod{_ks}_{i}"):
                tour["modality_index"] = i
                st.session_state.mct_phase = "reviewing_modality"
                st.rerun()

        if st.button("➕ Add another Modality", key=f"mct_final_add_mod{_ks}"):
            st.session_state.mct_phase = "select_modalities"
            st.rerun()

        is_last_tour = qidx == len(queue) - 1
        if is_last_tour:
            btn_label = "✅ Confirm & Publish all tours" if len(queue) > 1 else "✅ Confirm this tour & Finish Review"
        else:
            btn_label = f"✅ Confirm & Continue to Tour {qidx + 2} of {len(queue)} →"
        if st.button(btn_label, type="primary", key=f"mct_final_confirm{_ks}"):
            if is_last_tour:
                st.session_state.mct_phase = "publishing"
            else:
                st.session_state.mct_queue_index = qidx + 1
                st.session_state.mct_phase = "reviewing_main"
            st.rerun()

        with st.expander("Not what you wanted?"):
            if st.button("🔙 Start over", key=f"mct_cancel_final{_ks}"):
                _reset_mct_state()
                st.rerun()
        return

    # ------------------------------------------------------------------
    # PHASE 7: publish - unchanged sequence (create tour + first Modality's
    # option active, then each remaining Modality's option, then deactivate
    # if the human chose draft/inactive)
    # ------------------------------------------------------------------
    if st.session_state.mct_phase == "publishing":
        queue = st.session_state.mct_queue

        st.subheader(f"Publishing {len(queue)} tour(s)..." if len(queue) > 1 else "Publishing tour...")

        mct_activation_choice = st.radio(
            "After publishing, should tours be Active or Inactive (draft)?",
            ["Inactive (draft) - recommended, review inside Travel Compositor before it goes live",
             "Active - live immediately"],
            index=0, key="mct_activation_choice"
        )
        mct_publish_as_active = mct_activation_choice.startswith("Active")

        # Pre-publish validation for each tour
        any_blocked = False
        for ti, tour in enumerate(queue):
            main_data = tour["main_data"]
            modalities = tour["modalities"]
            _pks = f"_pub_t{ti}"

            _mct_already_published_this_code = (
                tour.get("_published_as") is not None
            )
            if _mct_already_published_this_code:
                st.success(f"✅ **{tour['tour_code']}** already published as `{tour['_published_as']}`.")
                continue

            tour["tour_code"] = st.text_input(
                f"Tour Code ({tour['label'] or 'Tour ' + str(ti+1)})", value=tour["tour_code"],
                key=f"mct_publish_tour_code{_pks}",
                help="Change this here if Publish below rejects it as already taken."
            )
            _mct_publish_code_check = check_code_availability(client, "tour", supplier_id, tour["tour_code"])
            if _mct_publish_code_check and _mct_publish_code_check["exists"]:
                st.error(f"🚫 Tour Code `{tour['tour_code']}` is ALREADY TAKEN — change it above.")
                any_blocked = True

            combined_data = dict(main_data)
            modality_zero = dict(modalities[0]["data"])
            modality_zero.pop("supplements", None)
            combined_data.update(modality_zero)
            combined_data["supplements"] = main_data.get("supplements") or []

            extra_note = f" + {len(modalities) - 1} more Modalit{'y' if len(modalities) == 2 else 'ies'}" if len(modalities) > 1 else ""
            with st.expander(f"**{tour['tour_code']}** - Modality: {modalities[0]['code']}{extra_note}", expanded=(ti == 0)):
                dup_warning = check_duplicate_tour_name(client, supplier_id, main_data.get("tour_name"))
                if dup_warning:
                    st.warning(dup_warning)
                preview_payloads = None
                try:
                    preview_pre_config = HumanPreConfig(
                        supplier_id=supplier_id, provider_code=tour["tour_code"],
                        min_pax=min_pax, max_pax=max_pax, currency=currency,
                        modality_code=modalities[0]["code"],
                        on_request=on_request or min_pax_forces_on_request(combined_data.get("min_pax_guaranteed_departure")),
                        days_available_before_release=release_days
                    )
                    preview_payloads = build_closed_tour_payloads(preview_pre_config, combined_data, client)
                except Exception as e:
                    st.error(f"⚠️ Couldn't preview this tour's destinations for Tour Code "
                            f"`{tour['tour_code']}`. Details: {str(e)[:300]}.")
                    any_blocked = True
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

                if preview_payloads:
                    render_supplement_zero_price_notes(preview_payloads, key="supplement_occupancy_notes")
                if preview_payloads:
                    render_supplement_zero_price_notes(preview_payloads, key="price_list_overlap_notes")

                if mct_has_unresolved:
                    any_blocked = True
                    st.warning("🚫 Fix the destination(s) marked NOT FOUND above before publishing.")
                    mct_dest_rows = [{"#": i + 1, "Destination": d} for i, d in enumerate(main_data.get("itinerary_destinations", []))]
                    mct_dest_df = pd.DataFrame(mct_dest_rows) if mct_dest_rows else pd.DataFrame(columns=["#", "Destination"])

                    def _save_mct_publish_destinations(edited_df, main_data=main_data):
                        main_data["itinerary_destinations"] = [
                            str(row.get("Destination") or "").strip() for _, row in edited_df.iterrows()
                            if _safe_cell_str(row.get("Destination")).strip()
                        ]

                    editable_table(
                        "Itinerary destinations (in visit order)", mct_dest_df, f"mct_publish_destinations{_pks}",
                        on_save=_save_mct_publish_destinations,
                        column_config={"#": st.column_config.NumberColumn(disabled=True, default=0)}
                    )

            _warn_stale_images(main_data.get("image_urls"))

        # Check if all tours already published
        all_published = all(t.get("_published_as") is not None for t in queue)

        if not all_published:
            if any_blocked:
                st.info("Fix the issues above before publishing.")
            if st.button("🚀 Publish all to Travel Compositor", type="primary", disabled=any_blocked):
                for ti, tour in enumerate(queue):
                    if tour.get("_published_as") is not None:
                        continue
                    main_data = tour["main_data"]
                    modalities = tour["modalities"]
                    combined_data = dict(main_data)
                    modality_zero = dict(modalities[0]["data"])
                    modality_zero.pop("supplements", None)
                    combined_data.update(modality_zero)
                    combined_data["supplements"] = main_data.get("supplements") or []

                    with st.spinner(f"Publishing '{tour['tour_code']}' ({ti+1}/{len(queue)})..."):
                        try:
                            pre_config = HumanPreConfig(
                                supplier_id=supplier_id, provider_code=tour["tour_code"],
                                min_pax=min_pax, max_pax=max_pax, currency=currency,
                                modality_code=modalities[0]["code"],
                                on_request=on_request or min_pax_forces_on_request(combined_data.get("min_pax_guaranteed_departure")),
                                days_available_before_release=release_days
                            )
                            payloads = build_closed_tour_payloads(pre_config, combined_data, client)
                            if payloads.get("main_tour_error"):
                                show_publish_error(f"prepare **{tour['tour_code']}**'s payload", payloads["main_tour_error"])
                                continue
                            elif payloads["tour_option_error"]:
                                show_publish_error(f"prepare **{tour['tour_code']}**'s payload", payloads["tour_option_error"])
                                continue
                            elif payloads["unresolved_destinations"]:
                                st.error(f"❌ **{tour['tour_code']}**: couldn't resolve destination(s) "
                                        f"{payloads['unresolved_destinations']}.")
                                continue
                            else:
                                creation_payload = dict(payloads["main_tour_payload"])
                                creation_payload["active"] = True
                                creation_payload["modalityCodes"] = []
                                creation_payload["supplements"] = []
                                result = client.create_closed_tour(supplier_id, creation_payload)
                                if "error" in result:
                                    show_publish_error(f"create **{tour['tour_code']}**", result)
                                    continue
                                real_code = result.get("code", payloads["main_tour_code"])
                                mark_code_as_taken("tour", supplier_id, tour["tour_code"], result.get("name"))
                                if real_code and real_code != tour["tour_code"]:
                                    mark_code_as_taken("tour", supplier_id, real_code, result.get("name"))
                                created_modality_codes = []

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
                                    st.success(f"✅ **{tour['tour_code']}**: base modality '{modalities[0]['code']}' created.")
                                    created_modality_codes.append(modalities[0]["code"])

                                for m in modalities[1:]:
                                    try:
                                        mod_pre_config = HumanPreConfig(
                                            supplier_id=supplier_id, provider_code=tour["tour_code"],
                                            min_pax=min_pax, max_pax=max_pax, currency=currency,
                                            modality_code=m["code"],
                                            on_request=on_request or min_pax_forces_on_request(m["data"].get("min_pax_guaranteed_departure")),
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
                                            st.success(f"✅ **{tour['tour_code']}**: modality '{m['code']}' created.")
                                            created_modality_codes.append(m["code"])
                                    except Exception as e:
                                        show_publish_error(f"create **{tour['tour_code']}** modality '{m['code']}' (unexpected error)", str(e))
                                        continue

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
                                                  f"follow-up update failed - {finalize_result}. May need finishing manually.")
                                    else:
                                        state_label = "ACTIVE" if mct_publish_as_active else "inactive/draft"
                                        st.success(f"✅ **{tour['tour_code']}** published successfully as `{real_code}` ({state_label}).")
                                        tour["_published_as"] = real_code
                                        st.session_state.just_published_tour_code = real_code
                                        st.session_state.just_published_supplier_id = supplier_id
                                else:
                                    deactivate_payload = dict(payloads["main_tour_payload"])
                                    deactivate_payload["code"] = real_code
                                    deactivate_payload["active"] = False
                                    deactivate_payload["modalityCodes"] = []
                                    deactivate_payload["supplements"] = []
                                    deactivate_result = client.update_closed_tour(supplier_id, deactivate_payload)
                                    if "error" in deactivate_result:
                                        st.error(f"❌ **{tour['tour_code']}**: no Modality options created, AND "
                                                f"could not deactivate ({deactivate_result}). Deactivate manually.")
                                    else:
                                        st.error(f"❌ **{tour['tour_code']}**: no Modality options created. "
                                                f"Tour deactivated as `{real_code}`. Use 'Add a Modality' to finish.")
                        except Exception as e:
                            show_publish_error(f"publish **{tour['tour_code']}** (unexpected error)", str(e))

        if st.session_state.get("just_published_tour_code"):
            st.divider()
            published_codes = [t["_published_as"] for t in queue if t.get("_published_as")]
            st.caption(f"Published: **{', '.join(published_codes)}** (Supplier {supplier_id})")
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
                    reset_session_keep_core()
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
                    reset_session_keep_core()
                    st.session_state.cfg_prefill_supplier_id = prefill_supplier_id
                    st.session_state.prefill_existing_tour_code = prefill_tour_code
                    st.rerun()
            return

        if st.button("🆕 Start a new ClosedTour"):
            _reset_mct_state()
            st.rerun()
        return
