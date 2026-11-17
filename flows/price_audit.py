"""
flows/price_audit.py

Price Audit tool — upload a supplier's latest rate sheet and compare it against
current Travel Compositor prices. Informational only: nothing is changed.

Product types: Ticket, Transfer, Transport, ClosedTour.
Hotels already have their own inline price audit (price_audit.py / _render_hotel_price_audit_section).
"""

MODULE_BUILD = "2026-11-17-price-audit-flow"

import os
import tempfile
import streamlit as st
from typing import Any, Dict, List, Optional, Tuple

from app_helpers import _ur_pick_momira_supplier
from document_reader import extract_raw_text
from numeric_helpers import _safe_float


# ── constants ───────────────────────────────────────────────────────────
_PRODUCT_TYPES = ["Ticket", "Transfer", "Transport", "ClosedTour"]

_KEY = "pa_"  # widget key prefix


# ── TC data loaders (one per product type) ──────────────────────────────

def _load_tc_tickets(client, supplier_id: str, progress) -> Tuple[List[Dict], Optional[str]]:
    from price_refresh import load_supplier_tickets
    return load_supplier_tickets(client, supplier_id, progress=progress)


def _load_tc_transfers(client, supplier_id: str, progress) -> Tuple[List[Dict], Optional[str]]:
    from price_refresh import load_supplier_transfers
    return load_supplier_transfers(client, supplier_id, progress=progress)


def _load_tc_transports(client, supplier_id: str, progress) -> Tuple[List[Dict], Optional[str]]:
    from price_refresh import load_supplier_transports
    return load_supplier_transports(client, supplier_id, progress=progress)


def _load_tc_closed_tours(client, supplier_id: str, progress) -> Tuple[List[Dict], Optional[str]]:
    try:
        data = client.get_closed_tours(supplier_id, first=0, limit=200)
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    if isinstance(data, dict) and "error" in data:
        return [], str(data.get("message") or data.get("error"))
    tours = data.get("closedTours", data.get("closedtours", [])) if isinstance(data, dict) else (data or [])
    if isinstance(data, dict) and isinstance(data.get("closedTour"), list):
        tours = data["closedTour"]
    tours = [t for t in tours if isinstance(t, dict)]

    # paginate
    pagination = data.get("pagination", {}) if isinstance(data, dict) else {}
    total = pagination.get("totalResults", len(tours))
    first = 200
    while len(tours) < total:
        try:
            more = client.get_closed_tours(supplier_id, first=first, limit=200)
        except Exception:
            break
        more_list = more.get("closedTours", more.get("closedtours", more.get("closedTour", []))) \
            if isinstance(more, dict) else []
        if not more_list:
            break
        tours.extend(t for t in more_list if isinstance(t, dict))
        first += 200

    out = []
    total_mods = sum(len(t.get("modalityCodes") or []) for t in tours) or 1
    done = 0
    for tour in tours:
        code = tour.get("code")
        name = tour.get("name") or ((tour.get("datasheets") or {}).get("EN") or {}).get("name", "") or code
        currency = tour.get("currency") or "EUR"
        for mod_code in (tour.get("modalityCodes") or []):
            done += 1
            if progress:
                progress(done, total_mods, f"{name} — {mod_code}")
            try:
                opt = client.get_closed_tour_option(supplier_id, code, mod_code)
            except Exception:
                opt = None
            if not isinstance(opt, dict) or "error" in opt:
                out.append({"kind": "ClosedTour", "id": f"{code}/{mod_code}",
                            "name": f"{name} — {mod_code}", "currency": currency,
                            "prices": [], "fetch_failed": True})
                continue
            prices = []
            for pl in (opt.get("priceList") or []):
                if not isinstance(pl, dict):
                    continue
                prices.append({
                    "from": pl.get("from", ""),
                    "to": pl.get("to", ""),
                    "single": _safe_float(pl.get("singlePrice", 0)),
                    "double": _safe_float(pl.get("doublePrice", 0)),
                    "triple": _safe_float(pl.get("triplePrice", 0)),
                    "quadruple": _safe_float(pl.get("quadruplePrice", 0)),
                })
            out.append({"kind": "ClosedTour", "id": f"{code}/{mod_code}",
                        "name": f"{name} — {mod_code}", "currency": currency,
                        "prices": prices, "fetch_failed": False})
    return out, None


_LOADERS = {
    "Ticket": _load_tc_tickets,
    "Transfer": _load_tc_transfers,
    "Transport": _load_tc_transports,
    "ClosedTour": _load_tc_closed_tours,
}


