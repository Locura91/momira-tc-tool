"""
flows/closedtour_audit.py — ClosedTour audit with two modes.

Mode A — Audit all:
  Phase 1: basic info check (name, nights, times, currency, active status).
  Phase 2: price & modality audit (date validity, price ordering, rounding,
           child discounts, missing/zero prices, operational days).

Mode B — Audit existing modalities (document comparison):
  Human uploads a price document. AI extracts prices per modality and
  compares them against what is live in TC. Highlights differences.

After mode-A audit, each tour shows a "Review & update prices" button that
launches the CT Modality Price Review flow (flows/ct_modality_price_review.py)
pre-filled with the supplier + tour code, skipping Steps 1-2.
"""

import math
import os
import tempfile
from datetime import date, timedelta

import streamlit as st

from flows.flow_ui_helpers import load_momira_suppliers as _load_momira_suppliers
from list_response_utils import normalize_list_response

MODULE_BUILD = "2026-11-28-audit-doc-compare"

_KEY = "cta_"
_WRAPPER_KEYS = ("closedTour", "closedTours", "items", "data", "results", "content")


# ── Phase 1: basic info ────────────────────────────────────────────────────────

def _audit_basic_info(tour: dict) -> list[str]:
    issues = []
    code = tour.get("code") or "(no code)"
    name = tour.get("name") or ""
    if not name:
        ds = tour.get("datasheets") or {}
        name = (ds.get("EN") or {}).get("name", "")
    if not name:
        issues.append(f"[{code}] Missing tour name")
    if not tour.get("currency"):
        issues.append(f"[{code}] Missing currency")
    nights = tour.get("nights")
    if nights is None or nights == 0:
        issues.append(f"[{code}] Nights is {nights} (should be >= 1)")
    if not tour.get("startTime"):
        issues.append(f"[{code}] Start time is empty")
    if not tour.get("endTime"):
        issues.append(f"[{code}] End time is empty")
    if tour.get("active") is False:
        issues.append(f"[{code}] Tour is INACTIVE")
    modality_codes = tour.get("modalityCodes") or []
    if not modality_codes:
        issues.append(f"[{code}] No modalities/options defined")
    return issues


# ── Phase 2: price & modality checks ───────────────────────────────────────────

def _get_price_amount(money_vo) -> float | None:
    if money_vo is None:
        return None
    if isinstance(money_vo, dict):
        return money_vo.get("amount")
    return None


def _audit_option(option: dict, tour_code: str) -> list[str]:
    issues = []
    ocode = option.get("code") or "(no code)"
    label = f"[{tour_code} / {ocode}]"

    op_days = option.get("operationalDays") or []
    if not op_days:
        issues.append(f"{label} No operational days set")

    price_list = option.get("priceList") or []
    if not price_list:
        issues.append(f"{label} Empty priceList - no pricing defined")
        return issues

    today_str = date.today().isoformat()
    soon_str = (date.today() + timedelta(days=60)).isoformat()

    for i, entry in enumerate(price_list):
        start = entry.get("startDate") or ""
        end = entry.get("endDate") or ""
        season_label = entry.get("name") or f"season {i + 1}"
        prefix = f"{label} {season_label} ({start} to {end})"

        # Date checks
        if end and end < today_str:
            issues.append(f"{prefix}: EXPIRED")
        elif end and end <= soon_str:
            issues.append(f"{prefix}: expires within 60 days")

        # Price checks
        price_block = entry.get("price") or {}
        amounts = {}
        for occ in ("singlePrice", "doublePrice", "triplePrice", "quadruplePrice"):
            amounts[occ] = _get_price_amount(price_block.get(occ))

        # Check for missing/zero prices
        for occ, val in amounts.items():
            occ_label = occ.replace("Price", "")
            if val is None:
                issues.append(f"{prefix}: {occ_label} price is missing")
            elif val <= 0:
                issues.append(f"{prefix}: {occ_label} price is {val} (must be > 0)")

        # Price ordering: single >= double >= triple >= quadruple
        present = {k: v for k, v in amounts.items() if v is not None and v > 0}
        order = ["singlePrice", "doublePrice", "triplePrice", "quadruplePrice"]
        vals = [present.get(k) for k in order]
        for j in range(len(vals) - 1):
            if vals[j] is not None and vals[j + 1] is not None:
                if vals[j] < vals[j + 1]:
                    left = order[j].replace("Price", "")
                    right = order[j + 1].replace("Price", "")
                    issues.append(
                        f"{prefix}: {left} ({vals[j]}) < {right} ({vals[j + 1]}) "
                        f"- violates ordering rule"
                    )

        # Rounding check: prices must be rounded up to whole numbers
        for occ, val in amounts.items():
            if val is not None and val > 0:
                rounded_up = math.ceil(val)
                if val != rounded_up:
                    occ_label = occ.replace("Price", "")
                    issues.append(
                        f"{prefix}: {occ_label} price {val} is not rounded up "
                        f"(should be {rounded_up})"
                    )

        # Child discount percentage checks
        for disc_key in (
            "singleChildPercentageDiscount",
            "doubleChildPercentageDiscount",
            "tripleChildPercentageDiscount",
            "quadrupleChildPercentageDiscount",
        ):
            disc_val = price_block.get(disc_key)
            if disc_val is not None:
                if disc_val < 0 or disc_val > 100:
                    disc_label = disc_key.replace("ChildPercentageDiscount", "")
                    issues.append(
                        f"{prefix}: {disc_label} child discount {disc_val}% "
                        f"is outside 0-100 range"
                    )

    return issues


