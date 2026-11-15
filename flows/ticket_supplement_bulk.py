"""
Bulk ticket supplement tool — two modes:

1. CREATE a new supplement: append a brand-new supplement to every ticket modality
   (or selected modalities) for a supplier.
2. DELETE all supplements: wipe every supplement from every modality for a
   supplier (useful before re-adding supplements from scratch).

Both use PUT /tickets/{supplierId}/{ticketCode}, which sends the full modality
including its supplements array.

MODES MATCH THE TRANSFER TOOL ON PURPOSE (product owner, 2026-11-15): "if we developed
one thing for a specific product, to add it to all the other products as well". The
transfer tool dropped its update-existing-times mode and gained delete-all; this one
follows, so all four supplement tools behave the same way rather than each having its own
idea of what the modes are.
"""
import streamlit as st
from datetime import date

import supplement_apply
from app_helpers import _ur_pick_momira_supplier
from list_response_utils import normalize_list_response

MODULE_BUILD = "2026-10-24-rollover-ext"

_WRAPPER_FALLBACKS = ("items", "data", "results", "content")


def _load_ticket_modalities(client, supplier_id):
    """Fetch all tickets then each modality individually for full supplement data."""
    try:
        resp = client.get_tickets(supplier_id, first=0, limit=200)
    except Exception as e:
        st.error(f"Failed to fetch tickets: {e}")
        return None
    tickets = normalize_list_response(resp, ("ticket", "tickets") + _WRAPPER_FALLBACKS)
    tickets = [t for t in tickets if isinstance(t, dict)]
    if not tickets:
        st.warning("No tickets found for this supplier.")
        return None

    # ContractTicketVO lists its modalities as a flat `modalityCodes` array of strings,
    # not nested option objects — the full modality (with its supplements) only comes
    # back from the per-modality GET.
    modalities = []
    total = sum(len(t.get("modalityCodes") or []) for t in tickets)
    progress = st.progress(0, text="Loading ticket modalities…")
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
            if not isinstance(full, dict) or "error" in full:
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


def _supp_label(s, idx):
    trans = s.get("translations") or {}
    parts = [f"{lang}:{t.get('name')}" for lang, t in trans.items()
             if isinstance(t, dict) and t.get("name")]
    return " / ".join(parts) if parts else f"Supplement #{idx + 1}"


def _render_create_mode(modalities):
    """UI for creating a new supplement on ticket modalities."""
    st.subheader("New supplement details")

    name_en = st.text_input("Supplement name (EN)", key="tksb_c_name_en",
                            help="e.g. High Season Supplement")
    if not name_en:
        st.warning("Enter a supplement name.")
        return None

    c1, c2, c3 = st.columns(3)
    with c1:
        adult_price = st.number_input("Adult price supplement", min_value=0.0, step=0.5,
                                      key="tksb_c_adult")
    with c2:
        child_price = st.number_input("Children price supplement", min_value=0.0, step=0.5,
                                      key="tksb_c_child")
    with c3:
        infant_price = st.number_input("Infant price supplement", min_value=0.0, step=0.5,
                                       key="tksb_c_infant")

    st.markdown("**Date validity**")
    today = date.today()
    dc1, dc2 = st.columns(2)
    with dc1:
        start_date = st.date_input("startDate", value=today, key="tksb_c_start")
    with dc2:
        end_date = st.date_input("endDate",
                                 value=date(today.year + 1, today.month, today.day),
                                 key="tksb_c_end")

    st.divider()
    scope = st.radio("Add to which modalities?",
                     ["All modalities", "Select specific modalities"],
                     key="tksb_c_scope")

    selected_keys = None
    if scope == "Select specific modalities":
        options = {f"{m['ticket_code']}/{m['option_code']}":
                   f"{m['ticket_name']} / {m['option_name']}" for m in modalities}
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
        "translations": {"EN": {"name": name_en}},
    }
    target_count = len(selected_keys) if selected_keys else len(modalities)

    with st.expander("Preview new supplement"):
        for k, v in new_supp.items():
            st.text(f"  {k}: {v}")
        st.caption(f"Will be added to **{target_count}** modality/ies.")

    return ("create", new_supp, selected_keys, target_count)


