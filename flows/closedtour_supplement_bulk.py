"""
Bulk closed tour supplement tool — two modes:

1. CREATE a new supplement: append a brand-new supplement to every closed tour
   (or selected tours) for a supplier.
2. DELETE all supplements: wipe every supplement from every closed tour for a
   supplier (useful before re-adding supplements from scratch).

Both use PUT /closedtours/{supplierId}, which sends the full tour including its
supplements array.

MODES MATCH THE TRANSFER TOOL ON PURPOSE (product owner, 2026-11-15): "if we developed
one thing for a specific product, to add it to all the other products as well". The
transfer tool dropped its update-existing-times mode and gained delete-all; this one
follows, so all four supplement tools behave the same way.
"""
import copy
import streamlit as st
from datetime import date

from app_helpers import _ur_pick_momira_supplier
from list_response_utils import normalize_list_response

MODULE_BUILD = "2026-10-24-rollover-ext"

_WRAPPER_FALLBACKS = ("items", "data", "results", "content")


def _tour_code(t):
    """ContractClosedTourVO is keyed on `code`; `providerCode` is the DMC's own reference
    and does not resolve on GET /closedtours/{supplierId}/{code}."""
    return t.get("code") or t.get("providerCode") or ""


def _load_closed_tours(client, supplier_id):
    """Fetch all closed tours, re-fetching each individually for full supplement data."""
    try:
        resp = client.get_closed_tours(supplier_id, first=0, limit=200)
    except Exception as e:
        st.error(f"Failed to fetch closed tours: {e}")
        return None
    tours = normalize_list_response(resp, ("closedTour", "closedTours") + _WRAPPER_FALLBACKS)
    tours = [t for t in tours if isinstance(t, dict)]
    if not tours:
        st.warning("No closed tours found for this supplier.")
        return None

    full_tours = []
    progress = st.progress(0, text="Loading closed tour details…")
    for i, t in enumerate(tours):
        try:
            full = client.get_closed_tour(supplier_id, _tour_code(t))
        except Exception:
            full = None
        full_tours.append(full if isinstance(full, dict) and "error" not in full else t)
        progress.progress((i + 1) / len(tours))
    progress.empty()
    return full_tours


def _supp_label(s, idx):
    trans = s.get("translations") or {}
    parts = [f"{lang}:{t.get('name')}" for lang, t in trans.items()
             if isinstance(t, dict) and t.get("name")]
    return " / ".join(parts) if parts else f"Supplement #{idx + 1}"


def _render_create_mode(tours):
    """UI for creating a new supplement on closed tours."""
    st.subheader("New supplement details")

    name_en = st.text_input("Supplement name (EN)", key="ctsb_c_name_en",
                            help="e.g. Christmas Supplement, Peak Season Surcharge")
    if not name_en:
        st.warning("Enter a supplement name.")
        return None

    mandatory = st.checkbox("Mandatory", value=True, key="ctsb_c_mandatory")
    free = st.checkbox("Free (no charge)", value=False, key="ctsb_c_free")
    commissionable = st.checkbox("Commissionable", value=True, key="ctsb_c_commission")

    st.markdown("**Distribution prices** (per-person, per occupancy)")
    st.caption("single >= double >= triple >= quadruple")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        single_p = st.number_input("Single price", min_value=0.0, step=0.5, key="ctsb_c_single")
    with c2:
        double_p = st.number_input("Double price", min_value=0.0, step=0.5, key="ctsb_c_double")
    with c3:
        triple_p = st.number_input("Triple price", min_value=0.0, step=0.5, key="ctsb_c_triple")
    with c4:
        quad_p = st.number_input("Quadruple price", min_value=0.0, step=0.5, key="ctsb_c_quad")

    # House rule (CLAUDE.md): a quad booking is two double rooms, so an unstated quadruple
    # price follows the double rather than being left at zero.
    if quad_p == 0 and double_p > 0:
        quad_p = double_p
        st.caption(f"Quadruple not set — defaulting to the double price: {quad_p}")

    if single_p and double_p and single_p < double_p:
        st.warning("Single price should be >= double price "
                   "(fewer people sharing = higher per-person cost).")

    st.markdown("**Travel window**")
    today = date.today()
    dc1, dc2 = st.columns(2)
    with dc1:
        tw_start = st.date_input("Travel window start", value=today, key="ctsb_c_tw_start")
    with dc2:
        tw_end = st.date_input("Travel window end",
                               value=date(today.year + 1, today.month, today.day),
                               key="ctsb_c_tw_end")

    all_modality_codes = set()
    for t in tours:
        for mc in t.get("modalityCodes") or []:
            if mc:
                all_modality_codes.add(mc)

    selected_mod_codes = []
    if all_modality_codes and st.checkbox("Scope to specific modality codes",
                                          key="ctsb_c_scope_mod"):
        selected_mod_codes = st.multiselect("Modality codes", sorted(all_modality_codes),
                                            key="ctsb_c_mod_codes")

    st.divider()
    scope = st.radio("Add to which closed tours?",
                     ["All closed tours", "Select specific tours"], key="ctsb_c_scope")

    selected_codes = None
    if scope == "Select specific tours":
        options = {_tour_code(t): (t.get("name") or _tour_code(t)) for t in tours}
        picks = st.multiselect("Select tours", list(options.keys()),
                               format_func=lambda c: f"{options[c]} ({c})",
                               key="ctsb_c_tour_pick")
        if not picks:
            st.info("Select at least one tour.")
            return None
        selected_codes = set(picks)

    new_supp = {
        "mandatory": mandatory,
        "free": free,
        "commissionable": commissionable,
        "refundable": True,
        "onRequest": False,
        "priceInPercentage": False,
        "price": {
            "singlePrice": single_p,
            "doublePrice": double_p,
            "triplePrice": triple_p,
            "quadruplePrice": quad_p if quad_p else double_p,
        },
        "travelWindows": [{"startDate": tw_start.isoformat(),
                           "endDate": tw_end.isoformat()}],
        "translations": {"EN": {"name": name_en}},
    }
    if selected_mod_codes:
        new_supp["modalityCodes"] = selected_mod_codes

    target_count = len(selected_codes) if selected_codes else len(tours)

    with st.expander("Preview new supplement"):
        for k, v in new_supp.items():
            st.text(f"  {k}: {v}")
        st.caption(f"Will be added to **{target_count}** closed tour(s).")

    return ("create", new_supp, selected_codes, target_count)


