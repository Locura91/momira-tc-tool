"""
Missing Transfers — supplier-wide scan for one-way transfers with no reverse-direction pair.

CONFIRMED PRODUCT-OWNER REQUEST (2026-09-16), the natural next step once the single-transfer
duplicate-and-swap flow (flows/duplicate_transfer.py) was proven out and simplified:

    "Goal with the duplicate must be, that humans create one way transfers, then the app must
    be controlled by human and human adds the supplier as usually, the app checks is there are
    missing transfers and then provides a list with all possible missing transfers. The style
    can be similar to the bulk price transfer update."

So the intended workflow: a human enters a one-way transfer manually as usual (unchanged,
outside this app) -> picks a supplier here -> this screen scans that supplier's whole live
transfer list and lists every route that has no reverse-direction pair yet -> the human ticks
which ones to fill in (like flows/price_refresh.py's accept/clear-all + per-row checkboxes) ->
one "Create selected" action publishes them as a batch, with a progress bar and a final
created/failed summary - never a single all-or-nothing click.

CONFIRMED PRODUCT-OWNER FOLLOW-UP (same day, clarifying round-trip):
  - Pairing rule: "transfer flipped, it can not be looser - as it works right now is perfect.
    The route can not be touched, just swapped." -> see transfer_gap_finder.find_missing_reverse_transfers
    for the exact (no fuzzy matching) pairing logic.
  - "Yes, please flag possible missing transfers." -> every one-way transfer without an exact
    reverse counterpart is flagged, no threshold/filtering.
  - Batch style: "List + select multiple, publish as a batch" - checkboxes plus one batch
    "Create selected" action with a progress bar and a summary, not one-at-a-time confirmation.
  - Paging: "Paginated table, e.g. 25-50 rows per page" - a supplier's list can run into the
    hundreds, so this screen never renders more than one page's worth of rows at once.
  - Token/cost safety ("My biggest fear is, that we start creating in bulk new transfers and
    then the time/power/token from AI is gone and we miss all the process until then"): the SCAN
    itself is pure Python (a route-name comparison across the already-fetched list) - no AI call
    at all, so it costs nothing and can't run away no matter how many transfers a supplier has.
    AI only gets used once a human explicitly selects rows and clicks Create - one call per
    selected transfer, only for the rare description the literal swap can't confidently handle,
    watched via the same progress bar that tracks the whole batch (so nothing runs unattended or
    unaccounted-for) - identical AI usage to the single-transfer duplicate flow, just applied to
    more than one row per click. See transfer_gap_finder.build_and_rewrite_transfer_swap_payload,
    shared by both flows so the AI-rewrite behavior can never drift between them.
"""
import streamlit as st

import transfer_gap_finder
from app_helpers import _ur_pick_momira_supplier, show_publish_error

_PAGE_SIZE = 25

_RESET_KEYS = ("mtf_gaps", "mtf_supplier_id", "mtf_result", "mtf_page")


def _extract_transfer_list(result):
    """Same shape transfer_matcher.resolve_transfer_match already extracts from
    client.get_transfers()'s response - a plain list either way it comes back."""
    if isinstance(result, dict) and "error" in result:
        return None, result
    existing = result.get("transfer", []) if isinstance(result, dict) else (result or [])
    return existing, None


def render_missing_transfers_flow(client):
    """Standalone entry point - picks its own supplier, then renders the scan/select/batch-
    create body below. Kept for backward compatibility with this module's own test suite; the
    combined Step 1 menu entry (flows/transfer_duplicate_and_create.py, 2026-09-17) instead picks
    ONE supplier shared with the manual duplicate-by-id flow and calls
    _render_missing_transfers_body(client, supplier_id) directly, so the human never sees two
    separate supplier pickers on what is now one screen."""
    st.subheader("🔍 Missing Transfers")
    st.caption(
        "Scans one supplier's whole live Transfer list and flags every route with no exact "
        "reverse-direction pair yet (e.g. Hotel → Airport exists but Airport → Hotel doesn't). "
        "Pick which gaps to fill in and create them as a batch - each one is the same "
        "duplicate-and-swap this app already uses for a single Transfer."
    )

    supplier_id = _ur_pick_momira_supplier(client, "mtf")
    if not supplier_id:
        return

    _render_missing_transfers_body(client, supplier_id)


