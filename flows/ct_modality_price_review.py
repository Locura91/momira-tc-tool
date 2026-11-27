"""
ClosedTour Modality Price Review flow.

Supplier → Tour code → Document → AI extracts prices per modality → Review → Apply.

The human selects a supplier, enters the ClosedTour code, uploads a supplier
contract document, and the AI extracts proposed prices for each live modality.
The human reviews the AI proposal (and can correct it) before applying to TC.
"""
import os
import tempfile
import streamlit as st

FLOW_BUILD = "2026-11-27-ct-price-review-v1"

# ── Session-state helpers ──────────────────────────────────────────────────────

def _s(key, default=None):
    return st.session_state.get(key, default)

def _reset():
    for k in list(st.session_state.keys()):
        if k.startswith("ctpr_"):
            del st.session_state[k]
    st.rerun()


# ── Price display helpers ──────────────────────────────────────────────────────

_OCC_KEYS = ("singlePrice", "doublePrice", "triplePrice", "quadruplePrice")
_OCC_LABELS = {"singlePrice": "Single", "doublePrice": "Double",
                "triplePrice": "Triple", "quadruplePrice": "Quadruple"}


def _price_from_row(row, occ_key):
    """Extract a numeric price from a price_list row for a given occupancy key."""
    p = (row.get("price") or {}).get(occ_key)
    if isinstance(p, dict):
        return p.get("amount")
    if isinstance(p, (int, float)):
        return p
    return None


def _render_price_list_readonly(price_list, label="Current TC prices"):
    """Display a price_list read-only."""
    if not price_list:
        st.caption(f"_{label}: none defined_")
        return
    st.caption(label)
    from datetime import date as _date
    today = _date.today().isoformat()
    for entry in price_list:
        s, e = entry.get("startDate", "?"), entry.get("endDate", "?")
        expired = e != "?" and e < today
        icon = "🔴" if expired else "🟢"
        prices = "  |  ".join(
            f"{_OCC_LABELS[k]}: **{_price_from_row(entry, k) if _price_from_row(entry, k) is not None else 'N/A'}**"
            for k in _OCC_KEYS
        )
        st.markdown(f"{icon} {entry.get('name') or 'Season'} ({s} → {e}):  {prices}")
        if expired:
            st.caption(f"⚠️ Expired {e}")


# ── Main render function ───────────────────────────────────────────────────────