def _render_delete_mode(tours):
    """UI for deleting every supplement from every closed tour."""
    with_supps = [t for t in tours if (t.get("supplements") or [])]
    total_supps = sum(len(t.get("supplements") or []) for t in tours)

    if not total_supps:
        st.info("None of these closed tours have any supplements — nothing to delete.")
        return None

    st.warning(
        f"This will **delete all {total_supps} supplement(s)** from "
        f"**{len(with_supps)}** closed tour(s). "
        f"The remaining {len(tours) - len(with_supps)} tour(s) have no supplements and "
        f"will not be touched.")

    with st.expander(f"Show all supplements to be deleted ({total_supps})", expanded=False):
        for t in with_supps:
            tname = t.get("name") or _tour_code(t)
            for idx, s in enumerate(t.get("supplements") or []):
                price = s.get("price") or {}
                st.text(f"{tname}  |  {_supp_label(s, idx)}  |  "
                        f"S:{price.get('singlePrice', 0)} D:{price.get('doublePrice', 0)} "
                        f"T:{price.get('triplePrice', 0)} Q:{price.get('quadruplePrice', 0)}")

    if not st.checkbox("Yes, I want to delete all supplements from all closed tours for "
                       "this supplier", key="ctsb_d_confirm"):
        return None

    return ("delete", len(with_supps), total_supps)


def render_closedtour_supplement_bulk_flow(client):
    st.header("Bulk Closed Tour supplements")

    supplier_id = _ur_pick_momira_supplier(client, key_prefix="ctsb")
    if not supplier_id:
        return

    if st.session_state.get("ctsb_last_supplier_id") != supplier_id:
        st.session_state.ctsb_last_supplier_id = supplier_id
        st.session_state.ctsb_tours = None
        st.session_state.ctsb_applied_once = False

    if st.button("Load closed tours", key="ctsb_load"):
        with st.spinner("Fetching closed tours…"):
            result = _load_closed_tours(client, supplier_id)
            if result:
                st.session_state.ctsb_tours = result
                st.session_state.ctsb_applied_once = False
        st.rerun()

    tours = st.session_state.get("ctsb_tours")
    if not tours:
        return

    st.info(f"**{len(tours)}** closed tours loaded.")

    mode = st.radio("What do you want to do?",
                    ["Create a new supplement on tours", "Delete all supplements"],
                    key="ctsb_mode")
    st.divider()

    if mode == "Create a new supplement on tours":
        result = _render_create_mode(tours)
    else:
        result = _render_delete_mode(tours)

    if not result:
        return

    st.divider()
    if result[0] == "create":
        _, new_supp, selected_codes, target_count = result
        label = f"Add supplement to {target_count} tour(s)"
    else:
        _, tours_with_supps, total_supps = result
        label = f"⚠️ Delete all {total_supps} supplement(s) from {tours_with_supps} tour(s)"

    if st.session_state.get("ctsb_applied_once") and result[0] == "create":
        st.button(label, key="ctsb_apply", type="primary", disabled=True)
        st.caption("✅ Supplement already added — reload tours to add again or switch mode.")
        return
    if not st.button(label, key="ctsb_apply", type="primary"):
        return

    # ── Execute ──────────────────────────────────────────────────────
    progress = st.progress(0, text="Applying…")
    ok_count = 0
    err_count = 0
    errors = []

    if result[0] == "create":
        targets = [t for t in tours
                   if selected_codes is None or _tour_code(t) in selected_codes]
        for i, t in enumerate(targets):
            payload = copy.deepcopy(t)
            supplements = payload.get("supplements") or []
            supplements.append(copy.deepcopy(new_supp))
            payload["supplements"] = supplements
            try:
                client.update_closed_tour(supplier_id, payload)
                ok_count += 1
            except Exception as e:
                err_count += 1
                errors.append(f"{_tour_code(t)}: {e}")
            progress.progress((i + 1) / len(targets))
        if ok_count:
            st.session_state.ctsb_applied_once = True
    else:
        targets = [t for t in tours if (t.get("supplements") or [])]
        for i, t in enumerate(targets):
            payload = copy.deepcopy(t)
            payload["supplements"] = []
            try:
                client.update_closed_tour(supplier_id, payload)
                ok_count += 1
            except Exception as e:
                err_count += 1
                errors.append(f"{_tour_code(t)}: {e}")
            progress.progress((i + 1) / len(targets))

    progress.empty()
    if ok_count:
        st.success(f"Done — **{ok_count}** closed tour(s) updated successfully.")
    if err_count:
        st.error(f"**{err_count}** closed tour(s) failed:")
        for err in errors:
            st.text(err)
