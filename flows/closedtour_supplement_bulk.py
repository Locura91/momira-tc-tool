"""
Bulk closed tour supplement tool — two modes:

1. UPDATE existing supplements: change prices/dates/flags across all or
   filtered supplements for a supplier's closed tours.
2. CREATE a new supplement: append a brand-new supplement to every closed tour
   (or selected ones) for a supplier.

Supplements live on ContractClosedTourVO.supplements[] and are updated via
PUT /closedtours/{supplierId}.
"""
import copy
import streamlit as st
from datetime import date

from list_response_utils import normalize_list_response


def _tour_code(t):
    """ContractClosedTourVO is keyed on `code`; `providerCode` is the DMC's own
    reference and does not resolve on GET /closedtours/{supplierId}/{code}."""
    return t.get("code") or t.get("providerCode") or ""


def _load_closed_tours(client, supplier_id):
    """Fetch all closed tours, re-fetching each individually for full data."""
    try:
        resp = client.get_closed_tours(supplier_id)
    except Exception as e:
        st.error(f"Failed to fetch closed tours: {e}")
        return None
    tours = normalize_list_response(resp, ("closedTour", "closedTours",
                                           "items", "data", "results", "content"))
    if not tours:
        st.warning("No closed tours found for this supplier.")
        return None

    full_tours = []
    progress = st.progress(0, text="Loading closed tour details…")
    for i, t in enumerate(tours):
        code = _tour_code(t)
        try:
            full = client.get_closed_tour(supplier_id, code)
            full_tours.append(full)
        except Exception:
            full_tours.append(t)
        progress.progress((i + 1) / len(tours))
    progress.empty()
    return full_tours


def _collect_supplement_rows(tours):
    """Flatten all supplements across all closed tours."""
    rows = []
    for t in tours:
        code = _tour_code(t)
        name = t.get("name") or code
        for idx, s in enumerate(t.get("supplements") or []):
            trans = s.get("translations") or {}
            name_parts = []
            for lang, tr in trans.items():
                n = tr.get("name") or ""
                if n:
                    name_parts.append(f"{lang}:{n}")
            sname = " / ".join(name_parts) if name_parts else f"Supplement #{idx+1}"

            price = s.get("price") or {}
            rows.append({
                "tour_code": code,
                "tour_name": name,
                "supp_idx": idx,
                "name": sname,
                "mandatory": s.get("mandatory", False),
                "free": s.get("free", False),
                "singlePrice": price.get("singlePrice", 0),
                "doublePrice": price.get("doublePrice", 0),
                "triplePrice": price.get("triplePrice", 0),
                "quadruplePrice": price.get("quadruplePrice", 0),
                "travelWindows": s.get("travelWindows") or [],
                "modalityCodes": s.get("modalityCodes") or [],
            })
    return rows


def _render_update_mode(tours):
    """UI for updating existing closed tour supplements."""
    rows = _collect_supplement_rows(tours)
    if not rows:
        st.warning(f"None of the {len(tours)} closed tours have any supplements.")
        return None

    st.success(f"Found **{len(rows)}** supplement(s) across **{len(tours)}** closed tours.")

    unique_names = sorted({r["name"] for r in rows if r["name"]})
    scope = st.radio("Which supplements to update?",
                     ["All supplements", "Only supplements with a specific name"],
                     key="ctsb_scope")
    selected = rows
    if scope == "Only supplements with a specific name" and unique_names:
        pick = st.multiselect("Supplement name(s)", unique_names, key="ctsb_name_filter")
        if pick:
            selected = [r for r in rows if r["name"] in pick]
        else:
            st.info("Pick at least one supplement name.")
            return None

    st.info(f"**{len(selected)}** supplement(s) will be updated.")

    with st.expander(f"Preview ({len(selected)} supplements)", expanded=False):
        for r in selected:
            tw = ", ".join(f"{w.get('startDate','?')}→{w.get('endDate','?')}" for w in r["travelWindows"])
            st.text(f"{r['tour_name']}  |  {r['name']}  |  "
                    f"S:{r['singlePrice']} D:{r['doublePrice']} T:{r['triplePrice']} Q:{r['quadruplePrice']}  |  "
                    f"travel: {tw or 'none'}")

    st.subheader("New distribution prices")
    st.caption("Leave at 0 to keep unchanged. Remember: single >= double >= triple >= quadruple.")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        new_single = st.number_input("Single", min_value=0.0, step=0.5, key="ctsb_single")
    with c2:
        new_double = st.number_input("Double", min_value=0.0, step=0.5, key="ctsb_double")
    with c3:
        new_triple = st.number_input("Triple", min_value=0.0, step=0.5, key="ctsb_triple")
    with c4:
        new_quad = st.number_input("Quadruple", min_value=0.0, step=0.5, key="ctsb_quad")

    update_travel = st.checkbox("Also update travel window", key="ctsb_update_tw")
    new_tw_start = new_tw_end = None
    if update_travel:
        today = date.today()
        dc1, dc2 = st.columns(2)
        with dc1:
            new_tw_start = st.date_input("Travel window start", value=today, key="ctsb_tw_start")
        with dc2:
            new_tw_end = st.date_input("Travel window end",
                                       value=date(today.year + 1, today.month, today.day),
                                       key="ctsb_tw_end")

    affected = {}
    for r in selected:
        affected.setdefault(r["tour_code"], []).append(r["supp_idx"])

    return ("update", affected, new_single, new_double, new_triple, new_quad,
            new_tw_start, new_tw_end, len(selected))