def render_ct_modality_price_review(client):
    from app_helpers import momira_active_suppliers, _map_fetched_option_to_data, _fetch_url_text_safe
    from document_reader import extract_raw_text
    from ai_extractor import extract_modality_data
    from builder import normalize_price_list, fix_touching_season_boundaries, WEEKDAY_NAMES
    from schemas import ContractClosedTourOptionVO, OptionTranslation

    st.caption(f"Flow build: `{FLOW_BUILD}`")

    # ── Back / reset ───────────────────────────────────────────────────────────
    if st.button("🔙 Start over", key="ctpr_reset"):
        _reset()

    step = _s("ctpr_step", 1)

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 1 — Supplier
    # ══════════════════════════════════════════════════════════════════════════
    if step == 1:
        st.subheader("Step 1 — Select supplier")
        if not _s("ctpr_suppliers"):
            with st.spinner("Loading suppliers…"):
                try:
                    st.session_state.ctpr_suppliers = momira_active_suppliers(client.get_all_suppliers())
                except Exception as e:
                    st.error(f"Could not load suppliers: {e}")
                    return

        suppliers = _s("ctpr_suppliers", [])
        if not suppliers:
            st.error("No Momira_ suppliers found.")
            return

        opts = {f"{s.get('commercialName') or s.get('legalName')} — ID {s.get('id')}": s for s in suppliers}
        label = st.selectbox("Supplier", list(opts.keys()), key="ctpr_sup_select")
        sup = opts[label]

        if st.button("➡️ Continue", type="primary", key="ctpr_step1_cont"):
            st.session_state.ctpr_supplier_id = str(sup.get("id"))
            st.session_state.ctpr_supplier_name = sup.get("commercialName") or sup.get("legalName") or str(sup.get("id"))
            st.session_state.ctpr_step = 2
            st.rerun()
        return

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 2 — Tour code
    # ══════════════════════════════════════════════════════════════════════════
    if step == 2:
        supplier_id = _s("ctpr_supplier_id")
        st.subheader("Step 2 — Enter ClosedTour code")
        st.caption(f"Supplier: **{_s('ctpr_supplier_name')}**")

        tour_code = st.text_input("ClosedTour code (providerCode / tour code in TC)",
                                   value=_s("ctpr_tour_code_input", ""),
                                   key="ctpr_tour_code_input",
                                   placeholder="e.g. EGY-14")

        if st.button("🔍 Fetch tour from TC", type="primary", disabled=not tour_code.strip(), key="ctpr_fetch_tour"):
            with st.spinner(f"Fetching tour {tour_code.strip()}…"):
                try:
                    fetched = client.get_closed_tour(supplier_id, tour_code.strip())
                except Exception as e:
                    fetched = {"error": str(e)}

            if not fetched or "error" in fetched:
                st.error(f"Could not fetch tour `{tour_code.strip()}`: {fetched.get('error') if fetched else 'no response'}")
            else:
                mcodes = fetched.get("modalityCodes") or []
                if not mcodes:
                    st.warning("Tour found but has no modality codes — nothing to review.")
                else:
                    st.session_state.ctpr_fetched_tour = fetched
                    st.session_state.ctpr_tour_code = tour_code.strip()
                    st.session_state.ctpr_provider_code = fetched.get("providerCode") or tour_code.strip()
                    st.session_state.ctpr_nights = fetched.get("nights")
                    st.session_state.ctpr_currency = fetched.get("currency") or "EUR"
                    st.session_state.ctpr_modality_codes = mcodes
                    st.rerun()

        if _s("ctpr_fetched_tour"):
            ft = _s("ctpr_fetched_tour")
            mcodes = _s("ctpr_modality_codes", [])
            st.success(f"✅ Found: **{ft.get('name') or tour_code}**  |  {len(mcodes)} modality/modalities: `{'`, `'.join(mcodes)}`")
            st.caption(f"Nights: {ft.get('nights') or '?'}  |  Currency: {ft.get('currency') or '?'}  |  Supplier code: {ft.get('supplier') or '?'}")

            if st.button("➡️ Continue to document upload", type="primary", key="ctpr_step2_cont"):
                st.session_state.ctpr_step = 3
                st.rerun()
        return

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 3 — Document upload
    # ══════════════════════════════════════════════════════════════════════════
    if step == 3:
        supplier_id = _s("ctpr_supplier_id")
        tour_code = _s("ctpr_tour_code")
        st.subheader("Step 3 — Upload pricing document")
        st.caption(f"Supplier: **{_s('ctpr_supplier_name')}** · Tour: **{tour_code}** · "
                   f"Modalities: `{'`, `'.join(_s('ctpr_modality_codes', []))}`")

        url = st.text_input("Document URL (optional)", value="", key="ctpr_doc_url",
                             placeholder="https://…")
        uploaded = st.file_uploader("Or upload a file (PDF, Word, Excel, …)",
                                     key="ctpr_doc_upload",
                                     accept_multiple_files=False)

        if st.button("🔎 Extract text & continue", type="primary",
                     disabled=not (url.strip() or uploaded), key="ctpr_extract"):
            combined = []
            with st.spinner("Reading document…"):
                if url.strip():
                    text, err = _fetch_url_text_safe(url.strip())
                    if text:
                        combined.append(f"--- SOURCE: WEB PAGE ({url.strip()}) ---\n{text}")
                    else:
                        st.warning(f"Could not fetch URL: {err}")
                if uploaded:
                    suffix = os.path.splitext(uploaded.name)[1]
                    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                        tmp.write(uploaded.getbuffer())
                        tmp_path = tmp.name
                    doc_text = extract_raw_text(tmp_path)
                    combined.append(f"--- SOURCE: UPLOADED DOCUMENT ({uploaded.name}) ---\n{doc_text}")

            if not combined:
                st.error("No text could be extracted. Try a different document or URL.")
                return

            st.session_state.ctpr_raw_text = "\n\n".join(combined)
            st.session_state.ctpr_step = 4
            st.rerun()
        return

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 4 — AI extraction + review
    # ══════════════════════════════════════════════════════════════════════════
    if step == 4:
        supplier_id = _s("ctpr_supplier_id")
        tour_code = _s("ctpr_tour_code")
        provider_code = _s("ctpr_provider_code") or tour_code
        mcodes = _s("ctpr_modality_codes", [])
        nights = _s("ctpr_nights")
        currency = _s("ctpr_currency", "EUR")
        raw_text = _s("ctpr_raw_text", "")

        st.subheader("Step 4 — Review proposed prices")
        st.caption(f"Supplier: **{_s('ctpr_supplier_name')}** · Tour: **{tour_code}** · Currency: **{currency}**")

        # ── Fetch live modalities (cached) ─────────────────────────────────
        if not _s("ctpr_live_mods"):
            live_mods = []
            with st.spinner(f"Fetching {len(mcodes)} live modality/modalities from TC…"):
                for mcode in mcodes:
                    try:
                        opt = client.get_closed_tour_option(supplier_id, provider_code, mcode)
                        if opt and isinstance(opt, dict) and "error" not in opt:
                            live_mods.append({"code": mcode, "raw": opt,
                                              "data": _map_fetched_option_to_data(opt)})
                        else:
                            live_mods.append({"code": mcode, "raw": {}, "data": {}, "error": str(opt)})
                    except Exception as e:
                        live_mods.append({"code": mcode, "raw": {}, "data": {}, "error": str(e)})
            st.session_state.ctpr_live_mods = live_mods

        # ── Run AI extraction (cached) ─────────────────────────────────────
        if not _s("ctpr_ai_done"):
            ai_results = []
            for mod in _s("ctpr_live_mods", []):
                mcode = mod["code"]
                raw_name = (mod.get("raw") or {}).get("translations", {}).get("EN", {})
                if isinstance(raw_name, dict):
                    mname = raw_name.get("name") or mcode
                else:
                    mname = mcode
                hint = (f"Extract prices ONLY for the modality named '{mname}' (code: {mcode}). "
                        f"If the document lists prices for multiple modalities or service types, "
                        f"only return the ones for this specific modality.")
                with st.spinner(f"AI extracting prices for modality **{mcode}**…"):
                    try:
                        result = extract_modality_data(raw_text, human_hint=hint, tour_nights=nights)
                        ai_results.append({"code": mcode, "data": result, "error": None})
                    except Exception as e:
                        ai_results.append({"code": mcode, "data": {}, "error": str(e)})
            st.session_state.ctpr_ai_results = ai_results
            st.session_state.ctpr_ai_done = True
            st.rerun()

        live_mods = _s("ctpr_live_mods", [])
        ai_results = _s("ctpr_ai_results", [])
        ai_by_code = {r["code"]: r for r in ai_results}

        all_ready = True

        for mi, mod in enumerate(live_mods):
            mcode = mod["code"]
            merr = mod.get("error")
            ai = ai_by_code.get(mcode, {})
            ai_data = ai.get("data") or {}
            ai_err = ai.get("error")

            with st.expander(f"Modality: **{mcode}**" + (" ⚠️" if merr or ai_err else ""), expanded=True):
                if merr:
                    st.error(f"Could not load live modality from TC: {merr}")
                    all_ready = False
                    continue
                if ai_err:
                    st.warning(f"AI extraction failed for `{mcode}`: {ai_err}. Live prices shown; no proposal available.")

                # ── Live prices (read-only) ────────────────────────────────
                live_pl = mod["data"].get("price_list") or []
                _render_price_list_readonly(live_pl, label="Current TC prices (read-only)")

                st.markdown("---")

                # ── AI-proposed price_list (editable) ──────────────────────
                ai_pl_raw = fix_touching_season_boundaries(ai_data.get("price_list") or [])
                ai_pl = normalize_price_list(ai_pl_raw, currency)

                if not ai_pl:
                    st.warning("⚠️ AI found no prices in the document for this modality. "
                               "Check the document and try again, or enter prices manually below.")
                else:
                    if ai_data.get("pricing_notes"):
                        st.info(f"📝 AI note: {ai_data['pricing_notes']}")

                st.markdown("**Proposed prices from document** _(edit below before applying)_:")

                # For each proposed season row, render editable price fields
                # Store edits in session state under ctpr_edit_{mi}_{si}_{occ}
                edited_pl = []
                for si, row in enumerate(ai_pl):
                    s = row.get("startDate", "")
                    e = row.get("endDate", "")
                    sname = row.get("name") or f"Season {si + 1}"
                    st.markdown(f"**{sname}** ({s} → {e})")
                    edited_row = dict(row)
                    new_prices = {}
                    cols = st.columns(4)
                    for ci, occ_key in enumerate(_OCC_KEYS):
                        current_val = _price_from_row(row, occ_key)
                        key = f"ctpr_edit_{mi}_{si}_{occ_key}"
                        new_val = cols[ci].number_input(
                            _OCC_LABELS[occ_key],
                            value=float(current_val) if current_val is not None else 0.0,
                            min_value=0.0, step=1.0, format="%.2f",
                            key=key
                        )
                        new_prices[occ_key] = new_val

                    # Reconstruct price entry in TC format
                    price_obj = {}
                    for occ_key, val in new_prices.items():
                        price_obj[occ_key] = {"amount": val, "currencyCode": currency}
                    edited_row["price"] = price_obj
                    edited_pl.append(edited_row)

                # Operational days (from live, editable)
                live_op_days = mod["data"].get("operational_days") or list(WEEKDAY_NAMES)
                op_key = f"ctpr_op_{mi}"
                if op_key not in st.session_state:
                    st.session_state[op_key] = live_op_days
                st.session_state[op_key] = st.multiselect(
                    "Operational days", list(WEEKDAY_NAMES),
                    default=st.session_state[op_key], key=f"{op_key}_ms"
                )

                # On-request (from live, editable)
                or_key = f"ctpr_or_{mi}"
                or_default = bool((mod.get("raw") or {}).get("onRequest", False))
                if or_key not in st.session_state:
                    st.session_state[or_key] = or_default
                st.session_state[or_key] = st.checkbox(
                    "On Request", value=st.session_state[or_key], key=f"{or_key}_cb"
                )

                # Store edited price list for apply step
                st.session_state[f"ctpr_pl_{mi}"] = edited_pl

                if not edited_pl:
                    all_ready = False

        st.markdown("---")

        if not all_ready:
            st.warning("One or more modalities have issues — resolve them before applying.")

        if st.button("✅ Apply prices to TC", type="primary",
                     disabled=not all_ready, key="ctpr_apply"):
            st.session_state.ctpr_step = 5
            st.rerun()
        return

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 5 — Apply
    # ══════════════════════════════════════════════════════════════════════════
    if step == 5:
        supplier_id = _s("ctpr_supplier_id")
        tour_code = _s("ctpr_tour_code")
        provider_code = _s("ctpr_provider_code") or tour_code
        live_mods = _s("ctpr_live_mods", [])

        st.subheader("Step 5 — Applying prices to Travel Compositor")

        results = []
        for mi, mod in enumerate(live_mods):
            mcode = mod["code"]
            if mod.get("error"):
                results.append({"code": mcode, "status": "skipped", "msg": "Live fetch failed — skipped"})
                continue

            edited_pl = _s(f"ctpr_pl_{mi}", [])
            op_days = _s(f"ctpr_op_{mi}_ms", mod["data"].get("operational_days") or list(WEEKDAY_NAMES))
            on_request = _s(f"ctpr_or_{mi}_cb", False)

            raw = mod.get("raw") or {}
            translations = raw.get("translations") or {"EN": {"name": mcode}}

            # Build the option payload using the live record as base,
            # replacing only the priceList, operationalDays and onRequest.
            try:
                opt = ContractClosedTourOptionVO(
                    code=mcode,
                    operationalDays=op_days,
                    stopSales=mod["data"].get("stop_sales") or [],
                    priceList=edited_pl,
                    translations={
                        lang: OptionTranslation(
                            name=(v.get("name") if isinstance(v, dict) else mcode) or mcode,
                            remarks=(v.get("remarks") if isinstance(v, dict) else None)
                        )
                        for lang, v in translations.items()
                    } if translations else {"EN": OptionTranslation(name=mcode, remarks=None)},
                    onRequest=on_request,
                    quantityPerDay=raw.get("quantityPerDay") or 99,
                    useAdditionalOnRequestQuota=raw.get("useAdditionalOnRequestQuota") or False,
                )
                payload = opt.dict()
            except Exception as e:
                results.append({"code": mcode, "status": "error", "msg": f"Payload build failed: {e}"})
                continue

            with st.spinner(f"Updating modality **{mcode}**…"):
                try:
                    resp = client.update_closed_tour_option(supplier_id, provider_code, payload)
                    if isinstance(resp, dict) and "error" in resp:
                        results.append({"code": mcode, "status": "error", "msg": resp.get("message") or str(resp)})
                    else:
                        results.append({"code": mcode, "status": "ok", "msg": "Updated successfully"})
                except Exception as e:
                    results.append({"code": mcode, "status": "error", "msg": str(e)})

        # Show results
        any_error = False
        for r in results:
            if r["status"] == "ok":
                st.success(f"✅ **{r['code']}** — {r['msg']}")
            elif r["status"] == "skipped":
                st.warning(f"⏭️ **{r['code']}** — {r['msg']}")
            else:
                st.error(f"❌ **{r['code']}** — {r['msg']}")
                any_error = True

        if not any_error:
            st.balloons()
            st.success("All modality prices updated in Travel Compositor.")
        else:
            st.error("Some modalities could not be updated. Check the errors above.")

        if st.button("🔁 Review another tour", key="ctpr_restart"):
            _reset()
