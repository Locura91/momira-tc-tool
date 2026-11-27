"""
flows/supplement_bulk.py — combined "Bulk supplements" entry point.

All four product types (Transfer, Ticket, ClosedTour, Hotel) are selectable
via checkboxes. Any combination is allowed. The same supplement name and date
range are shared; each type shows its own price/amount fields because their
schemas differ significantly. All selected types are applied in one run.

PERCENT → ABSOLUTE conversion for Tickets:
  adult_supp = ceil(2-pax-occupancy-price / 2 * pct / 100)
  Fallback when no 2-pax entry: ceil(baseAdultPrice * pct / 100).
"""
import copy
import math
import re
import streamlit as st
from datetime import date
from typing import Optional

import supplement_apply
from list_response_utils import normalize_list_response

MODULE_BUILD = "2026-11-27-all-four-types"

_TIME_PAT = re.compile(r"^\d{2}:\d{2}$")

HOTEL_APPLY_TYPES = [
    "LODGING", "MEAL", "LODGING_AND_MEAL",
    "PER_NIGHT", "PER_NIGHT_PERSON", "PER_STAY", "PER_STAY_PERSON",
]

# ── data loaders ───────────────────────────────────────────────────────────────

def _load_transfers(client, supplier_id):
    try:
        resp = client.get_transfers(supplier_id)
    except Exception as e:
        st.error(f"Failed to fetch transfers: {e}"); return None
    items = (resp.get("transfer") or []) if isinstance(resp, dict) else []
    if not items:
        st.warning("No transfers found."); return None
    full, prog = [], st.progress(0, text="Loading transfers…")
    for i, t in enumerate(items):
        try:
            full.append(client.get_transfer(supplier_id, t.get("id", "")))
        except Exception:
            full.append(t)
        prog.progress((i + 1) / len(items))
    prog.empty()
    return full


def _load_ticket_modalities(client, supplier_id):
    _WRAPPER = ("ticket", "tickets", "items", "data", "results", "content")
    try:
        resp = client.get_tickets(supplier_id, first=0, limit=200)
    except Exception as e:
        st.error(f"Failed to fetch tickets: {e}"); return None
    tickets = [t for t in normalize_list_response(resp, _WRAPPER) if isinstance(t, dict)]
    if not tickets:
        st.warning("No tickets found."); return None
    total = sum(len(t.get("modalityCodes") or []) for t in tickets)
    mods, prog, idx = [], st.progress(0, text="Loading ticket modalities…"), 0
    for t in tickets:
        tcode = t.get("code") or ""
        tname = (t.get("name")
                 or ((t.get("datasheets") or {}).get("EN") or {}).get("name") or tcode)
        for ocode in t.get("modalityCodes") or []:
            try:
                full = client.get_ticket_option(supplier_id, tcode, ocode)
            except Exception:
                full = {}
            if not isinstance(full, dict) or "error" in full:
                full = {}
            mods.append({"ticket_code": tcode, "ticket_name": tname,
                         "option_code": ocode, "option_name": full.get("name") or ocode,
                         "raw": full})
            idx += 1
            prog.progress(idx / max(total, 1))
    prog.empty()
    return mods


def _load_closed_tours(client, supplier_id):
    _WRAPPER = ("closedTour", "closedTours", "items", "data", "results", "content")
    try:
        resp = client.get_closed_tours(supplier_id, first=0, limit=200)
    except Exception as e:
        st.error(f"Failed to fetch closed tours: {e}"); return None
    tours = [t for t in normalize_list_response(resp, _WRAPPER) if isinstance(t, dict)]
    if not tours:
        st.warning("No closed tours found."); return None
    full_tours, prog = [], st.progress(0, text="Loading closed tours…")
    for i, t in enumerate(tours):
        code = t.get("code") or t.get("providerCode") or ""
        try:
            full = client.get_closed_tour(supplier_id, code)
        except Exception:
            full = None
        full_tours.append(full if isinstance(full, dict) and "error" not in full else t)
        prog.progress((i + 1) / len(tours))
    prog.empty()
    return full_tours