def _render_missing_transfers_body(client, supplier_id):
    """Everything after the supplier is already known - split out (2026-09-17) so the combined
    Transfer create screen can share ONE supplier pick with the manual duplicate-by-id flow
    instead of rendering two separate pickers. See render_missing_transfers_flow's own docstring."""
    if st.button("🔍 Scan this supplier for missing transfers", type="primary", key="mtf_scan"):
        with st.spinner(f"Reading every Transfer for supplier {supplier_id}..."):
            result = client.get_transfers(supplier_id)
        existing, fetch_error = _extract_transfer_list(result)
        if fetch_error:
            st.error(f"❌ Couldn't read this supplier's transfers: {fetch_error.get('message', fetch_error)}")
            return
        gaps = transfer_gap_finder.find_missing_reverse_transfers(existing)
        st.session_state.mtf_gaps = [
            dict(g, accepted=False, index=i, widget_token=f"g{i}") for i, g in enumerate(gaps)
        ]
        st.session_state.mtf_supplier_id = supplier_id
        st.session_state.mtf_page = 0
        st.session_state.pop("mtf_result", None)
        st.rerun()

    gaps = st.session_state.get("mtf_gaps")
    if gaps is None:
        return

    scanned_supplier_id = st.session_state.get("mtf_supplier_id", supplier_id)

    if not gaps:
        st.success(
            f"✅ No gaps found for supplier {scanned_supplier_id} - every live Transfer's "
            "reverse direction already exists.")
        if st.button("🆕 Scan again", key="mtf_new_empty"):
            for k in _RESET_KEYS:
                st.session_state.pop(k, None)
            st.rerun()
        return

    st.info(f"Found **{len(gaps)}** Transfer(s) for supplier {scanned_supplier_id} with no "
           "reverse-direction pair yet.")

    acol1, acol2 = st.columns([1, 4])
    with acol1:
        if st.button("✅ Select all", key="mtf_select_all", use_container_width=True):
            for g in gaps:
                g["accepted"] = True
            st.rerun()
    with acol2:
        if st.button("Clear all", key="mtf_clear_all"):
            for g in gaps:
                g["accepted"] = False
            st.rerun()

    total_pages = max(1, (len(gaps) - 1) // _PAGE_SIZE + 1)
    page = min(st.session_state.get("mtf_page", 0), total_pages - 1)

    pcol1, pcol2, pcol3 = st.columns([1, 3, 1])
    with pcol1:
        if st.button("◀ Prev", key="mtf_prev", disabled=page <= 0):
            st.session_state.mtf_page = page - 1
            st.rerun()
    with pcol2:
        st.caption(f"Page {page + 1} of {total_pages} — {len(gaps)} gap(s) total, "
                  f"{_PAGE_SIZE} shown per page")
    with pcol3:
        if st.button("Next ▶", key="mtf_next", disabled=page >= total_pages - 1):
            st.session_state.mtf_page = page + 1
            st.rerun()

    start = page * _PAGE_SIZE
    for g in gaps[start:start + _PAGE_SIZE]:
        source = g["source"]
        cols = st.columns([1, 6])
        with cols[0]:
            # Token, not just the index - same reason as price_refresh.py's own widget-key
            # convention: a stale checkbox state must never bleed into a different gap on a
            # later scan.
            g["accepted"] = st.checkbox(
                "Yes", value=g["accepted"], key=f"mtf_ok_{g['index']}_{g['widget_token']}")
        with cols[1]:
            vehicle_bit = f" · {g['vehicle_type']}" if g.get("vehicle_type") else ""
            id_bit = f" ({source.get('id')})" if source.get("id") else ""
            st.markdown(
                f"**{source.get('name') or '(unnamed)'}**{id_bit} "
                f"— missing: **{g['missing_from_name']} → {g['missing_to_name']}**{vehicle_bit}")

    st.markdown("---")
    accepted = [g for g in gaps if g["accepted"]]
    st.warning(
        f"This will CREATE **{len(accepted)}** new Transfer(s) for supplier "
        f"{scanned_supplier_id} - one per selected gap, each a duplicate of its source Transfer "
        "with the route swapped (AI rewrite used automatically only where the literal swap "
        "can't confidently handle the description, same as the single-transfer duplicate flow).")

    if st.button(f"🚀 Create {len(accepted)} transfer(s)", type="primary",
                 disabled=not accepted, key="mtf_apply"):
        bar = st.progress(0.0, text="Creating…")
        created, failed = [], []
        total = len(accepted)
        for i, g in enumerate(accepted):
            source = g["source"]
            label = source.get("name") or f"{g['missing_from_name']} → {g['missing_to_name']}"
            bar.progress(min(i / max(total, 1), 1.0), text=f"Creating {label} ({i}/{total})")
            try:
                payload, swap_report, route_info = transfer_gap_finder.build_and_rewrite_transfer_swap_payload(source)
                resp = client.create_transfer(scanned_supplier_id, payload)
                if isinstance(resp, dict) and "error" in resp:
                    failed.append({"name": label, "detail": resp.get("message", resp)})
                else:
                    created.append({
                        "name": label,
                        "new_id": resp.get("id") if isinstance(resp, dict) else None,
                        "route": f"{route_info.get('new_departure_name', '?')} → "
                                f"{route_info.get('new_arrival_name', '?')}",
                    })
            except Exception as e:
                failed.append({"name": label, "detail": str(e)})
        bar.progress(1.0, text="Done")
        bar.empty()
        st.session_state.mtf_result = {"created": created, "failed": failed}
        st.rerun()

    result = st.session_state.get("mtf_result")
    if result:
        if result["created"]:
            st.success(f"✅ {len(result['created'])} transfer(s) created.")
            for c in result["created"]:
                st.write(f"- {c['name']}: {c['route']}"
                        + (f" (new id: {c['new_id']})" if c.get("new_id") else ""))
        if result["failed"]:
            st.error(f"❌ {len(result['failed'])} failed:")
            for f in result["failed"]:
                st.write(f"- **{f['name']}**: {f['detail']}")
        if st.button("🆕 Scan again", key="mtf_new"):
            for k in _RESET_KEYS:
                st.session_state.pop(k, None)
            st.rerun()
