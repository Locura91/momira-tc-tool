"""
Bulk transfer supplement tool — three modes:

1. UPDATE existing supplements: change startTime/endTime (and reset startDate to
   today) across all or filtered supplements for a supplier.
2. CREATE a new supplement: append a brand-new supplement to every transfer (or
   selected transfers) for a supplier.
3. DELETE all supplements: wipe every supplement from every transfer for a
   supplier (useful before re-adding supplements from scratch).

All three use PUT /transfer/{supplierId} which sends the full transfer including
its supplements array.
"""
import copy
import re
import streamlit as st
from datetime import date

from app_helpers import _ur_pick_momira_supplier

_TIME_PAT = re.compile(r"^\d{2}:\d{2}$")


def _load_transfers(client, supplier_id):
    """Fetch all transfers for a supplier, re-fetching each individually for
    full supplement data."""
    try:
        resp = client.get_transfers(supplier_id)
    except Exception as e:
        st.error(f"Failed to fetch transfers: {e}")
        return None
    transfers = resp.get("transfer") or []
    if not transfers:
        st.warning("No transfers found for this supplier.")
        return None

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
    return full_transfers


def _collect_supplement_rows(transfers):
    """Flatten all supplements across all transfers into a list of dicts."""
    rows = []
    for t in transfers:
        tid = t.get("id", "")
        tname = t.get("name") or t.get("routeName") or tid
        for idx, s in enumerate(t.get("supplements") or []):
            rows.append({
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
    return rows


def _render_update_mode(transfers):
    """UI for updating existing supplements."""
    rows = _collect_supplement_rows(transfers)

    if not rows:
        st.warning(f"None of the {len(transfers)} transfers have any supplements.")
        return None

    st.success(f"Found **{len(rows)}** supplement(s) across **{len(transfers)}** transfers.")

    unique_names = sorted({r["name"] for r in rows if r["name"]})
    scope = st.radio("Which supplements to update?",
                     ["All supplements", "Only supplements with a specific name"],
                     key="tsb_scope")

    selected_rows = rows
    if scope == "Only supplements with a specific name" and unique_names:
        pick = st.multiselect("Supplement name(s)", unique_names, key="tsb_name_filter")
        if pick:
            selected_rows = [r for r in rows if r["name"] in pick]
        else:
            st.info("Pick at least one supplement name.")
            return None

    st.info(f"**{len(selected_rows)}** supplement(s) will be updated.")

    with st.expander(f"Preview ({len(selected_rows)} supplements)", expanded=False):
        for r in selected_rows:
            st.text(f"{r['transfer_name']}  |  {r['name']}  |  "
                    f"time: {r['startTime']}-{r['endTime']}  |  "
                    f"dates: {r['startDate']} → {r['endDate']}  |  "
                    f"{r['type']} {r['amount']}")

    st.subheader("New values")
    col1, col2 = st.columns(2)
    with col1:
        new_start_time = st.text_input("New startTime (HH:MM)", value="",
                                       key="tsb_new_start_time", help="e.g. 20:00")
    with col2:
        new_end_time = st.text_input("New endTime (HH:MM)", value="",
                                     key="tsb_new_end_time", help="e.g. 08:00")

    today_str = date.today().isoformat()
    st.caption(f"startDate will be set to **{today_str}** (today) on all updated supplements.")

    if not new_start_time or not new_end_time:
        st.warning("Enter both startTime and endTime to proceed.")
        return None
    if not _TIME_PAT.match(new_start_time) or not _TIME_PAT.match(new_end_time):
        st.error("Times must be in HH:MM format (e.g. 22:00, 08:00).")
        return None

    affected = {}
    for r in selected_rows:
        affected.setdefault(r["transfer_id"], []).append(r["supp_idx"])

    return ("update", affected, new_start_time, new_end_time, len(selected_rows))


def _render_create_mode(transfers):
    """UI for creating a new supplement on transfers."""
    st.subheader("New supplement details")

    name = st.text_input("Supplement name", key="tsb_c_name",
                         help="e.g. Night Supplement, Weekend Supplement")
    if not name:
        st.warning("Enter a supplement name.")
        return None

    supp_type = st.selectbox("Type", ["ABSOLUTE", "PERCENT"], key="tsb_c_type",
                             help="ABSOLUTE = fixed amount, PERCENT = percentage of base price")
    amount = st.number_input("Amount", min_value=0.0, step=0.5, key="tsb_c_amount",
                             help="e.g. 15.0 for €15 (ABSOLUTE) or 50.0 for 50% (PERCENT)")

    st.markdown("**Time window** (leave empty if the supplement has no time condition)")
    tc1, tc2 = st.columns(2)
    with tc1:
        start_time = st.text_input("startTime (HH:MM)", value="", key="tsb_c_start_time",
                                   help="e.g. 22:00")
    with tc2:
        end_time = st.text_input("endTime (HH:MM)", value="", key="tsb_c_end_time",
                                 help="e.g. 06:00")

    if start_time and not _TIME_PAT.match(start_time):
        st.error("startTime must be HH:MM format.")
        return None
    if end_time and not _TIME_PAT.match(end_time):
        st.error("endTime must be HH:MM format.")
        return None

    st.markdown("**Date validity**")
    today = date.today()
    dc1, dc2 = st.columns(2)
    with dc1:
        start_date = st.date_input("startDate", value=today, key="tsb_c_start_date")
    with dc2:
        end_date = st.date_input("endDate", value=date(today.year + 1, today.month, today.day),
                                 key="tsb_c_end_date")

    st.divider()
    transfer_scope = st.radio("Add to which transfers?",
                              ["All transfers", "Select specific transfers"],
                              key="tsb_c_scope")

    selected_tids = None
    if transfer_scope == "Select specific transfers":
        options = {t.get("id", ""): (t.get("name") or t.get("routeName") or t.get("id", ""))
                   for t in transfers}
        picks = st.multiselect(
            "Select transfers",
            list(options.keys()),
            format_func=lambda tid: f"{options[tid]} ({tid})",
            key="tsb_c_transfer_pick")
        if not picks:
            st.info("Select at least one transfer.")
            return None
        selected_tids = set(picks)

    new_supp = {
        "active": True,
        "name": name,
        "type": supp_type,
        "amount": amount,
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
    }
    if start_time:
        new_supp["startTime"] = start_time
    if end_time:
        new_supp["endTime"] = end_time

    target_count = len(selected_tids) if selected_tids else len(transfers)

    with st.expander("Preview new supplement"):
        for k, v in new_supp.items():
            st.text(f"  {k}: {v}")
        st.caption(f"Will be added to **{target_count}** transfer(s).")

    return ("create", new_supp, selected_tids, target_count)


def _render_delete_mode(transfers):
    """UI for deleting all supplements from every transfer."""
    rows = _collect_supplement_rows(transfers)
    transfers_with_supps = len({r["transfer_id"] for r in rows})

    if not rows:
        st.warning(f"None of the {len(transfers)} transfers have any supplements — nothing to delete.")
        return None

    st.warning(
        f"This will **delete all {len(rows)} supplement(s)** from "
        f"**{transfers_with_supps}** transfer(s). "
        f"The remaining {len(transfers) - transfers_with_supps} transfer(s) have no supplements and will not be touched."
    )

    with st.expander(f"Show all supplements to be deleted ({len(rows)})", expanded=False):
        for r in rows:
            st.text(f"{r['transfer_name']}  |  {r['name']}  |  "
                    f"time: {r['startTime']}-{r['endTime']}  |  "
                    f"{r['type']} {r['amount']}")

    confirm = st.checkbox(
        "Yes, I want to delete all supplements from all transfers for this supplier",
        key="tsb_d_confirm")
    if not confirm:
        return None

    return ("delete", transfers_with_supps, len(rows))


def render_transfer_surcharge_bulk_flow(client):
    st.header("Bulk Transfer supplements")

    supplier_id = _ur_pick_momira_supplier(client, key_prefix="tsb")
    if not supplier_id:
        return

    # Reset loaded data when supplier changes
    if st.session_state.get("tsb_last_supplier_id") != supplier_id:
        st.session_state.tsb_last_supplier_id = supplier_id
        st.session_state.tsb_transfers = None

    if st.button("Load transfers", key="tsb_load"):
        with st.spinner("Fetching transfers…"):
            result = _load_transfers(client, supplier_id)
            if result:
                st.session_state.tsb_transfers = result
        st.rerun()

    transfers = st.session_state.get("tsb_transfers")
    if not transfers:
        return

    st.info(f"**{len(transfers)}** transfers loaded.")

    mode = st.radio("What do you want to do?",
                    ["Update existing supplement times",
                     "Create a new supplement on transfers",
                     "Delete all supplements"],
                    key="tsb_mode")

    st.divider()

    if mode == "Update existing supplement times":
        result = _render_update_mode(transfers)
    elif mode == "Create a new supplement on transfers":
        result = _render_create_mode(transfers)
    else:
        result = _render_delete_mode(transfers)

    if not result:
        return

    # ── Apply button ─────────────────────────────────────────────────
    st.divider()
    if result[0] == "update":
        _, affected, new_start_time, new_end_time, supp_count = result
        target_count = len(affected)
        label = f"Update {supp_count} supplement(s) on {target_count} transfer(s)"
    elif result[0] == "create":
        _, new_supp, selected_tids, target_count = result
        label = f"Add supplement to {target_count} transfer(s)"
    else:
        _, transfers_with_supps, total_supps = result
        label = f"⚠️ Delete all {total_supps} supplement(s) from {transfers_with_supps} transfer(s)"

    if not st.button(label, key="tsb_apply", type="primary"):
        return

    # ── Execute ──────────────────────────────────────────────────────
    today_str = date.today().isoformat()
    transfer_map = {t.get("id"): t for t in transfers}
    progress = st.progress(0, text="Applying…")
    ok_count = 0
    err_count = 0
    errors = []

    if result[0] == "update":
        items = list(affected.items())
        for i, (tid, supp_indices) in enumerate(items):
            t = transfer_map.get(tid)
            if not t:
                err_count += 1
                errors.append(f"{tid}: not found")
                continue
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
            progress.progress((i + 1) / len(items))

    elif result[0] == "create":
        targets = [t for t in transfers
                   if selected_tids is None or t.get("id") in selected_tids]
        for i, t in enumerate(targets):
            tid = t.get("id", "")
            payload = copy.deepcopy(t)
            supplements = payload.get("supplements") or []
            supplements.append(copy.deepcopy(new_supp))
            payload["supplements"] = supplements
            try:
                client.update_transfer(supplier_id, payload)
                ok_count += 1
            except Exception as e:
                err_count += 1
                errors.append(f"{tid}: {e}")
            progress.progress((i + 1) / len(targets))

    else:  # delete
        targets = [t for t in transfers if t.get("supplements")]
        for i, t in enumerate(targets):
            tid = t.get("id", "")
            payload = copy.deepcopy(t)
            payload["supplements"] = []
            try:
                client.update_transfer(supplier_id, payload)
                ok_count += 1
            except Exception as e:
                err_count += 1
                errors.append(f"{tid}: {e}")
            progress.progress((i + 1) / len(targets))

    progress.empty()

    if ok_count:
        st.success(f"Done — **{ok_count}** transfer(s) updated successfully.")
    if err_count:
        st.error(f"**{err_count}** transfer(s) failed:")
        for err in errors:
            st.text(err)