# ── Mode B: document comparison ────────────────────────────────────────────────

def _render_doc_compare(client, supplier_id, supplier_label):
    """Load all tours, ask for a document, compare AI-extracted prices vs TC."""
    from document_reader import extract_raw_text
    from ai_extractor import extract_modality_data

    st.markdown("### Document price comparison")
    st.caption(
        "Upload the supplier's price document. The AI reads prices for each "
        "existing modality and compares them against what is currently live in TC."
    )

    # ── Load tours ────────────────────────────────────────────────────────────
    if f"{_KEY}dc_tours" not in st.session_state or \
            st.session_state.get(f"{_KEY}dc_supplier") != supplier_id:
        if st.button("Load existing tours", key=f"{_KEY}dc_load", type="primary"):
            with st.spinner("Loading closed tours…"):
                try:
                    raw = client.get_closed_tours(supplier_id, first=0, limit=200)
                    tours = normalize_list_response(raw, _WRAPPER_KEYS)
                except Exception as e:
                    st.error(f"Failed to load: {e}"); return
            st.session_state[f"{_KEY}dc_tours"] = tours
            st.session_state[f"{_KEY}dc_supplier"] = supplier_id
            st.session_state.pop(f"{_KEY}dc_raw_text", None)
            st.session_state.pop(f"{_KEY}dc_results", None)
            st.rerun()
        return

    tours = st.session_state[f"{_KEY}dc_tours"]
    if not tours:
        st.info("No closed tours found for this supplier."); return

    total_mods = sum(len(t.get("modalityCodes") or []) for t in tours)
    st.info(f"**{len(tours)}** tour(s), **{total_mods}** modality/ies loaded.")

    # ── Document upload ───────────────────────────────────────────────────────
    if f"{_KEY}dc_raw_text" not in st.session_state:
        uploaded = st.file_uploader(
            "Upload price document (PDF, Word, Excel, …)",
            key=f"{_KEY}dc_upload",
            accept_multiple_files=False,
        )
        if st.button("Extract & compare", key=f"{_KEY}dc_extract",
                     type="primary", disabled=not uploaded):
            suffix = os.path.splitext(uploaded.name)[1]
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(uploaded.getbuffer())
                tmp_path = tmp.name
            with st.spinner("Reading document…"):
                doc_text = extract_raw_text(tmp_path)
            if not doc_text or not doc_text.strip():
                st.error("Could not extract text from the document. Try a different format.")
                return
            st.session_state[f"{_KEY}dc_raw_text"] = doc_text
            st.session_state.pop(f"{_KEY}dc_results", None)
            st.rerun()
        return

    raw_text = st.session_state[f"{_KEY}dc_raw_text"]

    # ── AI extraction (cached) ────────────────────────────────────────────────
    if f"{_KEY}dc_results" not in st.session_state:
        results = []
        prog = st.progress(0, text="Running AI extraction…")
        all_mods = [(t, mcode)
                    for t in tours
                    for mcode in (t.get("modalityCodes") or [])]
        for idx, (tour, mcode) in enumerate(all_mods):
            tcode = tour.get("code") or ""
            nights = tour.get("nights")
            # Fetch live option for comparison
            try:
                live_opt = client.get_closed_tour_option(supplier_id, tcode, mcode)
                if not isinstance(live_opt, dict) or "error" in live_opt:
                    live_opt = {}
            except Exception:
                live_opt = {}
            # AI extraction
            hint = (f"Extract prices ONLY for the modality/service named '{mcode}' "
                    f"(tour code: {tcode}). Focus on per-person occupancy prices "
                    f"(single/double/triple/quadruple room).")
            try:
                ai_data = extract_modality_data(raw_text, human_hint=hint,
                                                tour_nights=nights)
                ai_err = None
            except Exception as e:
                ai_data = {}
                ai_err = str(e)
            results.append({
                "tour_code": tcode, "mcode": mcode,
                "live_opt": live_opt, "ai_data": ai_data, "ai_err": ai_err,
            })
            prog.progress((idx + 1) / max(len(all_mods), 1))
        prog.empty()
        st.session_state[f"{_KEY}dc_results"] = results
        st.rerun()

    results = st.session_state[f"{_KEY}dc_results"]

    # ── Display comparison ────────────────────────────────────────────────────
    st.markdown("---")
    _OCC = ["singlePrice", "doublePrice", "triplePrice", "quadruplePrice"]
    _LABEL = {"singlePrice": "Single", "doublePrice": "Double",
               "triplePrice": "Triple", "quadruplePrice": "Quadruple"}
    diffs_total = 0

    for r in results:
        tcode, mcode = r["tour_code"], r["mcode"]
        live_opt = r["live_opt"]
        ai_data = r.get("ai_data") or {}
        ai_err = r.get("ai_err")

        # Build live prices: first active price-list entry
        live_pl = live_opt.get("priceList") or []
        live_prices = {}   # occ → list of {season, amount}
        for entry in live_pl:
            pb = entry.get("price") or {}
            sname = entry.get("name") or f"{entry.get('startDate','')}–{entry.get('endDate','')}"
            for occ in _OCC:
                mv = pb.get(occ)
                amt = mv.get("amount") if isinstance(mv, dict) else mv
                if amt is not None:
                    live_prices.setdefault(occ, []).append((sname, amt))

        # AI proposed prices (from price_list)
        ai_pl = ai_data.get("price_list") or []
        ai_prices = {}
        for entry in ai_pl:
            pb = entry.get("price") or {}
            sname = entry.get("name") or f"{entry.get('startDate','')}–{entry.get('endDate','')}"
            for occ in _OCC:
                val = pb.get(occ)
                if val is not None:
                    ai_prices.setdefault(occ, []).append((sname, val))

        # Detect mismatches (compare first season's first value for simplicity)
        mismatches = []
        for occ in _OCC:
            live_vals = [amt for _, amt in live_prices.get(occ, [])]
            ai_vals = [amt for _, amt in ai_prices.get(occ, [])]
            if ai_vals and live_vals and ai_vals[0] != live_vals[0]:
                mismatches.append(
                    f"{_LABEL[occ]}: TC={live_vals[0]} / Doc={ai_vals[0]}"
                )

        diffs_total += len(mismatches)
        has_issue = bool(ai_err or mismatches)

        with st.expander(
            f"{'⚠️' if has_issue else '✅'} {tcode} / {mcode}",
            expanded=has_issue,
        ):
            if ai_err:
                st.warning(f"AI extraction error: {ai_err}")

            col_live, col_doc = st.columns(2)
            with col_live:
                st.markdown("**Current TC prices**")
                if live_pl:
                    for entry in live_pl:
                        pb = entry.get("price") or {}
                        sname = entry.get("name") or "Season"
                        rows = []
                        for occ in _OCC:
                            mv = pb.get(occ)
                            amt = mv.get("amount") if isinstance(mv, dict) else mv
                            if amt is not None:
                                rows.append(f"{_LABEL[occ]}: {amt}")
                        st.caption(sname)
                        st.text("\n".join(rows) if rows else "—")
                else:
                    st.text("No prices in TC")

            with col_doc:
                st.markdown("**Document prices (AI)**")
                if ai_pl:
                    for entry in ai_pl:
                        pb = entry.get("price") or {}
                        sname = entry.get("name") or "Season"
                        rows = []
                        for occ in _OCC:
                            val = pb.get(occ)
                            if val is not None:
                                rows.append(f"{_LABEL[occ]}: {val}")
                        st.caption(sname)
                        st.text("\n".join(rows) if rows else "—")
                else:
                    st.text("Nothing extracted from document")

            if mismatches:
                st.error("**Differences:** " + " | ".join(mismatches))
            elif not ai_err and ai_pl:
                st.success("Prices match.")

    st.markdown("---")
    if diffs_total == 0:
        st.success("All modalities match the document.")
    else:
        st.warning(f"**{diffs_total}** price difference(s) found across modalities.")

    if st.button("🔄 Upload a different document", key=f"{_KEY}dc_reset"):
        st.session_state.pop(f"{_KEY}dc_raw_text", None)
        st.session_state.pop(f"{_KEY}dc_results", None)
        st.rerun()


