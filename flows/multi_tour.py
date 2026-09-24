"""
Multi-tour (ClosedTour) product flow, split out of app.py (Phase 1 restructure, zero behaviour
change).

render_multi_tour_flow moved here verbatim. Everything it references that is defined at app.py's
own top level is imported back from app via the same late-binding pattern used by
flows/ticket.py, flows/hotel.py and flows/multi_ticket.py: app.py imports this module only after
all of those names are already defined in its own namespace, so `from app import ...` resolves
correctly despite the circular import shape. **Note**: unlike the earlier modules, several names
this function needs (`apply_clarify_changes`, `check_code_availability`, `check_duplicate_tour_
name`, `mark_code_as_taken`, `try_code_variants`, `clarify_supplier_id`, `remember_clarification`,
`with_learned_guidance`, `render_house_rule_shortcut`, `HOUSE_RULE_CODEWORD`, `render_clarify_
result`) are defined LATER in app.py's file order than render_multi_tour_flow's own original
position - so `app.py`'s `from flows.multi_tour import render_multi_tour_flow` line has to sit
well after the function's old position (right alongside the flows.multi_ticket/flows.ticket
import lines, after `try_code_variants`), not at the old position itself.
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
from ui_components import (
    editable_table, editable_field, render_cancellation_policy_editor,
    render_closable_image_section, render_url_image_picker, render_doc_image_picker,
    render_stock_photo_picker, render_child_age_band, render_child_discount_editor,
    render_closedtour_supplements, render_currency_check, render_extra_child_notice,
    render_optional_time_input, render_stop_sales_editor,
    _add_page_images_to_doc_pool, _safe_cell_str,
)

from app import (
    ALL_WEEKDAYS, CURRENCY_OPTIONS, HOUSE_RULE_CODEWORD,
    _apply_min_pax_guaranteed_departure_note, _clean_modality_code, _clear_batch_widget_state,
    _fetch_url_text_safe, _modality_code_suspicious, _new_mct_tour, _reset_mct_state,
    _warn_page_image_upload_errors, _warn_stale_images, apply_clarify_changes,
    bump_widget_generation, check_code_availability, check_duplicate_tour_name,
    clarify_supplier_id, flow_widget_key, mark_code_as_taken, remember_clarification,
    remember_memory_panel, render_candidate_filter, render_clarify_result,
    render_house_rule_shortcut, render_supplement_zero_price_notes, reset_child_age_band_widgets,
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
                        human_hint=with_learned_guidance(supplier_id, "ClosedTour", extraction_hint),
                        # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-18): "human selects max
                        # occupancy by 2 or 3 pax for example, we must make sure that the
                        # contracts reads max double or triple occupancy for supplements and
                        # modalities... saves time and AI reader time." max_pax is the Max Pax
                        # already chosen in app.py's Step 3, before this extraction ever runs.
                        # 9 is that selector's unconstrained default (list(range(2,10)),
                        # index=7) - only a genuinely narrowed choice (2-4) is worth passing
                        # through; see _max_occupancy_focus_clause's own docstring for why this
                        # is a reading-effort hint, not the same thing as the separate,
                        # document-derived max_occupancy extraction field.
                        max_occupancy_hint=max_pax if max_pax and max_pax < 9 else None,
                    )
                    # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-23, verbatim): "...I cannot
                    # automatically use the images... at least the images are being detected...
                    # but i cannot automatically use them for my closedtours and neither for my
                    # tickets." Same fix as the single-tour ClosedTour/Ticket flows (app.py,
                    # flows/ticket.py) - every URL in mct_hosted_image_candidates is already a
                    # verified, R2-hosted image (uploaded AND public-URL-verified inside
                    # _add_page_images_to_doc_pool/upload_images_with_errors, computed above in
                    # PHASE 1 before this tour was even selected), so it's used directly instead
                    # of requiring a manual tick-and-"Add selected" click.
                    auto_images = list(dict.fromkeys(st.session_state.get("mct_hosted_image_candidates") or []))
                    tour["main_data"]["image_urls"] = auto_images or [FALLBACK_IMAGE]
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
        # CONFIRMED BUG (product-owner screenshot, 2026-09-24) - same fix as app.py's single-tour
        # ClosedTour flow: hotels_text is stored as HTML, this was the generic no-conversion
        # widget, so raw HTML tags leaked onto the review screen. See
        # ui_components._plain_marked_to_display_html's docstring for the full report.
        editable_field("Hotels", data, "hotels_text", widget="html_text_area", height=100, key_suffix="_main")
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
        elif st.session_state.get("mct_hosted_image_candidates"):
            # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-23) - see the extraction-time merge above:
            # this used to be a manual tick-and-"Add selected" picker (render_url_image_picker);
            # now it's a plain confirmation, since these were already folded into image_urls.
            st.caption(f"✅ {len([u for u in data.get('image_urls', []) if u != FALLBACK_IMAGE])} image(s) found in "
                      f"your document/page were added automatically.")
        else:
            st.caption(f"{len([u for u in data.get('image_urls', []) if u != FALLBACK_IMAGE])} image(s) selected.")

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

        render_candidate_filter(candidates, "mct_modcand", "modality")

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
                    _remaining_price_list, _nested_seasons, _unhandled_nesting_notes = \
                        split_nested_price_list_seasons(mod["data"]["price_list"])
                    mod["data"]["price_list"] = _remaining_price_list
                    for _nesting_note in _unhandled_nesting_notes:
                        st.warning(f"⚠️ {_nesting_note}")
                    for _nested_row in _nested_seasons:
                        _new_code = _mct_generate_split_modality_code(
                            mod["code"], _nested_row, [m["code"] for m in modalities])
                        _new_data = copy.deepcopy(mod["data"])
                        _new_data["price_list"] = [_nested_row]
                        modalities.append({
                            "code": _new_code,
                            "hint": (f"Auto-split from '{mod['code']}' for the overlapping "
                                     f"period {_nested_row.get('startDate')} to "
                                     f"{_nested_row.get('endDate')} - originally: "
                                     f"{mod['hint'] or mod['code']}"),
                            "data": _new_data,
                            "confirmed": False,
                        })
                        st.warning(
                            f"⚠️ **'{_nested_row.get('name') or 'A season'}'** "
                            f"({_nested_row.get('startDate')} – {_nested_row.get('endDate')}) "
                            f"overlapped with another season on **'{mod['code']}'** - Travel "
                            f"Compositor can't publish two overlapping price windows on one "
                            f"Modality, so it's been split out into a new Modality, "
                            f"**'{_new_code}'**, which you'll review right after this one. "
                            f"Check its Code, name and settings before publishing.")
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

        if min_pax_forces_on_request(data.get("min_pax_guaranteed_departure")):
            st.warning(f"🔒 {min_pax_guaranteed_departure_note(data.get('min_pax_guaranteed_departure'))} "
                      f"This Modality will be published **On Request** regardless of the On Request "
                      f"setting above - a note was also added to Policy remarks.")

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
        # CONFIRMED BUG FIX (2026-09-18, screenshot reported: "how can this error show up, even
        # when the upload was working fine?" - the screen showed "Tour Code ASW-4 is ALREADY TAKEN
        # by an existing tour" and a disabled Publish button, directly above "Just published:
        # CLOSEDTOUR-425935" for that EXACT same tour/code): once this tour code has actually been
        # published successfully this session, any LATER rerun of this phase (any widget
        # interaction while still here - the tour code input, the itinerary table, anything) re-ran
        # the duplicate-code check below against Travel Compositor. That check now, correctly,
        # finds the code taken - because THIS tour is what just took it - but showed it as a
        # blocking "change it before publishing" error, as if publishing had not happened yet. The
        # whole pre-publish section (Tour Code input, duplicate check, destination preview, Publish
        # button) has nothing left to do once this exact code is already published - it is skipped
        # entirely on any such rerun, leaving only the "Just published" panel below (which already
        # correctly displays either way).
        _mct_already_published_this_code = (
            st.session_state.get("just_published_tour_code") == tour["tour_code"]
            and st.session_state.get("just_published_supplier_id") == supplier_id
        )
        if not _mct_already_published_this_code:
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
                        modality_code=modalities[0]["code"],
                        on_request=on_request or min_pax_forces_on_request(combined_data.get("min_pax_guaranteed_departure")),
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

                # CONFIRMED ABSOLUTE HOUSE RULE (product owner, 2026-09-18): "a supplement can
                # never be 0 Euro. If so, then there is a mistake... does not need to be
                # included." build_supplement_vos (via build_closed_tour_payloads) already drops
                # any supplement priced at 0 in every occupancy before it ever reaches the
                # payload - this is where that removal (and the occupancy-stripping notes it
                # shares a list with) is actually shown to the human, matching the "flag it,
                # don't silently change it" convention every other *_notes field in this app
                # already uses.
                if preview_payloads:
                    render_supplement_zero_price_notes(preview_payloads, key="supplement_occupancy_notes")

                # CONFIRMED PRODUCT-OWNER RULE (2026-09-18): season date ranges that still
                # overlap after both the silent touching-boundary fix and the single-level
                # auto-split into a new Modality have already run (see builder.py's
                # build_closed_tour_payloads) - should normally never fire, since the extraction
                # -time wiring above already catches this before the human ever reaches this
                # screen, but shown here as a final visible safety net rather than a silently
                # populated field nobody reads, same convention as every other *_notes field.
                if preview_payloads:
                    render_supplement_zero_price_notes(preview_payloads, key="price_list_overlap_notes")

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

            _warn_stale_images(main_data.get("image_urls"))

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
                            modality_code=modalities[0]["code"],
                            # CONFIRMED PRODUCT-OWNER RULE (2026-09-03): "If Ticket or Closedtour has
                            # minimum of 3 pax or higher, we must set the ticket or closedtour on
                            # request" - forced regardless of the human's own On Request checkbox.
                            on_request=on_request or min_pax_forces_on_request(combined_data.get("min_pax_guaranteed_departure")),
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
