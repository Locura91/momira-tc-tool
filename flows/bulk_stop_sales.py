"""
flows/bulk_stop_sales.py — Bulk Stop Sales: block a date or date range across many products
at once, without reading an email.

WHAT IT IS FOR: a supplier closes for a public holiday, a high-season blackout, or a
maintenance window that affects many of their products at once. Rather than opening each
product individually in Travel Compositor and adding the same block, the human picks a
supplier, ticks the products they want to close, sets one date range, and clicks Apply.

MERGE, NEVER REPLACE: same rule as stop_sales_tool — each write reads the live record first
and APPENDS the new ranges to whatever is already there. Nothing already blocked is lost.

PRODUCT TYPE SUPPORT:
  Transfer   — stopSales lives directly on ContractTransferVO; we GET the record, merge,
                PUT it back whole.
  Ticket     — stopSales on each ContractTicketModalityVO; one PUT per modality.
  ClosedTour — stopSales on each ContractClosedTourOptionVO; one PUT per option.
  Hotel      — stopSales inside each ContractHotelRateVO, grouped per room; PUT whole rate.
  Transport  — Travel Compositor has NO stopSales field on transport contracts; excluded.

NOTE ON NAMING: Travel Compositor's StopSale schema is {start, end} only — there is no
name/label field. The "name" field shown in the UI is a local convenience label for the
human's own reference; it is not sent to TC and not stored.
"""

MODULE_BUILD = "2026-10-24-rollover-ext"

import json
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

import stop_sales_tool as sst
from ai_extractor import friendly_error_message
from ui_components import is_active_supplier
from flows.flow_ui_helpers import load_momira_suppliers

_PRODUCT_TYPES = ["Transfer", "Ticket", "ClosedTour", "Hotel"]
_TYPE_LABELS = {
    "Transfer": "Transfer",
    "Ticket": "Ticket",
    "ClosedTour": "Closed Tour",
    "Hotel": "Hotel",
}

# ── session-state helpers ────────────────────────────────────────────────────

def _get(key, default=None):
    return st.session_state.get(key, default)

def _set(key, val):
    st.session_state[key] = val


# ── product loaders ──────────────────────────────────────────────────────────

def _load_transfers(client, supplier_id: str) -> Tuple[List[Dict], Optional[str]]:
    try:
        data = client.get_transfers(supplier_id)
    except Exception as e:
        return [], friendly_error_message(e)
    if isinstance(data, dict) and "error" in data:
        return [], str(data.get("message") or data.get("error"))
    items = data.get("transfers", []) if isinstance(data, dict) else (data or [])
    out = []
    for t in (items or []):
        if not isinstance(t, dict):
            continue
        tid = t.get("id")
        name = t.get("name") or tid or "(unnamed)"
        out.append({"id": tid, "name": name, "kind": "Transfer"})
    return sorted(out, key=lambda x: x["name"]), None


def _load_tickets(client, supplier_id: str) -> Tuple[List[Dict], Optional[str]]:
    try:
        data = client.get_tickets(supplier_id, first=0, limit=200)
    except Exception as e:
        return [], friendly_error_message(e)
    if isinstance(data, dict) and "error" in data:
        return [], str(data.get("message") or data.get("error"))
    tickets = data.get("tickets", []) if isinstance(data, dict) else (data or [])
    out = []
    for t in (tickets or []):
        if not isinstance(t, dict):
            continue
        code = t.get("code")
        name = t.get("name") or ((t.get("datasheets") or {}).get("EN") or {}).get("name") or code
        modalities = t.get("modalityCodes") or []
        out.append({"id": code, "name": name, "modalities": modalities, "kind": "Ticket"})
    return sorted(out, key=lambda x: x["name"]), None


def _load_tours(client, supplier_id: str) -> Tuple[List[Dict], Optional[str]]:
    try:
        data = client.get_closed_tours(supplier_id, first=0, limit=200)
    except Exception as e:
        return [], friendly_error_message(e)
    if isinstance(data, dict) and "error" in data:
        return [], str(data.get("message") or data.get("error"))
    tours = data.get("closedTours", []) if isinstance(data, dict) else (data or [])
    out = []
    for t in (tours or []):
        if not isinstance(t, dict):
            continue
        code = t.get("providerCode") or t.get("code")
        name = t.get("name") or ((t.get("datasheets") or {}).get("EN") or {}).get("name") or code
        modalities = t.get("optionCodes") or []
        out.append({"id": code, "name": name, "modalities": modalities, "kind": "ClosedTour"})
    return sorted(out, key=lambda x: x["name"]), None


