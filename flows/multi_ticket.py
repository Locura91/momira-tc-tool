"""
Multi-ticket product flows, split out of app.py (Phase 1 restructure, zero behaviour change).

render_multi_ticket_flow (batch-create) and render_multi_ticket_update_flow (batch-update) moved
here verbatim, together, matching the plan's module grouping. The three `_mtu_*` helpers
(`_mtu_fetch_live_ticket`, `_mtu_resolve_modality_name`, `_mtu_clear_geo_confirmation`) that sit
between the two functions in app.py's own source stay there - they're small (under 150 lines
combined) and fall under the later `app_helpers.py` module per the plan - and are imported back
below along with everything else this file references that's defined at app.py's own top level.
Everything else it references is imported back from app via the same late-binding pattern used
by flows/ticket.py and flows/hotel.py: app.py imports this module only after all of those names
are already defined in its own namespace, so `from app import ...` resolves correctly despite the
circular import shape.
"""
import os
import re
import tempfile
import pandas as pd
import streamlit as st

from schemas import TicketHumanPreConfig
from builder import build_ticket_payloads
from document_reader import extract_raw_text, extract_images
from document_reader import scanned_document_warning as document_reader_scanned_warning
from ai_extractor import (
    apply_clarification, check_ticket_content_drift, detect_ticket_modalities,
    detect_ticket_variants, extract_ticket_main_info, extract_ticket_modality_data,
    friendly_error_message, min_pax_forces_on_request, min_pax_guaranteed_departure_note,
)
from pexels_client import search_images
from pixabay_client import search_images as search_images_pixabay
from r2_client import upload_images_with_errors as upload_images_r2_with_errors
from geocoding_client import geocode, geocode_search, parse_google_maps_url
import cancellation_links
from image_dimensions import FALLBACK_IMAGE
from ui_components import (
    editable_table, editable_field, render_cancellation_policy_editor,
    render_closable_image_section, render_url_image_picker, render_doc_image_picker,
    render_stock_photo_picker, render_child_age_band, render_currency_check,
    render_duration_editor, render_stop_sales_editor,
    render_ticket_modality_supplements_editor, render_ticket_pricing_editor,
    merge_what_to_bring_into_voucher_remarks,
    _add_page_images_to_doc_pool, _clean_time_table_rows, _safe_cell_str, _safe_float,
)

