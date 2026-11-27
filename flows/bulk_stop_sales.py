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
  Transport  — stopSales on each ContractTransportOptionVO; one PUT per option (all options
                of a selected transport are updated automatically).

NOTE ON NAMING: Travel Compositor's StopSale schema is {start, end} only — there is no
name/label field. The "name" field shown in the UI is a local convenience label for the
human's own reference; it is not sent to TC and not stored.
"""

MODULE_BUILD = "2026-11-27-repeat-off-by-default"

import json
import re as _re_bss
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

import stop_sales_tool as sst
from ai_extractor import friendly_error_message
from ui_components import is_active_supplier
from flows.flow_ui_helpers import load_momira_suppliers
from list_response_utils import normalize_list_response


# ── destination filter helpers ─────────────────────────────────────────────────
# Reuses the pattern from flows/price_refresh.py so a user searching for "Koh Lipe"
# matches "Koh Lipe Harbour" and "Koh Lipe Pier" alike - substring, not exact.
_TRANSPORT_PREFIX = _re_bss.compile(
    r"^(?:one[- ]?way|round[- ]?trip|private|shared|)[\s]*"
    r"(?:transfer|transport|)[\s]*(?:from\s+)?",
    _re_bss.IGNORECASE,
)


def _extract_cities_from_name(name: str) -> List[str]:
    """Pull city names out of a product name like 'One-way transfer from Hat Yai Airport
    (HDY) - Koh Lipe Harbour'. Strips the 'transfer from' prefix and any trailing
    parenthetical codes like '(HDY)' so the dropdown shows clean names."""
    if not name:
        return []
    cities = []
    for sep in ["→", " - ", " – ", " — "]:
        if sep in name:
            for part in name.split(sep):
                city = _re_bss.sub(r"\s*\([^)]*\)", "", part).strip()
                city = _TRANSPORT_PREFIX.sub("", city).strip()
                if city:
                    cities.append(city)
            break
    return cities


def _product_matches_destinations(product: Dict, selected: List[str]) -> bool:
    """A product matches when any of the selected cities appears in its name as a
    substring (case-insensitive). 'Koh Li' matches 'Koh Lipe Harbour' and 'Koh Lipe
    Pier' both - the operator's own real request, since a destination has many
    variants (airport, pier, harbour, city) that should all count as the same place."""
    if not selected:
        return True
    name_lower = (product.get("name") or "").lower()
    return any(s.lower() in name_lower for s in selected)


# ── yearly recurrence helpers ──────────────────────────────────────────────────
def _expand_yearly(start: date, end: date, years: int) -> List[Dict[str, str]]:
    """Expand a single stop-sale range into N consecutive years of the same slot.

    Example: start=2027-05-01, end=2027-10-31, years=10 produces ten {start, end}
    dicts for 2027-2036 inclusive. CONFIRMED USE CASE (operator, 2026-11-23): a
    seasonal route that only runs Nov-Apr needs a stop sale for May-Oct repeated for
    the next 10 years so a booking in year +7 is correctly refused.

    Feb 29 on a non-leap year is clamped to Feb 28 so the range stays valid rather
    than silently skipping that year.
    """
    if years < 1:
        years = 1
    out = []
    for i in range(years):

        def _shift(d: date, by: int) -> date:
            try:
                return d.replace(year=d.year + by)
            except ValueError:
                # Feb 29 on a non-leap year - step back to Feb 28 rather than drop
                return d.replace(year=d.year + by, day=28)

        s = _shift(start, i)
        e = _shift(end, i)
        out.append({"start": s.isoformat(), "end": e.isoformat()})
    return out

# Travel Compositor wraps list responses under a singular or plural key depending on
# account/version, so every loader below sniffs the shape via normalize_list_response
# rather than naming one key. Getting this wrong is silent: the call succeeds and the
# product list just comes back empty.
_WRAPPER_FALLBACKS = ("items", "data", "results", "content")

_PRODUCT_TYPES = ["Transfer", "Ticket", "ClosedTour", "Hotel", "Transport"]
_TYPE_LABELS = {
    "Transfer": "Transfer",
    "Ticket": "Ticket",
    "ClosedTour": "Closed Tour",
    "Hotel": "Hotel",
    "Transport": "Transport",
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
    items = normalize_list_response(data, ("transfer", "transfers") + _WRAPPER_FALLBACKS)
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
    tickets = normalize_list_response(data, ("ticket", "tickets") + _WRAPPER_FALLBACKS)
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
    tours = normalize_list_response(data, ("closedTour", "closedTours") + _WRAPPER_FALLBACKS)
    out = []
    for t in (tours or []):
        if not isinstance(t, dict):
            continue
        # ContractClosedTourVO carries both `code` (the TC contract code that
        # /closedtours/{supplierId}/{code} is keyed on) and `providerCode` (the DMC's
        # own reference). Only `code` resolves on the GET, so it wins here.
        code = t.get("code") or t.get("providerCode")
        name = t.get("name") or ((t.get("datasheets") or {}).get("EN") or {}).get("name") or code
        modalities = t.get("modalityCodes") or []
        out.append({"id": code, "name": name, "modalities": modalities, "kind": "ClosedTour"})
    return sorted(out, key=lambda x: x["name"]), None


def _load_hotels(client, supplier_id: str) -> Tuple[List[Dict], Optional[str]]:
    try:
        data = client.get_hotels(supplier_id)
    except Exception as e:
        return [], friendly_error_message(e)
    if isinstance(data, dict) and "error" in data:
        return [], str(data.get("message") or data.get("error"))
    hotels = normalize_list_response(data, ("hotel", "hotels") + _WRAPPER_FALLBACKS)
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


def _load_transports(client, supplier_id: str) -> Tuple[List[Dict], Optional[str]]:
    try:
        data = client.get_transports(supplier_id)
    except Exception as e:
        return [], friendly_error_message(e)
    if isinstance(data, dict) and "error" in data:
        return [], str(data.get("message") or data.get("error"))
    items = normalize_list_response(data, ("transport", "transports") + _WRAPPER_FALLBACKS)
    out = []
    for t in (items or []):
        if not isinstance(t, dict):
            continue
        tid = t.get("id")
        name = t.get("name") or tid or "(unnamed)"
        option_codes = t.get("optionCodes") or []
        out.append({"id": tid, "name": name, "options": option_codes, "kind": "Transport"})
    return sorted(out, key=lambda x: x["name"]), None


# ── apply helpers ─────────────────────────────────────────────────────────────

def _normalise_transfer_stop_sale_in(entry: Dict) -> Optional[Dict]:
    """TC's ContractTransferStopSaleVO uses startDate/endDate, UNLIKE every other
    contract's stopSales which uses LocalDateRangeVO {start, end}. Normalise an
    incoming (live-from-GET) entry to {start, end} so merge_stop_sales can compare
    it against new ranges on the same keys. CONFIRMED REAL BUG (operator,
    2026-11-23): writing with {start, end} on a Transfer silently did nothing -
    TC ignored unknown fields and left stopSales empty."""
    if not isinstance(entry, dict):
        return None
    s = entry.get("start") or entry.get("startDate")
    e = entry.get("end") or entry.get("endDate")
    if not s or not e:
        return None
    return {"start": str(s), "end": str(e)}


def _to_transfer_wire_shape(entry: Dict) -> Dict:
    """Convert {start, end} back to {startDate, endDate} so TC's PUT /transfer
    accepts it. See _normalise_transfer_stop_sale_in for the asymmetry."""
    return {"startDate": entry["start"], "endDate": entry["end"]}


def _apply_transfer(client, supplier_id: str, product: Dict, new_ranges: List[Dict]) -> Dict:
    """GET the transfer, merge stop sales, PUT it back.

    The returned dict carries the TC id and the count of ranges actually added so
    the result banner can show '2 Transfers updated with 20 new stop-sale ranges'
    and name each by TRANSFER-nnnnnn - the operator asked for this cross-check
    after a confusing session where they weren't sure whether TC had actually
    received the ranges they'd just applied (2026-11-23).

    CONFIRMED REAL BUG (same session, verified against the TC API spec at
    docs/tc_api_reference.md line 10912): TC's ContractTransferStopSaleVO uses
    startDate/endDate, not start/end. We were writing {start, end} and TC was
    silently dropping those unknown fields - the UI reported success, TC had
    nothing. This function now writes the correct wire shape."""
    tid = product["id"]
    name = product["name"]
    try:
        rec = client.get_transfer(supplier_id, tid)
    except Exception as e:
        return {"status": "failed", "name": name, "id": tid,
                "detail": friendly_error_message(e)}
    if isinstance(rec, dict) and "error" in rec:
        return {"status": "failed", "name": name, "id": tid,
                "detail": str(rec.get("message") or rec.get("error"))}
    import stop_sales_parser as ssp
    # Normalise whatever TC's GET returned (could be startDate/endDate or
    # legacy start/end on an older record) to {start, end} so the merge works.
    live = [n for n in (_normalise_transfer_stop_sale_in(s) for s in (rec.get("stopSales") or []))
            if n is not None]
    result = ssp.merge_stop_sales(live, new_ranges)
    if not result["added"]:
        return {"status": "unchanged", "name": name, "id": tid,
                "detail": f"every date was already blocked on {tid}"}
    payload = dict(rec)
    # Write back in TC's required Transfer-specific wire shape - every merged
    # entry, both the existing ones we read in and the new ones we added.
    payload["stopSales"] = [_to_transfer_wire_shape(r) for r in result["merged"]]
    try:
        res = client.update_transfer(supplier_id, payload)
    except Exception as e:
        return {"status": "failed", "name": name, "id": tid,
                "detail": friendly_error_message(e)}
    if isinstance(res, dict) and "error" in res:
        return {"status": "failed", "name": name, "id": tid,
                "detail": str(res.get("message") or res.get("error"))}
    return {"status": "updated", "name": name, "id": tid,
            "added": result["added"],
            "detail": f"{tid}: {len(result['added'])} new range(s) added"}


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


def _apply_transport(client, supplier_id: str, product: Dict, new_ranges: List[Dict]) -> List[Dict]:
    """Apply stop sales to ALL options of a transport automatically."""
    results = []
    for opt_code in (product.get("options") or []):
        try:
            opt = client.get_transport_option(supplier_id, product["id"], opt_code)
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
        r = sst.apply_to_transport_option(client, supplier_id, product["id"], opt, new_ranges)
        r["name"] = f"{product['name']} / {opt_code}"
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

    st.caption("ℹ️ Transport stop sales are applied to ALL options/modalities of each selected transport automatically.")

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
                    "Transport": _load_transports,
                }
                for pt in selected_types:
                    items, err = loaders[pt](client, supplier_id)
                    if err:
                        errors.append(f"{_TYPE_LABELS[pt]}: {err}")
                    all_products.extend(items)
                # Drop checkbox state from a previous load, or boxes ticked for
                # products that are no longer listed stay ticked behind the scenes.
                for _k in [k for k in st.session_state if k.startswith("bss_prod_")]:
                    del st.session_state[_k]
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

    # ── destination filter ────────────────────────────────────────────────────
    # CONFIRMED REAL REQUEST (operator, 2026-11-23): after supplier + product type,
    # narrow further by destination city. The Koh Lipe case: a supplier has 24
    # transfers, most of them touch Koh Lipe in some form (Harbour, Pier, Airport),
    # and the operator wants to see ONLY those at once to apply a May-Oct stop sale.
    _destination_filter: List[str] = []
    _all_cities = set()
    for p in all_products:
        for c in _extract_cities_from_name(p.get("name") or ""):
            _all_cities.add(c)
    _sorted_cities = sorted(_all_cities)
    if _sorted_cities:
        _destination_filter = st.multiselect(
            f"Filter by destination ({len(_sorted_cities)} cities found — leave empty to see all products)",
            _sorted_cities, key="bss_city_filter",
            placeholder="e.g. Koh Lipe — matches every variant (Pier, Harbour, Airport)")
        if _destination_filter:
            st.caption(f"ℹ️ Substring match: selecting 'Koh Lipe' also includes 'Koh Lipe Pier', "
                       f"'Koh Lipe Harbour', etc.")

    filtered_products = [p for p in all_products
                         if _product_matches_destinations(p, _destination_filter)]
    if not filtered_products:
        st.warning("No products match that destination filter. Clear it to see all.")
        st.stop()

    # ── product selection ─────────────────────────────────────────────────────
    _filter_note = f" (filtered from {len(all_products)})" if _destination_filter else ""
    st.markdown(f"**Select products** ({len(filtered_products)} found{_filter_note})")

    # Each product's checkbox is keyed by kind+id, so the same code appearing as
    # both (say) a Ticket and a ClosedTour gets its own box. Select all / Clear all
    # write straight into those widget keys: once a keyed checkbox exists in session
    # state Streamlit ignores its `value=` argument, so setting a separate id set
    # would leave every box visually unchanged.
    def _wkey(p):
        return f"bss_prod_{p['kind']}_{p['id']}"

    sel_cols = st.columns([1, 1])
    with sel_cols[0]:
        if st.button("✅ Select all (visible)", key="bss_sel_all"):
            # CONFIRMED (operator, 2026-11-23): Select all now applies to the FILTERED
            # list only - selecting all when a destination filter is active was
            # surprising if it also ticked products that weren't on screen.
            for p in filtered_products:
                st.session_state[_wkey(p)] = True
            st.rerun()
    with sel_cols[1]:
        if st.button("☐ Clear all", key="bss_sel_none"):
            for p in all_products:  # clear every box, filtered or not
                st.session_state[_wkey(p)] = False
            st.rerun()

    # Group by type for display - the filtered list only.
    by_type: Dict[str, List] = {}
    for p in filtered_products:
        by_type.setdefault(p["kind"], []).append(p)

    def _display_name(p: Dict) -> str:
        """Append the TC product ID at the end of the display label - the operator
        asked for this so a row can be cross-checked against the TC admin page by
        number. Transfers read TRANSFER-nnnnnn, transports TRANSPORT-nnnnnn, etc.
        Stored p['name'] and p['id'] stay unchanged - this is display-only."""
        pid = p.get("id") or ""
        if not pid:
            return p.get("name") or ""
        # Already looks like e.g. 'TRANSFER-454499' - just append, else prefix the
        # kind to match TC's own ID naming (TRANSFER-, TRANSPORT-, TICKET-, ...).
        if "-" in str(pid):
            tag = pid
        else:
            tag = f"{p.get('kind', '').upper()}-{pid}" if p.get("kind") else pid
        return f"{p.get('name') or ''} — {tag}"

    selected_products = []
    for pt in selected_types:
        items = by_type.get(pt, [])
        if not items:
            continue
        with st.expander(f"{_TYPE_LABELS[pt]} ({len(items)})", expanded=True):
            for p in items:
                if st.checkbox(_display_name(p), key=_wkey(p)):
                    selected_products.append(p)
    _set("bss_selected_ids", {p["id"] for p in selected_products})
    if not selected_products:
        st.info("Tick at least one product above.")
        st.stop()

    st.success(f"**{len(selected_products)} product(s) selected**")

    # ── date ranges (multiple) ────────────────────────────────────────────────
    # CONFIRMED REAL REQUEST (operator, 2026-11-23): one bulk apply must support
    # multiple separate date ranges, so a single trip to this tool can block
    # e.g. Chinese Spring Festival (Feb 10-20) AND Golden Week (Oct 1-7) in one go.
    # Each range also gets an optional "repeat yearly for N years" expansion - the
    # Phuket-Koh Lipe case (closed May-Oct for the next 10 years), Nyepi in Indonesia
    # (one day every year), Christmas/New Year's Eve blackouts in Vietnam, etc.
    st.markdown("**Stop sale date ranges**")

    if _get("bss_ranges_count") is None:
        _set("bss_ranges_count", 1)
    ranges_count = _get("bss_ranges_count")

    new_ranges: List[Dict[str, str]] = []
    _range_summaries: List[str] = []
    _any_invalid = False
    for idx in range(ranges_count):
        rcol_a, rcol_b, rcol_c, rcol_d = st.columns([3, 3, 2, 1])
        with rcol_a:
            r_start = st.date_input(f"From (inclusive)", value=date.today(),
                                    format="DD/MM/YYYY", key=f"bss_start_{idx}")
        with rcol_b:
            # CONFIRMED REAL REQUEST (operator, 2026-11-23): once the operator picks
            # a start date, the end date must default to the SAME date (so a
            # single-day stop sale needs just one click), not to today. The human
            # can still extend the end date further into the future if they want a
            # range. If the user has already picked an end date in a previous
            # interaction that now lies BEFORE the new start date, bump it forward
            # to the start date - a stop sale ending before it begins is never
            # what was meant.
            _end_key = f"bss_end_{idx}"
            _existing_end = st.session_state.get(_end_key)
            if _existing_end is None or _existing_end < r_start:
                st.session_state[_end_key] = r_start
            r_end = st.date_input(f"To (inclusive)", value=r_start,
                                  format="DD/MM/YYYY", key=_end_key)
        with rcol_c:
            _repeat_key = f"bss_repeat_{idx}"
            _years_key = f"bss_years_{idx}"
            do_repeat = st.checkbox(
                "Repeat yearly",
                value=st.session_state.get(_repeat_key, False),
                key=_repeat_key,
                help="Tick to repeat this exact date range (same month/day) "
                     "for multiple consecutive years — e.g. a seasonal closure "
                     "or a recurring public holiday. Leave unticked for a "
                     "one-off block that only applies in the year shown.")
            if do_repeat:
                r_years = st.number_input(
                    "How many years?", min_value=2, max_value=10, value=2, step=1,
                    key=_years_key,
                    help="2 = this year + next year. 10 = this year plus the next 9.")
            else:
                r_years = 1
                st.session_state.pop(_years_key, None)
        with rcol_d:
            st.markdown("<br>", unsafe_allow_html=True)  # vertical align with inputs
            if idx > 0 and st.button("🗑", key=f"bss_del_range_{idx}",
                                     help="Remove this range"):
                _set("bss_ranges_count", max(1, ranges_count - 1))
                # Drop the removed range's widget state so slots renumber cleanly.
                for k in (f"bss_start_{idx}", f"bss_end_{idx}", f"bss_years_{idx}"):
                    st.session_state.pop(k, None)
                st.rerun()

        if r_end < r_start:
            st.error(f"Range {idx + 1}: End date must be on or after start date.")
            _any_invalid = True
            continue

        expanded = _expand_yearly(r_start, r_end, int(r_years))
        new_ranges.extend(expanded)

        if r_years == 1:
            if r_start == r_end:
                _range_summaries.append(f"**{r_start.strftime('%d.%m.%Y')}** (single day)")
            else:
                days = (r_end - r_start).days + 1
                _range_summaries.append(
                    f"**{r_start.strftime('%d.%m.%Y')} – {r_end.strftime('%d.%m.%Y')}** "
                    f"({days} days)")
        else:
            last_year = r_start.year + int(r_years) - 1
            _range_summaries.append(
                f"**{r_start.strftime('%d.%m.')}–{r_end.strftime('%d.%m.')}** every year "
                f"from **{r_start.year}** through **{last_year}** "
                f"({int(r_years)} year(s), {len(expanded)} ranges)")

    if _any_invalid:
        st.stop()

    acol1, acol2 = st.columns([1, 3])
    with acol1:
        if st.button("➕ Add another range", key="bss_add_range"):
            _set("bss_ranges_count", ranges_count + 1)
            st.rerun()

    for s in _range_summaries:
        st.caption(f"• {s}")
    if len(new_ranges) > 1:
        st.caption(f"ℹ️ Total **{len(new_ranges)}** stop-sale range(s) will be applied "
                   f"per product.")

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
                                # TC's ContractTransferStopSaleVO uses startDate/endDate,
                                # unlike every other stopSales shape. Fall through to
                                # start/end in case TC ever normalises or an older
                                # record still carries legacy keys.
                                n = _normalise_transfer_stop_sale_in(s)
                                if n and (n["end"] or "") < today_str:
                                    expired_items.append({
                                        "kind": kind, "pid": pid, "pname": pname,
                                        "label": pname,
                                        "start": n["start"], "end": n["end"],
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
                                    # Normalise to {start,end} first - TC's wire
                                    # shape for Transfer is {startDate,endDate}.
                                    live_norm = [n for n in (
                                        _normalise_transfer_stop_sale_in(s)
                                        for s in (rec.get("stopSales") or []))
                                                 if n is not None]
                                    kept = [s for s in live_norm
                                            if not any(
                                                s["start"] == r["start"] and
                                                s["end"] == r["end"]
                                                for r in ranges_to_remove)]
                                    payload = dict(rec)
                                    # Write back in Transfer's required wire shape.
                                    payload["stopSales"] = [
                                        _to_transfer_wire_shape(k) for k in kept]
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
    _range_word = "range" if len(new_ranges) == 1 else "ranges"
    st.warning(
        f"This will block **{len(new_ranges)} date {_range_word}** "
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
            product_results: List[Dict] = []
            if kind == "Transfer":
                product_results.append(_apply_transfer(client, supplier_id, product, new_ranges))
            elif kind == "Ticket":
                product_results.extend(_apply_ticket(client, supplier_id, product, new_ranges))
            elif kind == "ClosedTour":
                product_results.extend(_apply_tour(client, supplier_id, product, new_ranges))
            elif kind == "Hotel":
                product_results.extend(_apply_hotel(client, supplier_id, product, new_ranges))
            elif kind == "Transport":
                product_results.extend(_apply_transport(client, supplier_id, product, new_ranges))
            # Stamp every result with the TC product id so the result banner can
            # show e.g. TRANSFER-454499 next to each row (operator's cross-check
            # request, 2026-11-23). Done here rather than in each _apply_* helper
            # so a result that comes back without an id (sub-modality level) still
            # knows which parent product it belonged to.
            for r in product_results:
                r.setdefault("id", product.get("id") or "")
            all_results.extend(product_results)
        bar.empty()
        _set("bss_result", all_results)
        st.rerun()

    results = _get("bss_result")
    if results:
        updated = [r for r in results if r["status"] == "updated"]
        failed = [r for r in results if r["status"] == "failed"]
        unchanged = [r for r in results if r["status"] == "unchanged"]

        if updated:
            # CONFIRMED REAL REQUEST (operator, 2026-11-23): after Apply, the operator
            # wasn't sure if TC had actually received the 10-year stop sale because
            # the success line was too vague. List every updated TC id with the count
            # of ranges added, so the operator can click into each one on TC and
            # cross-check. Counting ranges across all products too, since one Apply
            # with 10 years x 2 products = 20 individual stop-sale records.
            total_added = sum(len(r.get("added") or []) for r in updated)
            st.success(f"✅ Stop sale applied to {len(updated)} modality/rate(s) — "
                       f"**{total_added}** new stop-sale range(s) written in total.")
            with st.expander(f"✅ {len(updated)} updated — click to see every TC id",
                             expanded=True):
                for r in updated:
                    _rid = r.get("id") or ""
                    _added_n = len(r.get("added") or [])
                    st.markdown(f"- **{r['name']}** — `{_rid}` "
                                f"({_added_n} range(s) added)")
        if unchanged:
            with st.expander(f"➖ {len(unchanged)} already blocked (nothing changed)"):
                for r in unchanged:
                    _rid = r.get("id") or ""
                    st.markdown(f"- **{r['name']}** `{_rid}` — {r.get('detail', '')}")
        if failed:
            st.error(f"❌ {len(failed)} failed.")
            for r in failed:
                _rid = r.get("id") or ""
                st.markdown(f"- **{r['name']}** `{_rid}`: {r.get('detail', '')}")
