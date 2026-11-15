"""
Bulk ticket supplement tool — two modes:

1. UPDATE existing supplements: change prices/dates across all or filtered
   supplements for a supplier's ticket modalities.
2. CREATE a new supplement: append a brand-new supplement to every ticket
   modality (or selected ones) for a supplier.

Supplements live on ContractTicketOptionVO.supplements[] and are updated via
PUT /tickets/{supplierId}/{ticketCode}.
"""
import copy
import streamlit as st
from datetime import date

from list_response_utils import normalize_list_response


def _load_ticket_modalities(client, supplier_id):
    """Fetch all tickets then each modality individually for full supplement data."""
    try:
        resp = client.get_tickets(supplier_id)
    except Exception as e:
        st.error(f"Failed to fetch tickets: {e}")
        return None
    tickets = normalize_list_response(resp, ("ticket", "tickets",
                                             "items", "data", "results", "content"))
    if not tickets:
        st.warning("No tickets found for this supplier.")
        return None

    # ContractTicketVO lists its modalities as a flat `modalityCodes` array of
    # strings, not as nested option objects — the full modality (with its
    # supplements) only comes back from the per-modality GET.
    modalities = []
    progress = st.progress(0, text="Loading ticket modalities…")
    total = sum(len(t.get("modalityCodes") or []) for t in tickets)
    i = 0
    for t in tickets:
        tcode = t.get("code") or ""
        tname = (t.get("name")
                 or ((t.get("datasheets") or {}).get("EN") or {}).get("name")
                 or tcode)
        for ocode in t.get("modalityCodes") or []:
            try:
                full = client.get_ticket_option(supplier_id, tcode, ocode)
            except Exception:
                full = {}
            modalities.append({
                "ticket_code": tcode,
                "ticket_name": tname,
                "option_code": ocode,
                "option_name": full.get("name") or ocode,
                "raw": full,
            })
            i += 1
            progress.progress(i / max(total, 1))
    progress.empty()
    return modalities


def _collect_supplement_rows(modalities):
    """Flatten all supplements across all ticket modalities."""
    rows = []
    for m in modalities:
        for idx, s in enumerate(m["raw"].get("supplements") or []):
            trans = s.get("translations") or {}
            name_parts = []
            for lang, t in trans.items():
                n = t.get("name") or ""
                if n:
                    name_parts.append(f"{lang}:{n}")
            rows.append({
                "ticket_code": m["ticket_code"],
                "ticket_name": m["ticket_name"],
                "option_code": m["option_code"],
                "option_name": m["option_name"],
                "supp_idx": idx,
                "name": " / ".join(name_parts) if name_parts else f"Supplement #{idx+1}",
                "adultPrice": s.get("adultPriceSupplement", 0),
                "childPrice": s.get("childrenPriceSupplement", 0),
                "infantPrice": s.get("infantPriceSupplement", 0),
                "startDate": s.get("startDate") or "",
                "endDate": s.get("endDate") or "",
            })
    return rows


def _render_update_mode(modalities):
    """UI for updating existing supplements."""
    rows = _collect_supplement_rows(modalities)
    if not rows:
        st.warning(f"None of the {len(modalities)} modalities have any supplements.")
        return None

    st.success(f"Found **{len(rows)}** supplement(s) across **{len(modalities)}** modalities.")

    unique_names = sorted({r["name"] for r in rows if r["name"]})
    scope = st.radio("Which supplements to update?",
                     ["All supplements", "Only supplements with a specific name"],
                     key="tksb_scope")
    selected = rows
    if scope == "Only supplements with a specific name" and unique_names:
        pick = st.multiselect("Supplement name(s)", unique_names, key="tksb_name_filter")
        if pick:
            selected = [r for r in rows if r["name"] in pick]
        else:
            st.info("Pick at least one supplement name.")
            return None

    st.info(f"**{len(selected)}** supplement(s) will be updated.")

    with st.expander(f"Preview ({len(selected)} supplements)", expanded=False):
        for r in selected:
            st.text(f"{r['ticket_name']} / {r['option_name']}  |  {r['name']}  |  "
                    f"adult: {r['adultPrice']}  child: {r['childPrice']}  infant: {r['infantPrice']}  |  "
                    f"{r['startDate']} → {r['endDate']}")

    st.subheader("New values")
    st.caption("Leave a field at 0 to keep it unchanged.")
    c1, c2, c3 = st.columns(3)
    with c1:
        new_adult = st.number_input("Adult price supplement", min_value=0.0, step=0.5, key="tksb_adult")
    with c2:
        new_child = st.number_input("Children price supplement", min_value=0.0, step=0.5, key="tksb_child")
    with c3:
        new_infant = st.number_input("Infant price supplement", min_value=0.0, step=0.5, key="tksb_infant")

    update_dates = st.checkbox("Also update date range", key="tksb_update_dates")
    new_start = new_end = None
    if update_dates:
        today = date.today()
        dc1, dc2 = st.columns(2)
        with dc1:
            new_start = st.date_input("New startDate", value=today, key="tksb_start")
        with dc2:
            new_end = st.date_input("New endDate", value=date(today.year + 1, today.month, today.day), key="tksb_end")

    affected = {}
    for r in selected:
        key = (r["ticket_code"], r["option_code"])
        affected.setdefault(key, []).append(r["supp_idx"])

    return ("update", affected, new_adult, new_child, new_infant, new_start, new_end, len(selected))