from app import (
    ALL_WEEKDAYS, CURRENCY_OPTIONS, HOUSE_RULE_CODEWORD, SHARED_WIDGET_STATE_PREFIXES,
    _apply_min_pax_guaranteed_departure_note, _clean_modality_code, _clear_batch_widget_state,
    _dmy_date_field, _fetch_url_text_safe, _geo_search_default, _map_fetched_ticket_to_data,
    _merge_extraction_over_baseline, _mt_clear_geo_confirmation, _mtu_clear_geo_confirmation,
    _mtu_fetch_live_ticket, _mtu_resolve_modality_name, _warn_page_image_upload_errors,
    _warn_stale_images, apply_clarify_changes, check_code_availability,
    check_modality_code_availability, clarify_supplier_id, floor_start_date_for_new_data,
    get_existing_ticket_codes, mark_code_as_taken, remember_clarification,
    remember_memory_panel, render_clarify_result, render_house_rule_shortcut,
    render_publish_blockers, render_skip_item_button, render_ticket_language_options,
    reset_child_age_band_widgets, reset_stale_editable_field_widgets, show_publish_error,
    with_learned_guidance,
)


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
                        # never substituted into the name.
                        # CONFIRMED PRODUCT-OWNER RULE (2026-09-03): when the document gives NO
                        # such supplier code, the Modality Code now defaults to the excursion's
                        # own name instead of the generic "Standard" - "Standard" is meaningless
                        # once a batch has several excursions (every row would start out
                        # identical), while the excursion name is already unique and immediately
                        # recognizable. modality_name (what the CLIENT sees) is unaffected by
                        # this - it stays the plain "Standard"/"Standard Private" convention.
                        _base_modality_name = "Standard Private" if e.get("is_private") else "Standard"
                        _supplier_code = str(e.get("supplier_code") or "").strip()
                        _excursion_label = str(e.get("label") or "").strip()
                        # CONFIRMED BUG FIX (product owner, 2026-09-03), real API error: "Modality
                        # Code cannot contain '/' or '\' - it becomes part of a URL and breaks
                        # lookups" for e.g. "Turtles/Tortoises: Three Island Cruise (Praslin)" - the
                        # 2026-09-03 "default Modality Code to the excursion name" rule (see
                        # comment above) started feeding raw excursion names straight into
                        # modality_code without running them through the SAME sanitizer every
                        # other AI-suggested-code call site already uses (_clean_modality_code,
                        # defined above - strips / \ + - . that Travel Compositor's API rejects).
                        _modality_code = (
                            f"{_base_modality_name.upper().replace(' ', '_')}_{_supplier_code}" if _supplier_code
                            else (_clean_modality_code(_excursion_label) or _base_modality_name)
                        )
                        candidates.append({
                            "label": e.get("label", ""), "ticket_code": "",
                            "modality_code": _modality_code,
                            "modality_name": _base_modality_name,
                            "selected": True,
                            # Real AI-detected excursion - safe to later restrict extraction
                            # to just this one (see is_genuine_variant usage in PHASE 3 below).
                            "is_genuine_variant": True,
                            # Only when a real supplier code was actually used does modality_code
                            # deliberately differ from the excursion name - mark it "touched" so
                            # PHASE 2's auto-sync (below) never overwrites a genuine supplier code
                            # with the plain excursion name.
                            "_modcode_touched": bool(_supplier_code),
                        })
                    if not candidates:
                        # No usable excursion name/title could be found at all (rare - see
                        # detect_ticket_variants' own docstring) - the "Ticket Name" typed in
                        # PHASE 2 is just a display label, not a real variant to filter the
                        # source by - is_genuine_variant stays False so PHASE 3 never sends it
                        # to the AI as a variant filter (doing so caused the AI to search for a
                        # nonexistent named variant and return an empty extraction - same bug as
                        # the ClosedTour flow had).
                        # Prefill the Ticket Code from what was already entered back in Step 3
                        # (default_ticket_code) so the human doesn't have to type it again here.
                        # modality_code starts blank (not "Standard") because there's no excursion
                        # name yet to default it to - PHASE 2 below auto-fills it from the Ticket
                        # Name the human types there, per the same "default to the excursion name"
                        # rule as the multi-excursion branch above.
                        candidates = [{"label": "", "ticket_code": default_ticket_code, "modality_code": "",
                                      "modality_name": "Standard",
                                      "selected": True, "is_genuine_variant": False}]
                    elif len(candidates) == 1:
                        # CONFIRMED BUG FIX (product owner, 2026-09-05): "To set up a new ticket,
                        # the ticket name must be the name of the detected excursion." A
                        # single-excursion document now still comes back from
                        # detect_ticket_variants with its own real label (see that function's
                        # docstring) instead of an empty list, so it flows through the same loop
                        # as the multi-excursion branch above and already gets that real label as
                        # its "Ticket Name" on the "Set up this Ticket" screen - no more forcing
                        # the human to retype a name the AI already found. The one thing that
                        # loop doesn't set for a lone real excursion is the Ticket Code, which the
                        # human already typed back in Step 3 - carry that over here exactly like
                        # the no-name fallback above does, so it isn't lost just because a real
                        # name was detected this time.
                        candidates[0]["ticket_code"] = default_ticket_code

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
                _modcode_key = f"mt_modcode_{i}"
                # CONFIRMED PRODUCT-OWNER RULE (2026-09-03): a Modality Code that's still blank
                # (no supplier code was detected, and this is the no-variants-detected fallback
                # candidate where the excursion name wasn't known until the Ticket Name above was
                # typed) defaults to that name, kept in sync as the human keeps typing it - right
                # up until they type into this field directly, at which point their own value
                # wins permanently. Writing st.session_state[key] before the widget call below is
                # the standard Streamlit way to update an already-created widget's value.
                if not cand.get("_modcode_touched") and (cand.get("label") or "").strip():
                    # Same sanitizer as the PHASE 1 default above - a label like "Island Duo:
                    # Praslin and La Digue B/B (Mahe)" contains "/" and would otherwise be synced
                    # here verbatim, straight into a field the real API rejects any "/" or "\" in.
                    st.session_state[_modcode_key] = _clean_modality_code(cand["label"].strip()) or cand["label"].strip()
                cand["modality_code"] = st.text_input(
                    "Modality Code", value=cand["modality_code"], key=_modcode_key,
                    help="What the SUPPLIER sees. If the document assigns this exact service its own "
                         "reference code (e.g. a 'Tour Code' column reading 'WT1'), that code has "
                         "already been appended here automatically - edit if needed. Otherwise this "
                         "defaults to the excursion's name and stays in sync with it until you edit "
                         "this field yourself."
                )
                # Compare against the SANITIZED label (not the raw one) - otherwise a label
                # containing "/" or similar would never equal its own (necessarily different,
                # cleaned) auto-synced code, permanently and incorrectly marking this "touched"
                # on the very first render, before the human ever typed into this field.
                _clean_label = _clean_modality_code((cand.get("label") or "").strip()) or (cand.get("label") or "").strip()
                if cand["modality_code"].strip() != _clean_label:
                    cand["_modcode_touched"] = True

        if st.button("➕ Add another excursion manually"):
            candidates.append({"label": "", "ticket_code": "", "modality_code": "",
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

        st.progress(idx / len(queue))
        with st.expander("Not what you wanted?"):
            if st.button("🔙 Cancel this batch - return to single-Ticket flow", key=f"mt_cancel_{idx}"):
                for key in ["mt_phase", "mt_raw_text", "mt_candidates", "mt_queue", "mt_queue_index",
                           "mt_doc_raw_images", "mt_hosted_image_candidates"]:
                    st.session_state.pop(key, None)
                # CONFIRMED BUG FIX (product owner, 2026-09-03): "if I start a new batch, this
                # cant be seen: I have not included images to the new service" - this used to
                # sweep only SHARED_WIDGET_STATE_PREFIXES, missing this flow's own "mt_"-prefixed
                # widget keys entirely (the skip button a few lines below already includes "mt_" -
                # see its widget_state_prefixes= - this reset point was just never updated to
                # match). A per-item stock-photo picker's "closed, N image(s) added" state
                # (mt_pixabay_{idx}_closed / mt_pexels_{idx}_closed, keyed by the item's
                # POSITION in the queue) survived into the next batch's positionally-identical
                # item, showing "1 image(s) added" for a brand-new service that never had any
                # images added at all.
                _clear_batch_widget_state(["mt_"] + SHARED_WIDGET_STATE_PREFIXES)
                st.rerun()

        # CONFIRMED PRODUCT-OWNER FIX (2026-09-03): this used to sit near the bottom of Step 1,
        # after the name/description/cancellation fields already had a chance to load - "remove
        # this one" is a decision made from the ticket's NAME alone, so it belongs at the very
        # top of the screen, before that name is even shown, not buried below several fields of
        # detail nobody needs to read for something they're about to discard.
        render_skip_item_button(
            current['label'] or current['ticket_code'], queue, idx,
            "mt_queue", "mt_queue_index",
            ["mt_phase", "mt_raw_text", "mt_candidates", "mt_queue", "mt_queue_index",
             "mt_doc_raw_images", "mt_hosted_image_candidates"],
            button_key=f"mt_skip_{idx}",
            widget_state_prefixes=["mt_"] + SHARED_WIDGET_STATE_PREFIXES
        )

        st.subheader(f"Reviewing ticket {idx + 1} of {len(queue)}: **{current['label'] or current['ticket_code']}** (code: {current['ticket_code']})")

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
            # Price-validity code (product owner, 2026-09-08) - see price_validity.py's own
            # docstring. Blank by default; when set, the app appends "(YYYYMMDD)" to Voucher
            # Remarks automatically at publish time (build_ticket_payloads -> with_price_validity_
            # code) - no need to type the code by hand.
            editable_field("Prices confirmed valid until (optional - the app adds the "
                           "\"(YYYYMMDD)\" marker to Voucher Remarks automatically)", data,
                           "price_valid_until_date", widget="text_input", key_suffix=f"_{idx}")
            # CONFIRMED PRODUCT-OWNER RULE (2026-08-12): the separate Manual Notes box is no longer
            # needed for Tickets - every field (Voucher Remarks, Condition, Stop Sales, Modality
            # Supplements, etc.) is now directly editable with its own pencil/text box, so a human
            # can add anything a document doesn't say straight into the real field instead of a
            # side note that only gets appended to Voucher Remarks at publish time.

            # CONFIRMED PRODUCT-OWNER FIX (2026-09-03): "The City is not needed to write down
            # there, the information is coming from the geolocation and we don't need to write it
            # here" - a raw City text box here duplicated the "Location for ..." section right
            # below, which already shows the resolved place and lets you search for or paste a
            # better one. Removed; `data["city"]` (still set by extraction) continues to seed
            # that geolocation search below, it's just no longer shown as its own editable field.

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
                mt_geo = geocode(_geo_search_default(client, mt_city))  # cached in geocoding_client - cheap to call every rerun

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
                mt_geo_query = st.text_input("Search for a location", value=_geo_search_default(client, mt_city), key=f"mt_geo_query_{idx}")
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

                st.markdown("**Or paste a Google Maps link:**")
                st.caption("Find the place in Google Maps, hit Share (or copy the address-bar URL), and paste "
                          "it here - the coordinates are read out of the link automatically.")
                mt_maps_url = st.text_input("Google Maps link", key=f"mt_geo_maps_url_{idx}", placeholder="https://maps.google.com/...")
                if st.button("🔗 Use this link's coordinates", key=f"mt_geo_maps_url_btn_{idx}", disabled=not mt_maps_url.strip()):
                    with st.spinner("Reading coordinates from the link..."):
                        mt_url_geo = parse_google_maps_url(mt_maps_url)
                    if mt_url_geo["valid"]:
                        data["manual_latitude"] = mt_url_geo["latitude"]
                        data["manual_longitude"] = mt_url_geo["longitude"]
                        data["manual_coords_for_city"] = mt_city
                        _mt_clear_geo_confirmation(current, idx)
                        st.rerun()
                    else:
                        st.error(mt_url_geo["error"])

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

            # CONFIRMED FIX (2026-09-03, product owner): "estimated duration must be seen within
            # the app if used days, minutes or hours" - a hardcoded "(hours)" label was wrong
            # whenever duration_type was actually "DAYS", and there was no way to enter minutes
            # at all. render_duration_editor shows/edits the real unit alongside the number, and
            # never requires a value (see its own docstring).
            render_duration_editor(data, f"mt_{idx}")

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
                    _apply_min_pax_guaranteed_departure_note(
                        data, ("cancellation_policy_text", "voucher_remarks"),
                        data.get("min_pax_guaranteed_departure"))
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

        if min_pax_forces_on_request(data.get("min_pax_guaranteed_departure")):
            st.warning(f"🔒 {min_pax_guaranteed_departure_note(data.get('min_pax_guaranteed_departure'))} "
                      f"This Ticket will be published **On Request** regardless of the On Request setting "
                      f"above - a note was also added to Condition/Voucher Remarks.")

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
            data["start_date"] = _dmy_date_field("Valid From (DD/MM/YYYY)", f"mt_start_date_{idx}", value_iso=data.get("start_date", ""))
        with dcol2:
            data["end_date"] = _dmy_date_field("Valid Until (DD/MM/YYYY)", f"mt_end_date_{idx}", value_iso=data.get("end_date", ""))
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
                f"afterward via **Price update to existing Products -> Ticket -> \"2: Add new Modality to "
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
        # CONFIRMED PRODUCT-OWNER BUG (2026-09-03): "i have an error here, but I can not go back to
        # change the error - this is very bad, the human must be able to go back and solve the
        # error and not start completely new over." Root cause: `mt_failed_items` (below) only ever
        # captured the ONE specific failure mode where the Ticket itself was created but its
        # Modality option POST then failed. Any failure that happened BEFORE the Ticket was
        # created at all - e.g. TicketHumanPreConfig(...) raising a pydantic validation error, such
        # as the real one reported ("Modality Code cannot contain '/' or '\\'") - fell into the
        # generic `except Exception` below with no recovery captured whatsoever: the item was just
        # skipped, and the ONLY way forward was "Start a new batch", discarding every already-typed
        # field for every ticket in the whole batch, not just the one that failed. This new list
        # captures exactly that "failed before the Ticket could be created" case, with its full
        # editable data (including Modality Code, the field the real error was actually about), so
        # it can be fixed and retried right here - same pattern as mt_failed_items below, just one
        # stage earlier.
        if "mt_precreate_failed_items" not in st.session_state:
            st.session_state.mt_precreate_failed_items = []
        st.subheader(f"Ready to publish {len(queue)} Tickets - one by one")
        for q in queue:
            extra_count = len(q.get("extra_modalities", []))
            extra_note = f" + {extra_count} additional modalit{'y' if extra_count == 1 else 'ies'}" if extra_count else ""
            st.write(f"- **{q['ticket_code']}** ({q['label']}) - Modality: {q['modality_code']}{extra_note}")

        _warn_stale_images([u for q in queue for u in (q.get("data", {}).get("image_urls") or [])])

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
                    # CONFIRMED PRODUCT-OWNER BUG FIX (2026-09-03): every "skip this item" path
                    # below (a failed TicketHumanPreConfig(...)/build_ticket_payloads(...) call,
                    # unresolved geolocation, a publish blocker, or any other unexpected exception)
                    # now records the item into mt_precreate_failed_items - see that list's
                    # docstring above - instead of just vanishing with no way to fix and retry it.
                    def _park_for_recovery(q=q):
                        st.session_state.mt_precreate_failed_items.append({
                            "ticket_code": q["ticket_code"], "label": q["label"],
                            "modality_code": q["modality_code"], "modality_name": q.get("modality_name"),
                            "data": q["data"], "extra_modalities": q.get("extra_modalities", []),
                        })
                    _ticket_was_created = False
                    try:
                        pre_config = TicketHumanPreConfig(
                            supplier_id=supplier_id, ticket_code=q["ticket_code"], currency=currency,
                            modality_code=q["modality_code"], modality_name=q.get("modality_name"),
                            # CONFIRMED PRODUCT-OWNER RULE (2026-09-03): "If Ticket or Closedtour has
                            # minimum of 3 pax or higher, we must set the ticket or closedtour on
                            # request" - forced regardless of the human's own On Request checkbox above.
                            on_request=on_request or min_pax_forces_on_request(q["data"].get("min_pax_guaranteed_departure")),
                            days_available_before_release=release_days, min_passengers=min_passengers, max_passengers=max_passengers
                        )
                        payloads = build_ticket_payloads(pre_config, q["data"], client)
                        if payloads["main_ticket_error"] or payloads["ticket_option_error"]:
                            show_publish_error(f"prepare **{q['ticket_code']}**'s payload",
                                              payloads['main_ticket_error'] or payloads['ticket_option_error'])
                            _park_for_recovery()
                            continue
                        if not payloads["geolocation_resolved"]:
                            st.error(f"❌ **{q['ticket_code']}**: geolocation not resolved - skipped. Fix the City "
                                    f"field and create this one individually via the normal Create flow instead.")
                            _park_for_recovery()
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
                            _park_for_recovery()
                            continue

                        creation_payload = dict(payloads["main_ticket_payload"])
                        creation_payload["active"] = True
                        result = client.create_ticket(supplier_id, creation_payload)
                        if "error" in result:
                            show_publish_error(f"create **{q['ticket_code']}**", result)
                            _park_for_recovery()
                            continue
                        real_code = result.get("code", payloads["main_ticket_code"])
                        # Once the Ticket record itself exists in Travel Compositor, a later failure
                        # (e.g. the deactivate-back-to-draft call below) must NOT be parked for
                        # "retry from scratch" recovery below - retrying would try to create a
                        # DUPLICATE ticket. mt_failed_items (option-creation failure) and manual
                        # follow-up inside Travel Compositor cover everything past this point.
                        _ticket_was_created = True
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
                        # CONFIRMED PRODUCT-OWNER BUG FIX (2026-09-03): this is the exact spot the
                        # reported failure landed - TicketHumanPreConfig(...) raising a pydantic
                        # validation error (e.g. "Modality Code cannot contain '/' or '\\'") happens
                        # before the Ticket is created, so it's always safe (and necessary) to park
                        # it for recovery here. Only skip parking if the Ticket record was already
                        # created (see _ticket_was_created above) - retrying that would create a
                        # duplicate ticket instead of fixing anything.
                        if not _ticket_was_created:
                            _park_for_recovery()
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
                        fdata["start_date"] = _dmy_date_field("Valid From (DD/MM/YYYY)", f"mtf_start_{fi_idx}", value_iso=fdata.get("start_date", ""))
                    with fdcol2:
                        fdata["end_date"] = _dmy_date_field("Valid Until (DD/MM/YYYY)", f"mtf_end_{fi_idx}", value_iso=fdata.get("end_date", ""))

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

        # CONFIRMED PRODUCT-OWNER BUG FIX (2026-09-03): "i have an error here, but I can not go
        # back to change the error - this is very bad, the human must be able to go back and solve
        # the error and not start completely new over" - see mt_precreate_failed_items' docstring
        # above. This is the recovery box for a Ticket that failed BEFORE it was even created
        # (unlike mt_failed_items above, which is for the Ticket-created-but-option-failed case) -
        # most commonly a rejected Modality Code, so that field is editable right here, same as
        # Ticket Name/Description/pricing/dates.
        if st.session_state.mt_precreate_failed_items:
            st.divider()
            st.subheader(f"⚠️ {len(st.session_state.mt_precreate_failed_items)} ticket(s) couldn't be created")
            st.caption("These never made it into Travel Compositor at all - fix whatever the error above "
                      "pointed at (often the Modality Code) and retry just this one, no need to redo the "
                      "whole batch.")
            for pf_idx, pf in enumerate(list(st.session_state.mt_precreate_failed_items)):
                with st.expander(f"🔧 {pf['ticket_code']} — {pf['label']}", expanded=True):
                    pfdata = pf["data"]
                    pf_col1, pf_col2 = st.columns(2)
                    with pf_col1:
                        pf["ticket_code"] = st.text_input(
                            "Ticket Code", value=pf["ticket_code"], key=f"mtp_code_{pf_idx}")
                    with pf_col2:
                        pf["modality_code"] = st.text_input(
                            "Modality Code", value=pf["modality_code"], key=f"mtp_modcode_{pf_idx}",
                            help="What the SUPPLIER sees. Travel Compositor rejects '/' and '\\' in this "
                                 "field - if that's what the error above mentioned, remove them here."
                        )

                    currency = render_currency_check(currency, CURRENCY_OPTIONS, "tk_cfg_currency", f"mtp_currency_{pf_idx}")
                    render_ticket_pricing_editor(pfdata, f"mtp_{pf_idx}", currency, max_passengers)

                    pf_tt_df = pd.DataFrame([{"Time (HH:MM)": t} for t in pfdata.get("time_tables", [])]) if pfdata.get("time_tables") else pd.DataFrame(columns=["Time (HH:MM)"])
                    def _save_mtp_tt(edf, pfdata=pfdata):
                        pfdata["time_tables"] = _clean_time_table_rows(edf)
                    editable_table("Start Time(s)", pf_tt_df, f"mtp_tt_{pf_idx}", on_save=_save_mtp_tt)
                    pf_dcol1, pf_dcol2 = st.columns(2)
                    with pf_dcol1:
                        pfdata["start_date"] = _dmy_date_field("Valid From (DD/MM/YYYY)", f"mtp_start_{pf_idx}", value_iso=pfdata.get("start_date", ""))
                    with pf_dcol2:
                        pfdata["end_date"] = _dmy_date_field("Valid Until (DD/MM/YYYY)", f"mtp_end_{pf_idx}", value_iso=pfdata.get("end_date", ""))

                    if st.button(f"🔄 Retry creating `{pf['ticket_code']}`", key=f"mtp_retry_{pf_idx}", type="primary"):
                        with st.spinner(f"Retrying '{pf['ticket_code']}'..."):
                            try:
                                retry_pre_config = TicketHumanPreConfig(
                                    supplier_id=supplier_id, ticket_code=pf["ticket_code"], currency=currency,
                                    modality_code=pf["modality_code"], modality_name=pf.get("modality_name"),
                                    on_request=on_request or min_pax_forces_on_request(pfdata.get("min_pax_guaranteed_departure")),
                                    days_available_before_release=release_days, min_passengers=min_passengers, max_passengers=max_passengers
                                )
                                retry_payloads = build_ticket_payloads(retry_pre_config, pfdata, client)
                                if retry_payloads["main_ticket_error"] or retry_payloads["ticket_option_error"]:
                                    show_publish_error(f"prepare **{pf['ticket_code']}**'s payload",
                                                      retry_payloads["main_ticket_error"] or retry_payloads["ticket_option_error"])
                                elif not retry_payloads["geolocation_resolved"]:
                                    st.error("❌ Geolocation not resolved - fix the City field via the normal Create flow instead.")
                                elif not render_publish_blockers(retry_payloads):
                                    pass  # render_publish_blockers already showed the specific error(s)
                                else:
                                    retry_creation_payload = dict(retry_payloads["main_ticket_payload"])
                                    retry_creation_payload["active"] = True
                                    retry_result = client.create_ticket(supplier_id, retry_creation_payload)
                                    if "error" in retry_result:
                                        show_publish_error(f"create **{pf['ticket_code']}**", retry_result)
                                    else:
                                        retry_real_code = retry_result.get("code", retry_payloads["main_ticket_code"])
                                        mark_code_as_taken("ticket", supplier_id, pf["ticket_code"], retry_result.get("name"))
                                        if retry_real_code and retry_real_code != pf["ticket_code"]:
                                            mark_code_as_taken("ticket", supplier_id, retry_real_code, retry_result.get("name"))
                                        retry_option_result = client.create_ticket_option(
                                            supplier_id, retry_real_code, retry_payloads["ticket_option_payload"])
                                        if "error" in retry_option_result:
                                            show_publish_error(f"create **{pf['ticket_code']}**'s option (created as `{retry_real_code}`)", retry_option_result)
                                            st.session_state.mt_failed_items.append({
                                                "ticket_code": pf["ticket_code"], "label": pf["label"],
                                                "real_code": retry_real_code,
                                                "modality_code": pf["modality_code"], "data": pfdata,
                                            })
                                        else:
                                            st.success(f"✅ **{pf['ticket_code']}**: base modality '{pf['modality_code']}' created.")
                                            if not mt_publish_as_active:
                                                retry_deactivate_payload = dict(retry_creation_payload)
                                                retry_deactivate_payload["active"] = False
                                                retry_deactivate_payload["code"] = retry_real_code
                                                retry_deactivate_result = client.update_ticket(supplier_id, retry_deactivate_payload)
                                                if "error" in retry_deactivate_result:
                                                    st.warning(f"⚠️ **{pf['ticket_code']}**: created and published, but switching "
                                                              f"back to inactive failed - {retry_deactivate_result}")
                                                else:
                                                    st.success(f"✅ **{pf['ticket_code']}** published successfully as `{retry_real_code}` (inactive/draft).")
                                            else:
                                                st.success(f"✅ **{pf['ticket_code']}** published and left ACTIVE as `{retry_real_code}` (as chosen above).")
                                        st.session_state.mt_precreate_failed_items = [
                                            x for x in st.session_state.mt_precreate_failed_items if x is not pf
                                        ]
                                        st.rerun()
                            except Exception as e:
                                show_publish_error(f"retry creating **{pf['ticket_code']}** (unexpected error)", str(e))

        st.write("")
        st.divider()
        if st.button("🆕 Start a new batch"):
            for key in ["mt_phase", "mt_raw_text", "mt_candidates", "mt_queue", "mt_queue_index",
                       "mt_doc_raw_images", "mt_hosted_image_candidates", "mt_failed_items",
                       "mt_precreate_failed_items"]:
                st.session_state.pop(key, None)
            # CONFIRMED BUG FIX (product owner, 2026-09-03): "if I start a new batch, this cant
            # be seen: I have not included images to the new service" - this swept only
            # SHARED_WIDGET_STATE_PREFIXES, missing this flow's own "mt_"-prefixed widget keys
            # (e.g. mt_pixabay_{idx}_closed / mt_pexels_{idx}_closed, which remember "closed, N
            # image(s) added" per queue position) - see the same fix a few hundred lines above
            # on the "Cancel this batch" button for the full explanation.
            _clear_batch_widget_state(["mt_"] + SHARED_WIDGET_STATE_PREFIXES)
            # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-03): "if I start a new batch for creating
            # a new service, please allow to change the currency as this can be always vary" -
            # tk_cfg_currency was left set from the batch that just finished, so Step 3's lock
            # (see "Locked - a currency, once set, cannot be changed" above) kept applying to
            # every batch after it too, even a brand-new one from a different supplier/rate
            # sheet. Reopening Step 3 (tk_step2_confirmed) and clearing the stored currency here
            # gives a genuinely new batch a fresh, editable currency choice, while an in-progress
            # batch's own Modalities are still protected by the same lock as before.
            st.session_state.tk_step2_confirmed = False
            st.session_state.pop("tk_cfg_currency", None)
            st.rerun()
        return


def render_multi_ticket_update_flow(client, supplier_id, on_request, release_days, tk_url, tk_files, max_passengers=9):
    """
    Batch flow for UPDATING MANY EXISTING Tickets from one document (e.g. a new supplier
    price-list that restates 20+ excursions already live on Travel Compositor).

    CONFIRMED PRODUCT-OWNER REQUEST (2026-09-08): the app already had a batch-CREATE flow for
    several NEW excursions from one document (render_multi_ticket_flow) but no equivalent for
    updating several EXISTING tickets from one document - "yes please fix it first, that's the
    reason i reached out to you first," said before attempting the Egypt catalogue import this
    was built for (nearly every LIVE ticket there needs its title/includes/excludes corrected
    from "(Entrance Ticket not included)" to entrance-included wording, plus a price update -
    all in one pass, across many tickets, from one uploaded price-list).

    Same phase/state-machine shape as render_multi_ticket_flow (its own docstring explains the
    "one real POST at a time, own success/failure status" reasoning - identical here), with its
    own "mtu_"-prefixed session_state throughout so it never collides with that flow's "mt_"
    state:
      1. GATHER: reuse the URL/document(s) already provided above, detect distinct excursions
         (detect_ticket_variants - same detector the create flow uses).
      2. MATCH: for each detected excursion, match it to an EXISTING live Ticket Code for this
         supplier. Defaults to the excursion's own detected supplier code/label when it exactly
         matches a live ticket's code (the confirmed common case: "Same codes - direct match")
         but always stays human-editable - other suppliers may not share that guarantee. Also
         picks which of that ticket's live Modality Codes to update.
      3. REVIEWING: per matched item, fetch the live ticket, run the SAME two-step extraction
         the create flow uses (extract_ticket_main_info then extract_ticket_modality_data,
         focused on this excursion via variant_hint), merge the main-info extraction OVER the
         live baseline (_merge_extraction_over_baseline / _map_fetched_ticket_to_data - same
         helpers the single-ticket "Whole ticket" update path already uses) so anything the new
         document doesn't restate is preserved rather than blanked, and surface
         check_ticket_content_drift so the human sees at a glance whether this is more than a
         price refresh.
      4. PUBLISHING: client.update_ticket + client.update_ticket_option per item, sequentially,
         each with its own clear status and a recovery path that doesn't lose the rest of the
         batch's edits if one item fails (mtu_update_failed_items / mtu_option_failed_items,
         same principle as the create flow's mt_precreate_failed_items / mt_failed_items).
    """
    if "mtu_phase" not in st.session_state:
        st.session_state.mtu_phase = "gather"

    # ------------------------------------------------------------------
    # PHASE 1: detect excursions from the source already provided above (same detector, same
    # gathering code as render_multi_ticket_flow's PHASE 1 - see that function for why URL fetch
    # failures/embedded-image extraction are handled the way they are).
    # ------------------------------------------------------------------
    if st.session_state.mtu_phase == "gather":
        if not (tk_url or tk_files):
            st.info("Provide a URL and/or upload document(s) above, then click below.")
        if st.button("🔎 Detect Excursions", disabled=not (tk_url or tk_files), key="mtu_detect_btn"):
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

                    # CONFIRMED REAL BUG (product owner, 2026-09-08, real Egypt-catalogue run):
                    # matching used to start from the AI-DETECTED excursion's own supplier_code/
                    # label and check whether THAT equalled a live Ticket's code - but an AI-read
                    # excursion title ("Pyramids, Sphinx & Egyptian Museum") is never equal to a
                    # code ("CAI-01"), so this matched 0 of 22 real excursions and forced 22
                    # manual code entries by hand ("No reason for new modality code if there is
                    # already modality code... Just check name, itinerary and included/excluded
                    # and prices" - the human should not have to do this typing at all when the
                    # codes already match). This flow only ever UPDATES existing tickets, so the
                    # ground truth is what's already live in Travel Compositor, not what the AI
                    # guesses from the document - exactly the same lesson price_refresh.py's
                    # Transfer/Transport matching already learned ("the list of products here
                    # comes from Travel Compositor, which is a fact, instead of from an AI
                    # reading a document, which is a judgement"). So: fetch this supplier's real
                    # ticket codes first, then just check whether the DOCUMENT mentions each one
                    # (a plain substring search) - not the other way around.
                    existing_items, _existing_items_err = get_existing_ticket_codes(client, supplier_id)

                    def _norm_code(s):
                        return re.sub(r"\s+", "", (s or "")).strip().lower()

                    raw_norm = _norm_code(raw_text)
                    matched_code_keys = set()
                    candidates = []
                    for item in existing_items:
                        code = (item.get("code") or "").strip()
                        if code and _norm_code(code) and _norm_code(code) in raw_norm:
                            candidates.append({
                                "label": item.get("name") or code,
                                "supplier_code": code,
                                "target_ticket_code": code,
                                "selected": True,
                                "is_genuine_variant": False,
                            })
                            matched_code_keys.add(code.strip().lower())

                    # The AI detector still runs, but only to offer anything the code search
                    # missed (a supplier who changed their own codes, or a genuinely new
                    # excursion not yet live) - UNCHECKED by default and without a pre-filled
                    # code, since matching those correctly is exactly what failed before.
                    detected = detect_ticket_variants(raw_text)
                    for e in detected:
                        _dsc = str(e.get("supplier_code") or "").strip()
                        if _dsc.lower() in matched_code_keys:
                            continue
                        candidates.append({
                            "label": e.get("label", ""),
                            "supplier_code": _dsc,
                            "target_ticket_code": "",
                            "selected": False,
                            "is_genuine_variant": True,
                        })

                    if not candidates:
                        candidates = [{"label": "", "supplier_code": "", "target_ticket_code": "",
                                      "selected": True, "is_genuine_variant": False}]

                    _warn_page_image_upload_errors(_add_page_images_to_doc_pool(tk_url, doc_raw_images, doc_image_urls))
                    if len(doc_image_urls) >= len(doc_raw_images):
                        doc_raw_images = []

                    st.session_state.mtu_raw_text = raw_text
                    st.session_state.mtu_candidates = candidates
                    st.session_state.mtu_doc_raw_images = doc_raw_images
                    st.session_state.mtu_hosted_image_candidates = list(dict.fromkeys(doc_image_urls))
                    st.session_state.mtu_phase = "match"
                    st.rerun()
                except Exception as e:
                    st.error(f"Detection failed: {friendly_error_message(e)}")
        return

    # ------------------------------------------------------------------
    # PHASE 2: match each detected excursion to an EXISTING live Ticket Code for this supplier.
    # ------------------------------------------------------------------
    if st.session_state.mtu_phase == "match":
        candidates = st.session_state.mtu_candidates
        _pre_matched_count = sum(1 for c in candidates if (c.get("target_ticket_code") or "").strip())
        st.subheader(f"Match {len(candidates)} detected excursion(s) to existing Tickets")
        st.caption(
            f"This UPDATES existing tickets - it never creates a new one, and the Ticket Code never "
            f"changes. **{_pre_matched_count} of {len(candidates)}** row(s) were matched automatically "
            f"because this supplier's own code (e.g. \"CAI-01\") was found written in the document "
            f"itself - those are already ticked and ready, no typing needed. Any row further down "
            f"with no code pre-filled is something the document mentions but whose code couldn't be "
            f"confirmed live - tick it and type the code by hand only if you want to include it "
            f"anyway."
        )

        existing_items, list_error = get_existing_ticket_codes(client, supplier_id)
        if list_error:
            st.warning(f"⚠️ Couldn't load the existing ticket list to help pre-fill matches: {list_error}. "
                      f"You can still type codes in manually below.")
        existing_lookup = {
            (item.get("code") or "").strip().lower(): item
            for item in existing_items if (item.get("code") or "").strip()
        }

        for i, cand in enumerate(candidates):
            if "target_ticket_code" not in cand:
                # CONFIRMED PRODUCT-OWNER RULE (2026-09-08): "Same codes - direct match" is the
                # common case (confirmed for the Egypt catalogue: CAI-01/LXR-01/ASW-01 etc. match
                # existing live ticket codes exactly) - default to it when the detected supplier
                # code (or, failing that, the excursion's own label) exactly matches an existing
                # ticket's code, but this is only ever a DEFAULT - the text_input below always
                # stays human-editable, since other suppliers may not share this guarantee.
                default_code = ""
                _sc = cand.get("supplier_code", "").strip().lower()
                _lbl = (cand.get("label") or "").strip().lower()
                if _sc and _sc in existing_lookup:
                    default_code = existing_lookup[_sc]["code"]
                elif _lbl and _lbl in existing_lookup:
                    default_code = existing_lookup[_lbl]["code"]
                cand["target_ticket_code"] = default_code

            ccol1, ccol2, ccol3 = st.columns([1, 3, 3])
            with ccol1:
                cand["selected"] = st.checkbox("Include", value=cand["selected"], key=f"mtu_sel_{i}")
            with ccol2:
                cand["label"] = st.text_input("Excursion", value=cand["label"], key=f"mtu_label_{i}")
            with ccol3:
                _code_key = f"mtu_code_{i}"
                # CONFIRMED REAL BUG (product owner, 2026-09-08, real batch-update run): a
                # text_input's `value=` is only honored the FIRST TIME its key is ever created -
                # every later rerun trusts whatever's already in session_state for that key,
                # ignoring a freshly (and correctly) computed default. This key is POSITIONAL
                # (mtu_code_{i}), so an earlier attempt in the same browser session - before the
                # ground-truth code matching existed, or from a previous batch reusing the same
                # row position - can leave a stale value permanently stuck here even after the
                # matching logic is fixed underneath it. Writing session_state[key] directly
                # BEFORE creating the widget (the same fix render_multi_ticket_flow's own
                # modality_code auto-sync already uses) is what actually makes a fresh default
                # visible - never overwrites a non-blank value the human already has there.
                if cand["target_ticket_code"] and not (st.session_state.get(_code_key) or "").strip():
                    st.session_state[_code_key] = cand["target_ticket_code"]
                cand["target_ticket_code"] = st.text_input(
                    "Existing Ticket Code to update", value=cand["target_ticket_code"], key=_code_key,
                    placeholder="e.g. CAI-01",
                    help="The ALREADY-LIVE Ticket Code this excursion's new info/pricing should be "
                         "published onto - not a new code."
                )

            cand["_live_modalities"] = []
            cand["_match_status"] = None
            code_val = cand["target_ticket_code"].strip()
            if cand["selected"] and code_val:
                # CONFIRMED PRODUCT-OWNER ADJUSTMENT (2026-09-08): check_code_availability's
                # normal framing ("exists" = bad, already taken) is inverted for an UPDATE flow -
                # here "exists" is exactly what's wanted (a real ticket to update), and "doesn't
                # exist" is the actual problem.
                check = check_code_availability(client, "ticket", supplier_id, code_val)
                if check is None:
                    st.warning(f"⚠️ Couldn't confirm `{code_val}` exists yet (connectivity) - will be "
                              f"re-checked before publishing.")
                    cand["_match_status"] = "unknown"
                elif check["exists"]:
                    st.success(f"✅ Matches existing ticket **{check.get('name') or '(unnamed)'}** "
                              f"(`{code_val}`) - will be UPDATED, not created.")
                    cand["_match_status"] = "ok"
                    live = _mtu_fetch_live_ticket(client, supplier_id, code_val)
                    if isinstance(live, dict) and "error" not in live:
                        cand["_live_modalities"] = live.get("modalityCodes") or []
                else:
                    st.error(f"🚫 No existing ticket found with code `{code_val}` for this supplier - "
                            f"this flow only UPDATES tickets that already exist. If this is genuinely a "
                            f"brand-new excursion, use **'1: Create new Ticket + 1 Modality'** instead.")
                    cand["_match_status"] = "not_found"

            default_mod = cand.get("modality_code", "")
            if not default_mod and cand["_live_modalities"]:
                _sc = cand.get("supplier_code", "").strip().lower()
                _sc_match = next((m for m in cand["_live_modalities"] if (m or "").strip().lower() == _sc), None) if _sc else None
                default_mod = _sc_match or cand["_live_modalities"][0]
            _modcode_key = f"mtu_modcode_{i}"
            # CONFIRMED REAL BUG (product owner, 2026-09-08, real batch-update run): same
            # stale-widget-key issue as mtu_code_{i} above - a text_input's `value=` is only
            # honored the FIRST time this positional key is created; a blank value left over
            # from an earlier attempt in this browser session stays stuck even after the
            # underlying default computation is fixed. Seed session_state BEFORE creating the
            # widget, but only when it's currently blank, so a real human edit is never clobbered.
            if default_mod and not (st.session_state.get(_modcode_key) or "").strip():
                st.session_state[_modcode_key] = default_mod
            mod_help = (
                f"Known Modality Codes on this ticket: {', '.join(cand['_live_modalities'])}"
                if cand["_live_modalities"] else
                "Type the exact live Modality Code to update (enter the Ticket Code above first to "
                "see this ticket's known Modality Codes here)."
            )
            cand["modality_code"] = st.text_input(
                f"Existing Modality Code to update — {cand['label'] or code_val or f'row {i + 1}'}",
                value=default_mod, key=_modcode_key, help=mod_help
            )
            st.divider()

        if st.button("➕ Add another excursion manually", key="mtu_add_row"):
            candidates.append({"label": "", "supplier_code": "", "selected": True, "is_genuine_variant": False})
            st.rerun()

        missing = []
        not_found = []
        new_queue = []
        seen_codes = {}
        for cand in candidates:
            if not cand["selected"]:
                continue
            code = cand["target_ticket_code"].strip()
            mod_code = cand["modality_code"].strip()
            label = cand["label"] or "(unnamed excursion)"
            if not code or not mod_code:
                missing.append(label)
                continue
            if cand.get("_match_status") == "not_found":
                not_found.append(f"{label} (`{code}`)")
                continue
            seen_codes.setdefault(code.lower(), []).append(label)
            new_queue.append({
                "label": cand["label"], "target_ticket_code": code, "modality_code": mod_code,
                # CONFIRMED PRODUCT-OWNER RULE (2026-09-08 follow-up): "the modality is the same
                # as the one existing or it is the same name as Excursion (max 40 signs)" - see
                # _mtu_resolve_modality_name's own docstring for why this can't just be left to
                # TicketHumanPreConfig's default (which would silently rename the Modality to
                # its CODE, not preserve its real name).
                "modality_name": _mtu_resolve_modality_name(client, supplier_id, code, mod_code, cand["label"]),
                "data": None, "live_ticket": None, "drift": None, "confirmed": False,
                "is_genuine_variant": cand.get("is_genuine_variant", False),
            })

        duplicate_codes = {code: labels for code, labels in seen_codes.items() if len(labels) > 1}

        if missing:
            st.error(f"🚫 These selected excursions are missing a Ticket Code or Modality Code and were "
                    f"excluded: {missing}")
        if not_found:
            st.error(f"🚫 These selected excursions don't match any existing ticket and were excluded: {not_found}")
        if duplicate_codes:
            for code, labels in duplicate_codes.items():
                st.error(f"🚫 Ticket Code `{code}` is used by more than one selected excursion "
                        f"({', '.join(labels)}) - each row must update a DIFFERENT ticket.")

        ready_to_review = new_queue and not missing and not not_found and not duplicate_codes
        st.caption(f"**{len(new_queue)}** ticket(s) ready to review." if ready_to_review else
                  "Fix the issues above before continuing.")

        if st.button("➡️ Start Reviewing", type="primary", disabled=not ready_to_review, key="mtu_start_review"):
            st.session_state.mtu_queue = new_queue
            st.session_state.mtu_queue_index = 0
            st.session_state.mtu_phase = "reviewing"
            st.rerun()
        return

    # ------------------------------------------------------------------
    # PHASE 3: review each matched ticket individually, one at a time - same two-step
    # main-info/modality shape as render_multi_ticket_flow's PHASE 3 (see that function's own
    # comment for why main info and pricing are two separate AI calls/steps), but starting from
    # the LIVE ticket as a baseline instead of a blank slate.
    # ------------------------------------------------------------------
    if st.session_state.mtu_phase == "reviewing":
        idx = st.session_state.mtu_queue_index
        queue = st.session_state.mtu_queue
        current = queue[idx]
        current.setdefault("step", "main")

        st.progress(idx / len(queue))
        with st.expander("Not what you wanted?"):
            if st.button("🔙 Cancel this batch - return to single-Ticket flow", key=f"mtu_cancel_{idx}"):
                for key in ["mtu_phase", "mtu_raw_text", "mtu_candidates", "mtu_queue", "mtu_queue_index",
                           "mtu_doc_raw_images", "mtu_hosted_image_candidates", "mtu_live_ticket_cache"]:
                    st.session_state.pop(key, None)
                _clear_batch_widget_state(["mtu_"] + SHARED_WIDGET_STATE_PREFIXES)
                st.rerun()

        render_skip_item_button(
            current['label'] or current['target_ticket_code'], queue, idx,
            "mtu_queue", "mtu_queue_index",
            ["mtu_phase", "mtu_raw_text", "mtu_candidates", "mtu_queue", "mtu_queue_index",
             "mtu_doc_raw_images", "mtu_hosted_image_candidates"],
            button_key=f"mtu_skip_{idx}",
            widget_state_prefixes=["mtu_"] + SHARED_WIDGET_STATE_PREFIXES
        )

        st.subheader(f"Reviewing ticket {idx + 1} of {len(queue)}: "
                    f"**{current['label'] or current['target_ticket_code']}** "
                    f"(updating: `{current['target_ticket_code']}` / Modality `{current['modality_code']}`)")

        variant_hint = current["label"] if current.get("is_genuine_variant") else None

        if current.get("live_ticket") is None:
            current["live_ticket"] = _mtu_fetch_live_ticket(client, supplier_id, current["target_ticket_code"])
        live_ticket = current["live_ticket"]
        live_ok = isinstance(live_ticket, dict) and "error" not in live_ticket
        if not live_ok:
            st.error(f"❌ Couldn't fetch the live ticket `{current['target_ticket_code']}`: {live_ticket}")
            if st.button("🔄 Retry fetch", key=f"mtu_retry_fetch_{idx}"):
                current["live_ticket"] = None
                st.rerun()
            return

        if current["data"] is None:
            with st.spinner(f"Extracting main ticket info{f' focused on ' + repr(current['label']) if variant_hint else ''} "
                            f"and comparing against what's currently live..."):
                try:
                    # CONFIRMED FIX (2026-09-08): merges the fresh extraction OVER the live
                    # ticket's own current data (_map_fetched_ticket_to_data /
                    # _merge_extraction_over_baseline - same helpers the single-ticket "Whole
                    # ticket" update path already uses) so a field the new document doesn't
                    # restate is preserved instead of blanked - while a field the new document
                    # DOES restate (e.g. a corrected title/includes/excludes once entrance
                    # tickets become included) correctly overwrites the stale live value.
                    baseline = _map_fetched_ticket_to_data(live_ticket)
                    fresh = extract_ticket_main_info(
                        st.session_state.mtu_raw_text, variant_hint=variant_hint,
                        human_hint=with_learned_guidance(supplier_id, "Ticket", ""))
                    current["data"] = _merge_extraction_over_baseline(baseline, fresh)
                    if not current["data"].get("image_urls"):
                        current["data"]["image_urls"] = [FALLBACK_IMAGE]
                    current["_cancellation_link_scope"] = cancellation_links.apply_cancellation_link_default(
                        current["data"], supplier_id, "Ticket")
                    live_datasheet = (live_ticket.get("datasheets") or {}).get("EN") or {}
                    try:
                        current["drift"] = check_ticket_content_drift(
                            st.session_state.mtu_raw_text, live_datasheet, human_hint=None)
                    except Exception as e:
                        current["drift"] = {"error": friendly_error_message(e)}
                except Exception as e:
                    st.error(f"⚠️ Couldn't extract main info for this excursion: {friendly_error_message(e)}")
                    if st.button("🔄 Retry extraction", key=f"mtu_retry_extract_{idx}"):
                        st.rerun()
                    return

        data = current["data"]

        # ==================================================================
        # STEP A: MAIN TICKET INFO
        # ==================================================================
        if current["step"] == "main":
            st.caption("**Step 1 of 2: Main ticket info.** Pricing/Modality comes next, as its own step.")

            _drift = current.get("drift")
            if isinstance(_drift, dict):
                if _drift.get("error"):
                    st.caption(f"(Couldn't run the AI content check against the live ticket: {_drift['error']})")
                elif _drift.get("has_changes"):
                    st.warning("⚠️ The new document may describe more than a price change vs. what's "
                              "currently live - double-check the fields below before publishing:")
                    for _c in _drift.get("changes") or []:
                        st.markdown(f"- {_c}")
                else:
                    st.caption("✅ AI check: the new document doesn't appear to describe any content "
                              "change beyond pricing.")

            editable_field("Ticket name", data, "ticket_name", widget="text_input", key_suffix=f"_{idx}")
            editable_field("Description", data, "description", widget="html_text_area", height=120, key_suffix=f"_{idx}")
            if not (data.get("ticket_name") or "").strip():
                st.error("🚫 Ticket name is empty - fill it in above before continuing.")
            if not (data.get("description") or "").strip():
                st.error("🚫 Description is empty - fill it in above before continuing.")
            if current.get("_cancellation_link_scope"):
                st.caption(f"ℹ️ This document didn't state its own cancellation terms - the table "
                          f"below was filled in from {current['_cancellation_link_scope']}. Edit or "
                          f"clear it if this ticket needs different terms.")
            render_cancellation_policy_editor(data, f"mtu_{idx}")
            editable_field("Condition (internal remarks)", data, "cancellation_policy_text", widget="text_area", height=80, key_suffix=f"_{idx}")
            merge_what_to_bring_into_voucher_remarks(data)
            editable_field("Voucher Remarks (shown to the customer, includes what to bring)", data,
                           "voucher_remarks", widget="text_area", height=100, key_suffix=f"_{idx}")
            # Price-validity code (product owner, 2026-09-08) - build_ticket_payloads already
            # bakes this into voucher_remarks via with_price_validity_code, same as the
            # single-ticket "Whole ticket" update path - no separate voucher-remarks-only call
            # needed here since this flow always republishes the full ticket payload anyway.
            editable_field("Prices confirmed valid until (optional - the app adds the "
                           "\"(YYYYMMDD)\" marker to Voucher Remarks automatically)", data,
                           "price_valid_until_date", widget="text_input", key_suffix=f"_{idx}")

            st.markdown(f"**📍 Location for {current['label'] or current['target_ticket_code']}**")
            mtu_city = data.get("city", "")
            if (data.get("manual_latitude") is not None and data.get("manual_longitude") is not None
                    and data.get("manual_coords_for_city") != mtu_city):
                data["manual_latitude"] = None
                data["manual_longitude"] = None
                data.pop("manual_coords_for_city", None)
                _mtu_clear_geo_confirmation(current, idx)
            if data.get("manual_latitude") is not None and data.get("manual_longitude") is not None:
                mtu_geo = {"latitude": data["manual_latitude"], "longitude": data["manual_longitude"],
                          "display_name": mtu_city, "valid": True}
            else:
                mtu_geo = geocode(_geo_search_default(client, mtu_city))

            if mtu_geo.get("valid"):
                mtu_lat, mtu_lng = mtu_geo["latitude"], mtu_geo["longitude"]
                mtu_maps_link = f"https://www.google.com/maps?q={mtu_lat},{mtu_lng}"
                st.markdown(
                    f"<div style='background-color:#d4edda; color:#155724; padding:8px 12px; "
                    f"border-radius:4px;'>📍 Resolved: <strong>{mtu_geo.get('display_name') or mtu_city}</strong>"
                    f"<br>Coordinates: {mtu_lat:.6f}, {mtu_lng:.6f} — "
                    f"<a href='{mtu_maps_link}' target='_blank'>Open in Google Maps to verify</a></div>",
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

            with st.expander("🔍 Search for a better match / fix this location", expanded=not mtu_geo.get("valid")):
                mtu_geo_query = st.text_input("Search for a location", value=_geo_search_default(client, mtu_city), key=f"mtu_geo_query_{idx}")
                if st.button("🔎 Search", key=f"mtu_geo_search_btn_{idx}"):
                    with st.spinner("Searching..."):
                        current["geo_search_results"] = geocode_search(mtu_geo_query, limit=5)
                if current.get("geo_search_results"):
                    for gi, candidate in enumerate(current["geo_search_results"]):
                        ggcol1, ggcol2 = st.columns([4, 1])
                        with ggcol1:
                            st.write(f"**{candidate['display_name']}**")
                            st.caption(f"{candidate['latitude']:.6f}, {candidate['longitude']:.6f} ({candidate.get('type', '')})")
                        with ggcol2:
                            if st.button("Use this", key=f"mtu_geo_pick_{idx}_{gi}"):
                                data["manual_latitude"] = candidate["latitude"]
                                data["manual_longitude"] = candidate["longitude"]
                                data["manual_coords_for_city"] = mtu_city
                                _mtu_clear_geo_confirmation(current, idx)
                                current["geo_search_results"] = None
                                st.rerun()

                st.markdown("**Or paste a Google Maps link:**")
                mtu_maps_url = st.text_input("Google Maps link", key=f"mtu_geo_maps_url_{idx}", placeholder="https://maps.google.com/...")
                if st.button("🔗 Use this link's coordinates", key=f"mtu_geo_maps_url_btn_{idx}", disabled=not mtu_maps_url.strip()):
                    with st.spinner("Reading coordinates from the link..."):
                        mtu_url_geo = parse_google_maps_url(mtu_maps_url)
                    if mtu_url_geo["valid"]:
                        data["manual_latitude"] = mtu_url_geo["latitude"]
                        data["manual_longitude"] = mtu_url_geo["longitude"]
                        data["manual_coords_for_city"] = mtu_city
                        _mtu_clear_geo_confirmation(current, idx)
                        st.rerun()
                    else:
                        st.error(mtu_url_geo["error"])

                st.markdown("**Or enter coordinates manually:**")
                mgcol1, mgcol2 = st.columns(2)
                with mgcol1:
                    mtu_man_lat = st.number_input("Latitude", value=data.get("manual_latitude"), format="%.6f", key=f"mtu_geo_manlat_{idx}", placeholder="e.g. 27.394900")
                with mgcol2:
                    mtu_man_lng = st.number_input("Longitude", value=data.get("manual_longitude"), format="%.6f", key=f"mtu_geo_manlng_{idx}", placeholder="e.g. 33.678400")
                if st.button("📍 Use these coordinates", key=f"mtu_geo_manual_btn_{idx}", disabled=mtu_man_lat is None or mtu_man_lng is None):
                    data["manual_latitude"] = mtu_man_lat
                    data["manual_longitude"] = mtu_man_lng
                    data["manual_coords_for_city"] = mtu_city
                    _mtu_clear_geo_confirmation(current, idx)
                    st.rerun()

            current["geo_confirmed"] = st.checkbox(
                "✅ I've checked this location and it's correct for this ticket",
                value=current.get("geo_confirmed", False), key=f"mtu_geo_confirm_{idx}",
                disabled=not mtu_geo.get("valid")
            )
            if not mtu_geo.get("valid"):
                st.info("👆 Resolve the location above before this ticket can be confirmed.")
            elif not current["geo_confirmed"]:
                st.info("👆 Please check the location above and confirm it's correct.")

            st.markdown(f"**Images for {current['label'] or current['target_ticket_code']}**")
            if data.get("image_urls") == [FALLBACK_IMAGE] or not data.get("image_urls"):
                st.caption("⚠️ No real image on file yet - using a generic placeholder. Pick at least one "
                          "real image below (Travel Compositor requires at least one image per Ticket).")
            else:
                st.caption(f"{len([u for u in data.get('image_urls', []) if u != FALLBACK_IMAGE])} image(s) selected "
                          f"(carried over from the live ticket unless you change them below).")

            def _mtu_add_url_images():
                selected = render_url_image_picker(st.session_state.mtu_hosted_image_candidates, f"mtu_found_{idx}")
                if selected:
                    current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                    data["image_urls"] = current_imgs + selected
                    return len(selected)
                return 0

            render_closable_image_section(
                bool(st.session_state.get("mtu_hosted_image_candidates")),
                f"🖼️ Images found in your document/page ({len(st.session_state.get('mtu_hosted_image_candidates') or [])})",
                f"mtu_found_{idx}_closed", _mtu_add_url_images
            )

            def _mtu_add_doc_image():
                added = render_doc_image_picker(st.session_state.mtu_doc_raw_images, f"mtu_doc_{idx}")
                if added:
                    current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                    data["image_urls"] = current_imgs + [added]
                    return 1
                return 0

            render_closable_image_section(
                bool(st.session_state.get("mtu_doc_raw_images")),
                f"📥 Images needing hosting ({len(st.session_state.get('mtu_doc_raw_images') or [])})",
                f"mtu_doc_{idx}_closed", _mtu_add_doc_image
            )

            mtu_default_query = current["label"] or data.get("ticket_name", "") or data.get("city", "")

            def _mtu_add_pexels():
                selected = render_stock_photo_picker("Pexels", search_images, mtu_default_query, f"mtu_pexels_{idx}")
                if selected:
                    current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                    data["image_urls"] = current_imgs + selected
                    return len(selected)
                return 0

            render_closable_image_section(True, "🖼️ Search free stock photos (Pexels)", f"mtu_pexels_{idx}_closed", _mtu_add_pexels)

            def _mtu_add_pixabay():
                selected = render_stock_photo_picker("Pixabay", search_images_pixabay, mtu_default_query, f"mtu_pixabay_{idx}")
                if selected:
                    current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                    data["image_urls"] = current_imgs + selected
                    return len(selected)
                return 0

            render_closable_image_section(True, "🖼️ Search free stock photos (Pixabay)", f"mtu_pixabay_{idx}_closed", _mtu_add_pixabay)

            render_duration_editor(data, f"mtu_{idx}")

            inc_df = pd.DataFrame([{"Item": x} for x in data.get("includes", [])]) if data.get("includes") else pd.DataFrame(columns=["Item"])
            def _save_mtu_includes(edf, data=data):
                data["includes"] = [str(r.get("Item") or "").strip() for _, r in edf.iterrows() if _safe_cell_str(r.get("Item")).strip()]
            editable_table("Includes", inc_df, f"mtu_includes_{idx}", on_save=_save_mtu_includes)

            exc_df = pd.DataFrame([{"Item": x} for x in data.get("excludes", [])]) if data.get("excludes") else pd.DataFrame(columns=["Item"])
            def _save_mtu_excludes(edf, data=data):
                data["excludes"] = [str(r.get("Item") or "").strip() for _, r in edf.iterrows() if _safe_cell_str(r.get("Item")).strip()]
            editable_table("Excludes", exc_df, f"mtu_excludes_{idx}", on_save=_save_mtu_excludes)

            mp_default = [{"Description": m.get("description", "")} for m in data.get("meeting_points", [])] or [{"Description": "Hotel Lobby"}]
            mp_df = pd.DataFrame(mp_default)
            def _save_mtu_mp(edf, data=data):
                data["meeting_points"] = [
                    {"description": str(r.get("Description") or "").strip(), "variable_location": str(r.get("Description") or "").strip().lower() == "hotel lobby"}
                    for _, r in edf.iterrows() if _safe_cell_str(r.get("Description")).strip()
                ]
            editable_table("Meeting Points", mp_df, f"mtu_mp_{idx}", on_save=_save_mtu_mp)

            name_and_description_valid = bool((data.get("ticket_name") or "").strip()) and bool((data.get("description") or "").strip())
            ready_for_modality = name_and_description_valid and mtu_geo.get("valid") and current.get("geo_confirmed")

            if st.button("➡️ Continue to Modality/Pricing", type="primary", disabled=not ready_for_modality, key=f"mtu_continue_modality_{idx}"):
                with st.spinner(f"Extracting pricing/Modality{f' focused on ' + repr(current['label']) if variant_hint else ''}..."):
                    try:
                        modality_data = extract_ticket_modality_data(
                            st.session_state.mtu_raw_text, variant_hint=variant_hint,
                            human_hint=with_learned_guidance(supplier_id, "Ticket", ""))
                    except Exception as e:
                        st.error(f"⚠️ Couldn't extract pricing/Modality for this excursion: {friendly_error_message(e)}")
                        return
                    data.update(modality_data)
                    _apply_min_pax_guaranteed_departure_note(
                        data, ("cancellation_policy_text", "voucher_remarks"),
                        data.get("min_pax_guaranteed_departure"))
                    reset_child_age_band_widgets(f"mtu_{idx}")
                    floor_start_date_for_new_data(data, widget_key=f"mtu_start_date_{idx}")
                    st.session_state.pop(f"mtu_{idx}_languages", None)
                    st.session_state.pop(f"mtu_op_days_{idx}", None)
                    st.session_state.pop(f"mtu_end_date_{idx}", None)
                    st.session_state.pop(f"mtu_{idx}_price_type", None)
                    st.session_state.pop(f"mtu_{idx}_service_price", None)
                current["step"] = "modality"
                st.rerun()
            if not ready_for_modality:
                st.info("Fill in Ticket name/Description and confirm the location above before continuing to Modality/Pricing.")
            return

        # ==================================================================
        # STEP B: MODALITY / PRICING
        # ==================================================================
        st.caption(f"**Step 2 of 2: Modality/Pricing for {current['label'] or current['target_ticket_code']}.**")
        if st.button("🔙 Back to main info", key=f"mtu_back_to_main_{idx}"):
            current["step"] = "main"
            st.rerun()

        if min_pax_forces_on_request(data.get("min_pax_guaranteed_departure")):
            st.warning(f"🔒 {min_pax_guaranteed_departure_note(data.get('min_pax_guaranteed_departure'))} "
                      f"This Ticket will be published **On Request** regardless of the On Request setting "
                      f"above - a note was also added to Condition/Voucher Remarks.")

        render_child_age_band(data, key_prefix=f"mtu_{idx}",
                              min_key="child_age_min", max_key="child_age_max")

        st.markdown("**Start Time(s)**")
        tt_df = pd.DataFrame([{"Time (HH:MM)": t} for t in data.get("time_tables", [])]) if data.get("time_tables") else pd.DataFrame(columns=["Time (HH:MM)"])
        def _save_mtu_timetables(edf, data=data):
            data["time_tables"] = _clean_time_table_rows(edf)
        editable_table("Start Time(s)", tt_df, f"mtu_timetables_{idx}", on_save=_save_mtu_timetables)
        if not data.get("time_tables"):
            st.caption("ℹ️ No start time set yet - optional, but add one if the excursion has a fixed departure time.")

        data["operational_days"] = st.multiselect(
            "Operational Days", ALL_WEEKDAYS, default=data.get("operational_days", ALL_WEEKDAYS), key=f"mtu_op_days_{idx}"
        )

        # CONFIRMED REAL RULE (product owner): an UPDATE never asks for things the live record
        # already has - this item's own live Currency (fetched with the ticket, not chosen once
        # for the whole batch) wins here, same as the single-ticket update path.
        item_currency = live_ticket.get("currency") or "EUR"
        item_currency = render_currency_check(item_currency, CURRENCY_OPTIONS, "tk_cfg_currency", f"mtu_currency_{idx}")
        st.markdown(f"**Pricing (in {item_currency})**")
        item_max_passengers = live_ticket.get("maxPassengers") or max_passengers
        render_ticket_pricing_editor(data, f"mtu_{idx}", item_currency, item_max_passengers)
        mtu_price_type = data["price_type"]

        dcol1, dcol2 = st.columns(2)
        with dcol1:
            data["start_date"] = _dmy_date_field("Valid From (DD/MM/YYYY)", f"mtu_start_date_{idx}", value_iso=data.get("start_date", ""))
        with dcol2:
            data["end_date"] = _dmy_date_field("Valid Until (DD/MM/YYYY)", f"mtu_end_date_{idx}", value_iso=data.get("end_date", ""))
        if data.get("pricing_notes"):
            st.warning(f"⚠️ {data['pricing_notes']}")

        render_stop_sales_editor(data, f"mtu_{idx}")
        render_ticket_modality_supplements_editor(data, f"mtu_{idx}")
        render_ticket_language_options(data, f"mtu_{idx}")

        st.markdown(f"**🤖 Tell AI what to fix - {current['label'] or current['target_ticket_code']}**")
        mtu_clarify_q = st.text_input("Your message", key=f"mtu_clarify_input_{idx}")
        if render_house_rule_shortcut(mtu_clarify_q, "Ticket", f"mtu_{idx}"):
            pass
        elif not mtu_clarify_q.strip():
            st.caption(f"Type a message above first — Send stays disabled until there's something to send. "
                      f"Start with \"{HOUSE_RULE_CODEWORD}\" to save a standing rule for every Ticket "
                      f"supplier instead of a one-off fix.")
        if not mtu_clarify_q.strip().upper().startswith(HOUSE_RULE_CODEWORD.upper()) and st.button(
                "Send", disabled=not mtu_clarify_q.strip(), key=f"mtu_clarify_send_{idx}"):
            with st.spinner("Thinking..."):
                result = apply_clarification(st.session_state.mtu_raw_text, data, mtu_clarify_q)
                st.session_state[f"mtu_clarify_result_{idx}"] = result
                remember_clarification(clarify_supplier_id(supplier_id), "Ticket", mtu_clarify_q, result)
                if result.get("changes"):
                    apply_clarify_changes(data, result, item_currency)
                    mtu_field_to_table_key = {
                        "includes": f"_editing_table_mtu_includes_{idx}",
                        "excludes": f"_editing_table_mtu_excludes_{idx}",
                        "meeting_points": f"_editing_table_mtu_mp_{idx}",
                        "time_tables": f"_editing_table_mtu_timetables_{idx}",
                        "stop_sales": f"_editing_table_mtu_{idx}_stop_sales",
                        "modality_supplements": f"_editing_table_mtu_{idx}_modality_supplements",
                        "occupancy_prices": f"_editing_table_mtu_{idx}_occupancy",
                    }
                    for field_name in result["changes"]:
                        table_key = mtu_field_to_table_key.get(field_name)
                        if table_key:
                            st.session_state[table_key] = False
                    reset_stale_editable_field_widgets(result["changes"], key_suffix=f"_{idx}")
                    if "operational_days" in result["changes"]:
                        st.session_state.pop(f"mtu_op_days_{idx}", None)
                st.rerun()
        if st.session_state.get(f"mtu_clarify_result_{idx}"):
            r = st.session_state[f"mtu_clarify_result_{idx}"]
            render_clarify_result(r)
        remember_memory_panel(clarify_supplier_id(supplier_id), "Ticket", "mtu")

        if mtu_price_type == "SERVICE":
            price_valid = bool(data.get("base_service_price", 0))
        elif mtu_price_type == "OCCUPANCY":
            _occ_rows = data.get("occupancy_prices") or []
            _zero_occ = [o.get("occupancy") for o in _occ_rows if not _safe_float(o.get("amount"), fallback=0.0)]
            price_valid = bool(_occ_rows) and not _zero_occ
            if _zero_occ:
                st.error(f"🚫 No price for occupancy: **{', '.join(str(o) for o in _zero_occ)}** - "
                         f"these would be sellable for free. Enter a price for each, or remove the row.")
        else:
            price_valid = any([data.get("base_adult_price", 0), data.get("base_children_price", 0), data.get("base_infant_price", 0)])
        name_and_description_valid = bool((data.get("ticket_name") or "").strip()) and bool((data.get("description") or "").strip())
        can_continue = price_valid and name_and_description_valid

        is_last = idx == len(queue) - 1
        btn_label = "✅ Confirm this Ticket & Finish Review" if is_last else "✅ Confirm this Ticket & Continue →"
        if st.button(btn_label, type="primary", disabled=not can_continue, key=f"mtu_confirm_{idx}"):
            current["confirmed"] = True
            current["_currency"] = item_currency
            current["_max_passengers"] = item_max_passengers
            if is_last:
                st.session_state.mtu_phase = "publishing"
            else:
                st.session_state.mtu_queue_index += 1
            st.rerun()
        if not price_valid:
            st.info("Add at least one non-zero price before continuing.")
        return

    # ------------------------------------------------------------------
    # PHASE 4: publish all confirmed Tickets, ONE BY ONE
    # ------------------------------------------------------------------
    if st.session_state.mtu_phase == "publishing":
        queue = st.session_state.mtu_queue
        if "mtu_update_failed_items" not in st.session_state:
            # Failed BEFORE any live ticket was actually touched (payload build error,
            # unresolved geolocation, a publish blocker) - always safe to retry in full.
            st.session_state.mtu_update_failed_items = []
        if "mtu_option_failed_items" not in st.session_state:
            # The ticket's own details WERE updated successfully - only the Modality's
            # pricing/schedule failed - retrying must only redo the option, not the ticket
            # details again (same split as render_multi_ticket_flow's mt_failed_items).
            st.session_state.mtu_option_failed_items = []

        st.subheader(f"Ready to publish {len(queue)} Ticket updates - one by one")
        for q in queue:
            st.write(f"- **{q['target_ticket_code']}** ({q['label']}) - Modality: {q['modality_code']}")

        _warn_stale_images([u for q in queue for u in (q.get("data", {}).get("image_urls") or [])])

        if st.button("🚀 Publish all updates (one by one)", type="primary", key="mtu_publish_all"):
            for q in queue:
                with st.spinner(f"Updating '{q['target_ticket_code']}'..."):
                    def _park_update_failure(q=q):
                        st.session_state.mtu_update_failed_items.append({
                            "target_ticket_code": q["target_ticket_code"], "label": q["label"],
                            "modality_code": q["modality_code"], "modality_name": q.get("modality_name"),
                            "data": q["data"], "live_ticket": q.get("live_ticket"),
                        })
                    _ticket_was_updated = False
                    try:
                        item_currency = q.get("_currency") or (q.get("live_ticket") or {}).get("currency") or "EUR"
                        item_min_passengers = (q.get("live_ticket") or {}).get("minPassengers") or 1
                        item_max_passengers = q.get("_max_passengers") or (q.get("live_ticket") or {}).get("maxPassengers") or max_passengers
                        # CONFIRMED PRODUCT-OWNER RULE (2026-09-08 follow-up): "we do not need to
                        # ask the human again for release date, this is already set and wont
                        # change" - each ticket's OWN live daysAvailableBeforeRelease wins, same
                        # "an UPDATE never asks for things the live record already has" rule as
                        # currency/passenger limits just above. `release_days` (the Step 3
                        # fallback, unused for this action - see TICKET_ACTION_FIELDS) only
                        # covers a live ticket whose own value can't be read.
                        item_release_days = (q.get("live_ticket") or {}).get("daysAvailableBeforeRelease")
                        if item_release_days in (None, ""):
                            item_release_days = release_days
                        pre_config = TicketHumanPreConfig(
                            supplier_id=supplier_id, ticket_code=q["target_ticket_code"], currency=item_currency,
                            modality_code=q["modality_code"], modality_name=q.get("modality_name"),
                            on_request=on_request or min_pax_forces_on_request(q["data"].get("min_pax_guaranteed_departure")),
                            days_available_before_release=item_release_days,
                            min_passengers=item_min_passengers, max_passengers=item_max_passengers,
                        )
                        payloads = build_ticket_payloads(pre_config, q["data"], client)
                        if payloads["main_ticket_error"] or payloads["ticket_option_error"]:
                            show_publish_error(f"prepare **{q['target_ticket_code']}**'s payload",
                                              payloads['main_ticket_error'] or payloads['ticket_option_error'])
                            _park_update_failure()
                            continue
                        if not payloads["geolocation_resolved"]:
                            st.error(f"❌ **{q['target_ticket_code']}**: geolocation not resolved - skipped.")
                            _park_update_failure()
                            continue
                        if not render_publish_blockers(payloads):
                            st.error(f"🚫 **{q['target_ticket_code']}**: skipped - see the error(s) above.")
                            _park_update_failure()
                            continue

                        mtu_update_payload = dict(payloads["main_ticket_payload"])
                        mtu_update_payload["code"] = q["target_ticket_code"]
                        # CONFIRMED FIX (same class of bug as the single-ticket update path,
                        # audit CRITICAL #2, 2026-09-01): build_ticket_payloads always sets
                        # active=False (correct for a brand-new ticket) - the LIVE record's own
                        # active state must win on an update instead, or every published update
                        # here would silently take a live/active ticket off sale.
                        _mtu_live_active = (q.get("live_ticket") or {}).get("active")
                        if _mtu_live_active is not None:
                            mtu_update_payload["active"] = _mtu_live_active

                        result = client.update_ticket(supplier_id, mtu_update_payload)
                        if "error" in result:
                            show_publish_error(f"update **{q['target_ticket_code']}**", result)
                            _park_update_failure()
                            continue
                        _ticket_was_updated = True
                        st.success(f"✅ **{q['target_ticket_code']}**: ticket details updated.")

                        mtu_update_option_payload = dict(payloads["ticket_option_payload"])
                        mtu_update_option_payload["code"] = q["modality_code"]
                        option_result = client.update_ticket_option(supplier_id, q["target_ticket_code"], mtu_update_option_payload)
                        if "error" in option_result:
                            show_publish_error(f"update **{q['target_ticket_code']}**'s Modality "
                                              f"'{q['modality_code']}'", option_result)
                            st.session_state.mtu_option_failed_items.append({
                                "target_ticket_code": q["target_ticket_code"], "label": q["label"],
                                "modality_code": q["modality_code"], "modality_name": q.get("modality_name"),
                                "data": q["data"], "live_ticket": q.get("live_ticket"),
                            })
                            continue
                        st.success(f"✅ **{q['target_ticket_code']}**: Modality '{q['modality_code']}' pricing/schedule updated.")
                    except Exception as e:
                        show_publish_error(f"update **{q['target_ticket_code']}** (unexpected error - "
                                          f"skipped, rest of batch continues)", str(e))
                        if not _ticket_was_updated:
                            _park_update_failure()
                        continue

        if st.session_state.mtu_option_failed_items:
            st.divider()
            st.subheader(f"⚠️ {len(st.session_state.mtu_option_failed_items)} ticket(s) updated but their Modality failed")
            st.caption("The ticket's own details WERE updated successfully - only the Modality's pricing/"
                      "schedule failed. Adjust below and retry just the Modality - no need to redo the "
                      "whole batch.")
            for fi_idx, fi in enumerate(list(st.session_state.mtu_option_failed_items)):
                with st.expander(f"🔧 {fi['target_ticket_code']} — {fi['label']}", expanded=True):
                    fdata = fi["data"]
                    fi_currency = (fi.get("live_ticket") or {}).get("currency") or "EUR"
                    fi_currency = render_currency_check(fi_currency, CURRENCY_OPTIONS, "tk_cfg_currency", f"mtuf_currency_{fi_idx}")
                    fi_max_passengers = (fi.get("live_ticket") or {}).get("maxPassengers") or max_passengers
                    render_ticket_pricing_editor(fdata, f"mtuf_{fi_idx}", fi_currency, fi_max_passengers)

                    ftt_df = pd.DataFrame([{"Time (HH:MM)": t} for t in fdata.get("time_tables", [])]) if fdata.get("time_tables") else pd.DataFrame(columns=["Time (HH:MM)"])
                    def _save_mtuf_tt(edf, fdata=fdata):
                        fdata["time_tables"] = _clean_time_table_rows(edf)
                    editable_table("Start Time(s)", ftt_df, f"mtuf_tt_{fi_idx}", on_save=_save_mtuf_tt)
                    fdcol1, fdcol2 = st.columns(2)
                    with fdcol1:
                        fdata["start_date"] = _dmy_date_field("Valid From (DD/MM/YYYY)", f"mtuf_start_{fi_idx}", value_iso=fdata.get("start_date", ""))
                    with fdcol2:
                        fdata["end_date"] = _dmy_date_field("Valid Until (DD/MM/YYYY)", f"mtuf_end_{fi_idx}", value_iso=fdata.get("end_date", ""))

                    if st.button(f"🔄 Retry Modality for `{fi['target_ticket_code']}`", key=f"mtuf_retry_{fi_idx}", type="primary"):
                        with st.spinner(f"Retrying '{fi['target_ticket_code']}'..."):
                            try:
                                _fi_release_days = (fi.get("live_ticket") or {}).get("daysAvailableBeforeRelease")
                                if _fi_release_days in (None, ""):
                                    _fi_release_days = release_days
                                retry_pre_config = TicketHumanPreConfig(
                                    supplier_id=supplier_id, ticket_code=fi["target_ticket_code"], currency=fi_currency,
                                    modality_code=fi["modality_code"], modality_name=fi.get("modality_name"),
                                    on_request=on_request,
                                    days_available_before_release=_fi_release_days,
                                    min_passengers=(fi.get("live_ticket") or {}).get("minPassengers") or 1,
                                    max_passengers=fi_max_passengers,
                                )
                                retry_payloads = build_ticket_payloads(retry_pre_config, fdata, client)
                                if retry_payloads["ticket_option_error"]:
                                    show_publish_error(f"prepare **{fi['target_ticket_code']}**'s payload", retry_payloads["ticket_option_error"])
                                elif not retry_payloads["geolocation_resolved"]:
                                    st.error("❌ Geolocation not resolved.")
                                elif not render_publish_blockers(retry_payloads):
                                    pass
                                else:
                                    retry_option_result = client.update_ticket_option(
                                        supplier_id, fi["target_ticket_code"], {**retry_payloads["ticket_option_payload"], "code": fi["modality_code"]})
                                    if "error" in retry_option_result:
                                        show_publish_error(f"retry **{fi['target_ticket_code']}**'s Modality", retry_option_result)
                                    else:
                                        st.success(f"✅ **{fi['target_ticket_code']}**: Modality updated on retry.")
                                        st.session_state.mtu_option_failed_items = [
                                            x for x in st.session_state.mtu_option_failed_items if x is not fi
                                        ]
                                        st.rerun()
                            except Exception as e:
                                show_publish_error(f"retry **{fi['target_ticket_code']}**'s Modality (unexpected error)", str(e))

        if st.session_state.mtu_update_failed_items:
            st.divider()
            st.subheader(f"⚠️ {len(st.session_state.mtu_update_failed_items)} ticket(s) couldn't be updated")
            st.caption("Nothing was changed for these on Travel Compositor yet - fix whatever the error "
                      "above pointed at and retry just this one.")
            for pf_idx, pf in enumerate(list(st.session_state.mtu_update_failed_items)):
                with st.expander(f"🔧 {pf['target_ticket_code']} — {pf['label']}", expanded=True):
                    pfdata = pf["data"]
                    pf_currency = (pf.get("live_ticket") or {}).get("currency") or "EUR"
                    pf_currency = render_currency_check(pf_currency, CURRENCY_OPTIONS, "tk_cfg_currency", f"mtup_currency_{pf_idx}")
                    pf_max_passengers = (pf.get("live_ticket") or {}).get("maxPassengers") or max_passengers
                    render_ticket_pricing_editor(pfdata, f"mtup_{pf_idx}", pf_currency, pf_max_passengers)

                    pf_tt_df = pd.DataFrame([{"Time (HH:MM)": t} for t in pfdata.get("time_tables", [])]) if pfdata.get("time_tables") else pd.DataFrame(columns=["Time (HH:MM)"])
                    def _save_mtup_tt(edf, pfdata=pfdata):
                        pfdata["time_tables"] = _clean_time_table_rows(edf)
                    editable_table("Start Time(s)", pf_tt_df, f"mtup_tt_{pf_idx}", on_save=_save_mtup_tt)
                    pf_dcol1, pf_dcol2 = st.columns(2)
                    with pf_dcol1:
                        pfdata["start_date"] = _dmy_date_field("Valid From (DD/MM/YYYY)", f"mtup_start_{pf_idx}", value_iso=pfdata.get("start_date", ""))
                    with pf_dcol2:
                        pfdata["end_date"] = _dmy_date_field("Valid Until (DD/MM/YYYY)", f"mtup_end_{pf_idx}", value_iso=pfdata.get("end_date", ""))

                    if st.button(f"🔄 Retry updating `{pf['target_ticket_code']}`", key=f"mtup_retry_{pf_idx}", type="primary"):
                        with st.spinner(f"Retrying '{pf['target_ticket_code']}'..."):
                            try:
                                _pf_release_days = (pf.get("live_ticket") or {}).get("daysAvailableBeforeRelease")
                                if _pf_release_days in (None, ""):
                                    _pf_release_days = release_days
                                retry_pre_config = TicketHumanPreConfig(
                                    supplier_id=supplier_id, ticket_code=pf["target_ticket_code"], currency=pf_currency,
                                    modality_code=pf["modality_code"], modality_name=pf.get("modality_name"),
                                    on_request=on_request or min_pax_forces_on_request(pfdata.get("min_pax_guaranteed_departure")),
                                    days_available_before_release=_pf_release_days,
                                    min_passengers=(pf.get("live_ticket") or {}).get("minPassengers") or 1,
                                    max_passengers=pf_max_passengers,
                                )
                                retry_payloads = build_ticket_payloads(retry_pre_config, pfdata, client)
                                if retry_payloads["main_ticket_error"] or retry_payloads["ticket_option_error"]:
                                    show_publish_error(f"prepare **{pf['target_ticket_code']}**'s payload",
                                                      retry_payloads["main_ticket_error"] or retry_payloads["ticket_option_error"])
                                elif not retry_payloads["geolocation_resolved"]:
                                    st.error("❌ Geolocation not resolved.")
                                elif not render_publish_blockers(retry_payloads):
                                    pass
                                else:
                                    retry_update_payload = dict(retry_payloads["main_ticket_payload"])
                                    retry_update_payload["code"] = pf["target_ticket_code"]
                                    _retry_live_active = (pf.get("live_ticket") or {}).get("active")
                                    if _retry_live_active is not None:
                                        retry_update_payload["active"] = _retry_live_active
                                    retry_result = client.update_ticket(supplier_id, retry_update_payload)
                                    if "error" in retry_result:
                                        show_publish_error(f"update **{pf['target_ticket_code']}**", retry_result)
                                    else:
                                        st.success(f"✅ **{pf['target_ticket_code']}**: ticket details updated on retry.")
                                        retry_option_result = client.update_ticket_option(
                                            supplier_id, pf["target_ticket_code"],
                                            {**retry_payloads["ticket_option_payload"], "code": pf["modality_code"]})
                                        if "error" in retry_option_result:
                                            show_publish_error(f"update **{pf['target_ticket_code']}**'s Modality (updated as `{pf['target_ticket_code']}`)", retry_option_result)
                                            st.session_state.mtu_option_failed_items.append({
                                                "target_ticket_code": pf["target_ticket_code"], "label": pf["label"],
                                                "modality_code": pf["modality_code"], "modality_name": pf.get("modality_name"),
                                                "data": pfdata, "live_ticket": pf.get("live_ticket"),
                                            })
                                        else:
                                            st.success(f"✅ **{pf['target_ticket_code']}**: Modality '{pf['modality_code']}' updated too.")
                                        st.session_state.mtu_update_failed_items = [
                                            x for x in st.session_state.mtu_update_failed_items if x is not pf
                                        ]
                                        st.rerun()
                            except Exception as e:
                                show_publish_error(f"retry updating **{pf['target_ticket_code']}** (unexpected error)", str(e))

        st.write("")
        st.divider()
        if st.button("🆕 Start a new batch update", key="mtu_new_batch"):
            for key in ["mtu_phase", "mtu_raw_text", "mtu_candidates", "mtu_queue", "mtu_queue_index",
                       "mtu_doc_raw_images", "mtu_hosted_image_candidates", "mtu_update_failed_items",
                       "mtu_option_failed_items", "mtu_live_ticket_cache"]:
                st.session_state.pop(key, None)
            _clear_batch_widget_state(["mtu_"] + SHARED_WIDGET_STATE_PREFIXES)
            st.rerun()
        return