def _load_hotels(client, supplier_id):
    _WRAPPER = ("hotel", "hotels", "items", "data", "results", "content")
    try:
        resp = client.get_hotels(supplier_id)
    except Exception as e:
        st.error(f"Failed to fetch hotels: {e}"); return None
    hotels = [h for h in normalize_list_response(resp, _WRAPPER) if isinstance(h, dict)]
    if not hotels:
        st.warning("No hotels found."); return None
    full_hotels, prog = [], st.progress(0, text="Loading hotels…")
    for i, h in enumerate(hotels):
        code = h.get("providerCode") or h.get("code") or ""
        try:
            full = client.get_hotel(supplier_id, code)
        except Exception:
            full = None
        full_hotels.append(full if isinstance(full, dict) and "error" not in full else h)
        prog.progress((i + 1) / len(hotels))
    prog.empty()
    return full_hotels


# ── per-type supplement dict builders ─────────────────────────────────────────

def _build_transfer_supp(spec):
    d = {"active": True, "name": spec["tr_name"], "type": spec["tr_type"],
         "amount": spec["tr_amount"],
         "startDate": spec["startDate"], "endDate": spec["endDate"]}
    if spec.get("tr_start_time"):
        d["startTime"] = spec["tr_start_time"]
    if spec.get("tr_end_time"):
        d["endTime"] = spec["tr_end_time"]
    return d


def _build_ticket_supp(spec, modality):
    if spec["tk_type"] == "ABSOLUTE":
        adult = spec["tk_amount"]
    else:
        pct = spec["tk_amount"]
        occ = modality.get("occupancyPrices") or []
        two = next((p for p in occ if isinstance(p, dict) and p.get("occupancy") == 2), None)
        if two and (two.get("amount") or 0) > 0:
            adult = math.ceil(two["amount"] / 2 * pct / 100)
        else:
            adult = math.ceil((modality.get("baseAdultPrice") or 0) * pct / 100)
    return {"adultPriceSupplement": adult, "childrenPriceSupplement": 0.0,
            "infantPriceSupplement": 0.0,
            "startDate": spec["startDate"], "endDate": spec["endDate"],
            "translations": {"EN": {"name": spec["name"]}}}


def _build_closedtour_supp(spec):
    quad = spec["ct_quad"] if spec["ct_quad"] > 0 else spec["ct_double"]
    return {
        "mandatory": True, "free": False, "commissionable": True,
        "refundable": True, "onRequest": False, "priceInPercentage": False,
        "price": {"singlePrice": spec["ct_single"], "doublePrice": spec["ct_double"],
                  "triplePrice": spec["ct_triple"], "quadruplePrice": quad},
        "travelWindows": [{"startDate": spec["startDate"], "endDate": spec["endDate"]}],
        "translations": {"EN": {"name": spec["name"]}},
    }


def _build_hotel_supp(spec):
    return {
        "type": spec["ht_type"],
        "apply": spec["ht_apply"],
        "value": spec["ht_value"],
        "childValue": spec["ht_child_value"],
        "providerRoomCodes": [],
        "mealPlans": [],
        "releaseDays": 0,
        "travelWindows": [{"startDate": spec["startDate"], "endDate": spec["endDate"]}],
        "names": [{"languageCode": "EN", "content": spec["name"]}],
    }


# ── apply helpers ──────────────────────────────────────────────────────────────

def _apply_transfers(client, sid, transfers, specs, selected_ids=None):
    targets = [t for t in transfers
               if selected_ids is None or t.get("id") in selected_ids]
    ok, errors = 0, []
    for t in targets:
        payload = copy.deepcopy(t)
        payload["supplements"] = list(t.get("supplements") or []) + \
                                  [_build_transfer_supp(s) for s in specs]
        try:
            resp = client.update_transfer(sid, payload)
            if isinstance(resp, dict) and "error" in resp:
                errors.append(f"{t.get('id')}: {resp.get('message') or resp}")
            else:
                ok += 1
        except Exception as e:
            errors.append(f"{t.get('id')}: {e}")
    return {"ok": ok, "failed": len(errors), "errors": errors}


