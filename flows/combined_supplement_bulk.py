"""
flows/combined_supplement_bulk.py

Bulk supplement tool for Transfer + Ticket in one shot.

The human defines one or more supplements (name, type, amount, date range,
optional time window) and applies all of them to both Transfers AND Ticket
modalities for one supplier in a single apply run.

PERCENT logic for Tickets:
  Ticket supplements are always a flat per-pax ABSOLUTE amount in TC
  (adultPriceSupplement). When the human enters type=PERCENT, the tool
  auto-computes the ABSOLUTE equivalent per modality:
    adult_supp = ceil(2-pax-total-occupancy-price / 2 * pct / 100)
  Fallback when no 2-pax entry exists: ceil(baseAdultPrice * pct / 100).

PERCENT for Transfers is stored as-is (TC natively supports PERCENT type).
"""
import copy
import math
import re
import streamlit as st
from datetime import date
from typing import Dict, List, Optional, Any

from app_helpers import _ur_pick_momira_supplier
from list_response_utils import normalize_list_response

MODULE_BUILD = "2026-11-27-combined-supplement-v1"

_TIME_PAT = re.compile(r"^\d{2}:\d{2}$")
_TICKET_WRAPPER_KEYS = ("ticket", "tickets", "items", "data", "results", "content")

# ── data loading ───────────────────────────────────────────────────────────────

def _load_transfers(client, supplier_id):
    try:
        resp = client.get_transfers(supplier_id)
    except Exception as e:
        st.error(f"Failed to fetch transfers: {e}")
        return None
    transfers = resp.get("transfer") or [] if isinstance(resp, dict) else []
    if not transfers:
        st.warning("No transfers found.")
        return None
    full = []
    prog = st.progress(0, text="Loading transfers…")
    for i, t in enumerate(transfers):
        tid = t.get("id", "")
        try:
            full.append(client.get_transfer(supplier_id, tid))
        except Exception:
            full.append(t)
        prog.progress((i + 1) / len(transfers))
    prog.empty()
    return full


def _load_ticket_modalities(client, supplier_id):
    try:
        resp = client.get_tickets(supplier_id, first=0, limit=200)
    except Exception as e:
        st.error(f"Failed to fetch tickets: {e}")
        return None
    tickets = normalize_list_response(resp, _TICKET_WRAPPER_KEYS)
    tickets = [t for t in tickets if isinstance(t, dict)]
    if not tickets:
        st.warning("No tickets found.")
        return None
    total = sum(len(t.get("modalityCodes") or []) for t in tickets)
    mods = []
    prog = st.progress(0, text="Loading ticket modalities…")
    idx = 0
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
            mods.append({
                "ticket_code": tcode,
                "ticket_name": tname,
                "option_code": ocode,
                "option_name": full.get("name") or ocode,
                "raw": full,
            })
            idx += 1
            prog.progress(idx / max(total, 1))
    prog.empty()
    return mods


# ── supplement form ────────────────────────────────────────────────────────────

def _render_one_supplement(idx: int) -> dict | None:
    """Render fields for one supplement entry. Returns spec dict or None on error."""
    today = date.today()

    cols_top = st.columns([3, 2, 2])
    with cols_top[0]:
        name = st.text_input("Supplement name", key=f"csb_name_{idx}",
                             placeholder="e.g. Chinese New Year, Christmas Eve")
    with cols_top[1]:
        supp_type = st.selectbox("Type", ["ABSOLUTE", "PERCENT"], key=f"csb_type_{idx}",
                                 help="PERCENT is auto-converted to ABSOLUTE for tickets (2-pax / 2 × %)")
    with cols_top[2]:
        amount = st.number_input("Amount", min_value=0.0, step=0.5, key=f"csb_amount_{idx}",
                                 help="Fixed € (ABSOLUTE) or percentage (PERCENT)")

    st.caption("⏰ Time window (transfers only — ignored for tickets)")
    cols_time = st.columns(2)
    with cols_time[0]:
        start_time = st.text_input("startTime HH:MM", value="",
                                   key=f"csb_stime_{idx}", placeholder="e.g. 22:00")
    with cols_time[1]:
        end_time = st.text_input("endTime HH:MM", value="",
                                 key=f"csb_etime_{idx}", placeholder="e.g. 06:00")

    cols_date = st.columns(2)
    with cols_date[0]:
        start_date = st.date_input("From (inclusive)", value=today,
                                   format="DD/MM/YYYY", key=f"csb_sd_{idx}")
    with cols_date[1]:
        _ek = f"csb_ed_{idx}"
        if st.session_state.get(_ek) is None or st.session_state[_ek] < start_date:
            st.session_state[_ek] = start_date
        end_date = st.date_input("To (inclusive)", value=st.session_state[_ek],
                                 format="DD/MM/YYYY", key=_ek)

    if not name:
        st.caption("⚠️ Enter a supplement name.")
        return None
    if start_time and not _TIME_PAT.match(start_time):
        st.error(f"Supplement {idx + 1}: startTime must be HH:MM."); return None
    if end_time and not _TIME_PAT.match(end_time):
        st.error(f"Supplement {idx + 1}: endTime must be HH:MM."); return None
    if end_date < start_date:
        st.error(f"Supplement {idx + 1}: end date must be on or after start date."); return None

    spec = {
        "name": name,
        "type": supp_type,
        "amount": amount,
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
    }
    if start_time:
        spec["startTime"] = start_time
    if end_time:
        spec["endTime"] = end_time
    return spec