# ── format TC prices into a readable summary for AI ────────────────────

def _format_tc_prices_for_ai(products: List[Dict], product_type: str) -> str:
    lines = []
    for p in products:
        if p.get("fetch_failed"):
            continue
        header = f"[{p.get('id')}] {p.get('name')} ({p.get('currency', 'EUR')})"

        if product_type == "ClosedTour":
            price_parts = []
            for pr in (p.get("prices") or []):
                date_range = f"{pr.get('from', '?')} to {pr.get('to', '?')}"
                price_parts.append(
                    f"  {date_range}: single={pr['single']}, double={pr['double']}, "
                    f"triple={pr['triple']}, quadruple={pr['quadruple']}")
            lines.append(header + "\n" + ("\n".join(price_parts) if price_parts else "  (no prices)"))

        elif product_type == "Ticket":
            opt_parts = []
            for opt in (p.get("options") or []):
                opt_parts.append(f"  {opt.get('name', opt.get('code', '?'))}: "
                                 f"{opt.get('unit_price', 0)} per unit "
                                 f"(min={opt.get('min_pax', 1)}, max={opt.get('max_pax', 1)})")
            for opt in (p.get("child_options") or []):
                opt_parts.append(f"  CHILD {opt.get('name', opt.get('code', '?'))}: "
                                 f"{opt.get('unit_price', 0)} per unit")
            lines.append(header + "\n" + ("\n".join(opt_parts) if opt_parts else "  (no prices)"))

        else:  # Transfer / Transport
            price_parts = []
            base = p.get("base_adult", 0)
            per_pax = p.get("price_per_pax", True)
            if per_pax:
                price_parts.append(f"  base adult={base}, child={p.get('base_child', 0)}, "
                                   f"infant={p.get('base_infant', 0)}")
            else:
                price_parts.append(f"  vehicle price={base}")
            for opt in (p.get("options") or []):
                if opt.get("fetch_failed"):
                    continue
                price_parts.append(f"  option {opt.get('code', '?')} "
                                   f"({opt.get('name', '')}): {opt.get('unit_price', 0)} "
                                   f"(pax {opt.get('min_pax', 1)}-{opt.get('max_pax', 1)})")
            lines.append(header + "\n" + "\n".join(price_parts))
    return "\n\n".join(lines)


# ── AI extraction + comparison ──────────────────────────────────────────

_PRICE_AUDIT_PROMPT = """You are auditing a supplier's rate sheet for a tour operator (Momira Travel).
You will receive:
1. The raw text of the supplier's document (rate sheet / price list).
2. A list of CURRENT prices already in the booking system (Travel Compositor) for this supplier's {product_type} products.

Your job: for every product in the CURRENT list, check whether the document contains a price for the same product,
and if so whether it matches or differs.

Return a JSON object with one key "findings" — an array of objects, one per product in the current list:
{{
  "findings": [
    {{
      "product_id": "<id from the current list>",
      "product_name": "<name from the current list>",
      "status": "match" | "mismatch" | "not_in_document",
      "current_price_summary": "<short text of the current prices>",
      "document_price_summary": "<what the document states, or empty if not found>",
      "details": "<explanation of the difference if mismatch, or empty>"
    }}
  ]
}}

Rules:
- Compare at the product/modality level. For each product in the current list, look for it in the document.
- "match" = the document's prices agree (within 1 unit tolerance).
- "mismatch" = the document has a price but it differs from the current system price. Always note BOTH values.
- "not_in_document" = the product wasn't found in the uploaded document.
- If the document contains products NOT in the current system, add them at the end with product_id="NEW" and status="new_in_document".
- Keep it factual. No recommendations."""


def _run_price_audit_ai(raw_text: str, tc_summary: str, product_type: str,
                         model: str = "claude-sonnet-5") -> Dict[str, Any]:
    from ai_extractor import _call_claude, _required_keys_schema
    system = _PRICE_AUDIT_PROMPT.format(product_type=product_type)
    user_msg = (f"=== CURRENT TRAVEL COMPOSITOR PRICES ===\n{tc_summary}\n\n"
                f"=== SUPPLIER DOCUMENT ===\n{raw_text}")
    defaults = {"findings": []}
    data = _call_claude(system, user_msg, model, max_tokens=16000,
                        input_schema=_required_keys_schema(defaults))
    if not isinstance(data.get("findings"), list):
        data["findings"] = []
    return data


# ── UI rendering ────────────────────────────────────────────────────────

