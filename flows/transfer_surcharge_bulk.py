"""
Bulk transfer supplement tool — two modes:

1. CREATE new supplements: append one or more brand-new supplements to every
   transfer (or selected transfers) for a supplier. Multiple supplements can
   be defined in a single apply (e.g. Chinese New Year + Christmas + Tet).
2. DELETE all supplements: wipe every supplement from every transfer for a
   supplier (useful before re-adding supplements from scratch).

Both use PUT /transfer/{supplierId} which sends the full transfer including
its supplements array.
"""
import re
import streamlit as st
from datetime import date

import supplement_apply
from app_helpers import _ur_pick_momira_supplier

_TIME_PAT = re.compile(r"^\d{2}:\d{2}$")

MODULE_BUILD = "2026-11-27-multi-supplement"


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


def _render_one_supplement(idx: int) -> dict | None:
    """Render fields for a single supplement entry. Returns the supplement dict
    or None if validation fails."""
    today = date.today()

    cols_top = st.columns([3, 2, 2])
    with cols_top[0]:
        name = st.text_input("Supplement name", key=f"tsb_name_{idx}",
                             placeholder="e.g. Chinese New Year, Christmas Eve")
    with cols_top[1]:
        supp_type = st.selectbox("Type", ["ABSOLUTE", "PERCENT"], key=f"tsb_type_{idx}")
    with cols_top[2]:
        amount = st.number_input("Amount", min_value=0.0, step=0.5, key=f"tsb_amount_{idx}",
                                 help="Fixed €-amount (ABSOLUTE) or percentage (PERCENT)")

    cols_time = st.columns(2)
    with cols_time[0]:
        start_time = st.text_input("startTime HH:MM (optional)", value="",
                                   key=f"tsb_stime_{idx}", placeholder="e.g. 22:00")
    with cols_time[1]:
        end_time = st.text_input("endTime HH:MM (optional)", value="",
                                 key=f"tsb_etime_{idx}", placeholder="e.g. 06:00")

    cols_date = st.columns(2)
    with cols_date[0]:
        start_date = st.date_input("From (inclusive)", value=today,
                                   format="DD/MM/YYYY", key=f"tsb_sd_{idx}")
    with cols_date[1]:
        # Auto-set end date to start date when start changes (same logic as bulk stop sales)
        _end_key = f"tsb_ed_{idx}"
        if st.session_state.get(_end_key) is None or st.session_state[_end_key] < start_date:
            st.session_state[_end_key] = start_date
        end_date = st.date_input("To (inclusive)", value=st.session_state[_end_key],
                                 format="DD/MM/YYYY", key=_end_key)

    # Validate
    if not name:
        st.caption("⚠️ Enter a supplement name above.")
        return None
    if start_time and not _TIME_PAT.match(start_time):
        st.error(f"Supplement {idx + 1}: startTime must be HH:MM format.")
        return None
    if end_time and not _TIME_PAT.match(end_time):
        st.error(f"Supplement {idx + 1}: endTime must be HH:MM format.")
        return None
    if end_date < start_date:
        st.error(f"Supplement {idx + 1}: end date must be on or after start date.")
        return None

    supp = {
        "active": True,
        "name": name,
        "type": supp_type,
        "amount": amount,
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
    }
    if start_time:
        supp["startTime"] = start_time
    if end_time:
        supp["endTime"] = end_time
    return supp


def _render_create_mode(transfers):
    """UI for creating one or more supplements on transfers."""
    st.subheader("New supplements to add")
    st.caption("Define one or more supplements below — all will be added to the selected "
               "transfers in a single apply. Click **＋ Add another supplement** to add "
               "e.g. Chinese New Year, Tet, Christmas, New Year's Eve at once.")

    if "tsb_supp_count" not in st.session_state:
        st.session_state.tsb_supp_count = 1

    count = st.session_state.tsb_supp_count
    supplements = []
    all_valid = True

    for idx in range(count):
        with st.expander(f"Supplement {idx + 1}", expanded=True):
            del_col, _ = st.columns([1, 5])
            if idx > 0:
                with del_col:
                    if st.button("🗑 Remove", key=f"tsb_del_{idx}"):
                        st.session_state.tsb_supp_count = max(1, count - 1)
                        for k in [f"tsb_name_{idx}", f"tsb_type_{idx}", f"tsb_amount_{idx}",
                                  f"tsb_stime_{idx}", f"tsb_etime_{idx}",
                                  f"tsb_sd_{idx}", f"tsb_ed_{idx}"]:
                            st.session_state.pop(k, None)
                        st.rerun()
            supp = _render_one_supplement(idx)
            if supp is None:
                all_valid = False
            else:
                supplements.append(supp)

    if st.button("＋ Add another supplement", key="tsb_add_supp"):
        st.session_state.tsb_supp_count += 1
        st.rerun()

    if not all_valid or not supplements:
        return None

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

    target_count = len(selected_tids) if selected_tids else len(transfers)

    with st.expander(f"Preview: {len(supplements)} supplement(s) → {target_count} transfer(s)"):
        for s in supplements:
            st.markdown(
                f"**{s['name']}** — {s['type']} {s['amount']} · "
                f"{s['startDate']} → {s['endDate']}"
                + (f" · time {s.get('startTime', '')}–{s.get('endTime', '')}"
                   if s.get('startTime') or s.get('endTime') else "")
            )
        st.caption(f"Will be added to **{target_count}** transfer(s).")

    return ("create", supplements, selected_tids, target_count)