def _load_hotels(client, supplier_id: str) -> Tuple[List[Dict], Optional[str]]:
    try:
        data = client.get_hotels(supplier_id)
    except Exception as e:
        return [], friendly_error_message(e)
    if isinstance(data, dict) and "error" in data:
        return [], str(data.get("message") or data.get("error"))
    hotels = data.get("hotels", []) if isinstance(data, dict) else (data or [])
    out = []
    for h in (hotels or []):
        if not isinstance(h, dict):
            continue
        code = h.get("providerCode") or h.get("code")
        name = h.get("name") or code
        rooms = h.get("rooms") or []
        room_names = [r if isinstance(r, str) else r.get("name", "") for r in rooms]
        out.append({"id": code, "name": name, "rooms": room_names, "kind": "Hotel"})
    return sorted(out, key=lambda x: x["name"]), None


# ── apply helpers ─────────────────────────────────────────────────────────────

def _apply_transfer(client, supplier_id: str, product: Dict, new_ranges: List[Dict]) -> Dict:
    """GET the transfer, merge stop sales, PUT it back."""
    tid = product["id"]
    try:
        rec = client.get_transfer(supplier_id, tid)
    except Exception as e:
        return {"status": "failed", "name": product["name"],
                "detail": friendly_error_message(e)}
    if isinstance(rec, dict) and "error" in rec:
        return {"status": "failed", "name": product["name"],
                "detail": str(rec.get("message") or rec.get("error"))}
    import stop_sales_parser as ssp
    live = [s for s in (rec.get("stopSales") or []) if isinstance(s, dict)]
    result = ssp.merge_stop_sales(live, new_ranges)
    if not result["added"]:
        return {"status": "unchanged", "name": product["name"],
                "detail": "every date was already blocked on this Transfer"}
    payload = dict(rec)
    payload["stopSales"] = result["merged"]
    try:
        res = client.update_transfer(supplier_id, payload)
    except Exception as e:
        return {"status": "failed", "name": product["name"],
                "detail": friendly_error_message(e)}
    if isinstance(res, dict) and "error" in res:
        return {"status": "failed", "name": product["name"],
                "detail": str(res.get("message") or res.get("error"))}
    return {"status": "updated", "name": product["name"], "added": result["added"]}


def _apply_ticket(client, supplier_id: str, product: Dict, new_ranges: List[Dict]) -> List[Dict]:
    results = []
    for mod_code in (product.get("modalities") or []):
        try:
            opt = client.get_ticket_option(supplier_id, product["id"], mod_code)
        except Exception as e:
            results.append({"status": "failed",
                            "name": f"{product['name']} / {mod_code}",
                            "detail": friendly_error_message(e)})
            continue
        if isinstance(opt, dict) and "error" in opt:
            results.append({"status": "failed",
                            "name": f"{product['name']} / {mod_code}",
                            "detail": str(opt.get("message") or opt.get("error"))})
            continue
        r = sst.apply_to_ticket_option(client, supplier_id, product["id"], opt, new_ranges)
        r["name"] = f"{product['name']} / {mod_code}"
        results.append(r)
    return results


def _apply_tour(client, supplier_id: str, product: Dict, new_ranges: List[Dict]) -> List[Dict]:
    results = []
    for opt_code in (product.get("modalities") or []):
        try:
            opt = client.get_closed_tour_option(supplier_id, product["id"], opt_code)
        except Exception as e:
            results.append({"status": "failed",
                            "name": f"{product['name']} / {opt_code}",
                            "detail": friendly_error_message(e)})
            continue
        if isinstance(opt, dict) and "error" in opt:
            results.append({"status": "failed",
                            "name": f"{product['name']} / {opt_code}",
                            "detail": str(opt.get("message") or opt.get("error"))})
            continue
        r = sst.apply_to_tour_option(client, supplier_id, product["id"], opt, new_ranges)
        r["name"] = f"{product['name']} / {opt_code}"
        results.append(r)
    return results