def _render_create_mode(tours):
    """UI for creating a new supplement on closed tours."""
    st.success(f"Loaded **{len(tours)}** closed tours.")

    st.subheader("New supplement details")

    name_en = st.text_input("Supplement name (EN)", key="ctsb_c_name_en",
                            help="e.g. Christmas Supplement, Peak Season Surcharge")

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

    if quad_p == 0 and double_p > 0:
        quad_p = double_p
        st.caption(f"Quadruple defaults to double price: {quad_p}")

    if single_p and double_p and single_p < double_p:
        st.warning("Single price should be >= double price (fewer people sharing = higher cost).")

    st.markdown("**Travel window**")
    today = date.today()
    dc1, dc2 = st.columns(2)
    with dc1:
        tw_start = st.date_input("Travel window start", value=today, key="ctsb_c_tw_start")
    with dc2:
        tw_end = st.date_input("Travel window end",
                               value=date(today.year + 1, today.month, today.day),
                               key="ctsb_c_tw_end")

    # Modality code scoping
    all_modality_codes = set()
    for t in tours:
        for mc in t.get("modalityCodes") or []:
            if mc:
                all_modality_codes.add(mc)

    scope_modalities = st.checkbox("Scope to specific modality codes", key="ctsb_c_scope_mod")
    selected_mod_codes = []
    if scope_modalities and all_modality_codes:
        selected_mod_codes = st.multiselect("Modality codes", sorted(all_modality_codes),
                                            key="ctsb_c_mod_codes")

    st.divider()
    tour_scope = st.radio("Add to which closed tours?",
                          ["All closed tours", "Select specific tours"],
                          key="ctsb_c_scope")

    selected_codes = None
    if tour_scope == "Select specific tours":
        options = {_tour_code(t): (t.get("name") or _tour_code(t))
                   for t in tours}
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
        "travelWindows": [{"startDate": tw_start.isoformat(), "endDate": tw_end.isoformat()}],
    }
    if name_en:
        new_supp["translations"] = {"EN": {"name": name_en}}
    if selected_mod_codes:
        new_supp["modalityCodes"] = selected_mod_codes

    target_count = len(selected_codes) if selected_codes else len(tours)

    with st.expander("Preview new supplement"):
        for k, v in new_supp.items():
            st.text(f"  {k}: {v}")
        st.caption(f"Will be added to **{target_count}** closed tour(s).")

    return ("create", new_supp, selected_codes, target_count)


def render_closedtour_supplement_bulk_flow(client):
    st.header("Bulk Closed Tour supplements")

    supplier_id = st.text_input("Supplier ID", key="ctsb_supplier_id")
    if not supplier_id:
        st.info("Enter a supplier ID to start.")
        return

    if st.button("Load closed tours", key="ctsb_load"):
        with st.spinner("Fetching closed tours…"):
            result = _load_closed_tours(client, supplier_id)
            if result:
                st.session_state.ctsb_tours = result
        st.rerun()

    tours = st.session_state.get("ctsb_tours")
    if not tours:
        return

    st.info(f"**{len(tours)}** closed tours loaded.")

    mode = st.radio("What do you want to do?",
                    ["Update existing supplement prices/dates", "Create a new supplement on tours"],
                    key="ctsb_mode")
    st.divider()

    if mode == "Update existing supplement prices/dates":
        result = _render_update_mode(tours)
    else:
        result = _render_create_mode(tours)

    if not result:
        return

    st.divider()
    if result[0] == "update":
        _, affected, ns, nd, nt, nq, tw_s, tw_e, supp_count = result
        label = f"Update {supp_count} supplement(s) on {len(affected)} tour(s)"
    else:
        _, new_supp, selected_codes, target_count = result
        label = f"Add supplement to {target_count} tour(s)"

    if not st.button(label, key="ctsb_apply", type="primary"):
        return

    # ── Execute ──────────────────────────────────────────────────────
    tour_map = {_tour_code(t): t for t in tours}
    progress = st.progress(0, text="Applying…")
    ok_count = 0
    err_count = 0
    errors = []

    if result[0] == "update":
        _, affected, ns, nd, nt, nq, tw_s, tw_e, _ = result
        items = list(affected.items())
        for i, (code, supp_indices) in enumerate(items):
            t = tour_map.get(code)
            if not t:
                err_count += 1
                errors.append(f"{code}: not found")
                continue
            payload = copy.deepcopy(t)
            supplements = payload.get("supplements") or []
            for idx in supp_indices:
                if idx < len(supplements):
                    price = supplements[idx].get("price") or {}
                    if ns:
                        price["singlePrice"] = ns
                    if nd:
                        price["doublePrice"] = nd
                    if nt:
                        price["triplePrice"] = nt
                    if nq:
                        price["quadruplePrice"] = nq
                    supplements[idx]["price"] = price
                    if tw_s and tw_e:
                        supplements[idx]["travelWindows"] = [
                            {"startDate": tw_s.isoformat(), "endDate": tw_e.isoformat()}
                        ]
            try:
                client.update_closed_tour(supplier_id, payload)
                ok_count += 1
            except Exception as e:
                err_count += 1
                errors.append(f"{code}: {e}")
            progress.progress((i + 1) / len(items))
    else:
        targets = tours if selected_codes is None else [
            t for t in tours if _tour_code(t) in selected_codes
        ]
        for i, t in enumerate(targets):
            code = _tour_code(t)
            payload = copy.deepcopy(t)
            supplements = payload.get("supplements") or []
            supplements.append(copy.deepcopy(new_supp))
            payload["supplements"] = supplements
            try:
                client.update_closed_tour(supplier_id, payload)
                ok_count += 1
            except Exception as e:
                err_count += 1
                errors.append(f"{code}: {e}")
            progress.progress((i + 1) / len(targets))

    progress.empty()
    if ok_count:
        st.success(f"Done — **{ok_count}** closed tour(s) updated successfully.")
    if err_count:
        st.error(f"**{err_count}** closed tour(s) failed:")
        for err in errors:
            st.text(err)
