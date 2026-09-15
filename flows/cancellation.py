"""
Bulk cancellation-policy flows, split out of app.py (Phase 1 restructure, zero behaviour change).

render_transport_cancellation_bulk_flow (Transport's own, in-production-since-2026-08-28 flow)
and render_generic_cancellation_bulk_flow (the ClosedTour/Ticket/Transfer/Hotel flow) moved here
verbatim, together, matching the plan's module grouping. `render_cancellation_bulk_flow` (the
top-level dispatcher that picks between these two by product type) sits BETWEEN them in app.py's
own source and stays there - it's a small dispatcher, not a `render_*_flow` of its own scope per
the plan - and calls both of these by name, resolved once app.py's `from flows.cancellation
import ...` line runs (Python looks up a call target at call time, so it doesn't matter that the
dispatcher is defined between the two functions' old positions - it just needs the import to have
run by the time it's actually CALLED, which happens well after module load). Everything these two
functions reference that is defined at app.py's own top level is imported back from app via the
same late-binding pattern used by the earlier flows modules.
"""
import streamlit as st
import cancellation_bulk_transport

from app import _ur_pick_momira_supplier


def render_transport_cancellation_bulk_flow(client):
    """Bulk-change the cancellation policy on every (or a chosen subset of) one supplier's
    already-live Transports - see cancellation_bulk_transport.py's module docstring for why
    this is its own deliberate action (never a side effect of a price refresh) and why it's
    scoped to Transport only (a real structured cancellationRanges field to safely overwrite;
    Transfer has no equivalent - its cancellation terms are baked into free-text voucher
    wording with no reliable anchor to safely locate and replace).

    CONFIRMED SCOPE DECISIONS (product owner, 2026-08-28, AskUserQuestion): per-supplier only
    for now (not multi-supplier/all-at-once); the new policy defaults from that supplier's
    saved Cancellation Link (cancellation_links.py) - or the house 30-day/free default when
    none is saved - always editable before applying; EVERY live Transport is listed with its
    CURRENT policy shown, so nothing "already filled out" differently is silently skipped -
    the human sees it and decides per row; and the customer-facing description text is
    rewritten to match, not just the structured field.
    """
    st.header("Bulk-update Cancellation Policy (Transport)")
    st.caption("Applies one cancellation policy to every live Transport of one supplier at "
              "once - both the structured field Travel Compositor enforces AND the matching "
              "sentence in each one's customer-facing description.")

    supplier_id = _ur_pick_momira_supplier(client, "ctb")
    if not supplier_id:
        st.info("Choose a supplier to continue.")
        return

    if st.session_state.get("ctb_supplier_id") != supplier_id:
        # Supplier changed - drop everything loaded for the previous one so nothing from a
        # different supplier's review screen can leak into this one.
        for key in ("ctb_rows", "ctb_new_tiers", "ctb_default_scope", "ctb_selected", "ctb_results"):
            st.session_state.pop(key, None)
        st.session_state.ctb_supplier_id = supplier_id

    if st.session_state.get("ctb_results"):
        st.markdown("---")
        st.subheader("Result")
        results = st.session_state.ctb_results
        skipped = [r for r in results if r.get("skipped")]
        ok = [r for r in results if r["ok"] and not r.get("skipped")]
        failed = [r for r in results if not r["ok"]]
        st.caption(f"{len(ok)} updated · {len(skipped)} left unchanged (existing policy already "
                  f"as strict) · {len(failed)} failed.")
        for r in ok:
            st.success(f"✅ **{r['name']}** updated.")
        for r in skipped:
            st.info(f"🛡️ **{r['name']}** — {r['detail']}.")
        for r in failed:
            st.error(f"🚫 **{r['name']}** — {r['detail']}")
        if st.button("↩️ Run again / start over", key="ctb_reset"):
            for key in ("ctb_rows", "ctb_new_tiers", "ctb_default_scope", "ctb_selected", "ctb_results"):
                st.session_state.pop(key, None)
            st.rerun()
        return

    if st.button("📥 Load this supplier's live Transports", key="ctb_load"):
        progress_bar = st.progress(0.0)
        status_line = st.empty()

        def _ctb_load_progress(done, total, name):
            status_line.caption(f"Fetching {done} / {total}: {name}...")
            if total:
                progress_bar.progress(min(1.0, done / total))

        rows, err = cancellation_bulk_transport.load_supplier_transports_for_cancellation(
            client, supplier_id, progress=_ctb_load_progress)
        progress_bar.empty()
        status_line.empty()
        if err:
            st.error(f"❌ Couldn't load Transports: {err}")
        else:
            st.session_state.ctb_rows = rows
            st.session_state.ctb_selected = {r["id"]: True for r in rows}
            st.rerun()

    rows = st.session_state.get("ctb_rows")
    if rows is None:
        return
    if not rows:
        st.info("This supplier has no live Transports.")
        return

    st.subheader(f"2 — New cancellation policy (will apply to up to {len(rows)} Transport(s))")

    if "ctb_new_tiers" not in st.session_state:
        default_tiers, scope_label = cancellation_bulk_transport.default_new_tiers(supplier_id)
        st.session_state.ctb_new_tiers = default_tiers
        st.session_state.ctb_default_scope = scope_label
    st.caption(f"Pre-filled from {st.session_state.get('ctb_default_scope', 'the house default')} — "
              f"edit below if this run needs something different. This does NOT change the "
              f"saved Cancellation Link itself, only what gets applied this run.")

    import pandas as pd
    from ui_components import editable_table, _safe_int, _safe_float

    # CONFIRMED REAL PRODUCTION BUG (product owner, 2026-09-11): this table used to ask for
    # "Cancellation Fee %", the INVERSE of how a human naturally thinks about the policy they
    # want to set (the house rule itself is phrased "100% refund", and Travel Compositor's own
    # wire field is the refund percentage - see schemas.ContractTransportCancellationRangeVO).
    # A human typing "100" here meaning "100% refund" instead silently applied a 0%-refund/
    # no-refund policy - confirmed live against 18+ Transports of one supplier (see
    # claude/bulk-cancellation-refund-fee-inversion-2026-09-11.md, project docs). Storage is
    # UNCHANGED (still {"days","fee_percentage"} - build_proposals/_cancellation_ranges_from_
    # tiers etc. all still expect that shape) - only this human-facing column is now Refund%,
    # converted right at this boundary (refund = 100 - fee) - same fix as cancellation_links.
    # render_cancellation_link_editor's identical table.
    _CTB_REFUND_COL = "Refund % if cancelled by this deadline"

    def _ctb_tier_table(tiers):
        table_rows = [{"Days before arrival (or more)": t.get("days"),
                       _CTB_REFUND_COL: round(100.0 - _safe_float(t.get("fee_percentage"), fallback=0.0), 4)}
                     for t in (tiers or []) if isinstance(t, dict)]
        return pd.DataFrame(table_rows) if table_rows else pd.DataFrame(
            columns=["Days before arrival (or more)", _CTB_REFUND_COL])

    def _ctb_df_to_tiers(edited_df):
        new_tiers = []
        for _, row in edited_df.iterrows():
            days_val = row.get("Days before arrival (or more)")
            if days_val is None or (isinstance(days_val, float) and pd.isna(days_val)):
                continue
            refund_pct = max(0.0, min(100.0, _safe_float(row.get(_CTB_REFUND_COL), fallback=100.0)))
            new_tiers.append({
                "days": _safe_int(days_val, fallback=0),
                "fee_percentage": max(0.0, min(100.0, 100.0 - refund_pct)),
            })
        return new_tiers

    def _ctb_save_new_tiers(edited_df):
        st.session_state.ctb_new_tiers = _ctb_df_to_tiers(edited_df)

    ctb_col_config = {
        "Days before arrival (or more)": st.column_config.NumberColumn(min_value=0, step=1),
        _CTB_REFUND_COL: st.column_config.NumberColumn(min_value=0, max_value=100, step=1),
    }
    editable_table("New policy", _ctb_tier_table(st.session_state.ctb_new_tiers), "ctb_new_policy",
                   on_save=_ctb_save_new_tiers, column_config=ctb_col_config)

    def _ctb_fmt_tiers(tiers):
        if not tiers:
            return "(system default — 30 days, 100% refund)"
        return "; ".join(f"{t['days']}+ days: {100.0 - t['fee_percentage']:.0f}% refund"
                         for t in sorted(tiers, key=lambda t: t["days"], reverse=True))

    proposals = cancellation_bulk_transport.build_proposals(rows, st.session_state.ctb_new_tiers)
    proposals_by_id = {p["id"]: p for p in proposals}

    st.subheader("3 — Review and choose which to update")
    bcol1, bcol2 = st.columns(2)
    with bcol1:
        if st.button("Select all", key="ctb_select_all"):
            st.session_state.ctb_selected = {p["id"]: True for p in proposals}
            # CONFIRMED BUG FIX (product owner, 2026-09-10): same widget-key-ignores-value=
            # issue as everywhere else this pattern is used - see widget_state.py's docstring.
            for p in proposals:
                st.session_state[f"ctb_pick_{p['id']}"] = True
            st.rerun()
    with bcol2:
        if st.button("Select none", key="ctb_select_none"):
            st.session_state.ctb_selected = {p["id"]: False for p in proposals}
            for p in proposals:
                st.session_state[f"ctb_pick_{p['id']}"] = False
            st.rerun()

    for p in proposals:
        route = f"  ·  {p['departure_code']} → {p['arrival_code']}" if (p["departure_code"] or p["arrival_code"]) else ""
        label = f"**{p['name']}**{route}  ·  id `{p['id']}`"
        if p["unchanged"]:
            st.session_state.ctb_selected[p["id"]] = False
            st.checkbox(f"{label}  ·  ✅ already matches — nothing to do", value=False, disabled=True,
                       key=f"ctb_pick_{p['id']}")
        elif p.get("existing_stricter"):
            # CONFIRMED REAL RULE (product owner, 2026-09-11): never overwrite a live policy
            # that's already at least as strict as the new one - see
            # builder.existing_cancellation_at_least_as_strict's own docstring. Disabled/
            # unchecked by default like an exact match, but with its own explanation so it's
            # not confused with "already identical".
            st.session_state.ctb_selected[p["id"]] = False
            st.checkbox(f"{label}  ·  🛡️ existing policy is already at least as strict — left alone",
                       value=False, disabled=True, key=f"ctb_pick_{p['id']}")
        else:
            st.session_state.ctb_selected[p["id"]] = st.checkbox(
                label, value=st.session_state.ctb_selected.get(p["id"], True), key=f"ctb_pick_{p['id']}")
        with st.expander("Details", expanded=False):
            dcol1, dcol2 = st.columns(2)
            with dcol1:
                st.caption("**Current**")
                st.text(_ctb_fmt_tiers(p["current_fee_tiers"]))
                st.caption(p["current_cancellation_snippet"] or "*(no cancellation text found)*")
            with dcol2:
                st.caption("**New**  ·  goes into Description")
                st.text(_ctb_fmt_tiers(p["new_fee_tiers"]))
                st.caption(p["new_cancellation_text"])
            if not p["existing_paragraph_found"]:
                st.warning("⚠️ No existing cancellation paragraph was found in Description — a "
                          "new one will be INSERTED into Description rather than replacing one. "
                          "Double-check the result afterward inside Travel Compositor.")
            if p.get("full_fetch_failed"):
                st.warning("⚠️ Couldn't re-fetch this Transport's own full record (only the "
                          "shorter list entry was available) - some rarely-used fields may be "
                          "filled in with a blank default rather than their real existing value. "
                          "Safe to include, but worth a quick check in Travel Compositor "
                          "afterward if this route uses any of those fields.")

    selected_ids = [pid for pid, v in st.session_state.ctb_selected.items() if v]
    st.caption(f"{len(selected_ids)} of {len(proposals)} selected.")
    if not selected_ids:
        return

    st.subheader("4 — Apply")
    st.warning(f"⚠️ This will PUT (update) {len(selected_ids)} Transport(s) — both the structured "
              f"cancellation field and the matching sentence in each one's description. Everything "
              f"else on each record (pricing, segments, images, dates) is left exactly as it is.")
    # CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): the "New policy" table above is a
    # live-editable table (editable_table) - while it's in live-edit mode, `st.session_state.
    # ctb_new_tiers` (and therefore every `proposals` entry's "New" column shown above) still
    # reflects the LAST SAVED numbers, not whatever's currently typed into the open table. This
    # button used to stay enabled through that whole window, so clicking "Apply" while the table
    # had unsaved edits pushed the OLD policy to every selected live Transport while the screen
    # displayed the new, not-yet-saved numbers right above it. Disabled until the table is saved.
    ctb_table_being_edited = bool(st.session_state.get("_editing_table_ctb_new_policy"))
    if ctb_table_being_edited:
        st.error("🚫 The New Policy table above has unsaved edits — click its own Save button "
                 "first, or this button would apply the OLD numbers while the screen shows new ones.")
    if st.button(f"🚀 Update {len(selected_ids)} Transport(s)", key="ctb_confirm", type="primary",
                 disabled=ctb_table_being_edited):
        to_apply = [proposals_by_id[pid] for pid in selected_ids]
        with st.spinner("Updating..."):
            results = cancellation_bulk_transport.apply_proposals(client, supplier_id, to_apply)
        st.session_state.ctb_results = results
        st.rerun()