def _apply_hotel(client, supplier_id: str, product: Dict, new_ranges: List[Dict]) -> List[Dict]:
    try:
        rec = client.get_hotel(supplier_id, product["id"])
    except Exception as e:
        return [{"status": "failed", "name": product["name"],
                 "detail": friendly_error_message(e)}]
    if isinstance(rec, dict) and "error" in rec:
        return [{"status": "failed", "name": product["name"],
                 "detail": str(rec.get("message") or rec.get("error"))}]
    rates = rec.get("rates") or []
    if not rates:
        return [{"status": "unchanged", "name": product["name"],
                 "detail": "no rates found on this hotel"}]
    room_names = product.get("rooms") or []
    results = []
    for rate in rates:
        if not isinstance(rate, dict):
            continue
        r = sst.apply_to_hotel_rate(client, supplier_id, product["id"],
                                     rate, new_ranges, room_names)
        rate_name = rate.get("name") or rate.get("code") or "(unnamed rate)"
        r["name"] = f"{product['name']} / {rate_name}"
        results.append(r)
    return results


# ── main render function ──────────────────────────────────────────────────────

def render_bulk_stop_sales_flow(client):
    st.header("🚫 Bulk Stop Sales")
    if st.button("🔙 Back", key="bss_back"):
        st.session_state.product_type = None
        st.rerun()
    st.caption("Block a date or date range across many products at once. "
               "Existing stop sales on each product are kept — these are merged in.")

    # ── supplier ──────────────────────────────────────────────────────────────
    suppliers = load_momira_suppliers(client)
    supplier_names = [s["name"] for s in suppliers] if suppliers else []
    supplier_name = st.selectbox("Supplier", supplier_names,
                                 index=None, placeholder="Choose a supplier…",
                                 key="bss_supplier")
    if not supplier_name:
        st.stop()
    supplier = next((s for s in suppliers if s["name"] == supplier_name), None)
    supplier_id = supplier["id"] if supplier else None
    if not supplier_id:
        st.error("Supplier not found.")
        st.stop()

    # ── product types ─────────────────────────────────────────────────────────
    st.markdown("**Which product types?**")
    type_cols = st.columns(len(_PRODUCT_TYPES))
    selected_types = []
    for i, pt in enumerate(_PRODUCT_TYPES):
        with type_cols[i]:
            if st.checkbox(_TYPE_LABELS[pt], value=False, key=f"bss_type_{pt}"):
                selected_types.append(pt)

    if not selected_types:
        st.info("Tick at least one product type above.")
        st.stop()

    st.caption("ℹ️ Transport is not listed — Travel Compositor does not support stop sales "
               "on Transport contracts.")

    # ── load products ─────────────────────────────────────────────────────────
    load_key = f"bss_products_{supplier_id}_{'_'.join(sorted(selected_types))}"
    if _get("bss_load_key") != load_key or not _get("bss_products"):
        if st.button("🔄 Load products", key="bss_load"):
            with st.spinner("Loading products from Travel Compositor…"):
                all_products: List[Dict] = []
                errors = []
                loaders = {
                    "Transfer": _load_transfers,
                    "Ticket": _load_tickets,
                    "ClosedTour": _load_tours,
                    "Hotel": _load_hotels,
                }
                for pt in selected_types:
                    items, err = loaders[pt](client, supplier_id)
                    if err:
                        errors.append(f"{_TYPE_LABELS[pt]}: {err}")
                    all_products.extend(items)
                _set("bss_products", all_products)
                _set("bss_load_key", load_key)
                _set("bss_selected_ids", set())
                if errors:
                    for e in errors:
                        st.warning(e)
            st.rerun()
        st.stop()

    all_products: List[Dict] = _get("bss_products") or []
    if not all_products:
        st.warning("No products found for this supplier and product type combination.")
        st.stop()

    # ── product selection ─────────────────────────────────────────────────────
    st.markdown(f"**Select products** ({len(all_products)} found)")

    sel_cols = st.columns([1, 1])
    with sel_cols[0]:
        if st.button("✅ Select all", key="bss_sel_all"):
            _set("bss_selected_ids", {p["id"] for p in all_products})
            st.rerun()
    with sel_cols[1]:
        if st.button("☐ Clear all", key="bss_sel_none"):
            _set("bss_selected_ids", set())
            st.rerun()

    selected_ids: set = _get("bss_selected_ids") or set()

    # Group by type for display
    by_type: Dict[str, List] = {}
    for p in all_products:
        by_type.setdefault(p["kind"], []).append(p)

    new_selected = set()
    for pt in selected_types:
        items = by_type.get(pt, [])
        if not items:
            continue
        with st.expander(f"{_TYPE_LABELS[pt]} ({len(items)})", expanded=True):
            for p in items:
                checked = p["id"] in selected_ids
                if st.checkbox(p["name"], value=checked, key=f"bss_prod_{p['id']}"):
                    new_selected.add(p["id"])
    _set("bss_selected_ids", new_selected)

    selected_products = [p for p in all_products if p["id"] in new_selected]
    if not selected_products:
        st.info("Tick at least one product above.")
        st.stop()

    st.success(f"**{len(selected_products)} product(s) selected**")

    # ── date range ────────────────────────────────────────────────────────────
    st.markdown("**Stop sale date range**")
    dcol1, dcol2 = st.columns(2)
    with dcol1:
        start_date = st.date_input("From (inclusive)", value=date.today(),
                                   format="DD.MM.YYYY", key="bss_start")
    with dcol2:
        end_date = st.date_input("To (inclusive)", value=date.today(),
                                 format="DD.MM.YYYY", key="bss_end")

    if end_date < start_date:
        st.error("End date must be on or after start date.")
        st.stop()

    new_ranges = [{"start": start_date.isoformat(), "end": end_date.isoformat()}]

    if start_date == end_date:
        st.caption(f"Single day: **{start_date.strftime('%d.%m.%Y')}**")
    else:
        days = (end_date - start_date).days + 1
        st.caption(f"**{start_date.strftime('%d.%m.%Y')} – {end_date.strftime('%d.%m.%Y')}** "
                   f"({days} days)")

    st.caption("ℹ️ Travel Compositor does not store a name/label on stop sales — "
               "the dates themselves are the record.")

    # ── remove expired stop sales ─────────────────────────────────────────────
    st.divider()
    with st.expander("🧹 Remove expired stop sales", expanded=False):
        st.caption(
            "Scans the selected products for stop-sale ranges whose end date is already in "
            "the past, shows you what would be removed, and deletes them all in one go."
        )
        today_str = date.today().isoformat()

        if st.button("🔍 Scan for expired stop sales", key="bss_scan_expired"):
            expired_items = []
            with st.spinner("Scanning…"):
                for product in selected_products:
                    kind = product["kind"]
                    pid = product["id"]
                    pname = product["name"]
                    if kind == "Transfer":
                        try:
                            rec = client.get_transfer(supplier_id, pid)
                        except Exception:
                            continue
                        if isinstance(rec, dict) and "error" not in rec:
                            for s in (rec.get("stopSales") or []):
                                if isinstance(s, dict) and (s.get("end") or "") < today_str:
                                    expired_items.append({
                                        "kind": kind, "pid": pid, "pname": pname,
                                        "label": pname,
                                        "start": s.get("start"), "end": s.get("end"),
                                    })
                    elif kind in ("Ticket", "ClosedTour"):
                        for mod_code in (product.get("modalities") or []):
                            try:
                                if kind == "Ticket":
                                    opt = client.get_ticket_option(supplier_id, pid, mod_code)
                                else:
                                    opt = client.get_closed_tour_option(supplier_id, pid, mod_code)
                            except Exception:
                                continue
                            if not isinstance(opt, dict) or "error" in opt:
                                continue
                            for s in (opt.get("stopSales") or []):
                                if isinstance(s, dict) and (s.get("end") or "") < today_str:
                                    expired_items.append({
                                        "kind": kind, "pid": pid, "pname": pname,
                                        "mod": mod_code,
                                        "label": f"{pname} / {mod_code}",
                                        "start": s.get("start"), "end": s.get("end"),
                                    })
                    elif kind == "Hotel":
                        try:
                            rec = client.get_hotel(supplier_id, pid)
                        except Exception:
                            continue
                        if isinstance(rec, dict) and "error" not in rec:
                            for rate in (rec.get("rates") or []):
                                if not isinstance(rate, dict):
                                    continue
                                rate_name = rate.get("name") or rate.get("code") or "(rate)"
                                for s in (rate.get("stopSales") or []):
                                    if isinstance(s, dict) and (s.get("end") or "") < today_str:
                                        expired_items.append({
                                            "kind": kind, "pid": pid, "pname": pname,
                                            "rate_code": rate.get("code"),
                                            "label": f"{pname} / {rate_name}",
                                            "start": s.get("start"), "end": s.get("end"),
                                        })
            _set("bss_expired_items", expired_items)
            st.rerun()

        expired_items = _get("bss_expired_items")
        if expired_items is not None:
            if not expired_items:
                st.success("No expired stop sales found on the selected products.")
            else:
                st.warning(
                    f"Found **{len(expired_items)} expired stop-sale range(s)** "
                    f"across the selected products:"
                )
                for item in expired_items:
                    st.markdown(
                        f"- **{item['label']}**: "
                        f"{item['start']} → {item['end']}"
                    )

                if st.button(
                    f"🗑️ Remove all {len(expired_items)} expired range(s)",
                    type="primary", key="bss_delete_expired"
                ):
                    deleted_count = 0
                    delete_errors = []
                    bar2 = st.progress(0.0, text="Removing…")

                    # Group by (kind, pid, mod/rate_code) to batch per record
                    from collections import defaultdict
                    groups = defaultdict(list)
                    for item in expired_items:
                        key = (item["kind"], item["pid"],
                               item.get("mod") or item.get("rate_code") or "")
                        groups[key].append({"start": item["start"], "end": item["end"]})

                    total = len(groups)
                    for idx, ((kind, pid, sub_key), ranges_to_remove) in \
                            enumerate(groups.items()):
                        bar2.progress((idx + 1) / total,
                                      text=f"{idx + 1}/{total}: {kind} {pid}")
                        try:
                            if kind == "Transfer":
                                rec = client.get_transfer(supplier_id, pid)
                                if isinstance(rec, dict) and "error" not in rec:
                                    live = [s for s in (rec.get("stopSales") or [])
                                            if isinstance(s, dict)]
                                    kept = [s for s in live
                                            if not any(
                                                s.get("start") == r["start"] and
                                                s.get("end") == r["end"]
                                                for r in ranges_to_remove)]
                                    payload = dict(rec)
                                    payload["stopSales"] = kept
                                    res = client.update_transfer(supplier_id, payload)
                                    if isinstance(res, dict) and "error" in res:
                                        delete_errors.append(
                                            f"Transfer {pid}: "
                                            f"{res.get('message') or res.get('error')}")
                                    else:
                                        deleted_count += len(ranges_to_remove)
                            elif kind == "Ticket":
                                opt = client.get_ticket_option(supplier_id, pid, sub_key)
                                if isinstance(opt, dict) and "error" not in opt:
                                    live = [s for s in (opt.get("stopSales") or [])
                                            if isinstance(s, dict)]
                                    kept = [s for s in live
                                            if not any(
                                                s.get("start") == r["start"] and
                                                s.get("end") == r["end"]
                                                for r in ranges_to_remove)]
                                    opt_payload = dict(opt)
                                    opt_payload["stopSales"] = kept
                                    res = client.update_ticket_option(
                                        supplier_id, pid, opt_payload)
                                    if isinstance(res, dict) and "error" in res:
                                        delete_errors.append(
                                            f"Ticket {pid}/{sub_key}: "
                                            f"{res.get('message') or res.get('error')}")
                                    else:
                                        deleted_count += len(ranges_to_remove)
                            elif kind == "ClosedTour":
                                opt = client.get_closed_tour_option(
                                    supplier_id, pid, sub_key)
                                if isinstance(opt, dict) and "error" not in opt:
                                    live = [s for s in (opt.get("stopSales") or [])
                                            if isinstance(s, dict)]
                                    kept = [s for s in live
                                            if not any(
                                                s.get("start") == r["start"] and
                                                s.get("end") == r["end"]
                                                for r in ranges_to_remove)]
                                    opt_payload = dict(opt)
                                    opt_payload["stopSales"] = kept
                                    res = client.update_closed_tour_option(
                                        supplier_id, pid, opt_payload)
                                    if isinstance(res, dict) and "error" in res:
                                        delete_errors.append(
                                            f"ClosedTour {pid}/{sub_key}: "
                                            f"{res.get('message') or res.get('error')}")
                                    else:
                                        deleted_count += len(ranges_to_remove)
                            elif kind == "Hotel":
                                rec = client.get_hotel(supplier_id, pid)
                                if isinstance(rec, dict) and "error" not in rec:
                                    updated_rates = []
                                    for rate in (rec.get("rates") or []):
                                        if not isinstance(rate, dict):
                                            updated_rates.append(rate)
                                            continue
                                        if (rate.get("code") or rate.get("name") or "") \
                                                == sub_key:
                                            live = [s for s in (rate.get("stopSales") or [])
                                                    if isinstance(s, dict)]
                                            kept_r = [s for s in live
                                                      if not any(
                                                          s.get("start") == r["start"] and
                                                          s.get("end") == r["end"]
                                                          for r in ranges_to_remove)]
                                            rate = dict(rate)
                                            rate["stopSales"] = kept_r
                                            deleted_count += len(ranges_to_remove)
                                        updated_rates.append(rate)
                                    payload = dict(rec)
                                    payload["rates"] = updated_rates
                                    res = client.update_hotel(supplier_id, payload)
                                    if isinstance(res, dict) and "error" in res:
                                        delete_errors.append(
                                            f"Hotel {pid}: "
                                            f"{res.get('message') or res.get('error')}")
                                        deleted_count -= len(ranges_to_remove)
                        except Exception as e:
                            delete_errors.append(f"{kind} {pid}: {friendly_error_message(e)}")

                    bar2.empty()
                    _set("bss_expired_items", None)
                    if deleted_count:
                        st.success(f"✅ Removed {deleted_count} expired range(s).")
                    if delete_errors:
                        st.error(f"❌ {len(delete_errors)} error(s):")
                        for err in delete_errors:
                            st.markdown(f"- {err}")
                    st.rerun()

    # ── apply ─────────────────────────────────────────────────────────────────
    st.divider()
    st.warning(
        f"This will block **{start_date.strftime('%d.%m.%Y')} – {end_date.strftime('%d.%m.%Y')}** "
        f"on **{len(selected_products)} product(s)**. "
        f"Existing blocks are kept; these dates are added to them."
    )

    if st.button("✅ Apply stop sales", type="primary", key="bss_apply"):
        all_results = []
        bar = st.progress(0.0, text="Applying…")
        for i, product in enumerate(selected_products):
            bar.progress((i + 1) / len(selected_products),
                         text=f"{i + 1}/{len(selected_products)}: {product['name']}")
            kind = product["kind"]
            if kind == "Transfer":
                r = _apply_transfer(client, supplier_id, product, new_ranges)
                all_results.append(r)
            elif kind == "Ticket":
                all_results.extend(_apply_ticket(client, supplier_id, product, new_ranges))
            elif kind == "ClosedTour":
                all_results.extend(_apply_tour(client, supplier_id, product, new_ranges))
            elif kind == "Hotel":
                all_results.extend(_apply_hotel(client, supplier_id, product, new_ranges))
        bar.empty()
        _set("bss_result", all_results)
        st.rerun()

    results = _get("bss_result")
    if results:
        updated = [r for r in results if r["status"] == "updated"]
        failed = [r for r in results if r["status"] == "failed"]
        unchanged = [r for r in results if r["status"] == "unchanged"]

        if updated:
            st.success(f"✅ Stop sale applied to {len(updated)} modality/rate(s).")
        if unchanged:
            with st.expander(f"➖ {len(unchanged)} already blocked (nothing changed)"):
                for r in unchanged:
                    st.markdown(f"- **{r['name']}** — {r.get('detail', '')}")
        if failed:
            st.error(f"❌ {len(failed)} failed.")
            for r in failed:
                st.markdown(f"- **{r['name']}**: {r.get('detail', '')}")