def _render_supplements_form() -> list | None:
    """Multi-supplement form. Returns list of spec dicts or None if invalid."""
    st.subheader("Supplements to add")
    st.caption(
        "Define one or more supplements — all are applied in a single run. "
        "PERCENT type is stored natively on transfers. "
        "For tickets it is auto-converted: **adult supplement = ⌈2-pax price ÷ 2 × %⌉**."
    )

    if "csb_count" not in st.session_state:
        st.session_state.csb_count = 1

    count = st.session_state.csb_count
    supplements = []
    all_valid = True

    for idx in range(count):
        with st.expander(f"Supplement {idx + 1}", expanded=True):
            if idx > 0:
                if st.button("🗑 Remove", key=f"csb_del_{idx}"):
                    st.session_state.csb_count = max(1, count - 1)
                    for k in [f"csb_name_{idx}", f"csb_type_{idx}", f"csb_amount_{idx}",
                               f"csb_stime_{idx}", f"csb_etime_{idx}",
                               f"csb_sd_{idx}", f"csb_ed_{idx}"]:
                        st.session_state.pop(k, None)
                    st.rerun()
            supp = _render_one_supplement(idx)
            if supp is None:
                all_valid = False
            else:
                supplements.append(supp)

    if st.button("＋ Add another supplement", key="csb_add"):
        st.session_state.csb_count += 1
        st.rerun()

    if not all_valid or not supplements:
        return None
    return supplements


# ── apply helpers ──────────────────────────────────────────────────────────────

def _transfer_supp_dict(spec: dict) -> dict:
    """Convert a supplement spec to the Transfer supplement payload."""
    d = {
        "active": True,
        "name": spec["name"],
        "type": spec["type"],
        "amount": spec["amount"],
        "startDate": spec["startDate"],
        "endDate": spec["endDate"],
    }
    if spec.get("startTime"):
        d["startTime"] = spec["startTime"]
    if spec.get("endTime"):
        d["endTime"] = spec["endTime"]
    return d


def _ticket_adult_amount(spec: dict, modality: dict) -> float:
    """Compute the adultPriceSupplement for a ticket modality.

    ABSOLUTE: use the entered amount directly.
    PERCENT: find the 2-pax occupancy price, divide by 2, multiply by pct, ceil.
    """
    if spec["type"] == "ABSOLUTE":
        return spec["amount"]
    pct = spec["amount"]
    occ_prices = modality.get("occupancyPrices") or []
    two_pax = next((p for p in occ_prices
                    if isinstance(p, dict) and p.get("occupancy") == 2), None)
    if two_pax and (two_pax.get("amount") or 0) > 0:
        return math.ceil(two_pax["amount"] / 2 * pct / 100)
    # Fallback: use baseAdultPrice
    base = modality.get("baseAdultPrice") or 0
    return math.ceil(base * pct / 100)


def _ticket_supp_dict(spec: dict, modality: dict) -> dict:
    """Convert a supplement spec to the Ticket (TicketSupplementVO) payload."""
    return {
        "adultPriceSupplement": _ticket_adult_amount(spec, modality),
        "childrenPriceSupplement": 0.0,
        "infantPriceSupplement": 0.0,
        "startDate": spec["startDate"],
        "endDate": spec["endDate"],
        "translations": {"EN": {"name": spec["name"]}},
    }