def _apply_tickets(client, sid, modalities, specs, selected_keys=None):
    targets = [m for m in modalities
               if selected_keys is None
               or f"{m['ticket_code']}/{m['option_code']}" in selected_keys]
    ok, errors = 0, []
    for m in targets:
        raw = m["raw"]
        payload = copy.deepcopy(raw)
        payload["supplements"] = list(raw.get("supplements") or []) + \
                                  [_build_ticket_supp(s, raw) for s in specs]
        label = f"{m['ticket_code']}/{m['option_code']}"
        try:
            resp = client.update_ticket_option(sid, m["ticket_code"], payload)
            if isinstance(resp, dict) and "error" in resp:
                errors.append(f"{label}: {resp.get('message') or resp}")
            else:
                ok += 1
        except Exception as e:
            errors.append(f"{label}: {e}")
    return {"ok": ok, "failed": len(errors), "errors": errors}


def _apply_closedtours(client, sid, tours, specs, selected_codes=None):
    ok, errors = 0, []
    for t in tours:
        code = t.get("code") or t.get("providerCode") or ""
        if selected_codes and code not in selected_codes:
            continue
        for spec in specs:
            res = supplement_apply.apply_create(
                client, sid, "ClosedTour", [t], _build_closedtour_supp(spec))
            ok += res["ok"]
            errors.extend(f"{code}: {e}" for e in res["errors"])
    return {"ok": ok, "failed": len(errors), "errors": errors}


def _apply_hotels(client, sid, hotels, specs, selected_codes=None):
    ok, errors = 0, []
    for h in hotels:
        code = h.get("providerCode") or h.get("code") or ""
        if selected_codes and code not in selected_codes:
            continue
        for spec in specs:
            res = supplement_apply.apply_create(
                client, sid, "Hotel", [h], _build_hotel_supp(spec))
            ok += res["ok"]
            errors.extend(f"{code}: {e}" for e in res["errors"])
    return {"ok": ok, "failed": len(errors), "errors": errors}


# ── supplement form ────────────────────────────────────────────────────────────

