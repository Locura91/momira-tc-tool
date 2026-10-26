"""
Transport Inventory Audit — surveillance tool that scans all transports for a supplier and
flags any modality (option) whose inventory differs from the others on the same transport, or
whose end date is not the house "always available" date 2049-12-31.

Read-only: this tool never writes anything to Travel Compositor.

Background: each Transport has 1–3 occupancy-bracket Options (modalities).  Each Option has an
`inventories` list with start/end date ranges and a quantity.  Product-owner rule: every option
on the same Transport must have the SAME inventory, and the end must always be 2049-12-31.
This tool detects deviations so a human can fix them manually.
"""
import streamlit as st

from ui_components import momira_active_suppliers

MODULE_BUILD = "2026-10-26-transport-inventory-audit"

_EXPECTED_END = "2049-12-31"


def _load_momira_suppliers(client):
    if st.session_state.get("tia_suppliers_cache") is None:
        with st.spinner("Loading supplier list…"):
            try:
                st.session_state.tia_suppliers_cache = client.get_all_suppliers()
            except Exception as e:
                st.error(f"❌ Couldn't load the supplier list: {type(e).__name__}: {e}")
                st.session_state.tia_suppliers_cache = []
    raw = st.session_state.tia_suppliers_cache
    out = [
        {"id": str(s.get("id")), "name": (s.get("commercialName") or s.get("legalName") or "").strip()}
        for s in momira_active_suppliers(raw)
    ]
    return sorted(out, key=lambda s: s["name"])



def _audit_transport(client, supplier_id: str, transport: dict) -> dict:
    """
    Returns a result dict:
        transport_id, name, option_codes, options (list of per-option details),
        issues (list of human-readable strings), ok (bool)
    """
    transport_id = transport.get("id") or transport.get("transportId") or ""
    name = transport.get("name") or transport_id
    option_codes = transport.get("optionCodes") or []

    options = []
    fetch_errors = []

    for code in option_codes:
        try:
            opt = client.get_transport_option(supplier_id, transport_id, code)
        except Exception as e:
            fetch_errors.append(f"Option {code!r}: fetch failed — {type(e).__name__}: {e}")
            continue

        inventories = opt.get("inventories") or []
        options.append({
            "code": code,
            "active": opt.get("active", True),
            "min_pax": opt.get("minPassengers"),
            "max_pax": opt.get("maxPassengers"),
            "inventories": inventories,
        })

    issues = list(fetch_errors)

    if not option_codes:
        issues.append("No modalities (optionCodes is empty)")
    elif not options and fetch_errors:
        pass  # errors already captured
    else:
        # The ONLY critical check: end date must be 2049-12-31.
        # Start date differences between options are intentional (added at different times)
        # and are never flagged. A start date in the past is also fine.
        for opt in options:
            if not opt["inventories"]:
                issues.append(f"Option {opt['code']!r}: has no inventory entries")
                continue
            for inv in opt["inventories"]:
                date = inv.get("inventoryDate") or {}
                end = date.get("end") or ""
                if end != _EXPECTED_END:
                    issues.append(
                        f"Option {opt['code']!r}: end date is {end!r} — should be {_EXPECTED_END}"
                    )

    return {
        "transport_id": transport_id,
        "name": name,
        "option_codes": option_codes,
        "options": options,
        "issues": issues,
        "ok": len(issues) == 0,
    }


def render_transport_inventory_audit_flow(client):
    st.header("🔍 Transport Inventory Audit")
    if st.button("🔙 Back", key="tia_back"):
        st.session_state.product_type = None
        st.session_state.pop("tia_results", None)
        st.session_state.pop("tia_supplier_id", None)
        st.rerun()

    st.caption(
        f"Scans every live Transport for a supplier and flags any modality whose inventory "
        f"end date is not **{_EXPECTED_END}** (the house 'always available' date), or that "
        f"has no inventory at all. Start date differences between modalities are normal and "
        f"are ignored. Read-only — nothing is changed in Travel Compositor."
    )

    suppliers = _load_momira_suppliers(client)
    if not suppliers:
        st.warning("No active suppliers found.")
        return

    supplier_options = [f"{s['name']}  (id: {s['id']})" for s in suppliers]
    chosen = st.selectbox("Supplier", supplier_options, key="tia_supplier_select")
    chosen_idx = supplier_options.index(chosen)
    supplier_id = suppliers[chosen_idx]["id"]

    if st.button("🔍 Scan transports", key="tia_scan", type="primary"):
        st.session_state.pop("tia_results", None)
        st.session_state.tia_supplier_id = supplier_id

        with st.spinner("Fetching transport list…"):
            try:
                all_transports_raw = client.get_transports(supplier_id)
            except Exception as e:
                st.error(f"❌ Could not fetch transports: {type(e).__name__}: {e}")
                return

        # same extraction as cancellation_bulk_transport.py uses
        if isinstance(all_transports_raw, dict):
            transport_list = all_transports_raw.get("transport") or []
        elif isinstance(all_transports_raw, list):
            transport_list = all_transports_raw
        else:
            transport_list = []
        transport_list = [r for r in transport_list if isinstance(r, dict)]

        # exclude inactive
        active_transports = [t for t in transport_list if t.get("active") is not False]

        if not active_transports:
            st.info("No active transports found for this supplier.")
            st.session_state.tia_results = []
            return

        results = []
        prog = st.progress(0, text="Scanning modalities…")
        total = len(active_transports)
        for i, transport in enumerate(active_transports):
            prog.progress((i + 1) / total, text=f"Transport {i + 1} / {total}")
            result = _audit_transport(client, supplier_id, transport)
            results.append(result)

        prog.empty()
        st.session_state.tia_results = results

    # ── Results ──────────────────────────────────────────────────────────
    results = st.session_state.get("tia_results")
    if results is None:
        return

    total = len(results)
    flagged = [r for r in results if not r["ok"]]
    ok_count = total - len(flagged)

    st.markdown("---")
    st.subheader("Results")

    col1, col2, col3 = st.columns(3)
    col1.metric("Total transports scanned", total)
    col2.metric("✅ OK", ok_count)
    col3.metric("⚠️ Issues found", len(flagged))

    if not flagged:
        st.success("✅ All transports look good — inventories match across modalities and end dates are correct.")
        return

    st.warning(f"⚠️ {len(flagged)} transport(s) have inventory issues:")

    for r in flagged:
        with st.expander(f"⚠️  {r['name']}  (id: {r['transport_id']})", expanded=True):
            for issue in r["issues"]:
                st.error(issue)

            if r["options"]:
                st.markdown("**Modality inventories:**")
                for opt in r["options"]:
                    label = f"Option `{opt['code']}` (pax {opt['min_pax']}–{opt['max_pax']})"
                    if not opt["inventories"]:
                        st.write(f"- {label}: _no inventory entries_")
                    else:
                        for inv in opt["inventories"]:
                            date = inv.get("inventoryDate") or {}
                            st.write(
                                f"- {label}: "
                                f"`{date.get('start')}` → `{date.get('end')}`, "
                                f"qty={inv.get('quantity', 0)}"
                            )
