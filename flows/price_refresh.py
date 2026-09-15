"""
Price-refresh flows, split out of app.py (Phase 1 restructure, zero behaviour change).

render_price_refresh_flow (Transfer/Transport rate-sheet refresh) and
render_ticket_price_refresh_flow (Ticket occupancy-price refresh) moved here verbatim, together,
matching the plan's module grouping - they sat contiguously in app.py's own source, no helpers
interleaved between them. Everything they reference that is defined at app.py's own top level is
imported back from app via the same late-binding pattern used by the earlier flows modules:
app.py imports this module only after all of those names are already defined in its own
namespace, so `from app import ...` resolves correctly despite the circular import shape. Every
needed internal name here is defined earlier in app.py's file order than these functions' own
original position, so the import-back line stays at that original position.
"""
import os
import tempfile
import streamlit as st

from fts_transfer_matrix import classify_fts_matrix_file
from document_reader import extract_raw_text
from ai_extractor import friendly_error_message
import price_refresh
from ui_components import is_active_supplier

from app import (
    _fetch_url_text_safe, _stamp_proposal_widget_tokens,
    render_transport_manual_adjustment_flow, render_transport_price_consistency_flow,
)


def render_price_refresh_flow(client, preselected_kind=None):
    """Update the prices of transports that already exist, from a new rate sheet.

    CONFIRMED REAL DESIGN (product owner): "would the app be better if TRANSPORTS are only
    being updated instead of created... the AI can suggest which price for which Transport,
    the only interaction is that the human could click Yes if the new updated price is correct
    or NO if it is incorrect." He was right, and the reason is worth recording: the list of
    products here comes from Travel Compositor, which is a fact, instead of from an AI reading
    a document, which is a judgement - and it was that judgement that kept coming back empty.
    Nothing here resolves a location, estimates a duration, names anything, or creates
    anything. Only numbers move.

    preselected_kind: when the caller (the Update/Refresh dispatcher) already knows which
    product type this is - the human picked "Transfer" or "Transport" one screen up - pass it
    here to skip asking "Which product type?" again. CONFIRMED REAL BUG (reported by product
    owner): setting st.session_state.pr_kind before calling this used to only change the
    radio's default selection, not skip rendering the radio itself - the same question appeared
    twice in a row. Only asked fresh when this is None (no caller currently does that, but kept
    as the honest fallback rather than assuming there's always a preselection)."""
    st.header("💶 Refresh prices from a rate sheet")
    st.caption("For a rate sheet covering products that already exist. The list of routes comes "
              "from Travel Compositor, not from the document — the document is only asked what "
              "each one now costs. Nothing is created, and **nothing but prices changes** — "
              "validity dates, times, names and modality structure are left exactly as they are.")
    if preselected_kind:
        kind = preselected_kind
        st.caption(f"Product type: **{kind}** (already chosen above).")
    else:
        kind = st.radio("Which product type?", [price_refresh.KIND_TRANSPORT, price_refresh.KIND_TRANSFER],
                        horizontal=True, key="pr_kind")

    if st.session_state.suppliers_cache is None:
        with st.spinner("Loading supplier list…"):
            try:
                st.session_state.suppliers_cache = client.get_all_suppliers()
            except Exception as e:
                st.error(f"Couldn't load the supplier list: {friendly_error_message(e)}")
                st.session_state.suppliers_cache = []
    momira = [x for x in (st.session_state.suppliers_cache or [])
              if (x.get("commercialName") or x.get("legalName") or "").strip().lower().startswith("momira_") and is_active_supplier(x)]
    supplier_id = None
    if momira:
        options = {f"{x.get('commercialName') or x.get('legalName')} — ID {x.get('id')}": str(x.get("id"))
                   for x in momira}
        supplier_id = options[st.selectbox("Supplier", list(options.keys()), key="pr_supplier")]
    else:
        st.error("Could not load the supplier list from Travel Compositor.")
        with st.expander("⚠️ Emergency manual entry"):
            st.caption("Only use this if the supplier list above failed to load - type the numeric Travel Compositor supplier ID directly.")
            supplier_id = st.text_input("Supplier ID (numeric)", key="pr_supplier_manual").strip()

    # CONFIRMED REAL PRODUCT DECISION (product owner, 2026-09-11, right after confirming the
    # earlier bulk write into TRANSPORT-423137/423138/423142/423134 had actually persisted
    # correctly): "we must then update in the future in bulk only the pricevehicle or the
    # priceperpax... ignore the modalities... add just for transport an update field so the human
    # can manually add a absolute or percentage number." A second, simpler mode alongside the
    # existing document-reading flow (kept for when there IS a rate sheet to read) - added only
    # for Transport, which has one shared vehicle/base price field; Transfer has no such single
    # field (pricesByOccupancy is per-bracket with no shared base), so this mode isn't offered
    # for it at all. Chosen BEFORE the rate-sheet uploader so picking it skips that section
    # entirely rather than leaving unused document-upload widgets on screen.
    if kind == price_refresh.KIND_TRANSPORT and supplier_id:
        pr_mode = st.radio(
            "How do you want to set the new price?",
            ["📄 From a rate sheet document", "🔢 Manual %/absolute adjustment",
             "🔎 Yearly price consistency check (read-only)"],
            horizontal=True, key="pr_source_mode")
        if pr_mode == "🔢 Manual %/absolute adjustment":
            render_transport_manual_adjustment_flow(client, supplier_id)
            return
        if pr_mode == "🔎 Yearly price consistency check (read-only)":
            render_transport_price_consistency_flow(client, supplier_id)
            return

    st.subheader("1 — The new rate sheet")
    url = st.text_input("Rate sheet URL (optional)", key="pr_url")
    files = st.file_uploader("Upload the rate sheet", type=["pdf", "docx", "xlsx", "pptx", "csv"],
                             accept_multiple_files=True, key="pr_files")
    hint = st.text_input("Instruction (optional)", key="pr_hint",
                         placeholder="e.g. only the Hurghada section, private transfers only")

    def _pr_read_and_build(routes, raw_text, fts_csv_tmp_paths, hint, scope, base_bracket=None):
        """Read the document for these already-loaded routes and put the proposals on screen.

        Factored out 2026-09-11 so the modality confirmation step below can run BETWEEN loading
        the supplier's products and reading the document, without loading anything twice - see
        that step's own comment for the product owner's request it implements. `scope` is the
        human's answer as a list of (min_pax, max_pax) brackets, or None for "every modality",
        which is exactly the behaviour this flow had before the step existed.

        base_bracket is the human's own answer to a second, separate question (product owner,
        2026-09-11, after the scope step above already shipped: "if the selected modality we
        want to update in this exact update process, if this will be the base price or if it has
        to be calculated to the base price on top? Sedan Modality would be base price and if I
        would update the Hiace the price difference must be calculated and the price must then
        be added to the price supplement") - a (min_pax, max_pax) pair naming which modality IS
        the base (vehiclePrice/baseAdultPrice), or None for "auto-detect from the live read"
        (price_refresh._current_base_option's original behaviour, unchanged when this is None).
        Stashed onto every route dict as base_bracket_override so rebuild_prices - called later,
        well after this function returns, from apply_proposals and the review screen's own
        preview - picks it up without any further threading; see rebuild_prices' own comment."""
        for route in routes:
            if base_bracket is not None:
                route["base_bracket_override"] = base_bracket
            else:
                route.pop("base_bracket_override", None)
        findings = None
        if fts_csv_tmp_paths:
            # Either one file (a one-vehicle round - "One round for Sedan and one round for
            # Hiace", product owner, 2026-09-11) or both together works the same way here - see
            # lookup_prices_from_fts_matrix's own docstring for what a one-vehicle round does to
            # the proposals it builds.
            vehicles = " + ".join(sorted(fts_csv_tmp_paths))
            with st.spinner(f"Reading {len(routes)} route(s) directly from the FTS rate "
                            f"matrix ({vehicles}, no AI needed, so it can't be cut off)…"):
                findings, fts_err = price_refresh.lookup_prices_from_fts_matrix(
                    routes, sedan_csv_path=fts_csv_tmp_paths.get("sedan"),
                    hiace_csv_path=fts_csv_tmp_paths.get("hiace"))
            if fts_err:
                st.warning(f"⚠️ This looked like an FTS rate-matrix CSV but couldn't be "
                           f"read ({fts_err}) — falling back to the normal reading.")
                findings = None
        if findings is None and (raw_text or "").strip():
            with st.spinner(f"Looking up prices for {len(routes)} route(s) in the document…"):
                try:
                    findings = price_refresh.lookup_prices(routes, raw_text, human_hint=hint)
                except Exception as e:
                    st.error(f"Couldn't read the document: {friendly_error_message(e)}")
                    findings = None
        elif findings is None:
            st.error("Couldn't read prices from the FTS matrix, and there's no other "
                     "document to fall back to.")
        for _p in (fts_csv_tmp_paths or {}).values():
            try:
                os.remove(_p)
            except OSError:
                pass
        if findings is not None:
            st.session_state.pr_routes = routes
            st.session_state.pr_proposals = _stamp_proposal_widget_tokens(
                price_refresh.build_proposals(routes, findings, scoped_brackets=scope))
            st.session_state.pr_raw_text = raw_text
            st.session_state.pr_scope = scope
            st.session_state.pr_base_override = base_bracket
            st.session_state.pop("pr_result", None)
            return True
        return False

    if st.button(f"🔍 Read prices for this supplier's {kind.lower()}s", type="primary",
                 disabled=not supplier_id, key="pr_read"):
        st.session_state.pop("pr_scope_answered", None)
        raw_parts = []
        # A file that turns out to be one of FTS's own two-file rate-matrix CSVs (see
        # fts_transfer_matrix.py's module docstring) is set aside here rather than text-extracted
        # - CONFIRMED REAL BUG (product owner, 2026-09-11): "again error for bulk price update...
        # Couldn't read the document: The AI's answer was too long and got cut off... This is
        # crucial and we must make it possible, that the document is fully read." This is the same
        # 271-route FTS document that already overflowed the bulk-import flow; below, when both a
        # "sedan" and a "hiace" file are recognized, price_refresh.lookup_prices_from_fts_matrix
        # reads them directly (no AI, so no token limit) instead of going through lookup_prices().
        fts_csv_tmp_paths = {}
        if url:
            page_text, page_err = _fetch_url_text_safe(url)
            if page_text is not None:
                raw_parts.append(page_text)
            else:
                st.warning(f"⚠️ Couldn't fetch that URL: {page_err}.")
        for uploaded in (files or []):
            suffix = os.path.splitext(uploaded.name)[1]
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(uploaded.getbuffer())
                tmp_path = tmp.name
            vehicle = (classify_fts_matrix_file(tmp_path)
                      if kind == price_refresh.KIND_TRANSPORT and suffix.lower() == ".csv" else None)
            if vehicle and vehicle not in fts_csv_tmp_paths:
                fts_csv_tmp_paths[vehicle] = tmp_path
            else:
                raw_parts.append(extract_raw_text(tmp_path))
                os.remove(tmp_path)
        if not raw_parts and not fts_csv_tmp_paths:
            st.error("No document to read — upload a rate sheet or give a URL.")
        else:
            raw_text = "\n\n".join(raw_parts)
            bar = st.progress(0.0, text=f"Reading this supplier's {kind.lower()}s from Travel Compositor…")

            def _tick(done, total, name):
                bar.progress(min(done / max(total, 1), 1.0), text=f"Reading {name} ({done}/{total})")

            routes, err = price_refresh.load_supplier_products(client, supplier_id, kind,
                                                               progress=_tick)
            bar.empty()
            if err:
                st.error(f"Couldn't read this supplier's {kind.lower()}s: {err}")
            elif not routes:
                st.warning(f"This supplier has no {kind.lower()}s yet. Create them with "
                           f"**Create & Update Products → {kind}** first; this flow only updates "
                           f"what already exists.")
            else:
                # CONFIRMED REAL REQUEST (product owner, 2026-09-11): "should we build a separate
                # human confirmation for the bulk transport price update. So the app detects all
                # modalities first, and before the AI reads the document, the app asks the human
                # which modality it is and which price it shall touch." Asked BEFORE the read (not
                # after) on purpose: the chosen modality is folded into the AI's own instruction,
                # so it steers the extraction itself rather than only filtering the result.
                # Confirmed shape: optional, everything preselected - a supplier whose transports
                # all have one modality never sees this step at all.
                _groups = price_refresh.modality_groups(routes) if kind == price_refresh.KIND_TRANSPORT else []
                if len(_groups) > 1:
                    st.session_state.pr_pending = {
                        "routes": routes, "raw_text": raw_text, "hint": hint,
                        "fts_csv_tmp_paths": fts_csv_tmp_paths, "groups": _groups,
                    }
                    st.rerun()
                # Single-modality supplier (or a Transfer): nothing to confirm, read straight
                # through exactly as before. The rerun stays conditional on a real result - see
                # the 2026-09-01 audit fix it preserves: an unconditional rerun here used to wipe
                # the error message a failed read had just put on screen.
                elif _pr_read_and_build(routes, raw_text, fts_csv_tmp_paths, hint, None):
                    st.rerun()

    # The modality confirmation step itself. Rendered only while a read is paused waiting for it.
    _pending = st.session_state.get("pr_pending")
    if _pending:
        st.subheader("2 — Which modality does this rate sheet price?")
        st.caption("Every modality is selected, which reprices exactly as before. Untick the ones "
                   "this document says nothing about — a rate sheet that only prices the Sedan "
                   "line must not be allowed to move the Hiace price with it.")
        _labels = [g["label"] for g in _pending["groups"]]
        _chosen = st.multiselect(
            "Modalities this document prices", _labels, default=_labels, key="pr_scope_pick",
            help="Grouped by passenger range, because that is the one thing that means the same "
                 "on every transport — modality codes differ from supplier to supplier.")

        # CONFIRMED FINAL TRANSPORT PRICE-STRUCTURE MODEL (product owner, 2026-09-11, full
        # 8-question Q&A): "if the selected modality we want to update in this exact update
        # process, if this will be the base price or if it has to be calculated to the base
        # price on top? Sedan Modality would be base price and if I would update the Hiace the
        # price difference must be calculated and the price must then be added to the price
        # supplement." An auto-detect fallback used to exist here and picked whichever option
        # currently carries no live supplement — REMOVED entirely, because that is exactly the
        # read that can be wrong: Travel Compositor's own API is confirmed to under-report a real
        # supplement as 0.0 (see claude/transport-supplement-admin-ui-vs-api-mismatch-2026-09-10.
        # md), and this app's own prior update run is confirmed to have zeroed out real
        # supplements by trusting that read. Which modality is the vehicle/base price is now a
        # required human decision every round, with no silent fallback if it's left unanswered.
        st.caption("Every modality on a transport shares one base (vehicle) price; every other "
                   "modality is stored as its own price supplement, in its own separate round. "
                   "This choice is required — pick whichever modality this round's document "
                   "prices as the vehicle rate.")
        _base_placeholder = "— choose the vehicle/base-price modality (required) —"
        _base_label_options = [_base_placeholder] + _labels
        _base_pick = st.selectbox("Which modality is the VEHICLE/BASE price this round?",
                                  _base_label_options, index=0, key="pr_base_pick")

        # CONFIRMED REAL REQUEST (product owner, 2026-09-11, same message as the worked-example
        # request above): "there should also be an AI text field, in case the human shall
        # clarify what the task is, in case the app does not work properly." Distinct from the
        # existing per-route "Not right? Tell the AI more about this route" expander further
        # down (which only fires AFTER a read, one route at a time, once a wrong result is
        # already on screen) - this one runs BEFORE the read, for the whole batch, specifically
        # for the modality/base-price detection this step is about: if the auto-detected
        # modality groups or the base-price choice above don't fit what's actually in the
        # document, the human can say so here and have it steer the very first read rather than
        # only correct individual rows afterwards.
        _clarify = st.text_area(
            "Anything the AI should know before reading this document? (optional)",
            key="pr_scope_clarify", placeholder=(
                "e.g. the Sedan and Hiace columns in this sheet are swapped from usual; or "
                "ignore the promo surcharge column; or ask any other clarifying question about "
                "how to read the modalities/base price"),
            help="Use this if the modality groups above look wrong, or if the base-price choice "
                 "doesn't match how this specific document is laid out.")
        _c1, _c2 = st.columns([1, 4])
        with _c1:
            if st.button("Read the document", type="primary", key="pr_scope_go",
                         disabled=not _chosen or _base_pick == _base_placeholder):
                _picked = [g for g in _pending["groups"] if g["label"] in _chosen]
                _scope = None if len(_picked) == len(_pending["groups"]) else \
                    [(g["min_pax"], g["max_pax"]) for g in _picked]
                _hint = _pending["hint"]
                if _scope:
                    # Folded into the AI's own instruction, so a scoped round also tells the
                    # reader WHICH rows to look for rather than only discarding the rest.
                    _hint = "\n".join(x for x in [
                        _hint,
                        "This rate sheet prices only these passenger brackets: "
                        + "; ".join(g["label"] for g in _picked)
                        + ". Ignore any other vehicle class or bracket in the document.",
                    ] if (x or "").strip())
                if (_clarify or "").strip():
                    _hint = "\n".join(x for x in [_hint, _clarify.strip()] if (x or "").strip())
                _base_group = next(g for g in _pending["groups"] if g["label"] == _base_pick)
                _base_bracket = (_base_group["min_pax"], _base_group["max_pax"])
                st.session_state.pop("pr_pending", None)
                if _pr_read_and_build(_pending["routes"], _pending["raw_text"],
                                      _pending["fts_csv_tmp_paths"], _hint, _scope, _base_bracket):
                    st.rerun()
        with _c2:
            if st.button("Cancel", key="pr_scope_cancel"):
                for _p in (_pending["fts_csv_tmp_paths"] or {}).values():
                    try:
                        os.remove(_p)
                    except OSError:
                        pass
                st.session_state.pop("pr_pending", None)
                st.rerun()
        return

    proposals = st.session_state.get("pr_proposals")
    if not proposals:
        return

    changed = [p for p in proposals if p["status"] == "changed"]
    unchanged = [p for p in proposals if p["status"] == "unchanged"]
    absent = [p for p in proposals if p["status"] == "not_in_document"]
    blocked = [p for p in proposals if p["status"] == "blocked_unreadable"]
    # CONFIRMED ABSOLUTE RULES (product owner, 2026-09-11): no auto-detect fallback when a route
    # has two or more live modalities, and a computed supplement that rounds to 0 is always an
    # error, never a legitimate change. Both are HARD blocks - a route in either list can never
    # be accepted until it's resolved (re-run with the base modality designated, or check the
    # document/live data for what's actually wrong), unlike blocked_unreadable which just needs a
    # re-run once the API hiccup clears.
    needs_base = [p for p in proposals if p["status"] == "blocked_needs_base_designation"]
    zero_supplement = [p for p in proposals if p["status"] == "blocked_zero_supplement"]

    st.subheader("2 — Check the new prices")
    st.caption(f"{len(changed)} route(s) would change · {len(unchanged)} already match the document · "
              f"{len(absent)} not found in it."
              + (f" · {len(blocked)} could not be read" if blocked else "")
              + (f" · {len(needs_base)} need a base modality designated" if needs_base else "")
              + (f" · {len(zero_supplement)} blocked on a zero supplement" if zero_supplement else ""))
    _scope = st.session_state.get("pr_scope")
    if _scope:
        st.info("🎯 This round is scoped to the "
                + " and ".join(f"**{lo}-{hi} pax**" for lo, hi in _scope)
                + " modality only. Every other modality is left exactly as it is — its price is "
                  "not read, not compared, and not written.")
    _base_override = st.session_state.get("pr_base_override")
    if _base_override:
        lo, hi = _base_override
        st.info(f"🧮 **{lo}-{hi} pax** is set as the base price modality for this round. Every "
                f"other modality's price is being stored as base + a price difference (a price "
                f"supplement), rather than auto-detected from which one currently reads with no "
                f"supplement.")

    # CONFIRMED REAL BUG (audit, 2026-08-24): routes with an unreadable option used to be filtered
    # out of this screen silently, while their remaining options were repriced around a base
    # computed from whatever happened to load - see build_proposals. They are now named here and
    # can never be accepted, so the operator knows to re-run rather than trusting a partial result.
    def _id_suffix(route):
        # CONFIRMED REAL REQUEST (product owner): show the TRANSFER-xxxxx / TRANSPORT-xxxxx id
        # next to the route so a human can find the exact record in Travel Compositor without
        # having to search by name.
        rid = route.get("id")
        return f"  ·  `{rid}`" if rid else ""

    if blocked:
        st.error(
            f"🚫 **{len(blocked)} route(s) could not be fully read from Travel Compositor** and have "
            f"been left untouched. This is usually a temporary API hiccup - re-run the price refresh "
            f"for this supplier and they should load. They are not repriced, because the base price "
            f"is shared across a transport's modalities: repricing the ones that did load would "
            f"silently move the price of the one that didn't."
        )
        for p in blocked:
            names = ", ".join(str(c) for c in (p.get("unreadable_options") or []) if c) or "unknown option(s)"
            st.markdown(f"- **{p['route'].get('name') or '(unnamed route)'}** — couldn't read: `{names}`")

    # CONFIRMED FINAL TRANSPORT PRICE-STRUCTURE MODEL (product owner, 2026-09-11): a route with
    # two or more live modalities has no auto-detect fallback any more - the human must have
    # designated one as the vehicle/base price in Step 2 above. This should be rare in practice
    # (Step 2 requires the choice before the document is even read), but a route can still land
    # here if its live modality structure changed between the choice and this screen.
    if needs_base:
        st.error(
            f"🚫 **{len(needs_base)} route(s) have two or more modalities and no vehicle/base "
            f"price was designated for them** - re-run this refresh and pick the vehicle/base "
            f"modality in Step 2. Nothing here is proposed or writable until that's answered; "
            f"there is no fallback guess."
        )
        for p in needs_base:
            st.markdown(f"- **{p['route'].get('name') or '(unnamed route)'}**{_id_suffix(p['route'])}")

    # CONFIRMED ABSOLUTE RULE (product owner, 2026-09-11): "Supplement cannot be 0, if it is 0
    # there is an error." A hard block, not a warning - two different vehicle classes can never
    # legitimately cost the same, so a computed 0 means the read (or the base designation) is
    # wrong, not that nothing needs to change here.
    if zero_supplement:
        st.error(
            f"🚫 **{len(zero_supplement)} route(s) would compute a price supplement of exactly 0** "
            f"for a modality that isn't the vehicle/base price - that can never be right, so "
            f"nothing on these routes is writable until it's resolved. Check whether the vehicle/"
            f"base modality was designated correctly, or use the AI text field in Step 2 to "
            f"clarify how this document should be read."
        )
        for p in zero_supplement:
            route = p["route"]
            for err in p.get("zero_supplement_errors") or []:
                period = (f" ({err['start_date']} → {err['end_date']})"
                         if err.get("start_date") or err.get("end_date") else "")
                st.markdown(f"- **{route.get('name') or route.get('id')}**{_id_suffix(route)} · "
                           f"**{err.get('name') or err.get('code')}**{period}: would be "
                           f"{err.get('would_be_price')} {route.get('currency') or ''} — exactly "
                           f"the vehicle/base price")

    # CONFIRMED REAL RULE (product owner, 2026-09-11): "if a transport has 2 or more modalities,
    # there will be always base price and multiple price supplements. It does not make sense to
    # have two modalities with the same price." Shown across EVERY status (changed, unchanged,
    # not found) because it is a statement about this app's CURRENT READ, not about this
    # document - a route reading flat is suspect whether or not this round is touching it. See
    # price_refresh.flat_price_modalities' own docstring for the confirmed mechanism (Travel
    # Compositor's API can return 0.0 for a real, nonzero supplement its own admin panel shows -
    # already caught once on real TRANSPORT-423015).
    _flat = [p for p in proposals if p.get("flat_price_codes")]
    if _flat:
        with st.expander(f"⚠️ {len(_flat)} route(s) show identical prices across modalities — "
                         f"likely a live-data read issue, not a document mismatch", expanded=True):
            st.caption("Two different vehicle classes should never legitimately cost the same. "
                      "When this app sees that, the more likely explanation is that Travel "
                      "Compositor's API returned 0.0 for a real supplement its own admin Prices "
                      "tab shows as nonzero — this app has no other field to read instead, so it "
                      "cannot tell the difference between 'genuinely no supplement' and 'the API "
                      "isn't disclosing one'. Check the Prices tab for these directly before "
                      "trusting this round's numbers for them.")
            for p in _flat:
                route = p["route"]
                price_bits = ", ".join(f"{o['min_pax']}-{o['max_pax']} pax ({o['code']}): {o['unit_price']}"
                                       for o in route.get("options") or []
                                       if o["code"] in p["flat_price_codes"])
                st.markdown(f"- **{route.get('name') or route.get('id')}**{_id_suffix(route)} · "
                           f"{price_bits} {route.get('currency') or ''} · status: *{p['status']}*")

    # Accept-all with exceptions: the product owner's own choice. Only rows that genuinely
    # CHANGED are ever ticked - a route the document never mentioned must not be swept up by a
    # single click, which is the one way "accept all" could do damage.
    acol1, acol2 = st.columns([1, 4])
    with acol1:
        if st.button("✅ Accept all", key="pr_accept_all", use_container_width=True):
            for p in proposals:
                p["accepted"] = p["status"] == "changed"
            st.rerun()
    with acol2:
        if st.button("Clear all", key="pr_clear_all"):
            for p in proposals:
                p["accepted"] = False
            st.rerun()

    for p in changed:
        route = p["route"]
        finding = p["finding"]
        head = f"**{route.get('name') or route.get('id')}**{_id_suffix(route)}"
        cols = st.columns([1, 6])
        with cols[0]:
            # Token, not just the index - see _stamp_proposal_widget_tokens for the confirmed bug
            # (a second run's route #0 arriving already ticked from the previous run's route #0).
            p["accepted"] = st.checkbox("Yes", value=p["accepted"],
                                        key=f"pr_ok_{p['index']}_{p.get('widget_token', 'g0')}")
        with cols[1]:
            st.markdown(head)
            # CONFIRMED REAL GAP (product owner): the AI's read price used to be take-it-or-
            # leave-it - a low-confidence or wrong read (like a bundled route with two
            # different underlying prices) had no way to be corrected other than rejecting the
            # whole row and fixing it some other way. Each bracket's "new" price is now directly
            # editable, pre-filled with what the AI read - overwrite it by hand and that's what
            # gets applied on Publish.
            for c in p["changes"]:
                pcol1, pcol2 = st.columns([3, 2])
                # CONFIRMED REAL REQUEST (product owner, 2026-09-11): "Hiace had three
                # supplements because of a high season and peak season time, but the app never
                # filled out the actual prices" - a single modality can carry several changes in
                # one round now, one per season the document states, each labelled with its own
                # date range so a human can tell them apart.
                _period_label = (f" ({c['start_date']} → {c['end_date']})"
                                 if c.get("start_date") or c.get("end_date") else "")
                with pcol2:
                    c["new"] = st.number_input(
                        f"New price ({c['min_pax']}-{c['max_pax']} pax){_period_label}",
                        min_value=0.0, step=1.0, value=float(c["new"]),
                        # Token, not just index+code - see _stamp_proposal_widget_tokens: without
                        # it, a re-read's corrected price was displayed as (and published as) the
                        # old one. Periods on the same option/code need their own key too, so the
                        # date range is folded in.
                        key=f"pr_price_{p['index']}_{c['code']}_{c.get('start_date') or ''}"
                            f"_{c.get('end_date') or ''}_{p.get('widget_token', 'g0')}",
                        label_visibility="collapsed")
                with pcol1:
                    # CONFIRMED REAL REQUEST (product owner): red when the price to apply
                    # differs from what's already live, green when (after any hand-edit above)
                    # it now matches - a quick visual scan instead of reading every number.
                    _ccy = route.get('currency') or ''
                    # CONFIRMED REAL REQUEST (product owner, 2026-09-11): "the App must
                    # understand the difference between BasePrice (per vehicle or per Pax)". The
                    # same number means completely different money depending on this flag - a
                    # per-vehicle 95 is what the whole car costs, a per-person 95 is 95 x the
                    # party - and this screen never said which, so an operator could not tell
                    # whether a rate sheet's number had been applied on the right basis. Stated
                    # on every row now, read from the live record's own pricePerPax.
                    _basis = "per person" if route.get("price_per_pax", True) else "per vehicle"
                    _kind_label = " *(vehicle price)*" if c.get("write_kind") == "vehicle" \
                        else " *(price supplement)*" if c.get("write_kind") == "supplement" else ""
                    _new_period_label = "  ·  *new period*" if c.get("is_new_period") else ""
                    if abs(c["new"] - c["old"]) < 0.005:
                        st.markdown(f"{c['min_pax']}–{c['max_pax']} pax{_period_label}: {c['old']} → "
                                   f":green[**{c['new']}**] {_ccy} *{_basis}*{_kind_label}  ·  "
                                   f"*matches the live price*")
                    else:
                        st.markdown(f"{c['min_pax']}–{c['max_pax']} pax{_period_label}: {c['old']} → "
                                   f":red[**{c['new']}**] {_ccy} *{_basis}*{_kind_label}{_new_period_label}")
            bits = []
            if finding.get("matched_row"):
                bits.append(f"from the row *“{finding['matched_row']}”*")
            if finding.get("confidence") and finding["confidence"] != "high":
                bits.append(f"**{finding['confidence']} confidence**")
            if finding.get("note"):
                bits.append(finding["note"])
            if p.get("currency_changed"):
                bits.append(f"⚠️ the document says **{finding['currency']}** but this transport is "
                            f"**{route.get('currency')}** — the price is applied as-is, not converted")
            if bits:
                st.caption("  ·  ".join(bits))
            # CONFIRMED FINAL TRANSPORT PRICE-STRUCTURE MODEL (product owner, 2026-09-11): "but
            # if we only update the Sedan price whey should the app touches even the Hiace price
            # supplement?" - the untouched-modality preview that used to run here was DELETED
            # entirely, not fixed: a round now reads, shows, computes, and writes exactly one
            # thing (the designated vehicle price, or the touched modality's own supplement) and
            # nothing else on the transport is mentioned, because nothing else is touched.
            #
            # CONFIRMED REAL REQUEST (product owner, 2026-09-11): "if a price is matching to a
            # modality which will be added to the price supplement, the app shall give one
            # example and show the human what the app would calculate and add to the product."
            # Called live (not read from p["supplement_examples"]) so a hand-edit to the "new
            # price" number above is reflected immediately. One line per changed PERIOD, not per
            # modality - see supplement_calculation_examples' own docstring.
            for _ex in price_refresh.supplement_calculation_examples(route, p["changes"]):
                _sign = "+" if _ex["supplement"] >= 0 else "−"
                _ex_period = (f" ({_ex['start_date']} → {_ex['end_date']})"
                             if _ex.get("start_date") or _ex.get("end_date") else "")
                st.caption(
                    f"🧮 **{_ex['name']}**'s new price{_ex_period} will be stored as base "
                    f"({_ex['base_name']}: {_ex['base_price']}) {_sign} a price supplement of "
                    f"**{abs(_ex['supplement'])} {_ccy}** = {_ex['new_price']} {_ccy} — this is "
                    f"exactly what gets written to the price supplement field on Publish.")
            # CONFIRMED REAL GAP (product owner): no way to redirect the AI when it read the
            # wrong row (e.g. picked Marsa Allam's price for a bundled Port Ghalib/Marsa Allam
            # route) short of fixing the number by hand above. This re-reads ONLY this one
            # route, with the extra instruction folded in, and replaces its proposal in place -
            # every other route in the batch is untouched.
            with st.expander("🤖 Not right? Tell the AI more about this route", expanded=False):
                route_hint = st.text_input(
                    "Extra instruction for this route only",
                    key=f"pr_hint_{p['index']}_{p.get('widget_token', 'g0')}",
                    placeholder="e.g. use the Port Ghalib price, not Marsa Allam")
                if st.button("🔁 Re-read this route", key=f"pr_reread_{p['index']}",
                             disabled=not route_hint.strip()):
                    with st.spinner("Re-reading this route..."):
                        combined_hint = "\n".join(
                            x for x in [(hint or "").strip(), route_hint.strip()] if x)
                        try:
                            single_finding = price_refresh.lookup_prices(
                                [route], st.session_state.pr_raw_text, human_hint=combined_hint
                            ).get(0)
                        except Exception as e:
                            st.error(f"Couldn't re-read this route: {friendly_error_message(e)}")
                            single_finding = None
                    if single_finding is not None:
                        rebuilt = _stamp_proposal_widget_tokens(price_refresh.build_proposals(
                            st.session_state.pr_routes, {p["index"]: single_finding}))
                        st.session_state.pr_proposals[p["index"]] = rebuilt[p["index"]]
                        st.rerun()
                    elif single_finding is None and route_hint.strip():
                        st.warning("The AI didn't find this route in the document even with the extra "
                                  "instruction - the current price is left as it was.")

    if unchanged:
        with st.expander(f"➖ {len(unchanged)} already at the document's price"):
            for p in unchanged:
                route = p["route"]
                price_bits = ", ".join(
                    f"{o['min_pax']}-{o['max_pax']} pax: {o['unit_price']}"
                    for o in (route.get("options") or []) if not o.get("fetch_failed"))
                st.markdown(f"- **{route.get('name')}**{_id_suffix(route)}  ·  "
                           f":green[{price_bits}] {route.get('currency') or ''}")
    if absent:
        with st.expander(f"❓ {len(absent)} not found in the document — match by hand if you want"):
            st.caption("The document may price these under a wording nobody matched, or the supplier "
                      "may have dropped them. Pick the row's price yourself to update one anyway.")
            for p in absent:
                route = p["route"]
                mcol1, mcol2, mcol3 = st.columns([3, 2, 1])
                with mcol1:
                    st.write(f"**{route.get('name')}**{_id_suffix(route)}")
                    # CONFIRMED REAL BUG (product owner, 2026-09-11): "the App... misses out on
                    # the price errors" - an FTS single-vehicle-round route that WAS matched to a
                    # rate-matrix row and DID have a price there, but couldn't be safely applied
                    # because this route's live option brackets don't exactly match FTS's 1-3/1-8
                    # convention (see lookup_prices_from_fts_matrix's own comment), used to land
                    # in this same "not found" bucket with no trace of why - indistinguishable
                    # from a route the document genuinely never priced. finding["matched_row"] is
                    # only ever set here when that happened (a plain not-found finding's own
                    # matched_row is ""), so its presence is what makes this a visible diagnostic
                    # instead of a silent drop.
                    if p["finding"].get("matched_row"):
                        st.warning(f"⚠️ {p['finding'].get('note') or 'Matched the document but could not be applied.'}")
                    st.caption(", ".join(f"{o['min_pax']}–{o['max_pax']} pax now {o['unit_price']}"
                                         for o in route["options"] if not o.get("fetch_failed")))
                with mcol2:
                    typed = st.number_input(
                        "New price per person/vehicle", min_value=0.0, step=1.0, value=0.0,
                        # Token, same reason as the other pr_ widgets - a hand-typed price from a
                        # previous run must not reappear under a new run's route #0.
                        key=f"pr_manual_{p['index']}_{p.get('widget_token', 'g0')}",
                        help="The base bracket's new price. The solo bracket is recalculated "
                             "from it using the same minimum-party rule.")
                with mcol3:
                    st.write("")
                    if st.button("Use", key=f"pr_use_{p['index']}", disabled=typed <= 0):
                        widest = max((o for o in route["options"] if not o.get("fetch_failed")),
                                     key=lambda o: (o["max_pax"] - o["min_pax"], -o["min_pax"]),
                                     default=None)
                        if widest:
                            manual = {"found": True, "minimum_pax": widest["min_pax"],
                                      "confidence": "high", "note": "price entered by hand",
                                      "matched_row": "entered by hand", "currency": "",
                                      "brackets": [{"min_pax": widest["min_pax"],
                                                    "max_pax": widest["max_pax"],
                                                    "price": float(typed),
                                                    "child_price": None, "infant_price": None}]}
                            rebuilt = _stamp_proposal_widget_tokens(price_refresh.build_proposals(
                                st.session_state.pr_routes, {p["index"]: manual}))
                            st.session_state.pr_proposals[p["index"]] = rebuilt[p["index"]]
                            st.rerun()
                        else:
                            # CONFIRMED REAL BUG (audit, 2026-08-28): every option on this route
                            # failed to fetch (fetch_failed=True), so `widest` is None and the
                            # click used to do nothing at all - no rerun, no message, the operator
                            # just sees the button appear not to work. Named explicitly instead.
                            st.error("Every bracket on this route failed to load from Travel "
                                    "Compositor, so there's nothing to price by hand yet. "
                                    "Re-run the price refresh and try again.")

    st.subheader("3 — Apply")
    accepted = [p for p in proposals if p.get("accepted") and p.get("changes")]
    st.warning(f"This changes prices on **{len(accepted)} live {kind.lower()}(s)** for supplier "
               f"{supplier_id}. Nothing else is touched — validity dates stay as they are, even "
               f"where they run to 2049 or 2099.")
    if st.button(f"🚀 Update {len(accepted)} {kind.lower()}(s)", type="primary",
                 disabled=not accepted, key="pr_apply"):
        bar = st.progress(0.0, text="Updating…")

        def _tick2(done, total, name):
            bar.progress(min(done / max(total, 1), 1.0), text=f"Updating {name} ({done}/{total})")

        st.session_state.pr_result = price_refresh.apply_proposals(
            client, supplier_id, proposals, progress=_tick2)
        bar.empty()
        st.rerun()

    result = st.session_state.get("pr_result")
    if result:
        if result["updated"]:
            st.success(f"✅ {len(result['updated'])} {kind.lower()}(s) repriced.")
            for u in result["updated"]:
                st.write(f"- {u['name']}: " + ", ".join(
                    f"{c['min_pax']}–{c['max_pax']} pax {c['old']} → {c['new']}" for c in u["changes"]))
                # CONFIRMED REAL DIAGNOSTIC NEED (product owner, 2026-09-11): a "repriced" success
                # here has been reported to NOT show up in Travel Compositor's own admin Prices
                # tab afterwards. Rather than guess again, this shows the EXACT request body we
                # sent and the EXACT body TC handed back for that route, so the next time this
                # happens the raw evidence needed to tell "TC silently dropped our write" apart
                # from "we wrote the wrong option" is right here, no Postman round-trip needed.
                _dbg = u.get("debug")
                if _dbg:
                    with st.expander(f"🔍 Raw request/response for {u['name']} (debug)"):
                        st.write(f"write_kind: `{_dbg.get('write_kind')}`")
                        if _dbg.get("transport_request") is not None:
                            st.caption("Transport (parent) request body:")
                            st.json(_dbg["transport_request"])
                            st.caption("Transport (parent) response:")
                            st.json(_dbg["transport_response"])
                        for req, resp in zip(_dbg.get("option_requests", []),
                                             _dbg.get("option_responses", [])):
                            st.caption(f"Option {req['code']} request body:")
                            st.json(req["payload"])
                            st.caption(f"Option {resp['code']} response:")
                            st.json(resp["response"])
        if result["failed"]:
            st.error(f"❌ {len(result['failed'])} failed:")
            for f in result["failed"]:
                st.write(f"- **{f.get('name')}**: {f.get('detail')}")
                if f.get("debug"):
                    with st.expander(f"🔍 Raw request/response for {f.get('name')} (debug)"):
                        st.json(f["debug"])
        if st.button("🆕 Start again", key="pr_new"):
            for key in ("pr_proposals", "pr_routes", "pr_raw_text", "pr_result"):
                st.session_state.pop(key, None)
            st.rerun()