def _render_delete_mode(modalities):
    """UI for deleting every supplement from every modality."""
    with_supps = [m for m in modalities if (m["raw"].get("supplements") or [])]
    total_supps = sum(len(m["raw"].get("supplements") or []) for m in modalities)

    if not total_supps:
        st.info("None of these modalities have any supplements — nothing to delete.")
        return None

    st.warning(
        f"This will **delete all {total_supps} supplement(s)** from "
        f"**{len(with_supps)}** modality/ies. "
        f"The remaining {len(modalities) - len(with_supps)} modality/ies have no "
        f"supplements and will not be touched.")

    with st.expander(f"Show all supplements to be deleted ({total_supps})", expanded=False):
        for m in with_supps:
            for idx, s in enumerate(m["raw"].get("supplements") or []):
                st.text(f"{m['ticket_name']} / {m['option_name']}  |  "
                        f"{_supp_label(s, idx)}  |  "
                        f"adult={s.get('adultPriceSupplement', 0)} "
                        f"child={s.get('childrenPriceSupplement', 0)} "
                        f"infant={s.get('infantPriceSupplement', 0)}  |  "
                        f"{s.get('startDate', '')} → {s.get('endDate', '')}")

    if not st.checkbox("Yes, I want to delete all supplements from all modalities for "
                       "this supplier", key="tksb_d_confirm"):
        return None

    return ("delete", len(with_supps), total_supps)


def render_ticket_supplement_bulk_flow(client):
    st.header("Bulk Ticket supplements")

    supplier_id = _ur_pick_momira_supplier(client, key_prefix="tksb")
    if not supplier_id:
        return

    if st.session_state.get("tksb_last_supplier_id") != supplier_id:
        st.session_state.tksb_last_supplier_id = supplier_id
        st.session_state.tksb_modalities = None
        st.session_state.tksb_applied_once = False

    if st.button("Load ticket modalities", key="tksb_load"):
        with st.spinner("Fetching tickets…"):
            result = _load_ticket_modalities(client, supplier_id)
            if result:
                st.session_state.tksb_modalities = result
                st.session_state.tksb_applied_once = False
        st.rerun()

    modalities = st.session_state.get("tksb_modalities")
    if not modalities:
        return

    st.info(f"**{len(modalities)}** ticket modalities loaded.")

    mode = st.radio("What do you want to do?",
                    ["Create a new supplement on modalities", "Delete all supplements"],
                    key="tksb_mode")
    st.divider()

    if mode == "Create a new supplement on modalities":
        result = _render_create_mode(modalities)
    else:
        result = _render_delete_mode(modalities)

    if not result:
        return

    st.divider()
    if result[0] == "create":
        _, new_supp, selected_keys, target_count = result
        label = f"Add supplement to {target_count} modality/ies"
    else:
        _, mods_with_supps, total_supps = result
        label = f"⚠️ Delete all {total_supps} supplement(s) from {mods_with_supps} modality/ies"

    if st.session_state.get("tksb_applied_once") and result[0] == "create":
        st.button(label, key="tksb_apply", type="primary", disabled=True)
        st.caption("✅ Supplement already added — reload modalities to add again or "
                   "switch mode.")
        return
    if not st.button(label, key="tksb_apply", type="primary"):
        return

    # ── Execute ──────────────────────────────────────────────────────
    bar = st.progress(0, text="Applying…")

    def _prog(done, total):
        bar.progress(done / max(total, 1), text=f"Applying… ({done}/{total})")

    if result[0] == "create":
        res = supplement_apply.apply_create(
            client, supplier_id, "Ticket", modalities, new_supp,
            selected_labels=selected_keys, progress=_prog)
        if res["ok"]:
            st.session_state.tksb_applied_once = True
    else:
        res = supplement_apply.apply_delete_all(
            client, supplier_id, "Ticket", modalities, progress=_prog)

    bar.empty()
    if res["ok"]:
        st.success(f"Done — **{res['ok']}** modality/ies updated successfully.")
    if res["failed"]:
        st.error(f"**{res['failed']}** modality/ies failed:")
        for err in res["errors"]:
            st.text(err)