def render_generic_cancellation_bulk_flow(client, product_type):
    """The ClosedTour / Ticket / Transfer / Hotel half of render_cancellation_bulk_flow, backed
    by cancellation_bulk.py. Same 4-step shape as render_transport_cancellation_bulk_flow
    (load -> set new policy -> review/select -> apply), with two differences: ClosedTour has
    no list endpoint, so codes must be typed in first; and Transfer/Hotel have no structured
    cancellationRanges field at all, so only the text side is shown/changed for those two.
    """
    import cancellation_bulk

    supplier_id = _ur_pick_momira_supplier(client, "cb")
    if not supplier_id:
        st.info("Choose a supplier to continue.")
        return

    if st.session_state.get("cb_supplier_id") != supplier_id:
        for key in ("cb_rows", "cb_new_tiers", "cb_default_scope", "cb_selected", "cb_results"):
            st.session_state.pop(key, None)
        st.session_state.cb_supplier_id = supplier_id

    if st.session_state.get("cb_results"):
        st.markdown("---")
        st.subheader("Result")
        results = st.session_state.cb_results
        skipped = [r for r in results if r.get("skipped")]
        ok = [r for r in results if r["ok"] and not r.get("skipped")]
        failed = [r for r in results if not r["ok"]]
        has_structured = product_type in cancellation_bulk._STRUCTURED_TIER_FIELDS
        if not has_structured and ok:
            # CONFIRMED REAL PLATFORM BEHAVIOR (product owner, 2026-09-11, live before/after
            # GET diff on a real Transfer: setting "30 days or prior" in Travel Compositor's
            # own Cancellation tab and clicking Save there did NOT change anything the API
            # returns - the admin UI's structured table for this product type isn't backed by
            # this resource at all, for anyone, not just this tool). Contrast confirmed the
            # same way for Transport: its cancellationRanges DOES appear in the GET response
            # after being set. So "updated" below is 100% true for the voucher text - it is
            # NOT a partial/silent-failure caveat - there is simply no structured field on
            # this product type's API for anything to write.
            st.info(f"ℹ️ {product_type} has no structured cancellation field in Travel "
                    f"Compositor's API at all (confirmed via a live GET before/after diff, "
                    f"2026-09-11) - only the customer-facing voucher text below was changed. "
                    f"If {product_type}'s own \"Cancellation\" tab in Travel Compositor shows "
                    f"a policy table, that table isn't wired to this API either - it didn't "
                    f"change when set directly there either. Worth asking Travel Compositor "
                    f"support whether {product_type} cancellation policies are settable via "
                    f"API at all.")
        st.caption(f"{len(ok)} updated · {len(skipped)} left unchanged (existing policy already "
                  f"as strict) · {len(failed)} failed.")
        for r in ok:
            st.success(f"✅ **{r['name']}** updated.")
        for r in skipped:
            st.info(f"🛡️ **{r['name']}** — {r['detail']}.")
        for r in failed:
            st.error(f"🚫 **{r['name']}** — {r['detail']}")
        if st.button("↩️ Run again / start over", key="cb_reset"):
            for key in ("cb_rows", "cb_new_tiers", "cb_default_scope", "cb_selected", "cb_results"):
                st.session_state.pop(key, None)
            st.rerun()
        return

    codes = None
    if product_type == "ClosedTour":
        st.caption("ClosedTour has no list-all endpoint in Travel Compositor - paste the codes "
                  "to check, one per line.")
        codes_text = st.text_area("ClosedTour codes", key="cb_ct_codes", height=100)
        codes = [c.strip() for c in codes_text.splitlines() if c.strip()]

    load_disabled = product_type == "ClosedTour" and not codes
    if st.button(f"📥 Load this supplier's live {product_type}(s)", key="cb_load", disabled=load_disabled):
        with st.spinner("Loading..."):
            rows, err = cancellation_bulk.load_supplier_services_for_cancellation(
                client, supplier_id, product_type, codes=codes)
            if err and not rows:
                st.error(f"❌ Couldn't load {product_type}(s): {err}")
            else:
                if err:
                    st.warning(f"⚠️ Some couldn't be loaded: {err}")
                st.session_state.cb_rows = rows
                st.session_state.cb_selected = {r["id"]: True for r in rows}
                st.rerun()

    rows = st.session_state.get("cb_rows")
    if rows is None:
        return
    if not rows:
        st.info(f"This supplier has no live {product_type}(s).")
        return

    has_structured = product_type in ("ClosedTour", "Ticket")
    st.subheader(f"2 — New cancellation policy (will apply to up to {len(rows)} {product_type}(s))")
    if not has_structured:
        st.caption(f"{product_type} has no separate structured cancellation field in Travel "
                  f"Compositor - only the customer-facing Voucher remarks text is rewritten.")

    if "cb_new_tiers" not in st.session_state:
        default_tiers, scope_label = cancellation_bulk.default_new_tiers(supplier_id, product_type)
        st.session_state.cb_new_tiers = default_tiers
        st.session_state.cb_default_scope = scope_label
    st.caption(f"Pre-filled from {st.session_state.get('cb_default_scope', 'the house default')} — "
              f"edit below if this run needs something different. This does NOT change the "
              f"saved Cancellation Link itself, only what gets applied this run.")

    import pandas as pd
    from ui_components import editable_table, _safe_int, _safe_float

    # CONFIRMED REAL PRODUCTION BUG (product owner, 2026-09-11): same "Cancellation Fee %"
    # inversion trap fixed in render_transport_cancellation_bulk_flow's identical table (see
    # that fix's own comment, and claude/bulk-cancellation-refund-fee-inversion-2026-09-11.md
    # in project docs) - a human typing the desired RESULT policy here naturally thinks in
    # refund%, not fee%. Storage stays {"days","fee_percentage"} - only this column's label and
    # the boundary conversion changed.
    _CB_REFUND_COL = "Refund % if cancelled by this deadline"

    def _cb_tier_table(tiers):
        table_rows = [{"Days before arrival (or more)": t.get("days"),
                       _CB_REFUND_COL: round(100.0 - _safe_float(t.get("fee_percentage"), fallback=0.0), 4)}
                     for t in (tiers or []) if isinstance(t, dict)]
        return pd.DataFrame(table_rows) if table_rows else pd.DataFrame(
            columns=["Days before arrival (or more)", _CB_REFUND_COL])

    def _cb_df_to_tiers(edited_df):
        new_tiers = []
        for _, row in edited_df.iterrows():
            days_val = row.get("Days before arrival (or more)")
            if days_val is None or (isinstance(days_val, float) and pd.isna(days_val)):
                continue
            refund_pct = max(0.0, min(100.0, _safe_float(row.get(_CB_REFUND_COL), fallback=100.0)))
            new_tiers.append({
                "days": _safe_int(days_val, fallback=0),
                "fee_percentage": max(0.0, min(100.0, 100.0 - refund_pct)),
            })
        return new_tiers

    def _cb_save_new_tiers(edited_df):
        st.session_state.cb_new_tiers = _cb_df_to_tiers(edited_df)

    cb_col_config = {
        "Days before arrival (or more)": st.column_config.NumberColumn(min_value=0, step=1),
        _CB_REFUND_COL: st.column_config.NumberColumn(min_value=0, max_value=100, step=1),
    }
    editable_table("New policy", _cb_tier_table(st.session_state.cb_new_tiers), "cb_new_policy",
                   on_save=_cb_save_new_tiers, column_config=cb_col_config)

    def _cb_fmt_tiers(tiers):
        if not tiers:
            return "(system default — 30 days, 100% refund)"
        return "; ".join(f"{t['days']}+ days: {100.0 - t['fee_percentage']:.0f}% refund"
                         for t in sorted(tiers, key=lambda t: t["days"], reverse=True))

    proposals = cancellation_bulk.build_proposals(rows, st.session_state.cb_new_tiers, product_type)
    proposals_by_id = {p["id"]: p for p in proposals}

    st.subheader("3 — Review and choose which to update")
    bcol1, bcol2 = st.columns(2)
    with bcol1:
        if st.button("Select all", key="cb_select_all"):
            st.session_state.cb_selected = {p["id"]: True for p in proposals}
            # CONFIRMED BUG FIX (product owner, 2026-09-10): same widget-key-ignores-value=
            # issue as everywhere else this pattern is used - see widget_state.py's docstring.
            for p in proposals:
                st.session_state[f"cb_pick_{p['id']}"] = True
            st.rerun()
    with bcol2:
        if st.button("Select none", key="cb_select_none"):
            st.session_state.cb_selected = {p["id"]: False for p in proposals}
            for p in proposals:
                st.session_state[f"cb_pick_{p['id']}"] = False
            st.rerun()

    for p in proposals:
        label = f"**{p['name']}**  ·  id `{p['id']}`"
        if p["unchanged"]:
            st.session_state.cb_selected[p["id"]] = False
            st.checkbox(f"{label}  ·  ✅ already matches — nothing to do", value=False, disabled=True,
                       key=f"cb_pick_{p['id']}")
        elif p.get("existing_stricter"):
            # CONFIRMED REAL RULE (product owner, 2026-09-11): never overwrite a live policy
            # that's already at least as strict as the new one - see
            # builder.existing_cancellation_at_least_as_strict's own docstring. For ClosedTour/
            # Ticket this compares the real structured field; for Transfer/Hotel (no structured
            # field at all) it compares tiers PARSED from the current voucher text - only when
            # that parse succeeded, see the existing_unparseable branch below for when it can't.
            st.session_state.cb_selected[p["id"]] = False
            st.checkbox(f"{label}  ·  🛡️ existing policy is already at least as strict — left alone",
                       value=False, disabled=True, key=f"cb_pick_{p['id']}")
        elif p.get("existing_unparseable"):
            # Transfer/Hotel only: the current voucher text states SOME policy but doesn't match
            # a shape this app's own text synthesizer could have written (a supplier's own
            # wording, or a hand-edited sentence) - builder.parse_cancellation_tiers_from_
            # voucher_text can't read it back into tiers, so we genuinely don't know whether it's
            # stricter or not. Left UNCHECKED by default (never silently overwritten) but NOT
            # disabled - a human who reads the current text in the expander below and decides
            # it's safe to replace can still tick it.
            st.session_state.cb_selected[p["id"]] = st.checkbox(
                f"{label}  ·  ⚠️ current policy text couldn't be read automatically — check the "
                f"Details below before including this one",
                value=st.session_state.cb_selected.get(p["id"], False), key=f"cb_pick_{p['id']}")
        else:
            st.session_state.cb_selected[p["id"]] = st.checkbox(
                label, value=st.session_state.cb_selected.get(p["id"], True), key=f"cb_pick_{p['id']}")
        with st.expander("Details", expanded=False):
            dcol1, dcol2 = st.columns(2)
            with dcol1:
                st.caption("**Current**")
                if has_structured:
                    st.text(_cb_fmt_tiers(p["current_fee_tiers"]))
                st.caption(p["current_cancellation_snippet"] or "*(no cancellation text found)*")
            with dcol2:
                st.caption("**New**")
                if has_structured:
                    st.text(_cb_fmt_tiers(p["new_fee_tiers"]))
                st.caption(p["new_cancellation_text"])
            if not p["existing_paragraph_found"]:
                st.warning("⚠️ No existing cancellation sentence was found in this service's text — "
                          "a new one will be INSERTED rather than replacing one. Double-check the "
                          "result afterward inside Travel Compositor.")
            if p.get("existing_unparseable"):
                st.warning("⚠️ The current text above states SOME policy, but doesn't match a "
                          "wording this app itself would have written, so it can't be "
                          "automatically compared to the new policy for strictness. Read it "
                          "yourself - if it already requires equal or more notice than the new "
                          "policy for the same refund, leave this row unchecked.")

    selected_ids = [pid for pid, v in st.session_state.cb_selected.items() if v]
    st.caption(f"{len(selected_ids)} of {len(proposals)} selected.")
    if not selected_ids:
        return

    st.subheader("4 — Apply")
    field_note = ("both the structured cancellation field and the matching sentence in each "
                  "one's Voucher remarks" if has_structured else "the matching sentence in "
                  "each one's Voucher remarks")
    st.warning(f"⚠️ This will PUT (update) {len(selected_ids)} {product_type}(s) — {field_note}. "
              f"Everything else on each record is left exactly as it is.")
    cb_table_being_edited = bool(st.session_state.get("_editing_table_cb_new_policy"))
    if cb_table_being_edited:
        st.error("🚫 The New Policy table above has unsaved edits — click its own Save button "
                 "first, or this button would apply the OLD numbers while the screen shows new ones.")
    if st.button(f"🚀 Update {len(selected_ids)} {product_type}(s)", key="cb_confirm", type="primary",
                 disabled=cb_table_being_edited):
        to_apply = [proposals_by_id[pid] for pid in selected_ids]
        with st.spinner("Updating..."):
            results = cancellation_bulk.apply_proposals(client, supplier_id, product_type, to_apply)
        st.session_state.cb_results = results
        st.rerun()