def _apply_transfers(client, supplier_id, transfers, supplements,
                     selected_ids: Optional[set] = None,
                     progress=None) -> Dict[str, Any]:
    targets = [t for t in transfers
               if selected_ids is None or (t.get("id") in selected_ids)]
    ok, errors = 0, []
    for n, t in enumerate(targets, 1):
        existing = list(t.get("supplements") or [])
        new_supps = [_transfer_supp_dict(s) for s in supplements]
        payload = copy.deepcopy(t)
        payload["supplements"] = existing + new_supps
        try:
            resp = client.update_transfer(supplier_id, payload)
            if isinstance(resp, dict) and "error" in resp:
                errors.append(f"{t.get('id')}: {resp.get('message') or resp}")
            else:
                ok += 1
        except Exception as e:
            errors.append(f"{t.get('id')}: {e}")
        if progress:
            progress(n, len(targets))
    return {"ok": ok, "failed": len(errors), "errors": errors}


def _apply_tickets(client, supplier_id, modalities, supplements,
                   selected_keys: Optional[set] = None,
                   progress=None) -> Dict[str, Any]:
    targets = [m for m in modalities
               if selected_keys is None
               or f"{m['ticket_code']}/{m['option_code']}" in (selected_keys or set())]
    ok, errors = 0, []
    for n, m in enumerate(targets, 1):
        raw = m["raw"]
        existing = list(raw.get("supplements") or [])
        new_supps = [_ticket_supp_dict(s, raw) for s in supplements]
        payload = copy.deepcopy(raw)
        payload["supplements"] = existing + new_supps
        label = f"{m['ticket_code']}/{m['option_code']}"
        try:
            resp = client.update_ticket_option(supplier_id, m["ticket_code"], payload)
            if isinstance(resp, dict) and "error" in resp:
                errors.append(f"{label}: {resp.get('message') or resp}")
            else:
                ok += 1
        except Exception as e:
            errors.append(f"{label}: {e}")
        if progress:
            progress(n, len(targets))
    return {"ok": ok, "failed": len(errors), "errors": errors}


# ── main render ────────────────────────────────────────────────────────────────

