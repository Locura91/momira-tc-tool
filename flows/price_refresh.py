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
from contextlib import nullcontext as _nullcontext
import streamlit as st

from fts_transfer_matrix import classify_fts_matrix_file
from document_reader import extract_raw_text
from ai_extractor import friendly_error_message
import price_refresh
from ui_components import is_active_supplier, momira_active_suppliers

from app_helpers import (
    _fetch_url_text_safe, _stamp_proposal_widget_tokens,
    render_transport_manual_adjustment_flow, render_transport_price_consistency_flow,
)

_TRANSPORT_TYPE_OPTIONS = [
    ("CAR", "Car"), ("VAN", "Van"), ("BUS", "Bus"), ("FERRY", "Ferry"),
    ("TRAIN", "Train"), ("PLANE", "Plane"), ("COMBINED", "Combined"),
]
_TRANSPORT_TYPE_LABEL_TO_KEY = {label: key for key, label in _TRANSPORT_TYPE_OPTIONS}


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
    st.header("\U0001f4b6 Refresh prices from a rate sheet")
    if st.button("\U0001f519 Back", key="pr_back"):
        st.session_state.product_type = None
        st.rerun()
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
    momira = momira_active_suppliers(st.session_state.suppliers_cache)
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
    # Transport type scoping: removed the upfront multiselect filter (product owner, 2026-11-17).
    # Step 2's transport_type_groups picker already asks which type this rate sheet covers when
    # the supplier has mixed types — the upfront filter was redundant and forced an unnecessary
    # extra click. Manual adjustment and consistency check still accept transport_types=None
    # (meaning all types), which is the right default.
    pr_transport_types = None

    # can manually add a absolute or percentage number." A second, simpler mode alongside the
    # existing document-reading flow (kept for when there IS a rate sheet to read) - added only
    # for Transport, which has one shared vehicle/base price field; Transfer has no such single
    # field (pricesByOccupancy is per-bracket with no shared base), so this mode isn't offered
    # for it at all. Chosen BEFORE the rate-sheet uploader so picking it skips that section
    # entirely rather than leaving unused document-upload widgets on screen.
    if kind == price_refresh.KIND_TRANSPORT and supplier_id:
        pr_mode = st.radio(
            "How do you want to set the new price?",
            ["\U0001f4c4 From a rate sheet document", "\U0001f522 Manual %/absolute adjustment",
             "\U0001f50e Yearly price consistency check (read-only)"],
            horizontal=True, key="pr_source_mode")
        if pr_mode == "\U0001f522 Manual %/absolute adjustment":
            render_transport_manual_adjustment_flow(client, supplier_id,
                                                   transport_types=pr_transport_types)
            return
        if pr_mode == "\U0001f50e Yearly price consistency check (read-only)":
            render_transport_price_consistency_flow(client, supplier_id,
                                                   transport_types=pr_transport_types)
            return

    st.subheader("1 — The new rate sheet")
    url = st.text_input("Rate sheet URL (optional)", key="pr_url")
    files = st.file_uploader("Upload the rate sheet", type=["pdf", "docx", "xlsx", "pptx", "csv", "html", "htm", "png", "jpg", "jpeg", "webp"],
                             accept_multiple_files=True, key="pr_files")
    hint = st.text_input("Instruction (optional)", key="pr_hint",
                         placeholder="e.g. only the Hurghada section, private transfers only")
    # Destination filter: pre-load routes to let the human pick which cities to check,
    # so the AI doesn't overflow on suppliers with 200+ routes.
    _cached_routes_key = f"pr_preloaded_routes_{supplier_id}_{kind}"
    if supplier_id and st.button("Load destinations for filtering", key="pr_load_destinations",
                                  type="secondary"):
        with st.spinner(f"Loading this supplier's {kind.lower()}s…"):
            _pre_routes, _pre_err = price_refresh.load_supplier_products(
                client, supplier_id, kind)
        if _pre_err:
            st.error(f"Couldn't load: {_pre_err}")
        elif _pre_routes:
            st.session_state[_cached_routes_key] = _pre_routes
            st.rerun()

    # Clear stale city selections from a different supplier/kind
    for _old_key in [k for k in st.session_state if k.startswith("pr_preloaded_routes_")
                     and k != _cached_routes_key]:
        del st.session_state[_old_key]
    _preloaded = st.session_state.get(_cached_routes_key)
    name_filter = ""
    if _preloaded:
        import re as _re_dest
        _TRANSPORT_PREFIX = _re_dest.compile(
            r"^(?:one[- ]?way|round[- ]?trip|private|shared|)[\s]*"
            r"(?:transfer|transport|)[\s]*(?:from\s+)?",
            _re_dest.IGNORECASE,
        )
        _cities = set()
        for _r in _preloaded:
            _rname = (_r.get("name") or "")
            for _sep in ["→", " - ", " – ", " — "]:
                if _sep in _rname:
                    for _part in _rname.split(_sep):
                        _city = _re_dest.sub(r"\s*\([^)]*\)", "", _part).strip()
                        _city = _TRANSPORT_PREFIX.sub("", _city).strip()
                        if _city:
                            _cities.add(_city)
                    break
        _sorted_cities = sorted(_cities)
        if _sorted_cities:
            _selected = st.multiselect(
                f"Filter by destination ({len(_preloaded)} routes total — select cities to narrow down)",
                _sorted_cities, key="pr_city_filter",
                placeholder="Leave empty to check all routes")
            if _selected:
                name_filter = ""  # handled via _selected below
                st.session_state["_pr_city_selection"] = _selected
            else:
                st.session_state.pop("_pr_city_selection", None)
        st.caption(f"ℹ️ {len(_preloaded)} route(s) loaded from this supplier. "
                   f"Select cities above to narrow down, or leave empty to check all.")

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
            _n = len(routes)
            _batch = price_refresh._PRICE_LOOKUP_BATCH_SIZE
            _total_batches = (_n - 1) // _batch + 1 if _n > _batch else 1
            if _n > _batch:
                _bar = st.progress(0.0, text=f"Looking up prices… batch 0/{_total_batches}")

                def _ai_tick(done, total):
                    _bar.progress(min(done / max(total, 1), 1.0),
                                  text=f"Looking up prices… batch {done}/{total}")
            else:
                _bar = None
                _ai_tick = None
            with st.spinner(f"Looking up prices for {_n} route(s) in the document…") if _bar is None else _nullcontext():
                try:
                    findings = price_refresh.lookup_prices(routes, raw_text, human_hint=hint,
                                                           progress=_ai_tick)
                except Exception as e:
                    st.error(f"Couldn't read the document: {friendly_error_message(e)}")
                    findings = None
            if _bar is not None:
                _bar.empty()
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

    if st.button(f"\U0001f50d Read prices for this supplier's {kind.lower()}s", type="primary",
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

            if _preloaded:
                routes, err = _preloaded, None
                bar.empty()
            else:
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
                # Filter routes by selected cities so the AI doesn't overflow on
                # suppliers with 200+ routes.
                filtered = routes
                _city_sel = st.session_state.get("_pr_city_selection")
                if _city_sel:
                    _before = len(filtered)
                    _city_lower = [c.lower() for c in _city_sel]
                    filtered = [r for r in filtered
                                if any(c in (r.get("name") or "").lower() for c in _city_lower)]
                    if filtered:
                        st.info(f"\U0001f3af Filtered to **{len(filtered)}** of {_before} "
                                f"route(s) matching: {', '.join(_city_sel)}")
                    else:
                        st.warning(f"No routes match the selected cities — "
                                   f"checking all {_before} route(s) instead.")
                        filtered = routes
                # Base modality is auto-detected per route by _current_base_option (the
                # option with zero supplement). No global modality/base picker needed —
                # routes can have different numbers of modalities (product owner, 2026-11-17).
                if _pr_read_and_build(filtered, raw_text, fts_csv_tmp_paths, hint, None):
                    st.rerun()

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
    _summary_parts = [f"{len(changed)} route(s) would change", f"{len(unchanged)} already match the document"]
    if absent:
        _summary_parts.append(f"{len(absent)} not in this document (ignored — only listed routes are compared)")
    if blocked:
        _summary_parts.append(f"{len(blocked)} could not be read")
    if needs_base:
        _summary_parts.append(f"{len(needs_base)} need a base modality designated")
    if zero_supplement:
        _summary_parts.append(f"{len(zero_supplement)} blocked on a zero supplement")
    st.caption(" · ".join(_summary_parts))
    _scope = st.session_state.get("pr_scope")
    if _scope:
        st.info("\U0001f3af This round is scoped to the "
                + " and ".join(f"**{lo}-{hi} pax**" for lo, hi in _scope)
                + " modality only. Every other modality is left exactly as it is — its price is "
                  "not read, not compared, and not written.")
    _base_override = st.session_state.get("pr_base_override")
    if _base_override:
        lo, hi = _base_override
        st.info(f"\U0001f9ee **{lo}-{hi} pax** is set as the base price modality for this round. Every "
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
            f"\U0001f6ab **{len(blocked)} route(s) could not be fully read from Travel Compositor** and have "
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
            f"\U0001f6ab **{len(needs_base)} route(s) have two or more modalities and no vehicle/base "
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
            f"\U0001f6ab **{len(zero_supplement)} route(s) would compute a price supplement of exactly 0** "
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

    def _has_cheaper_price(p):
        return any(c["new"] < c["old"] for c in p.get("changes", []))

    if changed:
        st.markdown(f"### 🔴 {len(changed)} route(s) — price difference found")

    # Accept-all with exceptions: only auto-accepts routes where the document price is HIGHER
    # or equal. Routes with any cheaper price require individual human acceptance (CLAUDE.md
    # bulk pricing rule: never auto-apply cheaper prices).
    acol1, acol2 = st.columns([1, 4])
    with acol1:
        if st.button("✅ Accept all (higher prices only)", key="pr_accept_all", use_container_width=True):
            for p in proposals:
                if p["status"] == "changed" and not _has_cheaper_price(p):
                    p["accepted"] = True
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
                        f"New adult price ({c['min_pax']}-{c['max_pax']} pax){_period_label}",
                        min_value=0.0, step=1.0, value=float(c["new"]),
                        key=f"pr_price_{p['index']}_{c['code']}_{c.get('start_date') or ''}"
                            f"_{c.get('end_date') or ''}_{p.get('widget_token', 'g0')}",
                        label_visibility="collapsed")
                    if c.get("child_new") is not None:
                        c["child_new"] = st.number_input(
                            f"New child price ({c['min_pax']}-{c['max_pax']} pax){_period_label}",
                            min_value=0.0, step=1.0, value=float(c["child_new"]),
                            key=f"pr_child_{p['index']}_{c['code']}_{c.get('start_date') or ''}"
                                f"_{c.get('end_date') or ''}_{p.get('widget_token', 'g0')}",
                            label_visibility="collapsed")
                    if c.get("infant_new") is not None:
                        c["infant_new"] = st.number_input(
                            f"New infant price ({c['min_pax']}-{c['max_pax']} pax){_period_label}",
                            min_value=0.0, step=1.0, value=float(c["infant_new"]),
                            key=f"pr_infant_{p['index']}_{c['code']}_{c.get('start_date') or ''}"
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
                        else " *(price supplement)*" if c.get("write_kind") == "supplement" and kind != price_refresh.KIND_TRANSFER else ""
                    _new_period_label = "  ·  *new period*" if c.get("is_new_period") else ""
                    if abs(c["new"] - c["old"]) < 0.005:
                        st.markdown(f"{c['min_pax']}–{c['max_pax']} pax{_period_label}: {c['old']} → "
                                   f":green[**{c['new']}**] {_ccy} *{_basis}*{_kind_label}  ·  "
                                   f"*matches the live price*")
                    elif c["new"] < c["old"]:
                        st.markdown(f"{c['min_pax']}–{c['max_pax']} pax{_period_label}: {c['old']} → "
                                   f":orange[**{c['new']}**] {_ccy} *{_basis}*{_kind_label}{_new_period_label}  ·  "
                                   f"⚠️ *cheaper — requires individual acceptance*")
                    else:
                        st.markdown(f"{c['min_pax']}–{c['max_pax']} pax{_period_label}: {c['old']} → "
                                   f":red[**{c['new']}**] {_ccy} *{_basis}*{_kind_label}{_new_period_label}")
                    # Child price line — shown when present on the change (transport vehicle changes).
                    if c.get("child_old") is not None or c.get("child_new") is not None:
                        _co = c.get("child_old", 0.0)
                        _cn = c.get("child_new", 0.0)
                        _csrc = c.get("child_source", "")
                        _csrc_label = " *(from document)*" if _csrc == "document" \
                            else " *(scaled with adult)*" if _csrc == "scaled" else ""
                        if abs(_cn - _co) < 0.005:
                            st.markdown(f"&nbsp;&nbsp;&nbsp;↳ child: :green[**{_co}**] {_ccy} — *unchanged*")
                        else:
                            st.markdown(f"&nbsp;&nbsp;&nbsp;↳ child: {_co} → :red[**{_cn}**] {_ccy}{_csrc_label}")
                        if c.get("infant_new") is not None or (c.get("infant_old") is not None and c["infant_old"] > 0):
                            _io = c.get("infant_old", 0.0)
                            _in = c.get("infant_new", _io)
                            if abs(_in - _io) < 0.005:
                                st.markdown(f"&nbsp;&nbsp;&nbsp;↳ infant: :green[**{_io}**] {_ccy} — *unchanged*")
                            else:
                                st.markdown(f"&nbsp;&nbsp;&nbsp;↳ infant: {_io} → :red[**{_in}**] {_ccy}")
            bits = []
            if finding.get("matched_row"):
                bits.append(f'from the row *“{finding["matched_row"]}”*')
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
            _all_options = [o for o in (route.get("options") or []) if not o.get("fetch_failed")]
            _changed_codes = {c["code"] for c in p["changes"]}
            _is_transfer = kind == price_refresh.KIND_TRANSFER
            _examples = price_refresh.supplement_calculation_examples(route, p["changes"]) if not _is_transfer else []
            _base_option = None
            _base_price = None
            if not _is_transfer and len(_all_options) >= 2:
                _base_option = price_refresh._current_base_option(
                    _all_options, forced_bracket=route.get("base_bracket_override"))
                _base_code_in_changes = next((c for c in p["changes"] if c["code"] == _base_option["code"]), None)
                _base_price = round(float(_base_code_in_changes["new"]) if _base_code_in_changes else _base_option["unit_price"], 2)
            _rows = []
            for o in sorted(_all_options, key=lambda x: (x.get("min_pax", 0), x.get("max_pax", 0))):
                _code = o.get("code") or o.get("name") or "?"
                _name = o.get("name") or _code
                _pax_label = f"{o.get('min_pax', '?')}–{o.get('max_pax', '?')} pax"
                if _code in _changed_codes:
                    _c = next(c for c in p["changes"] if c["code"] == _code)
                    _full = _c["new"]
                    _status = "🔴 changed" if _full != _c["old"] else "✅"
                    if _is_transfer:
                        _tc = round(_c.get("child_new", 0.0) or 0.0, 2)
                        _ti = round(_c.get("infant_new", 0.0) or 0.0, 2)
                        _rows.append(f"| {_pax_label} | **{_full}** {_ccy} | {_tc} | {_ti} | {_name} | {_status} |")
                    else:
                        if _base_option and _code == _base_option["code"]:
                            _supp = "— *(base)*"
                        elif _base_price is not None:
                            _supp = f"+{round(_full - _base_price, 2)}"
                        else:
                            _supp = "—"
                        _rows.append(f"| {_pax_label} | **{_full}** {_ccy} | "
                                     f"{_base_price or _full} | {_supp} | {_name} | {_status} |")
                else:
                    _full = round(float(o.get("unit_price", 0)), 2)
                    if _is_transfer:
                        _uc = round(o.get("unit_price_child", 0.0), 2)
                        _ui = round(o.get("unit_price_infant", 0.0), 2)
                        _rows.append(f"| {_pax_label} | {_full} {_ccy} | {_uc} | {_ui} | {_name} | ✅ correct |")
                    else:
                        if _base_option and _code == _base_option["code"]:
                            _supp = "— *(base)*"
                        elif _base_price is not None:
                            _supp = f"+{round(_full - _base_price, 2)}"
                        else:
                            _supp = "—"
                        _rows.append(f"| {_pax_label} | {_full} {_ccy} | "
                                     f"{_base_price or _full} | {_supp} | {_name} | ✅ correct |")
            if _rows:
                if _is_transfer:
                    st.markdown(
                        f"\n| Bracket | Adult | Child | Infant | Modality | Status |\n"
                        f"|---------|-------|-------|--------|----------|--------|\n"
                        + "\n".join(_rows)
                    )
                else:
                    st.markdown(
                        f"\n| Bracket | Full price/pax | Base price | Supplement | Modality | Status |\n"
                        f"|---------|---------------|------------|------------|----------|--------|\n"
                        + "\n".join(_rows)
                    )
            # CONFIRMED REAL GAP (product owner): no way to redirect the AI when it read the
            # wrong row (e.g. picked Marsa Allam's price for a bundled Port Ghalib/Marsa Allam
            # route) short of fixing the number by hand above. This re-reads ONLY this one
            # route, with the extra instruction folded in, and replaces its proposal in place -
            # every other route in the batch is untouched.
            with st.expander("\U0001f916 Not right? Tell the AI more about this route", expanded=False):
                route_hint = st.text_input(
                    "Extra instruction for this route only",
                    key=f"pr_hint_{p['index']}_{p.get('widget_token', 'g0')}",
                    placeholder="e.g. use the Port Ghalib price, not Marsa Allam")
                if st.button("\U0001f501 Re-read this route", key=f"pr_reread_{p['index']}",
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
        st.markdown(f"### ✅ {len(unchanged)} route(s) — prices correct")
        for p in unchanged:
            route = p["route"]
            _ccy_u = route.get('currency') or ''
            _opts_u = [o for o in (route.get("options") or []) if not o.get("fetch_failed")]
            _is_transfer_u = kind == price_refresh.KIND_TRANSFER
            _base_u = None
            _base_price_u = None
            if not _is_transfer_u and len(_opts_u) >= 2:
                _base_u = price_refresh._current_base_option(
                    _opts_u, forced_bracket=route.get("base_bracket_override"))
                _base_price_u = round(float(_base_u.get("unit_price", 0)), 2)
            _rows_u = []
            for o in sorted(_opts_u, key=lambda x: (x.get("min_pax", 0), x.get("max_pax", 0))):
                _full_u = round(float(o.get("unit_price", 0)), 2)
                _name_u = o.get("name") or o.get("code") or "?"
                _pax_u = f"{o.get('min_pax', '?')}–{o.get('max_pax', '?')} pax"
                if _is_transfer_u:
                    _uc_u = round(o.get("unit_price_child", 0.0), 2)
                    _ui_u = round(o.get("unit_price_infant", 0.0), 2)
                    _rows_u.append(f"| {_pax_u} | {_full_u} {_ccy_u} | {_uc_u} | {_ui_u} | {_name_u} |")
                else:
                    if _base_u and o.get("code") == _base_u.get("code"):
                        _supp_u = "— *(base)*"
                    elif _base_price_u is not None:
                        _supp_u = f"+{round(_full_u - _base_price_u, 2)}"
                    else:
                        _supp_u = "—"
                    _rows_u.append(f"| {_pax_u} | {_full_u} {_ccy_u} | "
                                   f"{_base_price_u or _full_u} | {_supp_u} | {_name_u} |")
            _child_u = round(route.get("base_child", 0.0), 2)
            _infant_u = round(route.get("base_infant", 0.0), 2)
            _child_line = ""
            if route.get("price_per_pax", True) and (_child_u > 0 or _infant_u > 0):
                _parts = []
                if _child_u > 0:
                    _parts.append(f"child: :green[**{_child_u}**]")
                if _infant_u > 0:
                    _parts.append(f"infant: {_infant_u}")
                _child_line = f" · {' · '.join(_parts)} {_ccy_u}"
            st.markdown(f":green[**{route.get('name')}**]{_id_suffix(route)}{_child_line}")
            if _rows_u:
                if _is_transfer_u:
                    st.markdown(
                        f"\n| Bracket | Adult | Child | Infant | Modality |\n"
                        f"|---------|-------|-------|--------|----------|\n"
                        + "\n".join(_rows_u)
                    )
                else:
                    st.markdown(
                        f"\n| Bracket | Full price/pax | Base price | Supplement | Modality |\n"
                        f"|---------|---------------|------------|------------|----------|\n"
                        + "\n".join(_rows_u)
                    )
    if absent:
        with st.expander(f"ℹ️ {len(absent)} TC route(s) not in this document — ignored unless you match by hand"):
            st.caption("These routes exist in TC but weren't found in the uploaded document. "
                      "This is normal when the document only covers one destination. "
                      "You can still match a price by hand if the document does list them under different wording.")
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

    # ── Price supplement date range check (Transport only) ─────────────
    _EXPECTED_END = "2049-12-31"
    if kind == price_refresh.KIND_TRANSPORT:
        routes = st.session_state.get("pr_routes") or []
        date_issues = []
        for route in routes:
            for opt in route.get("options", []):
                raw_opt = opt.get("raw")
                if not isinstance(raw_opt, dict):
                    continue
                for pi, price in enumerate(raw_opt.get("prices") or []):
                    p_end = price.get("endDate") or ""
                    if p_end and p_end != _EXPECTED_END:
                        date_issues.append({
                            "transport_id": route["id"],
                            "transport_name": route["name"],
                            "option_code": opt["code"],
                            "option_name": opt.get("name", opt["code"]),
                            "price_index": pi,
                            "price_name": price.get("name", ""),
                            "current_start": price.get("startDate", ""),
                            "current_end": p_end,
                        })

        if date_issues:
            st.markdown("---")
            st.subheader(f"Price supplement date ranges ({len(date_issues)} issue(s))")
            st.info(
                f"These modality price supplements have an end date other than **{_EXPECTED_END}**. "
                f"Select the ones to fix — they will be updated together with the price changes below."
            )
            new_end = st.date_input(
                "New end date for selected supplements",
                value=__import__("datetime").date(2049, 12, 31),
                key="pr_date_fix_end",
            )
            new_end_str = new_end.strftime("%Y-%m-%d") if new_end else _EXPECTED_END

            pr_date_selections = {}
            for i, issue in enumerate(date_issues):
                label = (
                    f"{issue['transport_name']} — {issue['option_name']}: "
                    f"'{issue['price_name']}' "
                    f"({issue['current_start']} -> **{issue['current_end']}**)"
                )
                pr_date_selections[i] = st.checkbox(label, value=True, key=f"pr_datefix_{i}")

            chosen_date_fixes = [date_issues[i] for i, sel in pr_date_selections.items() if sel]
            st.session_state.pr_date_fixes = chosen_date_fixes
            st.session_state.pr_date_fix_end = new_end_str
        else:
            st.session_state.pop("pr_date_fixes", None)

    st.markdown("---")
    st.subheader("3 — Apply")
    accepted = [p for p in proposals if p.get("accepted") and p.get("changes")]
    date_fixes = st.session_state.get("pr_date_fixes") or []
    date_fix_end = st.session_state.get("pr_date_fix_end", _EXPECTED_END)
    if date_fixes:
        st.warning(f"This changes prices on **{len(accepted)} live {kind.lower()}(s)** and fixes "
                   f"**{len(date_fixes)} price supplement date(s)** for supplier {supplier_id}.")
    else:
        st.warning(f"This changes prices on **{len(accepted)} live {kind.lower()}(s)** for supplier "
                   f"{supplier_id}. Nothing else is touched.")
    has_work = accepted or date_fixes
    _higher_only_accepted = [p for p in accepted
                             if any(c["new"] > c["old"] for c in p.get("changes", []))]
    _n_higher = len(_higher_only_accepted)
    _has_any_cheaper = any(any(c["new"] < c["old"] for c in p.get("changes", []))
                           for p in accepted)
    bcol1, bcol2 = st.columns(2)
    with bcol1:
        btn_label = f"Update {len(accepted)} {kind.lower()}(s)" if accepted else "Apply date fixes only"
        if date_fixes and accepted:
            btn_label = f"Update {len(accepted)} {kind.lower()}(s) + fix {len(date_fixes)} date(s)"
        _btn_all = st.button(btn_label, type="primary", disabled=not has_work, key="pr_apply")
    with bcol2:
        _btn_higher = st.button(
            f"Update only higher prices ({_n_higher} route(s))",
            disabled=not _higher_only_accepted,
            key="pr_apply_higher") if _has_any_cheaper else False
    _do_apply = _btn_all or _btn_higher
    if _do_apply:
        bar = st.progress(0.0, text="Updating...")

        def _tick2(done, total, name):
            bar.progress(min(done / max(total, 1), 1.0), text=f"Updating {name} ({done}/{total})")

        if accepted:
            _apply_proposals = proposals
            if _btn_higher:
                import copy
                _apply_proposals = copy.deepcopy(proposals)
                for p in _apply_proposals:
                    if p.get("accepted") and p.get("changes"):
                        p["changes"] = [c for c in p["changes"] if c["new"] >= c["old"]]
                        if not p["changes"]:
                            p["accepted"] = False
            st.session_state.pr_result = price_refresh.apply_proposals(
                client, supplier_id, _apply_proposals, progress=_tick2)
        else:
            st.session_state.pr_result = {"updated": [], "failed": [], "skipped": []}

        if date_fixes:
            date_fix_results = []
            by_option = {}
            for fix in date_fixes:
                key = (fix["transport_id"], fix["option_code"])
                by_option.setdefault(key, []).append(fix)
            for (t_id, opt_code), _ in by_option.items():
                try:
                    opt = client.get_transport_option(supplier_id, t_id, opt_code)
                except Exception as e:
                    date_fix_results.append({"option_code": opt_code, "ok": False, "detail": str(e)})
                    continue
                changed = False
                for price in (opt.get("prices") or []):
                    p_end = price.get("endDate") or ""
                    if p_end and p_end != date_fix_end:
                        price["endDate"] = date_fix_end
                        changed = True
                if not changed:
                    date_fix_results.append({"option_code": opt_code, "ok": True, "detail": "already correct"})
                    continue
                try:
                    resp = client.update_transport_option(supplier_id, t_id, opt)
                except Exception as e:
                    date_fix_results.append({"option_code": opt_code, "ok": False, "detail": str(e)})
                    continue
                if isinstance(resp, dict) and "error" in resp:
                    date_fix_results.append({"option_code": opt_code, "ok": False,
                                             "detail": str(resp.get("message") or resp.get("error"))})
                else:
                    date_fix_results.append({"option_code": opt_code, "ok": True,
                                             "detail": f"endDate -> {date_fix_end}"})
            st.session_state.pr_date_fix_results = date_fix_results

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
                    with st.expander(f"\U0001f50d Raw request/response for {u['name']} (debug)"):
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
                    with st.expander(f"\U0001f50d Raw request/response for {f.get('name')} (debug)"):
                        st.json(f["debug"])

        date_fix_results = st.session_state.get("pr_date_fix_results")
        if date_fix_results:
            st.markdown("---")
            st.subheader("Date fix results")
            ok_fixes = [r for r in date_fix_results if r["ok"]]
            failed_fixes = [r for r in date_fix_results if not r["ok"]]
            if ok_fixes:
                st.success(f"{len(ok_fixes)} price supplement date(s) fixed.")
                for r in ok_fixes:
                    st.write(f"- Option `{r['option_code']}`: {r['detail']}")
            if failed_fixes:
                st.error(f"{len(failed_fixes)} date fix(es) failed:")
                for r in failed_fixes:
                    st.write(f"- Option `{r['option_code']}`: {r['detail']}")

        if st.button("\U0001f195 Start again", key="pr_new"):
            for key in ("pr_proposals", "pr_routes", "pr_raw_text", "pr_result",
                         "pr_date_fixes", "pr_date_fix_end", "pr_date_fix_results"):
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
    st.header("\U0001f39f️ Refresh Ticket prices from a rate sheet")
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
    momira = momira_active_suppliers(st.session_state.suppliers_cache)
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
    files = st.file_uploader("Upload the rate sheet", type=["pdf", "docx", "xlsx", "pptx", "csv", "html", "htm", "png", "jpg", "jpeg", "webp"],
                             accept_multiple_files=True, key="tpr_files")
    hint = st.text_input("Instruction (optional)", key="tpr_hint",
                         placeholder="e.g. only the Alexandria tours section")

    if st.button("\U0001f50d Read prices for this supplier's Tickets", type="primary",
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
                    # Check for itinerary changes (description/includes/excludes)
                    with st.spinner("Checking for itinerary changes…"):
                        try:
                            itinerary_findings = price_refresh.lookup_ticket_itinerary(routes, raw_text)
                        except Exception:
                            itinerary_findings = {}
                    itinerary_diffs = {}
                    for ri, itin in itinerary_findings.items():
                        if ri < len(routes):
                            diff = price_refresh.compare_ticket_itinerary(routes[ri], itin)
                            if diff.get("has_changes"):
                                itinerary_diffs[ri] = {**diff, "doc": itin}
                    st.session_state.tpr_routes = routes
                    st.session_state.tpr_proposals = _stamp_proposal_widget_tokens(
                        price_refresh.build_ticket_proposals(routes, findings))
                    st.session_state.tpr_raw_text = raw_text
                    st.session_state.tpr_itinerary_diffs = itinerary_diffs
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

    st.subheader("2 — Check the new prices & operational end date")
    _tsummary = [f"{len(changed)} Modality(ies) would change", f"{len(unchanged)} already match the document"]
    if absent:
        _tsummary.append(f"{len(absent)} not in this document (ignored)")
    if blocked:
        _tsummary.append(f"{len(blocked)} could not be read")
    if unsupported:
        _tsummary.append(f"{len(unsupported)} not supported yet (SERVICE pricing)")
    st.caption(" · ".join(_tsummary))

    # CONFIRMED PRODUCT-OWNER REQUEST (2026-11-09): ticket price refresh must DUPLICATE the
    # modality (POST) instead of updating in-place (PUT).  The original stays untouched; a new
    # modality is created with the new prices and new date range.
    from datetime import date as _date, timedelta as _timedelta
    # Find the latest endDate across all changed modalities — the new season starts the day after
    _cur_end_date = None
    _cur_end_str = None
    for _p in changed:
        _raw = (_p.get("route") or {}).get("raw") or {}
        _end = _raw.get("endDate")
        if _end:
            try:
                _parsed = _date.fromisoformat(str(_end)[:10])
                if _cur_end_date is None or _parsed > _cur_end_date:
                    _cur_end_date = _parsed
                    _cur_end_str = _end
            except (ValueError, TypeError):
                pass
    # New season starts the day after the existing modality ends
    _default_start = (_cur_end_date + _timedelta(days=1)) if _cur_end_date else _date.today()
    _default_end = _default_start.replace(year=_default_start.year + 1)
    if _cur_end_str:
        st.info(f"ℹ️ Current operational end date on the existing Modality: **{_cur_end_str}** "
                f"— new modality will start on **{_default_start.strftime('%d.%m.%Y')}** (next day)")
    col_start, col_end = st.columns(2)
    with col_start:
        tpr_new_start = st.date_input(
            "Start date for the NEW Modality",
            value=_default_start,
            min_value=_default_start,
            format="DD/MM/YYYY",
            key="tpr_new_start_date",
            help="Defaults to the day after the existing modality's end date.",
        )
    with col_end:
        tpr_new_end = st.date_input(
            "End date for the NEW Modality",
            value=_default_end,
            min_value=_default_start,
            format="DD/MM/YYYY",
            key="tpr_new_end_date",
            help="Sets the endDate on each newly created Modality.",
        )
    tpr_update_dates = st.checkbox(
        "Set date range on the new modality (recommended)",
        value=True, key="tpr_update_dates_cb"
    )

    if blocked:
        st.error(f"\U0001f6ab **{len(blocked)} Modality(ies) could not be fully read from Travel "
                f"Compositor** and have been left untouched. Re-run the price refresh and they "
                f"should load.")
        for p in blocked:
            st.markdown(f"- **{p['route'].get('name') or '(unnamed)'}**")

    if unsupported:
        with st.expander(f"⚠️ {len(unsupported)} Modality(ies) not supported yet "
                         f"(SERVICE pricing)"):
            st.caption("SERVICE-priced Modalities (one flat total per booking) are not yet "
                      "supported — update those by hand for now.")
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
            _is_dist = route.get("price_type") == "DISTRIBUTION"
            for c in p["changes"]:
                pcol1, pcol2 = st.columns([3, 2])
                _pax_label = "per person" if _is_dist else f"{c['min_pax']} pax"
                with pcol2:
                    c["new"] = st.number_input(
                        f"New adult price ({_pax_label})", min_value=0.0, step=1.0,
                        value=float(c["new"]),
                        key=f"tpr_price_{p['index']}_{c['code']}_{p.get('widget_token', 'g0')}",
                        label_visibility="collapsed")
                with pcol1:
                    _ccy = route.get('currency') or ''
                    if abs(c["new"] - c["old"]) < 0.005:
                        st.markdown(f"{_pax_label}: {c['old']} → :green[**{c['new']}**] {_ccy}  ·  "
                                   f"*matches the live price*")
                    else:
                        st.markdown(f"{_pax_label}: {c['old']} → :red[**{c['new']}**] {_ccy}")
                    if c.get("child_new") is not None:
                        st.caption(f"child ({_pax_label}): "
                                  f"{c.get('child_old') if c.get('child_old') is not None else '?'} → "
                                  f"{c['child_new']} {_ccy} (from the document)")
                    elif c.get("child_old") is not None:
                        st.caption(f"child ({_pax_label}) moves with the adult price "
                                  f"(document gave no separate child price)")
            bits = []
            if finding.get("matched_row"):
                bits.append(f'from the row *“{finding["matched_row"]}”*')
            if finding.get("confidence") and finding["confidence"] != "high":
                bits.append(f"**{finding['confidence']} confidence**")
            if finding.get("note"):
                bits.append(finding["note"])
            if p.get("currency_changed"):
                bits.append(f"⚠️ the document says **{finding['currency']}** but this Ticket is "
                            f"**{route.get('currency')}** — the price is applied as-is, not converted")
            if bits:
                st.caption("  ·  ".join(bits))
            with st.expander("\U0001f916 Not right? Tell the AI more about this Ticket", expanded=False):
                route_hint = st.text_input(
                    "Extra instruction for this Ticket only",
                    key=f"tpr_hint_{p['index']}_{p.get('widget_token', 'g0')}",
                    placeholder="e.g. use the half-day price, not the full-day one")
                if st.button("\U0001f501 Re-read this Ticket", key=f"tpr_reread_{p['index']}",
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
        with st.expander(f"ℹ️ {len(absent)} TC ticket(s) not in this document — ignored unless you match by hand"):
            st.caption("These tickets exist in TC but weren't found in the uploaded document. "
                      "This is normal when the document only covers some products. "
                      "You can still match a price by hand if the document lists them under different wording.")
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

    # ── Itinerary change detection ────────────────────────────────────
    itinerary_diffs = st.session_state.get("tpr_itinerary_diffs") or {}
    if itinerary_diffs:
        st.subheader(f"2b — Itinerary changes detected ({len(itinerary_diffs)})")
        st.info("The document contains description/includes/excludes text that differs from "
                "what is currently live in Travel Compositor. Review and confirm which changes "
                "to apply alongside the new modality.")
        _itin_accepted = st.session_state.setdefault("tpr_itinerary_accepted", {})
        for ri, diff_data in itinerary_diffs.items():
            route = proposals[ri]["route"] if ri < len(proposals) else {}
            ticket_label = route.get("name") or f"Ticket index {ri}"
            ticket_code = route.get("ticket_code") or ""
            with st.expander(f"📝 {ticket_label}", expanded=True):
                changes = diff_data.get("changes") or {}
                if "description" in changes:
                    st.markdown("**Description changed:**")
                    _old_desc = price_refresh._strip_html(changes["description"]["old"])
                    _new_desc = price_refresh._strip_html(changes["description"]["new"])
                    col_old, col_new = st.columns(2)
                    with col_old:
                        st.caption("Current (TC)")
                        st.text_area("", value=_old_desc, height=150,
                                    disabled=True, key=f"tpr_itin_old_desc_{ri}")
                    with col_new:
                        st.caption("New (document)")
                        st.text_area("", value=_new_desc, height=150,
                                    disabled=True, key=f"tpr_itin_new_desc_{ri}")

                if "includes" in changes:
                    st.markdown("**Includes changed:**")
                    _old_inc = changes["includes"]["old"]
                    _new_inc = changes["includes"]["new"]
                    added = [x for x in _new_inc if x not in _old_inc]
                    removed = [x for x in _old_inc if x not in _new_inc]
                    if added:
                        st.markdown("  ".join(f":green[+ {a}]" for a in added))
                    if removed:
                        st.markdown("  ".join(f":red[- {r}]" for r in removed))

                if "excludes" in changes:
                    st.markdown("**Excludes changed:**")
                    _old_exc = changes["excludes"]["old"]
                    _new_exc = changes["excludes"]["new"]
                    added = [x for x in _new_exc if x not in _old_exc]
                    removed = [x for x in _old_exc if x not in _new_exc]
                    if added:
                        st.markdown("  ".join(f":green[+ {a}]" for a in added))
                    if removed:
                        st.markdown("  ".join(f":red[- {r}]" for r in removed))

                _itin_accepted[ri] = st.checkbox(
                    "Apply these itinerary changes", value=_itin_accepted.get(ri, True),
                    key=f"tpr_itin_accept_{ri}")
    elif itinerary_diffs is not None:
        st.caption("✅ No itinerary changes detected — description, includes & excludes match the document.")

    st.subheader("3 — Apply")
    accepted = [p for p in proposals if p.get("accepted") and p.get("changes")]
    _itin_to_apply = {ri: d for ri, d in itinerary_diffs.items()
                      if st.session_state.get("tpr_itinerary_accepted", {}).get(ri)}
    _date_note = (f" · date range **{tpr_new_start.strftime('%d.%m.%Y')}** – **{tpr_new_end.strftime('%d.%m.%Y')}**"
                  if tpr_update_dates else "")
    _itin_note = f" · **{len(_itin_to_apply)} itinerary update(s)**" if _itin_to_apply else ""
    st.warning(f"This will **create {len(accepted)} NEW Modality(ies)**{_date_note}{_itin_note} with updated prices "
               f"for supplier {supplier_id}. Existing modalities stay untouched.")
    if st.button(f"\U0001f680 Create {len(accepted)} new Modality(ies)", type="primary",
                 disabled=not accepted, key="tpr_apply"):
        bar = st.progress(0.0, text="Creating…")

        def _tick2(done, total, name):
            bar.progress(min(done / max(total, 1), 1.0), text=f"Creating {name} ({done}/{total})")

        _new_end_iso = tpr_new_end.isoformat() if tpr_update_dates else None
        _new_start_iso = tpr_new_start.isoformat() if tpr_update_dates else None
        st.session_state.tpr_result = price_refresh.apply_ticket_proposals(
            client, supplier_id, proposals, progress=_tick2,
            new_end_date=_new_end_iso, new_start_date=_new_start_iso,
            itinerary_changes=_itin_to_apply or None)
        bar.empty()
        st.rerun()

    result = st.session_state.get("tpr_result")
    if result:
        if result["updated"]:
            st.success(f"✅ {len(result['updated'])} new Modality(ies) created with updated prices.")
            for u in result["updated"]:
                _code_note = f" (new code: `{u['new_code']}`)" if u.get("new_code") else ""
                st.write(f"- {u['name']}{_code_note}: " + ", ".join(
                    f"{c['min_pax']} pax {c['old']} → {c['new']}" for c in u["changes"]))
        if result["failed"]:
            st.error(f"❌ {len(result['failed'])} failed:")
            for f in result["failed"]:
                st.write(f"- **{f.get('name')}**: {f.get('detail')}")
        itin_results = result.get("itinerary_results") or []
        if itin_results:
            ok_itin = [r for r in itin_results if r["ok"]]
            fail_itin = [r for r in itin_results if not r["ok"]]
            if ok_itin:
                st.success(f"📝 {len(ok_itin)} ticket itinerary(ies) updated.")
                for r in ok_itin:
                    st.write(f"- Ticket `{r['ticket_code']}`: {r['detail']}")
            if fail_itin:
                st.error(f"📝 {len(fail_itin)} itinerary update(s) failed:")
                for r in fail_itin:
                    st.write(f"- Ticket `{r['ticket_code']}`: {r['detail']}")
        if st.button("\U0001f195 Start again", key="tpr_new"):
            for key in ("tpr_proposals", "tpr_routes", "tpr_raw_text", "tpr_result",
                         "tpr_itinerary_diffs", "tpr_itinerary_accepted"):
                st.session_state.pop(key, None)
            st.rerun()