# ── Streamlit UI ───────────────────────────────────────────────────────────────

def render_closedtour_audit(client):
    # If the user clicked "Review & update prices" for a specific tour, hand off
    # to the CT Modality Price Review flow with the state pre-filled.
    if st.session_state.get(f"{_KEY}goto_price_review"):
        from flows.ct_modality_price_review import render_ct_modality_price_review
        render_ct_modality_price_review(client)
        return

    st.subheader("ClosedTour Audit")

    # Supplier selection
    suppliers = _load_momira_suppliers(client)
    if not suppliers:
        st.warning("No active suppliers found.")
        return

    supplier_options = {f"{s['name']} ({s['id']})": s["id"] for s in suppliers}
    selected = st.selectbox(
        "Select supplier",
        list(supplier_options.keys()),
        key=f"{_KEY}supplier",
    )
    supplier_id = supplier_options[selected]

    # Mode selection
    mode = st.radio(
        "Audit mode",
        ["Audit all (basic info + price checks)", "Audit existing modalities (compare with document)"],
        key=f"{_KEY}mode",
        help="'Audit all' runs the full two-phase check on every tour. "
             "'Compare with document' loads existing modalities and checks whether "
             "their prices match a supplier document you upload."
    )

    if mode.startswith("Audit existing"):
        _render_doc_compare(client, supplier_id, selected)
        return

    st.caption("Two-phase check: basic tour information, then prices and modalities.")

    if not st.button("Run audit", type="primary", key=f"{_KEY}run"):
        return

    # Load tours
    with st.spinner("Loading closed tours..."):
        try:
            raw = client.get_closed_tours(supplier_id, first=0, limit=200)
        except Exception as e:
            st.error(f"Failed to load closed tours: {e}")
            return

    tours = normalize_list_response(raw, _WRAPPER_KEYS)
    if not tours:
        st.info("This supplier has no closed tours.")
        return

    st.markdown(f"Found **{len(tours)}** closed tour(s).")

    # ── Phase 1 ──────────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Phase 1: Basic Information Check")

    phase1_all_ok = True
    phase1_issues_total = 0

    for tour in tours:
        code = tour.get("code") or "(no code)"
        name = tour.get("name") or ""
        if not name:
            ds = tour.get("datasheets") or {}
            name = (ds.get("EN") or {}).get("name", code)

        issues = _audit_basic_info(tour)
        status_icon = "+" if not issues else "!"
        modality_count = len(tour.get("modalityCodes") or [])

        with st.expander(
            f"{'OK' if not issues else 'ISSUES'} | {code} - {name} "
            f"({tour.get('nights', '?')} nights, {modality_count} modalities)",
            expanded=bool(issues),
        ):
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"**Code:** `{code}`")
                st.markdown(f"**Name:** {name}")
                st.markdown(f"**Nights:** {tour.get('nights', 'N/A')}")
                st.markdown(f"**Pax:** {tour.get('minPax', '?')}-{tour.get('maxPax', '?')}")
            with col2:
                st.markdown(f"**Currency:** {tour.get('currency', 'N/A')}")
                st.markdown(f"**Start time:** {tour.get('startTime') or 'N/A'}")
                st.markdown(f"**End time:** {tour.get('endTime') or 'N/A'}")
                active = tour.get("active")
                st.markdown(f"**Active:** {'Yes' if active else 'No'}")

            if issues:
                phase1_all_ok = False
                phase1_issues_total += len(issues)
                for iss in issues:
                    st.warning(iss)
            else:
                st.success("All basic checks passed.")

    if phase1_all_ok:
        st.success(
            "Phase 1 complete - all basic information checks passed. "
            "Proceeding to price audit..."
        )
    else:
        st.error(
            f"Phase 1 found **{phase1_issues_total}** issue(s). "
            f"Review the warnings above. Proceeding to price audit anyway..."
        )

    # ── Phase 2 ──────────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Phase 2: Price & Modality Audit")

    phase2_issues_total = 0
    tours_checked = 0
    options_checked = 0

    for tour in tours:
        code = tour.get("code") or "(no code)"
        name = tour.get("name") or ""
        if not name:
            ds = tour.get("datasheets") or {}
            name = (ds.get("EN") or {}).get("name", code)

        modality_codes = tour.get("modalityCodes") or []
        if not modality_codes:
            continue

        tours_checked += 1
        tour_issues = []

        with st.expander(
            f"{code} - {name} ({len(modality_codes)} modalities)",
            expanded=False,
        ):
            for mcode in modality_codes:
                with st.spinner(f"Fetching option {mcode}..."):
                    try:
                        option = client.get_closed_tour_option(supplier_id, code, mcode)
                    except Exception as e:
                        st.error(f"Failed to load option {mcode}: {e}")
                        tour_issues.append(f"[{code}/{mcode}] API error: {e}")
                        continue

                if not option or isinstance(option, dict) and option.get("error"):
                    st.error(f"Option {mcode}: could not load from TC")
                    tour_issues.append(f"[{code}/{mcode}] Could not load")
                    continue

                options_checked += 1
                issues = _audit_option(option, code)
                tour_issues.extend(issues)

                st.markdown(f"**Modality: `{mcode}`**")
                price_list = option.get("priceList") or []
                op_days = option.get("operationalDays") or []
                st.caption(
                    f"{len(price_list)} price season(s), "
                    f"operates {', '.join(d[:3] for d in op_days) if op_days else 'NO DAYS SET'}"
                )

                # Show current prices
                for entry in price_list:
                    season_name = entry.get("name") or "Season"
                    start = entry.get("startDate") or "?"
                    end = entry.get("endDate") or "?"
                    price_block = entry.get("price") or {}

                    prices = {}
                    for occ in ("singlePrice", "doublePrice", "triplePrice", "quadruplePrice"):
                        mv = price_block.get(occ)
                        if mv and isinstance(mv, dict):
                            prices[occ] = mv.get("amount")
                        else:
                            prices[occ] = None

                    price_str = " | ".join(
                        f"{occ.replace('Price', '')}: {v if v is not None else 'N/A'}"
                        for occ, v in prices.items()
                    )
                    st.markdown(
                        f"  - **{season_name}** ({start} to {end}): {price_str}"
                    )

                # Show issues for this option
                option_issues = [i for i in issues]
                if option_issues:
                    for iss in option_issues:
                        st.warning(iss)
                else:
                    st.success(f"Modality `{mcode}` - all price checks passed.")

                st.markdown("---")

            phase2_issues_total += len(tour_issues)

            # ── Jump to price review for this tour ────────────────────────
            btn_key = f"{_KEY}price_review_{code}"
            if st.button(f"💶 Review & update prices for **{code}**",
                         key=btn_key, use_container_width=True):
                # Pre-fill the CT price review flow's session state so it
                # starts at Step 3 (document upload) with supplier + tour known.
                _supplier_name = selected  # the selectbox label carries the name
                st.session_state.ctpr_step = 3
                st.session_state.ctpr_supplier_id = supplier_id
                st.session_state.ctpr_supplier_name = _supplier_name
                st.session_state.ctpr_tour_code_input = code
                st.session_state.ctpr_tour_code = code
                st.session_state.ctpr_provider_code = tour.get("providerCode") or code
                st.session_state.ctpr_nights = tour.get("nights")
                st.session_state.ctpr_currency = tour.get("currency") or "EUR"
                st.session_state.ctpr_modality_codes = tour.get("modalityCodes") or modality_codes
                st.session_state.ctpr_fetched_tour = tour
                st.session_state[f"{_KEY}goto_price_review"] = True
                st.rerun()

    # Summary
    st.markdown("---")
    st.markdown("### Audit Summary")
    st.markdown(f"- **Tours checked:** {len(tours)}")
    st.markdown(f"- **Options/modalities checked:** {options_checked}")

    total_issues = phase1_issues_total + phase2_issues_total
    if total_issues == 0:
        st.success("All checks passed! No issues found.")
    else:
        st.error(
            f"**{total_issues}** issue(s) found in total "
            f"({phase1_issues_total} basic info, {phase2_issues_total} pricing)."
        )
