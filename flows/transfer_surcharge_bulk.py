"""
Bulk-update transfer surcharge times — change startTime, endTime, and reset
startDate to today across all (or selected) transfers for a supplier.

CONFIRMED USE CASE: supplier has N surcharges whose time window needs shifting
(e.g. night surcharge 22:00-06:00 → 20:00-08:00) and startDate must be reset to
today (TC API requires it).
"""
import streamlit as st
from datetime import date


def render_transfer_surcharge_bulk_flow(client):
    st.header("Bulk-update Transfer surcharge times")

    supplier_id = st.text_input("Supplier ID", key="tsb_supplier_id",
                                help="e.g. 50421")
    if not supplier_id:
        st.info("Enter a supplier ID to start.")
        return

    # ── Fetch all transfers ──────────────────────────────────────────
    if st.button("Load transfers", key="tsb_load"):
        with st.spinner("Fetching transfers…"):
            try:
                resp = client.get_transfers(supplier_id)
            except Exception as e:
                st.error(f"Failed to fetch transfers: {e}")
                return
            transfers = resp.get("transfer") or []
            if not transfers:
                st.warning("No transfers found for this supplier.")
                return

            # Re-fetch each transfer individually to get full supplements
            full_transfers = []
            progress = st.progress(0, text="Loading full transfer details…")
            for i, t in enumerate(transfers):
                tid = t.get("id", "")
                try:
                    full = client.get_transfer(supplier_id, tid)
                    full_transfers.append(full)
                except Exception:
                    full_transfers.append(t)
                progress.progress((i + 1) / len(transfers))
            progress.empty()

            st.session_state.tsb_transfers = full_transfers
        st.rerun()

    transfers = st.session_state.get("tsb_transfers")
    if not transfers:
        return

    # ── Collect surcharges across all transfers ──────────────────────
    surcharge_rows = []
    for t in transfers:
        tid = t.get("id", "")
        tname = t.get("name") or t.get("routeName") or tid
        supplements = t.get("supplements") or []
        for idx, s in enumerate(supplements):
            surcharge_rows.append({
                "transfer_id": tid,
                "transfer_name": tname,
                "supp_idx": idx,
                "name": s.get("name", ""),
                "type": s.get("type", ""),
                "amount": s.get("amount", 0),
                "startTime": s.get("startTime") or "",
                "endTime": s.get("endTime") or "",
                "startDate": s.get("startDate") or "",
                "endDate": s.get("endDate") or "",
                "active": s.get("active", True),
            })

    if not surcharge_rows:
        st.warning(f"None of the {len(transfers)} transfers have any surcharges/supplements.")
        return

    st.success(f"Found **{len(surcharge_rows)}** surcharge(s) across **{len(transfers)}** transfers.")

    # ── Filter scope ─────────────────────────────────────────────────
    unique_names = sorted({r["name"] for r in surcharge_rows if r["name"]})
    scope = st.radio("Which surcharges to update?",
                     ["All surcharges", "Only surcharges with a specific name"],
                     key="tsb_scope")

    selected_rows = surcharge_rows
    if scope == "Only surcharges with a specific name" and unique_names:
        pick = st.multiselect("Surcharge name(s)", unique_names, key="tsb_name_filter")
        if pick:
            selected_rows = [r for r in surcharge_rows if r["name"] in pick]
        else:
            st.info("Pick at least one surcharge name.")
            return

    st.info(f"**{len(selected_rows)}** surcharge(s) will be updated.")

    # ── Show current values ──────────────────────────────────────────
    with st.expander(f"Preview ({len(selected_rows)} surcharges)", expanded=False):
        for r in selected_rows:
            st.text(f"{r['transfer_name']}  |  {r['name']}  |  "
                    f"time: {r['startTime']}-{r['endTime']}  |  "
                    f"dates: {r['startDate']} → {r['endDate']}  |  "
                    f"{r['type']} {r['amount']}")

    # ── New values ───────────────────────────────────────────────────
    st.subheader("New values")
    col1, col2 = st.columns(2)
    with col1:
        new_start_time = st.text_input("New startTime (HH:MM)", value="",
                                       key="tsb_new_start_time",
                                       help="e.g. 20:00")
    with col2:
        new_end_time = st.text_input("New endTime (HH:MM)", value="",
                                     key="tsb_new_end_time",
                                     help="e.g. 08:00")

    today_str = date.today().isoformat()
    st.caption(f"startDate will be set to **{today_str}** (today) on all updated surcharges.")

    if not new_start_time or not new_end_time:
        st.warning("Enter both startTime and endTime to proceed.")
        return

    # Basic HH:MM validation
    import re
    time_pat = re.compile(r"^\d{2}:\d{2}$")
    if not time_pat.match(new_start_time) or not time_pat.match(new_end_time):
        st.error("Times must be in HH:MM format (e.g. 22:00, 08:00).")
        return

    # ── Apply ────────────────────────────────────────────────────────
    st.divider()
    if not st.button(f"Apply to {len(selected_rows)} surcharge(s)", key="tsb_apply",
                     type="primary"):
        return

    # Group selected rows by transfer_id so we PUT each transfer once
    affected_transfers = {}
    for r in selected_rows:
        affected_transfers.setdefault(r["transfer_id"], []).append(r["supp_idx"])

    progress = st.progress(0, text="Updating transfers…")
    ok_count = 0
    err_count = 0
    errors = []

    transfer_map = {t.get("id"): t for t in transfers}

    for i, (tid, supp_indices) in enumerate(affected_transfers.items()):
        t = transfer_map.get(tid)
        if not t:
            err_count += 1
            errors.append(f"{tid}: not found in loaded data")
            continue

        # Modify supplements in-place on a copy
        import copy
        payload = copy.deepcopy(t)
        supplements = payload.get("supplements") or []
        for idx in supp_indices:
            if idx < len(supplements):
                supplements[idx]["startTime"] = new_start_time
                supplements[idx]["endTime"] = new_end_time
                supplements[idx]["startDate"] = today_str

        try:
            client.update_transfer(supplier_id, payload)
            ok_count += 1
        except Exception as e:
            err_count += 1
            errors.append(f"{tid}: {e}")

        progress.progress((i + 1) / len(affected_transfers))

    progress.empty()

    if ok_count:
        st.success(f"Updated **{ok_count}** transfer(s) ({len(selected_rows)} surcharges).")
    if err_count:
        st.error(f"**{err_count}** transfer(s) failed:")
        for err in errors:
            st.text(err)
