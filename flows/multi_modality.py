"""
Multi-modality (ClosedTour "add Modality to an existing tour") flow, split out of app.py
(Phase 1 restructure, zero behaviour change).

render_multi_modality_flow moved here verbatim. Everything it references that is defined at
app.py's own top level is imported back from app via the same late-binding pattern used by the
earlier flows modules: app.py imports this module only after all of those names are already
defined in its own namespace, so `from app import ...` resolves correctly despite the circular
import shape. **Note**: like flows/multi_tour.py (module 4), several names this function needs
(`apply_clarify_changes`, `clarify_supplier_id`, `remember_clarification`, `render_clarify_
result`, `render_house_rule_shortcut`, `HOUSE_RULE_CODEWORD`, `render_skip_item_button`,
`fetched_tour_matches_code`, `try_code_variants`) are defined LATER in app.py's file order than
this function's own original position (it was physically early, lines 548-873) - so app.py's
`from flows.multi_modality import render_multi_modality_flow` line sits well after that original
position, alongside the other flows-import lines (after `try_code_variants`), not at it.
"""
import os
import tempfile
import pandas as pd
import streamlit as st

from schemas import HumanPreConfig
from builder import build_closed_tour_payloads, coerce_price_list_shape
from document_reader import extract_raw_text
from document_reader import scanned_document_warning as document_reader_scanned_warning
from ai_extractor import (
    apply_clarification, detect_multiple_modalities, extract_option_only_data,
    friendly_error_message, min_pax_forces_on_request, min_pax_guaranteed_departure_note,
)
from date_format import to_iso_date as _iso, to_display_date as _disp
from ui_components import (
    editable_table, render_child_discount_editor, render_extra_child_notice,
    render_stop_sales_editor, _safe_cell_str,
)

from app import (
    ALL_WEEKDAYS, HOUSE_RULE_CODEWORD, SHARED_WIDGET_STATE_PREFIXES, _clean_modality_code,
    _clear_batch_widget_state, _fetch_url_text_safe, _modality_code_suspicious,
    apply_clarify_changes, clarify_supplier_id, fetched_tour_matches_code,
    remember_clarification, remember_memory_panel, render_candidate_filter,
    render_clarify_result, render_house_rule_shortcut, render_skip_item_button,
    reset_stale_editable_field_widgets, show_publish_error, try_code_variants,
)


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
        render_candidate_filter(candidates, "mm", "modality")
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

            # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-18): "the child discount must be added to
            # all modality fields" - same gap as flows/multi_tour.py (see its own comment for the
            # full reasoning): each Modality in this queue is extracted independently by its own
            # AI call against the SAME shared source document, and doesn't always re-detect a
            # document-wide child_discount_percentage on every single item even when the source
            # states it once for the whole tour. Unlike multi_tour.py there's no dedicated "first
            # Modality" - this flow is a flat queue - so the earliest already-reviewed item that
            # actually HAS a value (queue[0] once it's been extracted, by construction the first
            # one reviewed) is used as the fallback source. Falls back ONLY when THIS item's own
            # extraction came back with none at all - an item that DID detect its own (possibly
            # genuinely different) discount keeps it; nothing here can silently overwrite a real
            # per-Modality value. Still fully editable/removable per Modality below via
            # render_child_discount_editor.
            if (idx > 0 and queue[0]["data"]
                    and current["data"].get("child_discount_percentage") is None):
                current["data"]["child_discount_percentage"] = \
                    queue[0]["data"].get("child_discount_percentage")

        data = current["data"]

        if data.get("schedule_notes"):
            st.info(f"🔎 {data['schedule_notes']}")

        if min_pax_forces_on_request(data.get("min_pax_guaranteed_departure")):
            st.warning(f"🔒 {min_pax_guaranteed_departure_note(data.get('min_pax_guaranteed_departure'))} "
                      f"This Modality will be published **On Request** regardless of the On Request "
                      f"setting below - this flow doesn't edit the tour's Policy remarks, so add this "
                      f"note there yourself in Travel Compositor if it isn't already stated.")

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
                            modality_code=q["code"],
                            # CONFIRMED PRODUCT-OWNER RULE (2026-09-03): "If Ticket or Closedtour
                            # has minimum of 3 pax or higher, we must set the ticket or closedtour
                            # on request" - this flow has no tour-level Policy remarks in scope
                            # (see extract_option_only_data's own docstring), so only the On
                            # Request forcing applies here; the note itself is surfaced as an
                            # on-screen warning below instead (see the min_pax_forces_on_request
                            # check right before the pricing table further up this screen).
                            on_request=on_request or min_pax_forces_on_request(q["data"].get("min_pax_guaranteed_departure"))
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