def render_combined_supplement_bulk(client):
    st.header("Bulk Supplements — Transfer + Ticket")
    st.caption(
        "Apply the same supplements to Transfers **and** Ticket modalities for one "
        "supplier in a single run. PERCENT supplements are auto-converted to ABSOLUTE "
        "for tickets (⌈2-pax price ÷ 2 × %⌉, rounded up)."
    )
    st.caption(f"Build: `{MODULE_BUILD}`")

    # ── Supplier ────────────────────────────────────────────────────────
    supplier_id = _ur_pick_momira_supplier(client, key_prefix="csb")
    if not supplier_id:
        return

    if st.session_state.get("csb_last_sid") != supplier_id:
        st.session_state.csb_last_sid = supplier_id
        for k in ["csb_transfers", "csb_modalities", "csb_applied"]:
            st.session_state.pop(k, None)

    # ── Product type selection ───────────────────────────────────────────
    st.markdown("**Apply to:**")
    col_t, col_k = st.columns(2)
    with col_t:
        do_transfers = st.checkbox("Transfers", value=True, key="csb_do_transfers")
    with col_k:
        do_tickets = st.checkbox("Tickets", value=True, key="csb_do_tickets")

    if not do_transfers and not do_tickets:
        st.warning("Select at least one product type.")
        return

    # ── Load data ────────────────────────────────────────────────────────
    need_load = (
        (do_transfers and st.session_state.get("csb_transfers") is None) or
        (do_tickets and st.session_state.get("csb_modalities") is None)
    )
    if need_load:
        if st.button("Load products", key="csb_load", type="primary"):
            if do_transfers:
                with st.spinner("Loading transfers…"):
                    st.session_state.csb_transfers = _load_transfers(client, supplier_id)
            if do_tickets:
                with st.spinner("Loading ticket modalities…"):
                    st.session_state.csb_modalities = _load_ticket_modalities(client, supplier_id)
            st.rerun()
        return

    transfers = st.session_state.get("csb_transfers") or []
    modalities = st.session_state.get("csb_modalities") or []

    if do_transfers:
        st.info(f"**{len(transfers)}** transfer(s) loaded.")
    if do_tickets:
        st.info(f"**{len(modalities)}** ticket modality/ies loaded.")

    st.divider()

    # ── Supplement form ──────────────────────────────────────────────────
    supplements = _render_supplements_form()
    if not supplements:
        return

    st.divider()

    # ── Scope selectors ──────────────────────────────────────────────────
    selected_tids = None
    if do_transfers and transfers:
        scope_t = st.radio("Add to which transfers?",
                           ["All transfers", "Select specific transfers"],
                           key="csb_scope_t")
        if scope_t == "Select specific transfers":
            opts = {t.get("id", ""): (t.get("name") or t.get("routeName") or t.get("id", ""))
                    for t in transfers}
            picks = st.multiselect("Select transfers", list(opts.keys()),
                                   format_func=lambda k: f"{opts[k]} ({k})",
                                   key="csb_pick_t")
            if not picks:
                st.info("Select at least one transfer.")
                return
            selected_tids = set(picks)

    selected_mkeys = None
    if do_tickets and modalities:
        scope_m = st.radio("Add to which ticket modalities?",
                           ["All modalities", "Select specific modalities"],
                           key="csb_scope_m")
        if scope_m == "Select specific modalities":
            opts_m = {f"{m['ticket_code']}/{m['option_code']}":
                      f"{m['ticket_name']} / {m['option_name']}" for m in modalities}
            picks_m = st.multiselect("Select modalities", list(opts_m.keys()),
                                     format_func=lambda k: f"{opts_m[k]} ({k})",
                                     key="csb_pick_m")
            if not picks_m:
                st.info("Select at least one modality.")
                return
            selected_mkeys = set(picks_m)

    # ── Preview ──────────────────────────────────────────────────────────
    t_target = len(selected_tids) if selected_tids else len(transfers)
    m_target = len(selected_mkeys) if selected_mkeys else len(modalities)
    n_supps = len(supplements)

    with st.expander(f"Preview: {n_supps} supplement(s)"):
        for s in supplements:
            line = f"**{s['name']}** — {s['type']} {s['amount']}"
            line += f" · {s['startDate']} → {s['endDate']}"
            if s.get("startTime") or s.get("endTime"):
                line += f" · times {s.get('startTime', '')}–{s.get('endTime', '')} (transfers only)"
            if s["type"] == "PERCENT":
                line += " · _(tickets: auto-computed per modality)_"
            st.markdown(line)
        if do_transfers:
            st.caption(f"→ Transfers: **{t_target}** record(s)")
        if do_tickets:
            st.caption(f"→ Ticket modalities: **{m_target}** record(s)")

    # ── Apply button ─────────────────────────────────────────────────────
    if st.session_state.get("csb_applied"):
        st.button("Apply supplements", key="csb_apply", type="primary", disabled=True)
        st.caption("✅ Supplements applied — reload products to apply again.")
        return

    total_targets = (t_target if do_transfers else 0) + (m_target if do_tickets else 0)
    if not st.button(f"Apply {n_supps} supplement(s) to {total_targets} record(s)",
                     key="csb_apply", type="primary"):
        return

    # ── Execute ───────────────────────────────────────────────────────────
    all_ok, all_failed, all_errors = 0, 0, []
    bar = st.progress(0, text="Applying…")

    if do_transfers and transfers:
        def _prog_t(done, total):
            bar.progress(done / max(total, 1) * 0.5,
                         text=f"Transfers: {done}/{total}")
        res = _apply_transfers(client, supplier_id, transfers, supplements,
                               selected_ids=selected_tids, progress=_prog_t)
        all_ok += res["ok"]
        all_failed += res["failed"]
        all_errors.extend(f"[Transfer] {e}" for e in res["errors"])

    if do_tickets and modalities:
        def _prog_m(done, total):
            offset = 0.5 if do_transfers else 0.0
            bar.progress(offset + done / max(total, 1) * (1.0 - offset),
                         text=f"Ticket modalities: {done}/{total}")
        res = _apply_tickets(client, supplier_id, modalities, supplements,
                             selected_keys=selected_mkeys, progress=_prog_m)
        all_ok += res["ok"]
        all_failed += res["failed"]
        all_errors.extend(f"[Ticket] {e}" for e in res["errors"])

    bar.empty()

    if all_ok:
        st.success(f"Done — **{all_ok}** record(s) updated.")
        st.session_state.csb_applied = True
    if all_failed:
        st.error(f"**{all_failed}** record(s) failed:")
        for err in all_errors:
            st.text(err)