def _render_delete_mode(transfers):
    """UI for deleting all supplements from every transfer."""
    total_supps = sum(len(t.get("supplements") or []) for t in transfers)
    transfers_with_supps = sum(1 for t in transfers if t.get("supplements"))

    if not total_supps:
        st.warning(f"None of the {len(transfers)} transfers have any supplements — nothing to delete.")
        return None

    st.warning(
        f"This will **delete all {total_supps} supplement(s)** from "
        f"**{transfers_with_supps}** transfer(s). "
        f"The remaining {len(transfers) - transfers_with_supps} transfer(s) have no supplements and will not be touched."
    )

    with st.expander(f"Show all supplements to be deleted ({total_supps})", expanded=False):
        for t in transfers:
            for s in (t.get("supplements") or []):
                tname = t.get("name") or t.get("routeName") or t.get("id", "")
                st.text(f"{tname}  |  {s.get('name', '')}  |  "
                        f"time: {s.get('startTime','')}-{s.get('endTime','')}  |  "
                        f"{s.get('type','')} {s.get('amount',0)}")

    confirm = st.checkbox(
        "Yes, I want to delete all supplements from all transfers for this supplier",
        key="tsb_d_confirm")
    if not confirm:
        return None

    return ("delete", transfers_with_supps, total_supps)


def render_transfer_surcharge_bulk_flow(client):
    st.header("Bulk Transfer supplements")

    supplier_id = _ur_pick_momira_supplier(client, key_prefix="tsb")
    if not supplier_id:
        return

    # Reset loaded data when supplier changes
    if st.session_state.get("tsb_last_supplier_id") != supplier_id:
        st.session_state.tsb_last_supplier_id = supplier_id
        st.session_state.tsb_transfers = None
        st.session_state.tsb_applied_once = False

    if st.button("Load transfers", key="tsb_load"):
        with st.spinner("Fetching transfers…"):
            result = _load_transfers(client, supplier_id)
            if result:
                st.session_state.tsb_transfers = result
                st.session_state.tsb_applied_once = False
        st.rerun()

    transfers = st.session_state.get("tsb_transfers")
    if not transfers:
        return

    st.info(f"**{len(transfers)}** transfers loaded.")

    mode = st.radio("What do you want to do?",
                    ["Create a new supplement on transfers",
                     "Delete all supplements"],
                    key="tsb_mode")

    st.divider()

    if mode == "Create a new supplement on transfers":
        result = _render_create_mode(transfers)
    else:
        result = _render_delete_mode(transfers)

    if not result:
        return

    # ── Apply button ─────────────────────────────────────────────────
    st.divider()
    if result[0] == "create":
        _, supplements_list, selected_tids, target_count = result
        n = len(supplements_list)
        label = (
            f"Add {n} supplement(s) to {target_count} transfer(s)"
            if n > 1
            else f"Add supplement to {target_count} transfer(s)"
        )
    else:
        _, transfers_with_supps, total_supps = result
        label = f"⚠️ Delete all {total_supps} supplement(s) from {transfers_with_supps} transfer(s)"

    already_applied = st.session_state.get("tsb_applied_once") and result[0] == "create"
    if already_applied:
        st.button(label, key="tsb_apply", type="primary", disabled=True)
        st.caption("✅ Supplements added — reload transfers to add again or switch mode.")
        return
    if not st.button(label, key="tsb_apply", type="primary"):
        return

    # ── Execute ──────────────────────────────────────────────────────
    bar = st.progress(0, text="Applying…")
    all_ok = 0
    all_failed = 0
    all_errors = []

    if result[0] == "create":
        for s_idx, new_supp in enumerate(supplements_list):
            def _prog(done, total, _idx=s_idx, _n=len(supplements_list)):
                pct = (_idx / _n) + (done / max(total, 1)) / _n
                bar.progress(
                    min(pct, 1.0),
                    text=f"Supplement {_idx + 1}/{_n}: applying… ({done}/{total})"
                )
            res = supplement_apply.apply_create(
                client, supplier_id, "Transfer", transfers, new_supp,
                selected_labels=selected_tids, progress=_prog)
            all_ok += res.get("ok", 0)
            all_failed += res.get("failed", 0)
            all_errors.extend(res.get("errors", []))
        if all_ok:
            st.session_state.tsb_applied_once = True
    else:
        def _prog(done, total):
            bar.progress(done / max(total, 1), text=f"Applying… ({done}/{total})")
        res = supplement_apply.apply_delete_all(
            client, supplier_id, "Transfer", transfers, progress=_prog)
        all_ok = res.get("ok", 0)
        all_failed = res.get("failed", 0)
        all_errors = res.get("errors", [])

    bar.empty()

    if all_ok:
        st.success(f"Done — **{all_ok}** transfer(s) updated successfully.")
    if all_failed:
        st.error(f"**{all_failed}** transfer(s) failed:")
        for err in all_errors:
            st.text(err)
