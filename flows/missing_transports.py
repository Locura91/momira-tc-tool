"""
Missing Transports — supplier-wide scan for one-way transports with no reverse-direction pair.

CONFIRMED PRODUCT-OWNER REQUEST (2026-09-22, verbatim): "when duplicating transfer, I can select
the supplier and then direct I can scan this supplier for missing transfers - that should be
exactly the same for transport. currently I can only dubilicate one transpport at the time, but
that is not practical."

Direct Transport counterpart to flows/missing_transfers.py - same workflow: pick a supplier ->
this screen scans that supplier's whole live Transport list and lists every route that has no
reverse-direction pair yet -> the human ticks which ones to fill in (Select all / Clear all, same
as missing_transfers.py) -> one "Create selected" action publishes them as a batch, with a
progress bar and a final created/failed summary.

Pairing rule and the two-resource (parent + Option) publish sequence live in
transfer_gap_finder.find_missing_reverse_transports/create_duplicate_transport - see those
functions' own docstrings for the full history, including the two confirmed production bugs
(stale-optionCodes null PK, and Travel Compositor's "must add at least one modality" rejection)
the create sequence exists to avoid, and why the scan itself needs a bounded, cached
api_client.resolve_transport_base lookup per distinct location code rather than one per transport.
"""
import streamlit as st

import transfer_gap_finder
from app_helpers import _ur_pick_momira_supplier

_PAGE_SIZE = 25

_RESET_KEYS = ("mtp_gaps", "mtp_supplier_id", "mtp_result", "mtp_page")


def _extract_transport_list(result):
    """Same shape transport_matcher's own resolvers already extract from client.get_transports()'s
    response - a plain list either way it comes back."""
    if isinstance(result, dict) and "error" in result:
        return None, result
    existing = result.get("transport", []) if isinstance(result, dict) else (result or [])
    return existing, None


def render_missing_transports_flow(client):
    """Standalone entry point - picks its own supplier, then renders the scan/select/batch-create
    body below. Kept for backward compatibility with this module's own test suite; the combined
    Step 1 menu entry (flows/transport_duplicate_and_create.py, 2026-09-22) instead picks ONE
    supplier shared with the manual duplicate-by-id flow and calls
    _render_missing_transports_body(client, supplier_id) directly, so the human never sees two
    separate supplier pickers on what is now one screen."""
    st.subheader("🔍 Missing Transports")
    st.caption(
        "Scans one supplier's whole live Transport list and flags every route with no exact "
        "reverse-direction pair yet (e.g. Airport → Hotel exists but Hotel → Airport doesn't). "
        "Pick which gaps to fill in and create them as a batch - each one is the same "
        "duplicate-and-swap this app already uses for a single Transport."
    )

    supplier_id = _ur_pick_momira_supplier(client, "mtp")
    if not supplier_id:
        return

    _render_missing_transports_body(client, supplier_id)


