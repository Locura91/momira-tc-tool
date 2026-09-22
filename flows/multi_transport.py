"""
Multi-transport product flow, split out of app.py (Phase 1 restructure, zero behaviour change).

render_multi_transport_flow moved here verbatim. Everything it references that is defined at
app.py's own top level is imported back from app via the same late-binding pattern used by the
earlier flows modules: app.py imports this module only after all of those names are already
defined in its own namespace, so `from app import ...` resolves correctly despite the circular
import shape. Every needed internal name here is defined earlier in app.py's file order than this
function's own original position, so (unlike flows/multi_tour.py) the import-back line stays at
that original position.
"""
import os
import tempfile
import pandas as pd
import streamlit as st

from schemas import TransportHumanPreConfig
from builder import (
    build_transport_payloads, derive_arrival_from_duration, transport_type_is_confirmed_match,
)
from document_reader import extract_raw_text
from document_reader import scanned_document_warning as document_reader_scanned_warning
from ai_extractor import detect_transport_products, extract_transport_data, friendly_error_message
import cancellation_links
import draft_autosave
import extraction_memory
import publish_advisor
import service_notes
import supplier_images
import transport_matcher
from ui_components import (
    editable_table, editable_field, render_cancellation_policy_editor,
    _safe_float, _safe_int,
)

from app import (
    CURRENCY_OPTIONS, SHARED_WIDGET_STATE_PREFIXES, _clear_batch_widget_state,
    _fetch_url_text_safe, _warn_stale_images, ensure_return_candidates,
    render_batch_bulk_controls, render_candidate_filter, render_detection_diagnosis,
    render_direction_image_section, render_empty_detection_retry, render_publish_blockers,
    render_skip_item_button, seed_transport_from_candidate, show_publish_error,
    with_learned_guidance,
)


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
        # Price-validity code (product owner, 2026-09-08) - see price_validity.py's own
        # docstring. Transport has no separate Voucher Remarks field, so this code ends up
        # appended to Description instead (same place Transport's cancellation text already
        # goes - see builder.py's own comment on ContractTransportDataSheetVO).
        editable_field("Prices confirmed valid until (optional - the app adds the "
                       "\"(YYYYMMDD)\" marker to Description automatically)", data,
                       "price_valid_until_date", key_suffix=key_suffix)

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

            _warn_stale_images(data.get("image_urls"))

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
                            # CONFIRMED REAL PRODUCTION BUG (product owner, 2026-09-18, discovered
                            # via the duplicate-and-swap Transport flow - same root cause applies
                            # here, this path had simply never been exercised for a genuinely new
                            # Transport before): Travel Compositor rejects
                            # "modalityAvailableWhenActive: You must add at least one modality!"
                            # on any Transport create with active=true and zero Options - which
                            # every brand-new Transport genuinely has at this exact moment, since
                            # its Options can only be created AFTER the parent has a real id.
                            # build_transport_payloads also already fills transport_payload's
                            # optionCodes with the brand-new codes it's ABOUT to create (see its
                            # own docstring) - sending those unresolved codes on a create risks a
                            # separate "null PK" error, same class of problem
                            # build_transport_swap_payload hit and fixed for the duplicate flow.
                            # Same two-step fix here: create the parent with optionCodes EMPTY and
                            # active=False (nothing to violate, nothing to resolve), create every
                            # Option under the new id below, then a follow-up PUT (after Stage 2)
                            # sets the real optionCodes and flips active back to True.
                            create_payload = dict(build_result["transport_payload"])
                            create_payload["optionCodes"] = []
                            create_payload["active"] = False
                            result = client.create_transport(supplier_id, create_payload)

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
                                created_codes = []
                                for a in option_actions:
                                    if not a.get("option_payload"):
                                        continue
                                    if a["action"] == "update":
                                        opt_result = client.update_transport_option(supplier_id, final_id, a["option_payload"])
                                    else:
                                        opt_result = client.create_transport_option(supplier_id, final_id, a["option_payload"])
                                    if isinstance(opt_result, dict) and "error" in opt_result:
                                        option_failures.append((a["code"], opt_result))
                                    else:
                                        created_codes.append(a["code"])

                                for stale in (build_result.get("options_to_deactivate") or []):
                                    stale_result = client.update_transport_option(supplier_id, final_id, stale)
                                    if isinstance(stale_result, dict) and "error" in stale_result:
                                        option_failures.append((stale.get("code"), stale_result))

                                # STAGE 3 (fresh create only) - now that at least one Option
                                # genuinely exists, link the real optionCodes and flip the parent
                                # back to active=True. See STAGE 1's comment above for why it was
                                # created inactive with no codes in the first place.
                                if not chosen_existing_id:
                                    if created_codes:
                                        with st.spinner("Activating the new transport..."):
                                            link_payload = dict(build_result["transport_payload"])
                                            link_payload["id"] = final_id
                                            link_payload["optionCodes"] = created_codes
                                            link_payload["active"] = True
                                            link_result = client.update_transport(supplier_id, link_payload)
                                        if isinstance(link_result, dict) and "error" in link_result:
                                            st.warning(f"⚠️ Published (id: {final_id}) with "
                                                      f"{len(created_codes)} occupancy bracket(s), "
                                                      f"but couldn't link/activate them "
                                                      f"({link_result.get('message', link_result)}) "
                                                      f"- open the transport in Travel Compositor "
                                                      f"and set its optionCodes and active status "
                                                      f"manually.")
                                    else:
                                        st.warning(f"⚠️ Published (id: {final_id}), but every "
                                                  f"occupancy bracket failed - the transport was "
                                                  f"left **inactive** in Travel Compositor (it "
                                                  f"can't be active with no modalities). Add at "
                                                  f"least one occupancy bracket manually in "
                                                  f"Travel Compositor, then activate it there.")

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
            # CONFIRMED PRODUCT-OWNER REPORT (2026-09-22): the "found unfinished work"
            # draft-restore banner kept coming back even after a successful publish - the whole
            # batch succeeding means there's nothing left worth protecting. Safe to call on
            # every render of this success screen.
            draft_autosave.clear_on_publish_success()
            st.success(f"🎉 All {len(queue)} transport(s) in this batch published.")
            st.write("")
            st.divider()
            if st.button("🆕 Start a new batch", key="xtp_new_batch"):
                for key in XTP_STATE_KEYS:
                    st.session_state.pop(key, None)
                _clear_batch_widget_state(["xtp_"] + SHARED_WIDGET_STATE_PREFIXES)
                st.rerun()
        return
