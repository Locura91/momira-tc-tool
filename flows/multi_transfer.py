"""
Multi-transfer product flow, split out of app.py (Phase 1 restructure, zero behaviour change).

render_multi_transfer_flow moved here verbatim. Everything it references that is defined at
app.py's own top level is imported back from app via the same late-binding pattern used by the
earlier flows modules: app.py imports this module only after all of those names are already
defined in its own namespace, so `from app import ...` resolves correctly despite the circular
import shape. Every needed internal name here is defined earlier in app.py's file order than this
function's own original position, so the import-back line stays at that original position.
`render_direction_image_section` is shared with flows/multi_transport.py (module 5) - it stays in
app.py (candidate for the later app_helpers.py module) and both flow modules import it back.
"""
import os
import tempfile
import pandas as pd
import streamlit as st

from schemas import TransferHumanPreConfig
from builder import build_transfer_payload
from document_reader import extract_raw_text
from document_reader import scanned_document_warning as document_reader_scanned_warning
from ai_extractor import detect_transfer_products, extract_transfer_data, friendly_error_message
import cancellation_links
import draft_autosave
import extraction_memory
import service_notes
import supplier_images
import transfer_matcher
from ui_components import (
    editable_table, editable_field, render_cancellation_policy_editor,
    _safe_float, _safe_int,
)

from app import (
    CURRENCY_OPTIONS, SHARED_WIDGET_STATE_PREFIXES, _clear_batch_widget_state,
    _fetch_url_text_safe, _warn_stale_images, ensure_return_candidates,
    render_batch_bulk_controls, render_candidate_filter, render_detection_diagnosis,
    render_direction_image_section, render_empty_detection_retry, render_publish_blockers,
    render_skip_item_button, render_supplement_zero_price_notes, show_publish_error,
    with_learned_guidance,
)


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
        # CONFIRMED BUG (audit, 2026-09-25) - same class of issue as the ClosedTour Hotels widget
        # fix on 2026-09-24: these three fields are documented (ai_extractor.py's own
        # TRANSFER_EXTRACTION_SYSTEM_PROMPT) as multi-sentence prose ("1-2 short plain-English
        # sentences", "informational text about location-conditional costs", "any specific pickup
        # logistics/instructions"), exactly like Transport's equivalent fields just below in
        # flows/multi_transport.py - which correctly use widget="text_area". These three had no
        # widget= at all, defaulting to the single-line text_input, cramping multi-sentence text
        # into a box built for a short value. Matched to Transport's own heights for consistency.
        editable_field("Location note (e.g. a harbor-only pickup fee) — goes to Voucher Remarks, never applied to price",
                       data, "location_notes", widget="text_area", height=80, key_suffix=key_suffix)
        editable_field("Description", data, "description", widget="text_area", height=100, key_suffix=key_suffix)
        editable_field("Pickup information", data, "pickup_information", widget="text_area", height=80,
                       key_suffix=key_suffix)
        # Price-validity code (product owner, 2026-09-08) - see price_validity.py's own
        # docstring. Blank by default; when set, the app appends "(YYYYMMDD)" to Voucher Remarks
        # automatically at publish time - no need to type the code by hand.
        editable_field("Prices confirmed valid until (optional - the app adds the "
                       "\"(YYYYMMDD)\" marker to Voucher Remarks automatically)", data,
                       "price_valid_until_date", key_suffix=key_suffix)

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
        # Same widget-type fix as the three fields above - matches Transport's own
        # widget="text_area" for the identical field key.
        editable_field("Cancellation policy text (customer-facing summary)", data, "cancellation_policy_text",
                       widget="text_area", height=80, key_suffix=key_suffix)

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

            _warn_stale_images(data.get("image_urls"))
            # CONFIRMED ABSOLUTE HOUSE RULE (product owner, 2026-09-18): "a supplement can never
            # be 0 Euro" - a non-blocking note (not a publish_disabled gate), see
            # render_supplement_zero_price_notes' own docstring.
            render_supplement_zero_price_notes(build_result)

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
            # CONFIRMED PRODUCT-OWNER REPORT (2026-09-22): the "found unfinished work"
            # draft-restore banner kept coming back even after a successful publish - the whole
            # batch succeeding means there's nothing left worth protecting. Safe to call on
            # every render of this success screen.
            draft_autosave.clear_on_publish_success()
            st.success(f"🎉 All {len(queue)} transfer(s) in this batch published.")
            st.write("")
            st.divider()
            if st.button("🆕 Start a new batch", key="xtf_new_batch"):
                for key in ["xtf_phase", "xtf_raw_text", "xtf_candidates", "xtf_queue", "xtf_queue_index"]:
                    st.session_state.pop(key, None)
                _clear_batch_widget_state(["xtf_"] + SHARED_WIDGET_STATE_PREFIXES)
                st.rerun()
        return