def _render_missing_transports_body(client, supplier_id):
    """Everything after the supplier is already known - split out so the combined Transport create
    screen can share ONE supplier pick with the manual duplicate-by-id flow instead of rendering
    two separate pickers. See render_missing_transports_flow's own docstring."""
    if st.button("🔍 Scan this supplier for missing transports", type="primary", key="mtp_scan"):
        with st.spinner(f"Reading every Transport for supplier {supplier_id}..."):
            result = client.get_transports(supplier_id)
        existing, fetch_error = _extract_transport_list(result)
        if fetch_error:
            st.error(f"❌ Couldn't read this supplier's transports: {fetch_error.get('message', fetch_error)}")
            return
        with st.spinner("Comparing routes..."):
            gaps = transfer_gap_finder.find_missing_reverse_transports(existing, client)
        st.session_state.mtp_gaps = [
            dict(g, accepted=False, index=i, widget_token=f"g{i}") for i, g in enumerate(gaps)
        ]
        st.session_state.mtp_supplier_id = supplier_id
        st.session_state.mtp_page = 0
        st.session_state.pop("mtp_result", None)
        st.rerun()

    gaps = st.session_state.get("mtp_gaps")
    if gaps is None:
        return

    scanned_supplier_id = st.session_state.get("mtp_supplier_id", supplier_id)

    if not gaps:
        st.success(
            f"✅ No gaps found for supplier {scanned_supplier_id} - every live Transport's "
            "reverse direction already exists.")
        if st.button("🆕 Scan again", key="mtp_new_empty"):
            for k in _RESET_KEYS:
                st.session_state.pop(k, None)
            st.rerun()
        return

    st.info(f"Found **{len(gaps)}** Transport(s) for supplier {scanned_supplier_id} with no "
           "reverse-direction pair yet.")

    acol1, acol2 = st.columns([1, 4])
    with acol1:
        if st.button("✅ Select all", key="mtp_select_all", use_container_width=True):
            for g in gaps:
                g["accepted"] = True
            st.rerun()
    with acol2:
        if st.button("Clear all", key="mtp_clear_all"):
            for g in gaps:
                g["accepted"] = False
            st.rerun()

    total_pages = max(1, (len(gaps) - 1) // _PAGE_SIZE + 1)
    page = min(st.session_state.get("mtp_page", 0), total_pages - 1)

    pcol1, pcol2, pcol3 = st.columns([1, 3, 1])
    with pcol1:
        if st.button("◀ Prev", key="mtp_prev", disabled=page <= 0):
            st.session_state.mtp_page = page - 1
            st.rerun()
    with pcol2:
        st.caption(f"Page {page + 1} of {total_pages} — {len(gaps)} gap(s) total, "
                  f"{_PAGE_SIZE} shown per page")
    with pcol3:
        if st.button("Next ▶", key="mtp_next", disabled=page >= total_pages - 1):
            st.session_state.mtp_page = page + 1
            st.rerun()

    start = page * _PAGE_SIZE
    for g in gaps[start:start + _PAGE_SIZE]:
        source = g["source"]
        cols = st.columns([1, 6])
        with cols[0]:
            # Token, not just the index - same reason as flows/missing_transfers.py's own
            # widget-key convention: a stale checkbox state must never bleed into a different gap
            # on a later scan.
            g["accepted"] = st.checkbox(
                "Yes", value=g["accepted"], key=f"mtp_ok_{g['index']}_{g['widget_token']}")
        with cols[1]:
            type_bit = f" · {g['transport_type']}" if g.get("transport_type") else ""
            id_bit = f" ({source.get('id')})" if source.get("id") else ""
            st.markdown(
                f"**{source.get('name') or '(unnamed)'}**{id_bit} "
                f"— missing: **{g['missing_from_name']} → {g['missing_to_name']}**{type_bit}")

    st.markdown("---")
    accepted = [g for g in gaps if g["accepted"]]
    st.warning(
        f"This will CREATE **{len(accepted)}** new Transport(s) for supplier "
        f"{scanned_supplier_id} - one per selected gap, each a duplicate of its source Transport "
        "(parent record and every occupancy bracket) with the route swapped, same as the "
        "single-transport duplicate flow.")

    if st.button(f"🚀 Create {len(accepted)} transport(s)", type="primary",
                 disabled=not accepted, key="mtp_apply"):
        bar = st.progress(0.0, text="Creating…")
        created, partial, failed = [], [], []
        total = len(accepted)
        for i, g in enumerate(accepted):
            source = g["source"]
            label = source.get("name") or f"{g['missing_from_name']} → {g['missing_to_name']}"
            bar.progress(min(i / max(total, 1), 1.0), text=f"Creating {label} ({i}/{total})")
            outcome = transfer_gap_finder.create_duplicate_transport(client, scanned_supplier_id, source)
            if outcome["status"] == "created":
                created.append(outcome)
            elif outcome["status"] in ("partial", "created_unlinked"):
                partial.append(outcome)
            else:
                failed.append(outcome)
        bar.progress(1.0, text="Done")
        bar.empty()
        st.session_state.mtp_result = {"created": created, "partial": partial, "failed": failed}
        st.rerun()

    result = st.session_state.get("mtp_result")
    if result:
        if result["created"]:
            st.success(f"✅ {len(result['created'])} transport(s) created.")
            for c in result["created"]:
                st.write(f"- {c['name']}: {c['route']}"
                        + (f" (new id: {c['new_id']})" if c.get("new_id") else ""))
        if result["partial"]:
            st.warning(f"⚠️ {len(result['partial'])} transport(s) published with issues - check "
                      "Travel Compositor directly:")
            for p in result["partial"]:
                st.write(f"- **{p['name']}** (new id: {p.get('new_id') or '?'}): {p['detail']}")
        if result["failed"]:
            st.error(f"❌ {len(result['failed'])} failed:")
            for f in result["failed"]:
                st.write(f"- **{f['name']}**: {f['detail']}")
        if st.button("🆕 Scan again", key="mtp_new"):
            for k in _RESET_KEYS:
                st.session_state.pop(k, None)
            st.rerun()