def _render_one_supplement(idx, do_tr, do_tk, do_ct, do_ht):
    """Render one supplement entry. Returns a spec dict or None if invalid."""
    today = date.today()
    p = f"sb_{idx}"

    # Shared: name + dates
    name = st.text_input("Supplement name", key=f"{p}_name",
                         placeholder="e.g. Chinese New Year, Christmas Eve")
    cols_date = st.columns(2)
    with cols_date[0]:
        start_date = st.date_input("From (inclusive)", value=today,
                                   format="DD/MM/YYYY", key=f"{p}_sd")
    with cols_date[1]:
        ek = f"{p}_ed"
        if st.session_state.get(ek) is None or st.session_state[ek] < start_date:
            st.session_state[ek] = start_date
        end_date = st.date_input("To (inclusive)", value=st.session_state[ek],
                                 format="DD/MM/YYYY", key=ek)

    if not name:
        st.caption("⚠️ Enter a supplement name.")
        return None
    if end_date < start_date:
        st.error(f"Supplement {idx + 1}: end date must be on or after start date.")
        return None

    spec = {"name": name, "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat()}

    # Transfer fields
    if do_tr:
        st.markdown("**🚌 Transfer**")
        c1, c2, c3 = st.columns([2, 2, 1])
        with c1:
            spec["tr_type"] = st.selectbox("Type", ["ABSOLUTE", "PERCENT"], key=f"{p}_tr_type")
        with c2:
            spec["tr_amount"] = st.number_input("Amount", min_value=0.0, step=0.5,
                                                key=f"{p}_tr_amount")
        with c3:
            spec["tr_name"] = name  # same name, set below
        t1, t2 = st.columns(2)
        with t1:
            st_val = st.text_input("startTime HH:MM (optional)", value="",
                                   key=f"{p}_tr_stime", placeholder="e.g. 22:00")
            if st_val and not _TIME_PAT.match(st_val):
                st.error("startTime must be HH:MM."); return None
            spec["tr_start_time"] = st_val
        with t2:
            et_val = st.text_input("endTime HH:MM (optional)", value="",
                                   key=f"{p}_tr_etime", placeholder="e.g. 06:00")
            if et_val and not _TIME_PAT.match(et_val):
                st.error("endTime must be HH:MM."); return None
            spec["tr_end_time"] = et_val
        spec["tr_name"] = name

    # Ticket fields
    if do_tk:
        st.markdown("**🎟️ Ticket**")
        st.caption("PERCENT is auto-converted per modality: ⌈2-pax price ÷ 2 × %⌉")
        tc1, tc2 = st.columns(2)
        with tc1:
            spec["tk_type"] = st.selectbox("Type", ["ABSOLUTE", "PERCENT"], key=f"{p}_tk_type")
        with tc2:
            spec["tk_amount"] = st.number_input("Amount", min_value=0.0, step=0.5,
                                                key=f"{p}_tk_amount")

    # ClosedTour fields
    if do_ct:
        st.markdown("**🗺️ ClosedTour** _(per person per occupancy)_")
        st.caption("single ≥ double ≥ triple ≥ quadruple")
        cc1, cc2, cc3, cc4 = st.columns(4)
        with cc1:
            spec["ct_single"] = st.number_input("Single", min_value=0.0, step=0.5,
                                                key=f"{p}_ct_single")
        with cc2:
            spec["ct_double"] = st.number_input("Double", min_value=0.0, step=0.5,
                                                key=f"{p}_ct_double")
        with cc3:
            spec["ct_triple"] = st.number_input("Triple", min_value=0.0, step=0.5,
                                                key=f"{p}_ct_triple")
        with cc4:
            spec["ct_quad"] = st.number_input("Quadruple", min_value=0.0, step=0.5,
                                              key=f"{p}_ct_quad",
                                              help="Leave 0 to default to Double price")
        if (spec["ct_single"] and spec["ct_double"]
                and spec["ct_single"] < spec["ct_double"]):
            st.warning("Single should be ≥ Double.")

    # Hotel fields
    if do_ht:
        st.markdown("**🏨 Hotel**")
        hc1, hc2, hc3, hc4 = st.columns([2, 2, 1, 1])
        with hc1:
            spec["ht_apply"] = st.selectbox("Apply type", HOTEL_APPLY_TYPES, key=f"{p}_ht_apply")
        with hc2:
            spec["ht_type"] = st.selectbox("Type", ["ABSOLUTE", "PERCENT"], key=f"{p}_ht_type")
        with hc3:
            spec["ht_value"] = st.number_input("Adult", min_value=0.0, step=0.5,
                                               key=f"{p}_ht_value")
        with hc4:
            spec["ht_child_value"] = st.number_input("Child", min_value=0.0, step=0.5,
                                                     key=f"{p}_ht_child")

    return spec


def _render_supplements_form(do_tr, do_tk, do_ct, do_ht):
    count_key = "sb_count"
    if count_key not in st.session_state:
        st.session_state[count_key] = 1
    count = st.session_state[count_key]
    specs, all_valid = [], True
    for idx in range(count):
        with st.expander(f"Supplement {idx + 1}", expanded=True):
            if idx > 0:
                if st.button("🗑 Remove", key=f"sb_{idx}_del"):
                    st.session_state[count_key] = max(1, count - 1)
                    for suffix in ["_name", "_sd", "_ed",
                                   "_tr_type", "_tr_amount", "_tr_stime", "_tr_etime",
                                   "_tk_type", "_tk_amount",
                                   "_ct_single", "_ct_double", "_ct_triple", "_ct_quad",
                                   "_ht_apply", "_ht_type", "_ht_value", "_ht_child"]:
                        st.session_state.pop(f"sb_{idx}{suffix}", None)
                    st.rerun()
            spec = _render_one_supplement(idx, do_tr, do_tk, do_ct, do_ht)
            if spec is None:
                all_valid = False
            else:
                specs.append(spec)
    if st.button("＋ Add another supplement", key="sb_add"):
        st.session_state[count_key] += 1
        st.rerun()
    return specs if all_valid and specs else None


# ── main render ────────────────────────────────────────────────────────────────

def render_supplement_bulk_flow(client):
    from app_helpers import _ur_pick_momira_supplier

    st.header("➕ Bulk supplements")

    if "supplement_bulk_product" not in st.session_state:
        st.session_state.supplement_bulk_product = None

    # ── Type selection screen ───────────────────────────────────────────
    if st.session_state.supplement_bulk_product is None:
        if st.button("🔙 Back", key="supbulk_back"):
            st.session_state.product_type = None
            st.rerun()
        st.caption("Create supplements across many already-live products at once. "
                   "Pick one or more product types — all selected types are applied in one run.")

        col_a, col_b = st.columns(2)
        with col_a:
            cb_tr = st.checkbox("🚌 Transfer", value=True, key="supbulk_cb_tr")
            st.caption("Night, weekend and holiday supplements.")
            cb_ct = st.checkbox("🗺️ ClosedTour", value=False, key="supbulk_cb_ct")
            st.caption("Per-occupancy supplements (single / double / triple / quad).")
        with col_b:
            cb_tk = st.checkbox("🎟️ Ticket", value=True, key="supbulk_cb_tk")
            st.caption("Per-pax supplements. PERCENT → auto-converted to ABSOLUTE per modality.")
            cb_ht = st.checkbox("🏨 Hotel", value=False, key="supbulk_cb_ht")
            st.caption("Supplements via dedicated POST endpoint (apply type, adult/child value).")

        if not (cb_tr or cb_tk or cb_ct or cb_ht):
            st.warning("Select at least one product type.")
            return

        label_parts = ([" Transfer" if cb_tr else ""] + ["Ticket" if cb_tk else ""] +
                       ["ClosedTour" if cb_ct else ""] + ["Hotel" if cb_ht else ""])
        label = " + ".join(p for p in label_parts if p)

        if st.button(f"Continue with {label}", key="supbulk_proceed", type="primary",
                     use_container_width=True):
            # Save to non-widget keys so they can be freely written later
            st.session_state["sb_sel_tr"] = cb_tr
            st.session_state["sb_sel_tk"] = cb_tk
            st.session_state["sb_sel_ct"] = cb_ct
            st.session_state["sb_sel_ht"] = cb_ht
            st.session_state.supplement_bulk_product = "multi"
            # Clear any stale loaded data when re-entering
            for k in ["sb_transfers", "sb_modalities", "sb_tours", "sb_hotels",
                      "sb_last_sid", "sb_applied"]:
                st.session_state.pop(k, None)
            st.rerun()
        return

    # ── Back to type picker ─────────────────────────────────────────────
    if st.button("🔙 Pick a different product type", key="supbulk_change_type"):
        st.session_state.supplement_bulk_product = None
        for k in ["sb_sel_tr", "sb_sel_tk", "sb_sel_ct", "sb_sel_ht",
                  "sb_transfers", "sb_modalities", "sb_tours", "sb_hotels",
                  "sb_last_sid", "sb_applied", "sb_count"]:
            st.session_state.pop(k, None)
        st.rerun()

    do_tr = st.session_state.get("sb_sel_tr", False)
    do_tk = st.session_state.get("sb_sel_tk", False)
    do_ct = st.session_state.get("sb_sel_ct", False)
    do_ht = st.session_state.get("sb_sel_ht", False)

    type_label = " + ".join(
        p for p in (["Transfer" if do_tr else ""], ["Ticket" if do_tk else ""],
                    ["ClosedTour" if do_ct else ""], ["Hotel" if do_ht else ""]) if p[0]
    )
    st.caption(f"Product types: **{type_label}**")

    # ── Supplier ────────────────────────────────────────────────────────
    supplier_id = _ur_pick_momira_supplier(client, key_prefix="sb_sup")
    if not supplier_id:
        return

    if st.session_state.get("sb_last_sid") != supplier_id:
        st.session_state["sb_last_sid"] = supplier_id
        for k in ["sb_transfers", "sb_modalities", "sb_tours", "sb_hotels", "sb_applied"]:
            st.session_state.pop(k, None)

    # ── Load products ───────────────────────────────────────────────────
    need_load = (
        (do_tr and st.session_state.get("sb_transfers") is None) or
        (do_tk and st.session_state.get("sb_modalities") is None) or
        (do_ct and st.session_state.get("sb_tours") is None) or
        (do_ht and st.session_state.get("sb_hotels") is None)
    )
    if need_load:
        if st.button("Load products", key="sb_load", type="primary"):
            if do_tr:
                with st.spinner("Loading transfers…"):
                    st.session_state["sb_transfers"] = _load_transfers(client, supplier_id)
            if do_tk:
                with st.spinner("Loading ticket modalities…"):
                    st.session_state["sb_modalities"] = _load_ticket_modalities(
                        client, supplier_id)
            if do_ct:
                with st.spinner("Loading closed tours…"):
                    st.session_state["sb_tours"] = _load_closed_tours(client, supplier_id)
            if do_ht:
                with st.spinner("Loading hotels…"):
                    st.session_state["sb_hotels"] = _load_hotels(client, supplier_id)
            st.rerun()
        return

    transfers = st.session_state.get("sb_transfers") or []
    modalities = st.session_state.get("sb_modalities") or []
    tours = st.session_state.get("sb_tours") or []
    hotels = st.session_state.get("sb_hotels") or []

    if do_tr: st.info(f"**{len(transfers)}** transfer(s)")
    if do_tk: st.info(f"**{len(modalities)}** ticket modality/ies")
    if do_ct: st.info(f"**{len(tours)}** closed tour(s)")
    if do_ht: st.info(f"**{len(hotels)}** hotel(s)")

    st.divider()

    # ── Supplement form ─────────────────────────────────────────────────
    st.subheader("New supplements to add")
    specs = _render_supplements_form(do_tr, do_tk, do_ct, do_ht)
    if not specs:
        return

    st.divider()

    # ── Scope selectors ─────────────────────────────────────────────────
    sel_tids = sel_mkeys = sel_ctcodes = sel_htcodes = None

    if do_tr and transfers:
        scope = st.radio("Transfers: apply to?",
                         ["All transfers", "Select specific"], key="sb_scope_tr")
        if scope == "Select specific":
            opts = {t.get("id", ""): (t.get("name") or t.get("routeName") or t.get("id", ""))
                    for t in transfers}
            picks = st.multiselect("Select transfers", list(opts.keys()),
                                   format_func=lambda k: f"{opts[k]} ({k})",
                                   key="sb_pick_tr")
            if not picks:
                st.info("Select at least one transfer."); return
            sel_tids = set(picks)

    if do_tk and modalities:
        scope = st.radio("Ticket modalities: apply to?",
                         ["All modalities", "Select specific"], key="sb_scope_tk")
        if scope == "Select specific":
            opts_m = {f"{m['ticket_code']}/{m['option_code']}":
                      f"{m['ticket_name']} / {m['option_name']}" for m in modalities}
            picks_m = st.multiselect("Select modalities", list(opts_m.keys()),
                                     format_func=lambda k: f"{opts_m[k]} ({k})",
                                     key="sb_pick_tk")
            if not picks_m:
                st.info("Select at least one modality."); return
            sel_mkeys = set(picks_m)

    if do_ct and tours:
        scope = st.radio("ClosedTours: apply to?",
                         ["All closed tours", "Select specific"], key="sb_scope_ct")
        if scope == "Select specific":
            opts_ct = {(t.get("code") or t.get("providerCode") or ""):
                       (t.get("name") or t.get("code") or "") for t in tours}
            picks_ct = st.multiselect("Select closed tours", list(opts_ct.keys()),
                                      format_func=lambda k: f"{opts_ct[k]} ({k})",
                                      key="sb_pick_ct")
            if not picks_ct:
                st.info("Select at least one tour."); return
            sel_ctcodes = set(picks_ct)

    if do_ht and hotels:
        scope = st.radio("Hotels: apply to?",
                         ["All hotels", "Select specific"], key="sb_scope_ht")
        if scope == "Select specific":
            opts_ht = {(h.get("providerCode") or h.get("code") or ""):
                       (h.get("name") or h.get("providerCode") or "") for h in hotels}
            picks_ht = st.multiselect("Select hotels", list(opts_ht.keys()),
                                      format_func=lambda k: f"{opts_ht[k]} ({k})",
                                      key="sb_pick_ht")
            if not picks_ht:
                st.info("Select at least one hotel."); return
            sel_htcodes = set(picks_ht)

    # ── Apply ───────────────────────────────────────────────────────────
    t_target = len(sel_tids) if sel_tids else len(transfers)
    m_target = len(sel_mkeys) if sel_mkeys else len(modalities)
    ct_target = len(sel_ctcodes) if sel_ctcodes else len(tours)
    ht_target = len(sel_htcodes) if sel_htcodes else len(hotels)
    total_targets = ((t_target if do_tr else 0) + (m_target if do_tk else 0) +
                     (ct_target if do_ct else 0) + (ht_target if do_ht else 0))

    if st.session_state.get("sb_applied"):
        st.button(f"Apply {len(specs)} supplement(s)", key="sb_apply",
                  type="primary", disabled=True)
        st.caption("✅ Done — reload products to apply again.")
        return

    if not st.button(f"Apply {len(specs)} supplement(s) to {total_targets} record(s)",
                     key="sb_apply", type="primary"):
        return

    all_ok, all_failed, all_errors = 0, 0, []
    type_count = sum([do_tr, do_tk, do_ct, do_ht])
    bar = st.progress(0, text="Applying…")
    done_types = 0

    if do_tr and transfers:
        bar.progress(done_types / type_count, text="Applying to transfers…")
        res = _apply_transfers(client, supplier_id, transfers, specs, sel_tids)
        all_ok += res["ok"]; all_failed += res["failed"]
        all_errors.extend(f"[Transfer] {e}" for e in res["errors"])
        done_types += 1

    if do_tk and modalities:
        bar.progress(done_types / type_count, text="Applying to ticket modalities…")
        res = _apply_tickets(client, supplier_id, modalities, specs, sel_mkeys)
        all_ok += res["ok"]; all_failed += res["failed"]
        all_errors.extend(f"[Ticket] {e}" for e in res["errors"])
        done_types += 1

    if do_ct and tours:
        bar.progress(done_types / type_count, text="Applying to closed tours…")
        res = _apply_closedtours(client, supplier_id, tours, specs, sel_ctcodes)
        all_ok += res["ok"]; all_failed += res["failed"]
        all_errors.extend(f"[ClosedTour] {e}" for e in res["errors"])
        done_types += 1

    if do_ht and hotels:
        bar.progress(done_types / type_count, text="Applying to hotels…")
        res = _apply_hotels(client, supplier_id, hotels, specs, sel_htcodes)
        all_ok += res["ok"]; all_failed += res["failed"]
        all_errors.extend(f"[Hotel] {e}" for e in res["errors"])
        done_types += 1

    bar.progress(1.0, text="Done.")
    if all_ok:
        st.success(f"Done — **{all_ok}** record(s) updated.")
        st.session_state["sb_applied"] = True
    if all_failed:
        st.error(f"**{all_failed}** record(s) failed:")
        for err in all_errors:
            st.text(err)