def render_price_audit_flow(client):
    st.header("Price Audit")
    st.caption("Upload a supplier's rate sheet and compare it against current TC prices. "
               "Informational only — nothing is changed.")

    if st.button("🔙 Back", key=f"{_KEY}back"):
        st.session_state.product_type = None
        st.rerun()

    # Step 1: supplier
    supplier_id = _ur_pick_momira_supplier(client, _KEY)
    if not supplier_id:
        st.stop()

    # Step 2: product type
    product_type = st.selectbox("Product type", _PRODUCT_TYPES, key=f"{_KEY}ptype")

    # Step 3: document upload
    st.subheader("Upload rate sheet")
    uploaded = st.file_uploader(
        "Excel, CSV or PDF", type=["xlsx", "xls", "csv", "pdf", "docx", "doc"],
        accept_multiple_files=True, key=f"{_KEY}docs")

    if not uploaded:
        st.info("Upload at least one document to start the audit.")
        st.stop()

    # Step 4: run audit
    if st.button("Run Price Audit", key=f"{_KEY}run", type="primary"):
        # extract text from documents
        combined_parts = []
        for f in uploaded:
            suffix = os.path.splitext(f.name)[1]
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(f.getbuffer())
                tmp_path = tmp.name
            text = extract_raw_text(tmp_path)
            combined_parts.append(f"--- {f.name} ---\n{text}")
        raw_text = "\n\n".join(combined_parts)

        if not raw_text.strip():
            st.error("Could not extract any text from the uploaded documents.")
            st.stop()

        # load TC prices
        loader = _LOADERS[product_type]
        with st.spinner(f"Loading current {product_type} prices from TC..."):
            bar = st.progress(0)

            def _progress(done, total, label):
                bar.progress(min(done / max(total, 1), 1.0), text=label)

            products, err = loader(client, supplier_id, _progress)
            bar.empty()

        if err:
            st.error(f"Failed to load TC prices: {err}")
            st.stop()
        if not products:
            st.warning(f"No {product_type} products found for this supplier.")
            st.stop()

        st.success(f"Loaded {len(products)} {product_type} product(s) from TC.")

        tc_summary = _format_tc_prices_for_ai(products, product_type)

        with st.spinner("AI is comparing document prices against TC..."):
            result = _run_price_audit_ai(raw_text, tc_summary, product_type)

        st.session_state[f"{_KEY}result"] = result
        st.session_state[f"{_KEY}products_count"] = len(products)

    # Step 5: show results
    result = st.session_state.get(f"{_KEY}result")
    if not result:
        st.stop()

    findings = result.get("findings", [])
    if not findings:
        st.info("No findings returned by the audit.")
        st.stop()

    n_match = sum(1 for f in findings if f.get("status") == "match")
    n_mismatch = sum(1 for f in findings if f.get("status") == "mismatch")
    n_missing = sum(1 for f in findings if f.get("status") == "not_in_document")
    n_new = sum(1 for f in findings if f.get("status") == "new_in_document")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Matching", n_match)
    col2.metric("Mismatches", n_mismatch)
    col3.metric("Not in document", n_missing)
    col4.metric("New in document", n_new)

    # mismatches first
    if n_mismatch:
        st.subheader("Price mismatches")
        for f in findings:
            if f.get("status") != "mismatch":
                continue
            with st.expander(f"⚠️ {f.get('product_name', f.get('product_id', '?'))}", expanded=True):
                st.write(f"**Current TC:** {f.get('current_price_summary', '—')}")
                st.write(f"**Document:** {f.get('document_price_summary', '—')}")
                if f.get("details"):
                    st.write(f"**Details:** {f['details']}")

    if n_new:
        st.subheader("New products in document (not in TC)")
        for f in findings:
            if f.get("status") != "new_in_document":
                continue
            with st.expander(f"🆕 {f.get('product_name', f.get('product_id', '?'))}"):
                st.write(f"**Document:** {f.get('document_price_summary', '—')}")
                if f.get("details"):
                    st.write(f"**Details:** {f['details']}")

    if n_missing:
        st.subheader("TC products not found in document")
        for f in findings:
            if f.get("status") != "not_in_document":
                continue
            st.write(f"- {f.get('product_name', f.get('product_id', '?'))} — "
                     f"TC: {f.get('current_price_summary', '—')}")

    if n_match:
        with st.expander(f"Matching prices ({n_match})"):
            for f in findings:
                if f.get("status") != "match":
                    continue
                st.write(f"- {f.get('product_name', f.get('product_id', '?'))}: "
                         f"{f.get('current_price_summary', '—')}")
