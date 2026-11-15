"""
Transport Inventory Audit — surveillance tool that scans all transports for a supplier and
flags any modality (option) whose inventory or price supplement date range differs from the
house "always available" end date 2049-12-31.

Can FIX price supplement date ranges: when a modality's price supplement has a wrong endDate,
the human selects the affected rows and this tool PUTs the corrected option back to TC.

Background: each Transport has 1–3 occupancy-bracket Options (modalities).  Each Option has an
`inventories` list with start/end date ranges and a quantity, AND a `prices` list of price
supplement entries each with their own startDate/endDate.  Product-owner rule: every option
on the same Transport must have the SAME inventory, and BOTH the inventory end AND the price
supplement end must always be 2049-12-31.
"""
import copy
import streamlit as st

from flows.flow_ui_helpers import load_momira_suppliers as _load_momira_suppliers

MODULE_BUILD = "2026-11-14-price-date-fix"

_EXPECTED_END = "2049-12-31"


def _audit_transport(client, supplier_id: str, transport: dict) -> dict:
    """
    Returns a result dict with inventory issues, price date issues, and the raw option data
    needed to fix price dates via PUT.
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
        prices = opt.get("prices") or []
        translations = opt.get("translations") or {}
        en = translations.get("EN") or translations.get("en") or {}
        opt_name = en.get("name", "") if isinstance(en, dict) else ""

        options.append({
            "code": code,
            "name": opt_name,
            "active": opt.get("active", True),
            "min_pax": opt.get("minPassengers"),
            "max_pax": opt.get("maxPassengers"),
            "inventories": inventories,
            "prices": prices,
            "raw": opt,
        })

    issues = list(fetch_errors)
    price_date_fixes = []

    if not option_codes:
        issues.append("No modalities (optionCodes is empty)")
    elif not options and fetch_errors:
        pass
    else:
        for opt in options:
            if not opt["inventories"]:
                issues.append(f"Option {opt['code']!r}: has no inventory entries")
            else:
                for inv in opt["inventories"]:
                    date = inv.get("inventoryDate") or {}
                    end = date.get("end") or ""
                    if end != _EXPECTED_END:
                        issues.append(
                            f"Option {opt['code']!r}: inventory end date is {end!r} — should be {_EXPECTED_END}"
                        )

            for pi, price in enumerate(opt["prices"]):
                p_end = price.get("endDate") or ""
                p_start = price.get("startDate") or ""
                p_name = price.get("name") or ""
                if p_end and p_end != _EXPECTED_END:
                    label = opt["name"] or opt["code"]
                    issues.append(
                        f"Option {opt['code']!r} ({label}): price supplement "
                        f"'{p_name}' ends {p_end} — should be {_EXPECTED_END}"
                    )
                    price_date_fixes.append({
                        "transport_id": transport_id,
                        "transport_name": name,
                        "option_code": opt["code"],
                        "option_name": label,
                        "price_index": pi,
                        "price_name": p_name,
                        "current_start": p_start,
                        "current_end": p_end,
                        "raw_option": opt["raw"],
                    })

    return {
        "transport_id": transport_id,
        "name": name,
        "option_codes": option_codes,
        "options": options,
        "issues": issues,
        "price_date_fixes": price_date_fixes,
        "ok": len(issues) == 0,
    }


def _apply_price_date_fixes(client, supplier_id: str, fixes: list) -> list:
    """For each fix, GET the option fresh, change the price entry's endDate to _EXPECTED_END,
    and PUT it back. Returns a list of {option_code, ok, detail}."""
    by_option = {}
    for fix in fixes:
        key = (fix["transport_id"], fix["option_code"])
        by_option.setdefault(key, []).append(fix)

    results = []
    for (transport_id, option_code), option_fixes in by_option.items():
        try:
            opt = client.get_transport_option(supplier_id, transport_id, option_code)
        except Exception as e:
            results.append({"option_code": option_code, "transport_id": transport_id,
                            "ok": False, "detail": f"GET failed: {e}"})
            continue

        prices = opt.get("prices") or []
        changed = False
        for price in prices:
            p_end = price.get("endDate") or ""
            if p_end and p_end != _EXPECTED_END:
                price["endDate"] = _EXPECTED_END
                changed = True

        if not changed:
            results.append({"option_code": option_code, "transport_id": transport_id,
                            "ok": True, "detail": "already correct (no change needed)"})
            continue

        try:
            resp = client.update_transport_option(supplier_id, transport_id, opt)
        except Exception as e:
            results.append({"option_code": option_code, "transport_id": transport_id,
                            "ok": False, "detail": f"PUT failed: {e}"})
            continue

        if isinstance(resp, dict) and "error" in resp:
            results.append({"option_code": option_code, "transport_id": transport_id,
                            "ok": False,
                            "detail": str(resp.get("message") or resp.get("error"))})
        else:
            results.append({"option_code": option_code, "transport_id": transport_id,
                            "ok": True, "detail": f"endDate changed to {_EXPECTED_END}"})
    return results


def render_transport_inventory_audit_flow(client):
    st.header("Transport Inventory & Price Date Audit")
    if st.button("Back", key="tia_back"):
        st.session_state.product_type = None
        st.session_state.pop("tia_results", None)
        st.session_state.pop("tia_supplier_id", None)
        st.session_state.pop("tia_fix_results", None)
        st.rerun()

    st.caption(
        f"Scans every live Transport for a supplier and flags any modality whose inventory "
        f"end date or **price supplement end date** is not **{_EXPECTED_END}** (the house "
        f"'always available' date). Price supplement dates can be fixed directly from here."
    )

    suppliers = _load_momira_suppliers(client)
    if not suppliers:
        st.warning("No active suppliers found.")
        return

    supplier_options = [f"{s['name']}  (id: {s['id']})" for s in suppliers]
    chosen = st.selectbox("Supplier", supplier_options, key="tia_supplier_select")
    chosen_idx = supplier_options.index(chosen)
    supplier_id = suppliers[chosen_idx]["id"]

    if st.button("Scan transports", key="tia_scan", type="primary"):
        st.session_state.pop("tia_results", None)
        st.session_state.pop("tia_fix_results", None)
        st.session_state.tia_supplier_id = supplier_id

        with st.spinner("Fetching transport list..."):
            try:
                all_transports_raw = client.get_transports(supplier_id)
            except Exception as e:
                st.error(f"Could not fetch transports: {type(e).__name__}: {e}")
                return

        if isinstance(all_transports_raw, dict):
            transport_list = all_transports_raw.get("transport") or []
        elif isinstance(all_transports_raw, list):
            transport_list = all_transports_raw
        else:
            transport_list = []
        transport_list = [r for r in transport_list if isinstance(r, dict)]

        active_transports = [t for t in transport_list if t.get("active") is not False]

        if not active_transports:
            st.info("No active transports found for this supplier.")
            st.session_state.tia_results = []
            return

        results = []
        prog = st.progress(0, text="Scanning modalities...")
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

    all_price_fixes = []
    for r in results:
        all_price_fixes.extend(r.get("price_date_fixes") or [])

    st.markdown("---")
    st.subheader("Results")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Transports scanned", total)
    col2.metric("OK", ok_count)
    col3.metric("Issues found", len(flagged))
    col4.metric("Price dates to fix", len(all_price_fixes))

    if not flagged:
        st.success("All transports look good — inventories and price supplement dates are correct.")
        return

    # ── Price supplement date fixes ──────────────────────────────────────
    if all_price_fixes:
        st.markdown("---")
        st.subheader(f"Price supplement date fixes ({len(all_price_fixes)})")
        st.info(
            f"These modality price supplements have an end date other than {_EXPECTED_END}. "
            f"Select the ones to fix and click **Apply** to change their end date."
        )

        selected = {}
        for i, fix in enumerate(all_price_fixes):
            label = (
                f"{fix['transport_name']} — {fix['option_name']}: "
                f"'{fix['price_name']}' "
                f"({fix['current_start']} -> **{fix['current_end']}**)"
            )
            selected[i] = st.checkbox(label, value=True, key=f"tia_fix_{i}")

        chosen_fixes = [all_price_fixes[i] for i, sel in selected.items() if sel]

        if chosen_fixes:
            if st.button(
                f"Fix {len(chosen_fixes)} price date(s) to {_EXPECTED_END}",
                key="tia_apply_fixes", type="primary",
            ):
                supplier_id = st.session_state.get("tia_supplier_id", "")
                with st.spinner("Applying fixes..."):
                    fix_results = _apply_price_date_fixes(client, supplier_id, chosen_fixes)
                st.session_state.tia_fix_results = fix_results

        fix_results = st.session_state.get("tia_fix_results")
        if fix_results:
            st.markdown("---")
            st.subheader("Fix results")
            ok_fixes = [r for r in fix_results if r["ok"]]
            failed_fixes = [r for r in fix_results if not r["ok"]]
            if ok_fixes:
                st.success(f"{len(ok_fixes)} option(s) updated successfully.")
                for r in ok_fixes:
                    st.write(f"- Option `{r['option_code']}` on transport `{r['transport_id']}`: {r['detail']}")
            if failed_fixes:
                st.error(f"{len(failed_fixes)} option(s) failed:")
                for r in failed_fixes:
                    st.write(f"- Option `{r['option_code']}` on transport `{r['transport_id']}`: {r['detail']}")

    # ── All issues detail ────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("All flagged transports")

    for r in flagged:
        with st.expander(f"{r['name']}  (id: {r['transport_id']})", expanded=False):
            for issue in r["issues"]:
                st.error(issue)

            if r["options"]:
                st.markdown("**Modality details:**")
                for opt in r["options"]:
                    label = f"Option `{opt['code']}` — {opt.get('name') or '(unnamed)'} (pax {opt['min_pax']}-{opt['max_pax']})"
                    st.write(f"**{label}**")
                    if opt["inventories"]:
                        for inv in opt["inventories"]:
                            date = inv.get("inventoryDate") or {}
                            st.write(
                                f"  - Inventory: "
                                f"`{date.get('start')}` -> `{date.get('end')}`, "
                                f"qty={inv.get('quantity', 0)}"
                            )
                    if opt["prices"]:
                        for p in opt["prices"]:
                            end_marker = ""
                            if p.get("endDate") and p["endDate"] != _EXPECTED_END:
                                end_marker = " **<-- WRONG**"
                            st.write(
                                f"  - Price: `{p.get('startDate', '?')}` -> "
                                f"`{p.get('endDate', '?')}`{end_marker} | "
                                f"adult={p.get('adultPriceSupplement', 0)} "
                                f"child={p.get('childrenPriceSupplement', 0)} "
                                f"infant={p.get('infantPriceSupplement', 0)}"
                            )