def render_ticket_price_refresh_flow(client):
    """Update the occupancy prices of Tickets that already exist, from a new rate sheet - Phase
    1 of the product-owner's request (2026-08-25): "the next developement must be done, when we
    are talking about updating Tickets or ClosedTours for the new Seasons with new prices... The
    logic we have build for transfers and transports are great... Could we plan this the same
    for Tickets and closedtours." Explicitly scoped to base/occupancy price only, per "yes,
    please start with phase 1" - a Peak Season supplement is never added here (Phase 2) and an
    existing language-choice supplement's own price is never touched here (Phase 3).

    Same review pattern as render_price_refresh_flow (Transfer/Transport) above: the list of
    Tickets/Modalities comes from Travel Compositor, the document is only asked what each known
    CODE now costs, and nothing but occupancyPrices ever changes - dates, languages, supplements
    and modality structure are left exactly as they are."""
    st.header("🎟️ Refresh Ticket prices from a rate sheet")
    st.caption("For a rate sheet covering Tickets that already exist. The list of Tickets/"
              "Modalities comes from Travel Compositor, not from the document — the document is "
              "only asked what each known CODE now costs. Nothing is created, and **only the "
              "occupancy prices change** — dates, languages, supplements and modality structure "
              "are left exactly as they are. Phase 1 only: Peak Season supplements and "
              "language-choice supplement prices are not touched by this screen yet.")

    if st.session_state.suppliers_cache is None:
        with st.spinner("Loading supplier list…"):
            try:
                st.session_state.suppliers_cache = client.get_all_suppliers()
            except Exception as e:
                st.error(f"Couldn't load the supplier list: {friendly_error_message(e)}")
                st.session_state.suppliers_cache = []
    momira = [x for x in (st.session_state.suppliers_cache or [])
              if (x.get("commercialName") or x.get("legalName") or "").strip().lower().startswith("momira_") and is_active_supplier(x)]
    supplier_id = None
    if momira:
        options = {f"{x.get('commercialName') or x.get('legalName')} — ID {x.get('id')}": str(x.get("id"))
                   for x in momira}
        supplier_id = options[st.selectbox("Supplier", list(options.keys()), key="tpr_supplier")]
    else:
        st.error("Could not load the supplier list from Travel Compositor.")
        with st.expander("⚠️ Emergency manual entry"):
            st.caption("Only use this if the supplier list above failed to load - type the numeric Travel Compositor supplier ID directly.")
            supplier_id = st.text_input("Supplier ID (numeric)", key="tpr_supplier_manual").strip()

    st.subheader("1 — The new rate sheet")
    url = st.text_input("Rate sheet URL (optional)", key="tpr_url")
    files = st.file_uploader("Upload the rate sheet", type=["pdf", "docx", "xlsx", "pptx", "csv"],
                             accept_multiple_files=True, key="tpr_files")
    hint = st.text_input("Instruction (optional)", key="tpr_hint",
                         placeholder="e.g. only the Alexandria tours section")

    if st.button("🔍 Read prices for this supplier's Tickets", type="primary",
                 disabled=not supplier_id, key="tpr_read"):
        raw_parts = []
        if url:
            page_text, page_err = _fetch_url_text_safe(url)
            if page_text is not None:
                raw_parts.append(page_text)
            else:
                st.warning(f"⚠️ Couldn't fetch that URL: {page_err}.")
        for uploaded in (files or []):
            suffix = os.path.splitext(uploaded.name)[1]
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(uploaded.getbuffer())
                tmp_path = tmp.name
            raw_parts.append(extract_raw_text(tmp_path))
            os.remove(tmp_path)
        if not raw_parts:
            st.error("No document to read — upload a rate sheet or give a URL.")
        else:
            raw_text = "\n\n".join(raw_parts)
            bar = st.progress(0.0, text="Reading this supplier's Tickets from Travel Compositor…")

            def _tick(done, total, name):
                bar.progress(min(done / max(total, 1), 1.0), text=f"Reading {name} ({done}/{total})")

            routes, err = price_refresh.load_supplier_tickets(client, supplier_id, progress=_tick)
            bar.empty()
            if err:
                st.error(f"Couldn't read this supplier's Tickets: {err}")
            elif not routes:
                st.warning("This supplier has no Tickets yet. Create them with "
                           "**Create & Update Products → Ticket** first; this flow only updates "
                           "what already exists.")
            else:
                with st.spinner(f"Looking up prices for {len(routes)} Modality(ies) in the document…"):
                    try:
                        findings = price_refresh.lookup_ticket_prices(routes, raw_text, human_hint=hint)
                    except Exception as e:
                        st.error(f"Couldn't read the document: {friendly_error_message(e)}")
                        findings = None
                if findings is not None:
                    st.session_state.tpr_routes = routes
                    st.session_state.tpr_proposals = _stamp_proposal_widget_tokens(
                        price_refresh.build_ticket_proposals(routes, findings))
                    st.session_state.tpr_raw_text = raw_text
                    st.session_state.pop("tpr_result", None)
                    # CONFIRMED BUG FIX (full-app audit HIGH, 2026-09-01): see the matching fix
                    # in render_price_refresh_flow (Transfer/Transport) - this st.rerun() used to
                    # fire unconditionally after every branch, wiping the "no document"/"couldn't
                    # read"/"no Tickets yet" error or warning before the operator could read it
                    # and leaving the previous rate sheet's proposals on screen looking fresh.
                    # Moved inside the one branch that actually produced a new result.
                    st.rerun()

    proposals = st.session_state.get("tpr_proposals")
    if not proposals:
        return

    changed = [p for p in proposals if p["status"] == "changed"]
    unchanged = [p for p in proposals if p["status"] == "unchanged"]
    absent = [p for p in proposals if p["status"] == "not_in_document"]
    blocked = [p for p in proposals if p["status"] == "blocked_unreadable"]
    unsupported = [p for p in proposals if p["status"] == "unsupported_price_type"]

    st.subheader("2 — Check the new prices")
    st.caption(f"{len(changed)} Modality(ies) would change · {len(unchanged)} already match the "
              f"document · {len(absent)} not found in it."
              + (f" · {len(blocked)} could not be read" if blocked else "")
              + (f" · {len(unsupported)} not OCCUPANCY-priced (unsupported)" if unsupported else ""))

    if blocked:
        st.error(f"🚫 **{len(blocked)} Modality(ies) could not be fully read from Travel "
                f"Compositor** and have been left untouched. Re-run the price refresh and they "
                f"should load.")
        for p in blocked:
            st.markdown(f"- **{p['route'].get('name') or '(unnamed)'}**")

    if unsupported:
        with st.expander(f"⚠️ {len(unsupported)} Modality(ies) not supported yet "
                         f"(DISTRIBUTION/SERVICE pricing)"):
            st.caption("This screen only refreshes OCCUPANCY-priced Modalities (a per-headcount "
                      "table) so far. A DISTRIBUTION (flat per-adult/child) or SERVICE (one flat "
                      "total) Modality is listed here rather than guessed at — update it by hand "
                      "for now.")
            for p in unsupported:
                route = p["route"]
                st.markdown(f"- **{route.get('name') or '(unnamed)'}** — priceType "
                           f"`{route.get('price_type') or '?'}`")

    acol1, acol2 = st.columns([1, 4])
    with acol1:
        if st.button("✅ Accept all", key="tpr_accept_all", use_container_width=True):
            for p in proposals:
                p["accepted"] = p["status"] == "changed"
            st.rerun()
    with acol2:
        if st.button("Clear all", key="tpr_clear_all"):
            for p in proposals:
                p["accepted"] = False
            st.rerun()

    for p in changed:
        route = p["route"]
        finding = p["finding"]
        head = f"**{route.get('name')}**  ·  `{route.get('ticket_code')}/{route.get('modality_code')}`"
        cols = st.columns([1, 6])
        with cols[0]:
            p["accepted"] = st.checkbox("Yes", value=p["accepted"],
                                        key=f"tpr_ok_{p['index']}_{p.get('widget_token', 'g0')}")
        with cols[1]:
            st.markdown(head)
            for c in p["changes"]:
                pcol1, pcol2 = st.columns([3, 2])
                with pcol2:
                    c["new"] = st.number_input(
                        f"New adult price ({c['min_pax']} pax)", min_value=0.0, step=1.0,
                        value=float(c["new"]),
                        key=f"tpr_price_{p['index']}_{c['code']}_{p.get('widget_token', 'g0')}",
                        label_visibility="collapsed")
                with pcol1:
                    _ccy = route.get('currency') or ''
                    if abs(c["new"] - c["old"]) < 0.005:
                        st.markdown(f"{c['min_pax']} pax: {c['old']} → :green[**{c['new']}**] {_ccy}  ·  "
                                   f"*matches the live price*")
                    else:
                        st.markdown(f"{c['min_pax']} pax: {c['old']} → :red[**{c['new']}**] {_ccy}")
                    if c.get("child_new") is not None:
                        st.caption(f"child at {c['min_pax']} pax: "
                                  f"{c.get('child_old') if c.get('child_old') is not None else '?'} → "
                                  f"{c['child_new']} {_ccy} (from the document)")
                    elif c.get("child_old") is not None:
                        st.caption(f"child at {c['min_pax']} pax moves with the adult price "
                                  f"(document gave no separate child price)")
            bits = []
            if finding.get("matched_row"):
                bits.append(f"from the row *“{finding['matched_row']}”*")
            if finding.get("confidence") and finding["confidence"] != "high":
                bits.append(f"**{finding['confidence']} confidence**")
            if finding.get("note"):
                bits.append(finding["note"])
            if p.get("currency_changed"):
                bits.append(f"⚠️ the document says **{finding['currency']}** but this Ticket is "
                            f"**{route.get('currency')}** — the price is applied as-is, not converted")
            if bits:
                st.caption("  ·  ".join(bits))
            with st.expander("🤖 Not right? Tell the AI more about this Ticket", expanded=False):
                route_hint = st.text_input(
                    "Extra instruction for this Ticket only",
                    key=f"tpr_hint_{p['index']}_{p.get('widget_token', 'g0')}",
                    placeholder="e.g. use the half-day price, not the full-day one")
                if st.button("🔁 Re-read this Ticket", key=f"tpr_reread_{p['index']}",
                             disabled=not route_hint.strip()):
                    with st.spinner("Re-reading this Ticket..."):
                        combined_hint = "\n".join(
                            x for x in [(hint or "").strip(), route_hint.strip()] if x)
                        try:
                            single_finding = price_refresh.lookup_ticket_prices(
                                [route], st.session_state.tpr_raw_text, human_hint=combined_hint
                            ).get(0)
                        except Exception as e:
                            st.error(f"Couldn't re-read this Ticket: {friendly_error_message(e)}")
                            single_finding = None
                    if single_finding is not None:
                        rebuilt = _stamp_proposal_widget_tokens(price_refresh.build_ticket_proposals(
                            st.session_state.tpr_routes, {p["index"]: single_finding}))
                        st.session_state.tpr_proposals[p["index"]] = rebuilt[p["index"]]
                        st.rerun()
                    elif single_finding is None and route_hint.strip():
                        st.warning("The AI didn't find this code in the document even with the "
                                  "extra instruction - the current price is left as it was.")

    if unchanged:
        with st.expander(f"➖ {len(unchanged)} already at the document's price"):
            for p in unchanged:
                route = p["route"]
                price_bits = ", ".join(f"{o['min_pax']} pax: {o['unit_price']}"
                                       for o in (route.get("options") or []))
                st.markdown(f"- **{route.get('name')}**  ·  :green[{price_bits}] "
                           f"{route.get('currency') or ''}")
    if absent:
        # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-28): "the human shall review if the matched
        # tickets with the new documents are correct. Same as with transfers" - closing the one
        # gap between this screen and render_price_refresh_flow's (Transfer/Transport) own
        # "not found" section: a ticket the AI didn't match still gets a manual price + "Use"
        # button here, exactly like a transfer/transport route does, instead of being a
        # dead-end read-only list.
        with st.expander(f"❓ {len(absent)} not found in the document — match by hand if you want"):
            st.caption("The document may price these under a code nobody matched, or the "
                      "supplier may have dropped them. Pick the Modality's price yourself to "
                      "update one anyway.")
            for p in absent:
                route = p["route"]
                mcol1, mcol2, mcol3 = st.columns([3, 2, 1])
                with mcol1:
                    st.write(f"**{route.get('name')}**  ·  `{route.get('ticket_code')}/"
                            f"{route.get('modality_code')}`")
                    st.caption(", ".join(f"{o['min_pax']} pax now {o['unit_price']}"
                                         for o in (route.get("options") or [])))
                with mcol2:
                    typed = st.number_input(
                        "New adult price per person",
                        min_value=0.0, step=1.0, value=0.0,
                        # Token, same reason as the other tpr_ widgets - a hand-typed price from
                        # a previous run must not reappear under a new run's route #0.
                        key=f"tpr_manual_{p['index']}_{p.get('widget_token', 'g0')}",
                        help="The widest occupancy bracket's new price. The solo bracket, if "
                             "any, is recalculated from it using the same minimum-party rule.")
                with mcol3:
                    st.write("")
                    if st.button("Use", key=f"tpr_use_{p['index']}", disabled=typed <= 0):
                        widest = max(route.get("options") or [],
                                     key=lambda o: (o["max_pax"] - o["min_pax"], -o["min_pax"]),
                                     default=None)
                        if widest:
                            manual = {"found": True, "minimum_pax": widest["min_pax"],
                                      "confidence": "high", "note": "price entered by hand",
                                      "matched_row": "entered by hand", "currency": "",
                                      "brackets": [{"min_pax": widest["min_pax"],
                                                    "max_pax": widest["max_pax"],
                                                    "price": float(typed),
                                                    "child_price": None, "infant_price": None}]}
                            rebuilt = _stamp_proposal_widget_tokens(price_refresh.build_ticket_proposals(
                                st.session_state.tpr_routes, {p["index"]: manual}))
                            st.session_state.tpr_proposals[p["index"]] = rebuilt[p["index"]]
                            st.rerun()
                        else:
                            # Same defensive fix as the Transfer/Transport "Use" button above -
                            # this Modality has no readable occupancy bracket to price by hand.
                            st.error("This Modality has no occupancy bracket to price by hand. "
                                    "Re-run the price refresh and try again.")

    st.subheader("3 — Apply")
    accepted = [p for p in proposals if p.get("accepted") and p.get("changes")]
    st.warning(f"This changes prices on **{len(accepted)} live Ticket Modality(ies)** for "
               f"supplier {supplier_id}. Nothing else is touched.")
    if st.button(f"🚀 Update {len(accepted)} Modality(ies)", type="primary",
                 disabled=not accepted, key="tpr_apply"):
        bar = st.progress(0.0, text="Updating…")

        def _tick2(done, total, name):
            bar.progress(min(done / max(total, 1), 1.0), text=f"Updating {name} ({done}/{total})")

        st.session_state.tpr_result = price_refresh.apply_ticket_proposals(
            client, supplier_id, proposals, progress=_tick2)
        bar.empty()
        st.rerun()

    result = st.session_state.get("tpr_result")
    if result:
        if result["updated"]:
            st.success(f"✅ {len(result['updated'])} Modality(ies) repriced.")
            for u in result["updated"]:
                st.write(f"- {u['name']}: " + ", ".join(
                    f"{c['min_pax']} pax {c['old']} → {c['new']}" for c in u["changes"]))
        if result["failed"]:
            st.error(f"❌ {len(result['failed'])} failed:")
            for f in result["failed"]:
                st.write(f"- **{f.get('name')}**: {f.get('detail')}")
        if st.button("🆕 Start again", key="tpr_new"):
            for key in ("tpr_proposals", "tpr_routes", "tpr_raw_text", "tpr_result"):
                st.session_state.pop(key, None)
            st.rerun()