def _render_create_mode(modalities):
    """UI for creating a new supplement on ticket modalities."""
    st.success(f"Loaded **{len(modalities)}** ticket modalities.")

    st.subheader("New supplement details")

    name_en = st.text_input("Supplement name (EN)", key="tksb_c_name_en",
                            help="e.g. High Season Supplement")

    c1, c2, c3 = st.columns(3)
    with c1:
        adult_price = st.number_input("Adult price supplement", min_value=0.0, step=0.5, key="tksb_c_adult")
    with c2:
        child_price = st.number_input("Children price supplement", min_value=0.0, step=0.5, key="tksb_c_child")
    with c3:
        infant_price = st.number_input("Infant price supplement", min_value=0.0, step=0.5, key="tksb_c_infant")

    st.markdown("**Date validity**")
    today = date.today()
    dc1, dc2 = st.columns(2)
    with dc1:
        start_date = st.date_input("startDate", value=today, key="tksb_c_start")
    with dc2:
        end_date = st.date_input("endDate", value=date(today.year + 1, today.month, today.day), key="tksb_c_end")

    st.divider()
    mod_scope = st.radio("Add to which modalities?",
                         ["All modalities", "Select specific modalities"],
                         key="tksb_c_scope")

    selected_keys = None
    if mod_scope == "Select specific modalities":
        options = {}
        for m in modalities:
            k = f"{m['ticket_code']}/{m['option_code']}"
            options[k] = f"{m['ticket_name']} / {m['option_name']}"
        picks = st.multiselect("Select modalities", list(options.keys()),
                               format_func=lambda k: f"{options[k]} ({k})",
                               key="tksb_c_mod_pick")
        if not picks:
            st.info("Select at least one modality.")
            return None
        selected_keys = set(picks)

    new_supp = {
        "adultPriceSupplement": adult_price,
        "childrenPriceSupplement": child_price,
        "infantPriceSupplement": infant_price,
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
    }
    if name_en:
        new_supp["translations"] = {"EN": {"name": name_en}}

    target_count = len(selected_keys) if selected_keys else len(modalities)

    with st.expander("Preview new supplement"):
        for k, v in new_supp.items():
            st.text(f"  {k}: {v}")
        st.caption(f"Will be added to **{target_count}** modality/ies.")

    return ("create", new_supp, selected_keys, target_count)


def render_ticket_supplement_bulk_flow(client):
    st.header("Bulk Ticket supplements")

    supplier_id = st.text_input("Supplier ID", key="tksb_supplier_id")
    if not supplier_id:
        st.info("Enter a supplier ID to start.")
        return

    if st.button("Load ticket modalities", key="tksb_load"):
        with st.spinner("Fetching tickets…"):
            result = _load_ticket_modalities(client, supplier_id)
            if result:
                st.session_state.tksb_modalities = result
        st.rerun()

    modalities = st.session_state.get("tksb_modalities")
    if not modalities:
        return

    st.info(f"**{len(modalities)}** ticket modalities loaded.")

    mode = st.radio("What do you want to do?",
                    ["Update existing supplement prices/dates", "Create a new supplement on modalities"],
                    key="tksb_mode")
    st.divider()

    if mode == "Update existing supplement prices/dates":
        result = _render_update_mode(modalities)
    else:
        result = _render_create_mode(modalities)

    if not result:
        return

    st.divider()
    if result[0] == "update":
        _, affected, new_adult, new_child, new_infant, new_start, new_end, supp_count = result
        label = f"Update {supp_count} supplement(s) on {len(affected)} modality/ies"
    else:
        _, new_supp, selected_keys, target_count = result
        label = f"Add supplement to {target_count} modality/ies"

    if not st.button(label, key="tksb_apply", type="primary"):
        return

    # ── Execute ──────────────────────────────────────────────────────
    mod_map = {f"{m['ticket_code']}/{m['option_code']}": m for m in modalities}
    progress = st.progress(0, text="Applying…")
    ok_count = 0
    err_count = 0
    errors = []

    if result[0] == "update":
        items = list(affected.items())
        for i, ((tcode, ocode), supp_indices) in enumerate(items):
            key = f"{tcode}/{ocode}"
            m = mod_map.get(key)
            if not m:
                err_count += 1
                errors.append(f"{key}: not found")
                continue
            payload = copy.deepcopy(m["raw"])
            supplements = payload.get("supplements") or []
            for idx in supp_indices:
                if idx < len(supplements):
                    if new_adult:
                        supplements[idx]["adultPriceSupplement"] = new_adult
                    if new_child:
                        supplements[idx]["childrenPriceSupplement"] = new_child
                    if new_infant:
                        supplements[idx]["infantPriceSupplement"] = new_infant
                    if new_start:
                        supplements[idx]["startDate"] = new_start.isoformat()
                    if new_end:
                        supplements[idx]["endDate"] = new_end.isoformat()
            try:
                client.update_ticket_option(supplier_id, tcode, payload)
                ok_count += 1
            except Exception as e:
                err_count += 1
                errors.append(f"{key}: {e}")
            progress.progress((i + 1) / len(items))
    else:
        targets = modalities if selected_keys is None else [
            m for m in modalities
            if f"{m['ticket_code']}/{m['option_code']}" in selected_keys
        ]
        for i, m in enumerate(targets):
            payload = copy.deepcopy(m["raw"])
            supplements = payload.get("supplements") or []
            supplements.append(copy.deepcopy(new_supp))
            payload["supplements"] = supplements
            try:
                client.update_ticket_option(supplier_id, m["ticket_code"], payload)
                ok_count += 1
            except Exception as e:
                err_count += 1
                errors.append(f"{m['ticket_code']}/{m['option_code']}: {e}")
            progress.progress((i + 1) / len(targets))

    progress.empty()
    if ok_count:
        st.success(f"Done — **{ok_count}** modality/ies updated successfully.")
    if err_count:
        st.error(f"**{err_count}** modality/ies failed:")
        for err in errors:
            st.text(err)
