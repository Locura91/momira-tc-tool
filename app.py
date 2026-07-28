"""
Review UI for the DMC -> Travel Compositor Closed Tour pipeline.

Restructured as a strict sequential wizard so the human always specifies
WHAT they're doing (create/add-option/update-tour/update-option) and WHICH
supplier/tour/modality BEFORE any extraction happens - avoids the mistake
of extracting first and only later realizing the wrong action/tour was set.

Run with:
    streamlit run app.py

Reuses everything already built and tested:
    - api_client.py       (auth, destination resolution, uploads)
    - schemas.py          (validated payload models)
    - builder.py           (combines pre-config + extracted data + destinations)
    - document_reader.py  (PDF/Word/Excel -> raw text)
    - ai_extractor.py     (raw text -> structured English data)
    - web_extractor.py    (URL -> structured data, incl. destination scanning)
"""
import json
import tempfile
import os
import time
import streamlit as st
import pandas as pd

if hasattr(st, "secrets"):
    for _key in ["TRAVELC_BASE_URL", "TRAVELC_MICROSITE_ID", "TRAVELC_USERNAME",
                 "TRAVELC_PASSWORD", "ANTHROPIC_API_KEY", "PEXELS_API_KEY", "FREEIMAGE_API_KEY"]:
        try:
            if _key in st.secrets and _key not in os.environ:
                os.environ[_key] = st.secrets[_key]
        except Exception:
            pass

from api_client import TravelCompositorAPI
from schemas import HumanPreConfig, TicketHumanPreConfig
from builder import build_closed_tour_payloads, build_ticket_payloads
from document_reader import extract_raw_text, extract_images
from ai_extractor import extract_structured_data, extract_option_only_data, detect_tour_variants, detect_multiple_modalities, apply_clarification, extract_ticket_data, extract_ticket_option_only_data, detect_ticket_variants
from web_extractor import get_page_text, get_page_images
from pexels_client import search_images
from freeimage_client import upload_images as upload_images_freeimage

FALLBACK_IMAGE = "https://multiwander.com/wp-content/uploads/2026/07/Please-load-images.png"
ALL_WEEKDAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]

TICKET_ACTION_LABELS = {
    "create": "1: Create new Ticket + 1 Modality",
    "add_option": "2: Add new Modality to existing Ticket",
    "update_ticket": "3: Update existing Ticket (Not updating modality)",
    "update_option": "4: Update existing Ticket Modality",
}
TICKET_ACTION_FIELDS = {
    "create": ["ticket_code", "min_passengers", "max_passengers", "currency", "modality_code", "on_request", "release_days"],
    "add_option": ["existing_ticket_code", "modality_code", "on_request"],
    "update_ticket": ["existing_ticket_code", "release_days"],
    "update_option": ["existing_ticket_code", "modality_code", "on_request"],
}

ACTION_LABELS = {
    "create": "1: Create new ClosedTour + 1 Modality",
    "add_option": "2: Add new Modality to existing ClosedTour",
    "update_tour": "3: Update existing ClosedTour (Not updating modality)",
    "update_option": "4: Update existing ClosedTour Modality",
}
ACTION_FIELDS = {
    "create": ["provider_code", "min_pax", "max_pax", "currency", "modality_code", "on_request", "release_days"],
    "add_option": ["existing_tour_code", "modality_code", "on_request"],
    "update_tour": ["existing_tour_code", "release_days"],
    "update_option": ["existing_tour_code", "currency", "modality_code", "on_request"],
}

def editable_table(label, df, edit_key, on_save, num_rows="dynamic", column_config=None):
    """
    Shows a table in READ-ONLY display mode by default (clean st.dataframe),
    with a pencil button to switch into an editable st.data_editor.
    on_save(edited_df) is called with the final edited DataFrame BEFORE the
    rerun happens (rerun halts execution immediately, so applying the data
    inside this function - not after it returns - is required for the save
    to actually persist).
    """
    edit_flag_key = f"_editing_table_{edit_key}"
    if edit_flag_key not in st.session_state:
        st.session_state[edit_flag_key] = False

    if not st.session_state[edit_flag_key]:
        tcol, bcol = st.columns([12, 1])
        with tcol:
            st.markdown(f"**{label}**")
            st.dataframe(df, use_container_width=True, hide_index=True)
        with bcol:
            st.write("")
            if st.button("✏️", key=f"pencil_table_{edit_key}", help=f"Edit {label}"):
                st.session_state[edit_flag_key] = True
                st.rerun()
    else:
        st.markdown(f"**{label}** (editing)")
        edited = st.data_editor(
            df, num_rows=num_rows, use_container_width=True,
            key=f"editor_{edit_key}", column_config=column_config or {}
        )
        if st.button("✅ Save", key=f"save_table_{edit_key}", type="primary"):
            on_save(edited)
            st.session_state[edit_flag_key] = False
            st.rerun()


def editable_field(label, data_dict, field_key, widget="text_input", height=None, default_value=""):
    """
    Renders a field in READ-ONLY display mode by default, with a small
    pencil button to switch it into an editable widget. Saving switches
    back to display mode. Mutates data_dict[field_key] directly on save.
    """
    edit_flag_key = f"_editing_{field_key}"
    if edit_flag_key not in st.session_state:
        st.session_state[edit_flag_key] = False

    current_value = data_dict.get(field_key, default_value)
    if current_value in (None, ""):
        current_value = default_value

    if not st.session_state[edit_flag_key]:
        vcol, bcol = st.columns([12, 1])
        with vcol:
            st.markdown(f"**{label}**")
            if current_value:
                st.markdown(
                    f"<div style='white-space: pre-wrap; background:#f6f6f6; padding:8px; "
                    f"border-radius:4px;'>{current_value}</div>",
                    unsafe_allow_html=True
                )
            else:
                st.caption("(empty)")
        with bcol:
            st.write("")
            if st.button("✏️", key=f"pencil_{field_key}", help=f"Edit {label}"):
                st.session_state[edit_flag_key] = True
                st.rerun()
    else:
        widget_key = f"_widgetval_{field_key}"
        if widget == "text_area":
            new_value = st.text_area(label, value=current_value, height=height or 120, key=widget_key)
        elif widget == "number_input":
            new_value = st.number_input(label, min_value=1, value=int(current_value or 1), key=widget_key)
        else:
            new_value = st.text_input(label, value=current_value, key=widget_key)
        if st.button("✅ Save", key=f"save_{field_key}", type="primary"):
            data_dict[field_key] = new_value
            st.session_state[edit_flag_key] = False
            st.rerun()

    return data_dict.get(field_key, default_value)


def render_multi_modality_flow(client, url=None, uploaded_files=None):
    """
    Queue-based flow for adding MULTIPLE modalities from one shared source:
    1. Reuse the URL/document(s) already provided above (no re-entry), auto-detect
       distinct pricing categories, and let the human explicitly SELECT which
       detected ones to actually include (+ add more manually if needed)
    2. Review each SELECTED one individually - its OWN focused AI extraction (via a
       per-item hint), so modalities never get mixed up with each other
    3. Publish all of them SEQUENTIALLY, one real POST call at a time, each
       with its own clear success/failure status (not one opaque batch call)
    """
    if "mm_phase" not in st.session_state:
        st.session_state.mm_phase = "gather"

    supplier_id = st.session_state.cfg_supplier_id
    existing_tour_code = st.session_state.cfg_existing_tour_code
    currency = st.session_state.cfg_currency
    on_request = st.session_state.cfg_on_request

    # ------------------------------------------------------------------
    # PHASE 1: detect modalities from the source already provided above
    # ------------------------------------------------------------------
    if st.session_state.mm_phase == "gather":
        if not (url or uploaded_files):
            st.info("Provide a URL and/or upload document(s) above, then click below.")
        if st.button("🔎 Detect Modalities", disabled=not (url or uploaded_files)):
            with st.spinner("Gathering content and detecting distinct pricing categories..."):
                try:
                    combined_parts = []
                    if url:
                        combined_parts.append(f"--- SOURCE: WEB PAGE ({url}) ---\n{get_page_text(url)}")
                    for uploaded in (uploaded_files or []):
                        suffix = os.path.splitext(uploaded.name)[1]
                        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                            tmp.write(uploaded.getbuffer())
                            tmp_path = tmp.name
                        combined_parts.append(f"--- SOURCE: UPLOADED DOCUMENT ({uploaded.name}) ---\n{extract_raw_text(tmp_path)}")
                        os.remove(tmp_path)

                    raw_text = "\n\n".join(combined_parts)
                    detected = detect_multiple_modalities(raw_text)

                    candidates = []
                    for m in detected:
                        raw_code = (m.get("suggested_code") or m.get("label") or "").strip()
                        clean_code = "".join(c for c in raw_code if c not in "/\\+-")
                        candidates.append({"code": clean_code, "hint": m.get("label", ""), "selected": True})
                    if not candidates:
                        candidates = [{"code": "", "hint": "", "selected": True}]

                    st.session_state.mm_raw_text = raw_text
                    st.session_state.mm_candidates = candidates
                    st.session_state.mm_phase = "prepare_queue"
                    st.rerun()
                except Exception as e:
                    st.error(f"Extraction failed: {e}")
        return

    # ------------------------------------------------------------------
    # PHASE 2: explicitly SELECT which detected modalities to include
    # ------------------------------------------------------------------
    if st.session_state.mm_phase == "prepare_queue":
        st.subheader("Modalities detected - select which ones to include")
        st.caption("Untick any that don't apply - only SELECTED modalities will be reviewed and published. "
                  "Edit codes/hints as needed, or add more rows manually.")

        candidates = st.session_state.mm_candidates
        for i, cand in enumerate(candidates):
            ccol1, ccol2, ccol3 = st.columns([1, 3, 3])
            with ccol1:
                cand["selected"] = st.checkbox("Include", value=cand["selected"], key=f"mm_sel_{i}")
            with ccol2:
                cand["code"] = st.text_input("Modality Code", value=cand["code"], key=f"mm_code_{i}")
            with ccol3:
                cand["hint"] = st.text_input("Focus Hint", value=cand["hint"], key=f"mm_hint_{i}")

        if st.button("➕ Add another modality manually"):
            candidates.append({"code": "", "hint": "", "selected": True})
            st.rerun()

        invalid_codes = []
        new_queue = []
        for cand in candidates:
            if not cand["selected"]:
                continue
            code = cand["code"].strip()
            if not code:
                continue
            if any(c in code for c in ["/", "\\", "+", "-"]):
                invalid_codes.append(code)
                continue
            new_queue.append({"code": code, "hint": cand["hint"].strip(), "data": None, "confirmed": False})

        if invalid_codes:
            st.error(f"🚫 These codes contain invalid characters (/, \\, +, -) and were excluded: {invalid_codes}")

        st.caption(f"**{len(new_queue)}** modality(ies) selected to review and publish.")

        if st.button("➡️ Start Reviewing", type="primary", disabled=not new_queue):
            st.session_state.mm_queue = new_queue
            st.session_state.mm_queue_index = 0
            st.session_state.mm_phase = "reviewing"
            st.rerun()
        return

    # ------------------------------------------------------------------
    # PHASE 3: review each modality individually, one at a time
    # ------------------------------------------------------------------
    if st.session_state.mm_phase == "reviewing":
        idx = st.session_state.mm_queue_index
        queue = st.session_state.mm_queue
        current = queue[idx]

        st.subheader(f"Reviewing modality {idx + 1} of {len(queue)}: **{current['code']}**")
        st.progress((idx) / len(queue))

        if current["data"] is None:
            with st.spinner(f"Extracting pricing/schedule focused on '{current['hint'] or current['code']}'..."):
                current["data"] = extract_option_only_data(st.session_state.mm_raw_text, human_hint=current["hint"])

        data = current["data"]

        data["operational_days"] = st.multiselect(
            "Operational Days", ALL_WEEKDAYS, default=data.get("operational_days", ALL_WEEKDAYS), key=f"mm_days_{idx}"
        )
        with st.expander("Stop Sales"):
            stop_sales_json = st.text_area(
                "stopSales (JSON array)", json.dumps(data.get("stop_sales", []), indent=2), key=f"mm_stops_{idx}"
            )
            try:
                data["stop_sales"] = json.loads(stop_sales_json)
            except json.JSONDecodeError as e:
                st.error(f"stopSales isn't valid JSON: {e}")

        default_price_list = sorted(
            data.get("price_list") or [{"name": "Example row", "startDate": "2027-01-01", "endDate": "2027-12-31",
                                        "price": {"singlePrice": {"amount": 0, "currency": currency},
                                                 "doublePrice": {"amount": 0, "currency": currency}}}],
            key=lambda e: e.get("startDate", "")
        )
        price_df_rows = []
        for entry in default_price_list:
            price = entry.get("price", {}) or {}
            def _amt(key, price=price):
                block = price.get(key)
                return block.get("amount") if isinstance(block, dict) else None
            price_df_rows.append({"Name": entry.get("name", ""), "Start Date": entry.get("startDate", ""),
                                  "End Date": entry.get("endDate", ""), "Single": _amt("singlePrice"),
                                  "Double": _amt("doublePrice"), "Triple": _amt("triplePrice"), "Quadruple": _amt("quadruplePrice")})
        price_df = pd.DataFrame(price_df_rows)

        def _save_mm_price_list(edited_df, data=data, currency=currency):
            def _row_to_entry(row):
                price = {}
                for col, key in [("Single", "singlePrice"), ("Double", "doublePrice"), ("Triple", "triplePrice"), ("Quadruple", "quadruplePrice")]:
                    val = row.get(col)
                    if val is not None and not pd.isna(val):
                        price[key] = {"amount": float(val), "currency": currency}
                entry = {"startDate": str(row.get("Start Date", "")).strip(), "endDate": str(row.get("End Date", "")).strip(), "price": price}
                name = str(row.get("Name", "")).strip()
                if name:
                    entry["name"] = name
                return entry
            data["price_list"] = sorted(
                [_row_to_entry(r) for _, r in edited_df.iterrows() if str(r.get("Start Date", "")).strip() and str(r.get("End Date", "")).strip()],
                key=lambda e: e.get("startDate", "")
            )

        editable_table(f"Pricing - {current['code']}", price_df, f"mm_pricing_{idx}", on_save=_save_mm_price_list)

        is_last = idx == len(queue) - 1
        btn_label = "✅ Confirm this modality & Finish Review" if is_last else "✅ Confirm this modality & Continue →"
        if st.button(btn_label, type="primary", disabled=not data.get("price_list")):
            current["confirmed"] = True
            if is_last:
                st.session_state.mm_phase = "publishing"
            else:
                st.session_state.mm_queue_index += 1
            st.rerun()
        if not data.get("price_list"):
            st.info("Add at least one price row before continuing.")
        return

    # ------------------------------------------------------------------
    # PHASE 4: publish all confirmed modalities, ONE BY ONE
    # ------------------------------------------------------------------
    if st.session_state.mm_phase == "publishing":
        queue = st.session_state.mm_queue
        st.subheader(f"Ready to publish {len(queue)} modalities - one by one")
        for q in queue:
            st.write(f"- **{q['code']}** ({len(q['data'].get('price_list', []))} price row(s))")

        if st.button("🚀 Publish all (one by one)", type="primary"):
            for q in queue:
                with st.spinner(f"Publishing '{q['code']}'..."):
                    pre_config = HumanPreConfig(
                        supplier_id=supplier_id,
                        provider_code=st.session_state.get("fetched_tour_provider_code") or "XXX-1",
                        min_pax=1, max_pax=9, currency=currency,
                        modality_code=q["code"], on_request=on_request
                    )
                    payloads = build_closed_tour_payloads(pre_config, q["data"], client)
                    if payloads["tour_option_error"]:
                        st.error(f"❌ **{q['code']}**: invalid payload - {payloads['tour_option_error']}")
                        continue
                    result, used_code = try_code_variants(
                        lambda c: client.create_closed_tour_option(supplier_id, c, payloads["tour_option_payload"]),
                        existing_tour_code
                    )
                    if "error" in result:
                        st.error(f"❌ **{q['code']}**: failed - {result}")
                    else:
                        st.success(f"✅ **{q['code']}**: published successfully (code `{used_code}`).")

        if st.button("🆕 Start a new batch"):
            for key in ["mm_phase", "mm_raw_text", "mm_candidates", "mm_queue", "mm_queue_index"]:
                st.session_state.pop(key, None)
            st.rerun()
        return


def try_code_variants(call_fn, code):
    """
    Tries `code` as given, then falls back to toggling the 'CLOSEDTOUR-' prefix -
    we've seen conflicting evidence about whether Travel Compositor's lookup
    needs the ClosedTour/Provider Code or the internal CLOSEDTOUR-XXXXX code,
    so try both rather than betting on just one.
    Returns (result_dict, code_that_worked_or_None).
    """
    variants = [code]
    if code.upper().startswith("CLOSEDTOUR-"):
        variants.append(code[len("CLOSEDTOUR-"):])
    else:
        variants.append(f"CLOSEDTOUR-{code}")

    result = None
    for v in variants:
        result = call_fn(v)
        if "error" not in result:
            return result, v
    return result, None


def render_ticket_flow(client):
    """
    Full Ticket wizard (Steps 1-6), mirroring the ClosedTour flow's proven
    patterns but adapted for Tickets' real structural differences: one
    geolocation instead of an itinerary, passenger-type pricing (adult/
    child/infant) instead of room-occupancy, ONE price+date range per
    Modality instead of a seasonal array, structured meeting points.
    Uses tk_-prefixed session_state keys throughout to avoid any collision
    with the ClosedTour flow's state.
    """
    if "tk_step1_confirmed" not in st.session_state:
        st.session_state.tk_step1_confirmed = False
    if "tk_step2_confirmed" not in st.session_state:
        st.session_state.tk_step2_confirmed = False

    # ------------------------------------------------------------------
    # TICKET STEP 2: Action + Supplier
    # ------------------------------------------------------------------
    st.header("Ticket — Step 2: What do you want to do?")

    if st.session_state.tk_step1_confirmed:
        st.success(f"✅ Action: **{TICKET_ACTION_LABELS[st.session_state.tk_cfg_action]}** | "
                   f"Supplier ID: **{st.session_state.tk_cfg_supplier_id}**")
        if st.button("🔄 Change action / supplier", key="tk_change_action"):
            st.session_state.tk_step1_confirmed = False
            st.session_state.tk_step2_confirmed = False
            st.rerun()
    else:
        action_key = st.radio(
            "Choose one:", list(TICKET_ACTION_LABELS.keys()),
            format_func=lambda k: TICKET_ACTION_LABELS[k], key="tk_action_radio"
        )
        if st.session_state.suppliers_cache is None:
            with st.spinner("Loading supplier list from Travel Compositor..."):
                st.session_state.suppliers_cache = client.get_all_suppliers()

        supplier_id_choice = None
        if st.session_state.suppliers_cache:
            supplier_options = {
                f"{s.get('commercialName') or s.get('legalName') or '(unnamed)'} — ID {s.get('id')}": s.get("id")
                for s in st.session_state.suppliers_cache
            }
            selected_label = st.selectbox("Supplier (select by name)", list(supplier_options.keys()), key="tk_supplier_select")
            supplier_id_choice = str(supplier_options[selected_label])
            if st.button("🔄 Refresh supplier list", key="tk_refresh_suppliers"):
                st.session_state.suppliers_cache = None
                st.rerun()
        else:
            st.error("Could not load the supplier list from Travel Compositor.")
            with st.expander("⚠️ Emergency manual entry"):
                supplier_id_choice = st.text_input("Supplier ID (numeric)", value="", key="tk_supplier_manual")

        if st.button("➡️ Continue to Step 3", type="primary", disabled=not supplier_id_choice, key="tk_continue1"):
            st.session_state.tk_cfg_action = action_key
            st.session_state.tk_cfg_supplier_id = supplier_id_choice
            st.session_state.tk_step1_confirmed = True
            st.rerun()
        return

    # ------------------------------------------------------------------
    # TICKET STEP 3: Action-specific details
    # ------------------------------------------------------------------
    st.header("Ticket — Step 3: Details for this action")
    action = st.session_state.tk_cfg_action
    needed = TICKET_ACTION_FIELDS[action]
    supplier_id = st.session_state.tk_cfg_supplier_id

    if st.session_state.tk_step2_confirmed:
        st.success("✅ Step 3 details confirmed.")
        if st.button("🔄 Change details", key="tk_change_details"):
            st.session_state.tk_step2_confirmed = False
            st.rerun()
    else:
        ticket_code_in = min_pass_in = max_pass_in = currency_in = modality_code_in = existing_ticket_code_in = None
        on_request_in = False
        release_days_in = 30

        if "existing_ticket_code" in needed:
            existing_ticket_code_in = st.text_input(
                "Existing Ticket Code", placeholder="e.g. JAP-T1", key="tk_existing_code"
            ).strip()

            if st.button("🔍 Check what's already online for this code", disabled=not existing_ticket_code_in, key="tk_check_online"):
                with st.spinner("Fetching from Travel Compositor..."):
                    fetched = client.get_ticket(supplier_id, existing_ticket_code_in)
                    st.session_state.tk_fetched_ticket = fetched
                    st.session_state.tk_fetched_option = None
                    if isinstance(fetched, dict) and "error" not in fetched:
                        st.session_state.tk_fetched_currency = fetched.get("currency")

            if st.session_state.get("tk_fetched_ticket"):
                t = st.session_state.tk_fetched_ticket
                if "error" in t:
                    st.error(f"Not found or error: {t.get('message', t)}")
                else:
                    st.success(f"Found: **{t.get('name', '(no name)')}**")
                    st.caption(f"Will reuse Currency **{t.get('currency')}** from this ticket.")
                    existing_modalities = t.get("modalityCodes", [])
                    st.write(f"Existing modality codes: {existing_modalities if existing_modalities else '(none)'}")

        if "ticket_code" in needed:
            ticket_code_in = st.text_input("Ticket Code", value="", placeholder="e.g. JAP-T1", key="tk_ticket_code")
        if "min_passengers" in needed:
            min_pass_in = st.selectbox("Min Passengers", [1, 2], key="tk_min_pass")
        if "max_passengers" in needed:
            max_pass_in = st.selectbox("Max Passengers", list(range(2, 21)), index=7, key="tk_max_pass")
        if "currency" in needed:
            currency_in = st.text_input("Currency", value="", placeholder="e.g. EUR", key="tk_currency")
        if "modality_code" in needed:
            default_modality = st.session_state.get("tk_check_modality_pick", "") if action == "update_option" else ""
            label = "Modality Code to update" if action == "update_option" else "Unique Modality Code"
            modality_code_in = st.text_input(label, value=default_modality or "", placeholder="e.g. Standard 7 Days", key="tk_modality_code")
            if any(c in (modality_code_in or "") for c in ["/", "\\", "+", "-"]):
                st.error("🚫 The Modality Code cannot contain '/', '\\\\', '+', or '-' - it becomes part of a URL. Use spaces instead.")
        if "on_request" in needed:
            on_request_in = st.checkbox("On Request", value=False, key="tk_on_request")
        if "release_days" in needed:
            release_days_in = st.number_input(
                "Release Day (days before departure this ticket becomes bookable)",
                min_value=0, value=30, key="tk_release_days"
            )

        required_ok = True
        if "ticket_code" in needed and not (ticket_code_in or "").strip():
            required_ok = False
        if "currency" in needed and not (currency_in or "").strip():
            required_ok = False
        if "modality_code" in needed and not (modality_code_in or "").strip():
            required_ok = False
        if "modality_code" in needed and any(c in (modality_code_in or "") for c in ["/", "\\", "+", "-"]):
            required_ok = False
        if "existing_ticket_code" in needed and not existing_ticket_code_in:
            required_ok = False
        if action in ("add_option", "update_ticket") and not st.session_state.get("tk_fetched_currency"):
            required_ok = False
            st.info("Click 'Check what's already online for this code' above first - this fetches the "
                   "existing Currency so you don't have to re-enter it.")

        if st.button("➡️ Continue to Step 4", type="primary", disabled=not required_ok, key="tk_continue2"):
            if action in ("add_option", "update_ticket"):
                currency_in = st.session_state.get("tk_fetched_currency") or ""
            st.session_state.tk_cfg_ticket_code = ticket_code_in or ""
            st.session_state.tk_cfg_min_passengers = min_pass_in or 1
            st.session_state.tk_cfg_max_passengers = max_pass_in or 9
            st.session_state.tk_cfg_currency = currency_in or ""
            st.session_state.tk_cfg_modality_code = modality_code_in or ""
            st.session_state.tk_cfg_on_request = on_request_in
            st.session_state.tk_cfg_release_days = release_days_in
            st.session_state.tk_cfg_existing_ticket_code = existing_ticket_code_in or ""
            st.session_state.tk_step2_confirmed = True
            st.rerun()
        return

    # From here: everything reads from confirmed tk_cfg_* values.
    supplier_id = st.session_state.tk_cfg_supplier_id
    ticket_code = st.session_state.tk_cfg_ticket_code
    min_passengers = st.session_state.tk_cfg_min_passengers
    max_passengers = st.session_state.tk_cfg_max_passengers
    currency = st.session_state.tk_cfg_currency
    modality_code = st.session_state.tk_cfg_modality_code
    on_request = st.session_state.tk_cfg_on_request
    release_days = st.session_state.tk_cfg_release_days
    existing_ticket_code = st.session_state.tk_cfg_existing_ticket_code

    _tk_action_to_publish_label = {
        "create": "Create a brand-new ticket (+ first option)",
        "add_option": "Add a new option to an existing ticket",
        "update_ticket": "Update an existing ticket's details",
        "update_option": "Update an existing ticket option",
    }
    publish_action = _tk_action_to_publish_label[action]
    tk_is_option_only = action in ("add_option", "update_option")

    # ------------------------------------------------------------------
    # TICKET STEP 4: Input Source
    # ------------------------------------------------------------------
    st.header("Ticket — Step 4: Input Source")
    tk_url = st.text_input("Product page URL (optional)", key="tk_url")
    tk_files = st.file_uploader("Upload document(s) (optional)", type=["pdf", "docx", "xlsx"],
                                accept_multiple_files=True, key="tk_files")
    tk_hint = st.text_input("Extraction hint (optional)", key="tk_hint")

    if st.button("🔎 Extract", disabled=not (tk_url or tk_files), key="tk_extract_btn"):
        with st.spinner("Gathering content..."):
            try:
                combined_parts = []
                doc_raw_images = []
                doc_image_urls = []
                if tk_url:
                    combined_parts.append(f"--- SOURCE: WEB PAGE ({tk_url}) ---\n{get_page_text(tk_url)}")
                for uploaded in (tk_files or []):
                    suffix = os.path.splitext(uploaded.name)[1]
                    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                        tmp.write(uploaded.getbuffer())
                        tmp_path = tmp.name
                    combined_parts.append(f"--- SOURCE: UPLOADED DOCUMENT ({uploaded.name}) ---\n{extract_raw_text(tmp_path)}")
                    embedded_images = extract_images(tmp_path)
                    if embedded_images:
                        for i, (img_bytes, ext) in enumerate(embedded_images):
                            doc_raw_images.append((f"{os.path.splitext(uploaded.name)[0]}_img{i+1}.{ext or 'jpg'}", img_bytes))
                        try:
                            doc_image_urls.extend(upload_images_freeimage(embedded_images))
                        except Exception:
                            pass
                    os.remove(tmp_path)

                raw_text = "\n\n".join(combined_parts)

                if tk_is_option_only:
                    data = extract_ticket_option_only_data(raw_text, human_hint=tk_hint or None)
                    st.session_state.tk_extracted = data
                    st.session_state.tk_raw_preview = raw_text
                    st.session_state.tk_payloads = None
                    st.session_state.tk_doc_raw_images = doc_raw_images
                    st.success("Extraction complete. Review and edit below.")
                else:
                    excursions = detect_ticket_variants(raw_text)
                    if excursions:
                        st.session_state.tk_pending_variants = excursions
                        st.session_state.tk_pending_raw_text = raw_text
                        st.session_state.tk_pending_hint = tk_hint or None
                        st.session_state.tk_pending_url = tk_url or None
                        st.session_state.tk_pending_doc_images = doc_image_urls
                        st.session_state.tk_pending_doc_raw_images = doc_raw_images
                    else:
                        data = extract_ticket_data(raw_text, human_hint=tk_hint or None)
                        data["image_urls"] = (get_page_images(tk_url) if tk_url else []) + doc_image_urls
                        st.session_state.tk_extracted = data
                        st.session_state.tk_raw_preview = raw_text
                        st.session_state.tk_payloads = None
                        st.session_state.tk_doc_raw_images = doc_raw_images
                        st.success("Extraction complete. Review and edit below.")
            except Exception as e:
                st.error(f"Extraction failed: {e}")

    if st.session_state.get("tk_pending_variants"):
        excursions = st.session_state.tk_pending_variants
        st.warning(f"⚠️ This content describes {len(excursions)} distinct excursions — which one do you want to add?")
        labels = [e.get("label", f"Excursion {i+1}") for i, e in enumerate(excursions)]
        tk_chosen_idx = st.radio("Pick one:", range(len(labels)), format_func=lambda i: labels[i], key="tk_variant_radio")

        if st.button("✅ Confirm and Extract Full Details", key="tk_confirm_variant"):
            with st.spinner("Extracting full details for the selected excursion..."):
                try:
                    chosen_label = excursions[tk_chosen_idx].get("label", "")
                    data = extract_ticket_data(
                        st.session_state.tk_pending_raw_text, variant_hint=chosen_label,
                        human_hint=st.session_state.get("tk_pending_hint")
                    )
                    tk_pending_url = st.session_state.get("tk_pending_url")
                    data["image_urls"] = (get_page_images(tk_pending_url) if tk_pending_url else []) + st.session_state.get("tk_pending_doc_images", [])

                    st.session_state.tk_extracted = data
                    st.session_state.tk_raw_preview = f"(Extracted excursion: {chosen_label})\n\n{st.session_state.tk_pending_raw_text}"
                    st.session_state.tk_payloads = None
                    st.session_state.tk_doc_raw_images = st.session_state.get("tk_pending_doc_raw_images", [])
                    st.session_state.tk_pending_variants = None
                    st.session_state.tk_pending_raw_text = None
                    st.rerun()
                except Exception as e:
                    st.error(f"Extraction failed: {e}")

    # ------------------------------------------------------------------
    # TICKET STEP 5: Review & Edit
    # ------------------------------------------------------------------
    if st.session_state.get("tk_extracted"):
        data = st.session_state.tk_extracted
        st.header("Ticket — Step 5: Review & Edit")

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Original Source")
            st.text_area("Raw content", st.session_state.tk_raw_preview, height=500, disabled=True, key="tk_raw_display")

        with col2:
            if tk_is_option_only:
                st.subheader("Only pricing/schedule needed for this action")
                st.caption("Ticket details (name, description, city, meeting points) are skipped - "
                          "they belong to the existing ticket and aren't touched here.")
            else:
                st.subheader("Extracted Data (click ✏️ to edit)")
                editable_field("Ticket name", data, "ticket_name", widget="text_input")
                editable_field("Description", data, "description", widget="text_area", height=150)
                editable_field("City", data, "city", widget="text_input")
                editable_field("Duration (hours)", data, "duration", widget="number_input")

                inc_df = pd.DataFrame([{"Item": x} for x in data.get("includes", [])]) if data.get("includes") else pd.DataFrame(columns=["Item"])
                def _save_tk_includes(edf, data=data):
                    data["includes"] = [str(r["Item"]).strip() for _, r in edf.iterrows() if str(r.get("Item", "")).strip()]
                editable_table("Includes", inc_df, "tk_includes", on_save=_save_tk_includes)

                exc_df = pd.DataFrame([{"Item": x} for x in data.get("excludes", [])]) if data.get("excludes") else pd.DataFrame(columns=["Item"])
                def _save_tk_excludes(edf, data=data):
                    data["excludes"] = [str(r["Item"]).strip() for _, r in edf.iterrows() if str(r.get("Item", "")).strip()]
                editable_table("Excludes", exc_df, "tk_excludes", on_save=_save_tk_excludes)

                mp_default = [{"Description": m.get("description", "")} for m in data.get("meeting_points", [])] or [{"Description": "Hotel Lobby"}]
                mp_df = pd.DataFrame(mp_default)
                def _save_tk_mp(edf, data=data):
                    data["meeting_points"] = [
                        {"description": str(r["Description"]).strip(),
                         "variable_location": str(r["Description"]).strip().lower() == "hotel lobby"}
                        for _, r in edf.iterrows() if str(r.get("Description", "")).strip()
                    ]
                editable_table("Meeting Points", mp_df, "tk_meeting_points", on_save=_save_tk_mp)

                images_text = st.text_area("Image URLs (one per line)", "\n".join(data.get("image_urls", [])), key="tk_images_text")
                data["image_urls"] = [u.strip() for u in images_text.split("\n") if u.strip()] or [FALLBACK_IMAGE]

                if st.session_state.get("tk_doc_raw_images"):
                    with st.expander(f"📥 Download images found ({len(st.session_state.tk_doc_raw_images)})"):
                        for fname, img_bytes in st.session_state.tk_doc_raw_images:
                            st.download_button("⬇️ " + fname, data=img_bytes, file_name=fname, key=f"tk_dl_{fname}")

        st.subheader("🤖 Tell AI what to fix or clarify (optional)")
        tk_clarify_q = st.text_input("Your message", key="tk_clarify_input")
        if st.button("Send", disabled=not tk_clarify_q.strip(), key="tk_clarify_send"):
            with st.spinner("Thinking..."):
                result = apply_clarification(st.session_state.tk_raw_preview, data, tk_clarify_q)
                st.session_state.tk_clarify_result = result
                if result.get("changes"):
                    for field_name, new_value in result["changes"].items():
                        data[field_name] = new_value
                st.rerun()
        if st.session_state.get("tk_clarify_result"):
            r = st.session_state.tk_clarify_result
            st.info(r.get("summary", ""))
            if r.get("changes"):
                st.caption(f"✅ Applied changes to: {', '.join(r['changes'].keys())}")

        st.markdown("**Start Time(s)**")
        st.caption("A Ticket can have multiple valid start times (e.g. a 09:00 and a 14:00 departure). "
                  "If the document doesn't state one, please add at least one manually.")
        tt_df = pd.DataFrame([{"Time (HH:MM)": t} for t in data.get("time_tables", [])]) if data.get("time_tables") else pd.DataFrame(columns=["Time (HH:MM)"])
        def _save_tk_timetables(edf, data=data):
            data["time_tables"] = [str(r["Time (HH:MM)"]).strip() for _, r in edf.iterrows() if str(r.get("Time (HH:MM)", "")).strip()]
        editable_table("Start Time(s)", tt_df, "tk_timetables", on_save=_save_tk_timetables)
        if not data.get("time_tables"):
            st.warning("⚠️ No start time set for this Ticket yet - add at least one above before publishing.")

        st.subheader("Departure Schedule")
        if data.get("schedule_notes"):
            st.info(f"🔎 {data['schedule_notes']}")
        data["operational_days"] = st.multiselect("Operational Days", ALL_WEEKDAYS,
                                                   default=data.get("operational_days", ALL_WEEKDAYS), key="tk_op_days")
        with st.expander("Stop Sales"):
            ss_json = st.text_area("stopSales (JSON array)", json.dumps(data.get("stop_sales", []), indent=2), key="tk_stop_sales")
            try:
                data["stop_sales"] = json.loads(ss_json)
            except json.JSONDecodeError as e:
                st.error(f"stopSales isn't valid JSON: {e}")

        num_days = len(data.get("operational_days", []))
        num_stops = len(data.get("stop_sales", []))
        if num_days == 0:
            sched_label, sched_bg, sched_fg = "⚠️ No Operational Days selected", "#f8d7da", "#721c24"
        elif num_days == 7 and num_stops == 0:
            sched_label, sched_bg, sched_fg = "🟢 DAILY departure - runs every day", "#d4edda", "#155724"
        elif num_stops > 0:
            sched_label, sched_bg, sched_fg = (
                f"🟠 SPECIFIC DATE departure - {num_days} weekday(s) minus {num_stops} blocked range(s)",
                "#fff3cd", "#856404"
            )
        else:
            sched_label, sched_bg, sched_fg = (
                f"🔵 WEEKLY departure - runs every {', '.join(data.get('operational_days', []))}",
                "#d1ecf1", "#0c5460"
            )
        st.markdown(
            f"<div style='background-color:{sched_bg}; color:{sched_fg}; padding:10px 14px; "
            f"border-radius:4px; font-weight:bold; margin-bottom:10px;'>{sched_label}</div>",
            unsafe_allow_html=True
        )

        st.subheader(f"Pricing (per passenger type, in {currency or '(set Currency in Step 3)'})")
        st.caption("A Ticket Modality holds ONE price + ONE validity date range (not a seasonal table). "
                  "For holiday/seasonal price differences, use dated Supplements below instead.")
        pcol1, pcol2, pcol3 = st.columns(3)
        with pcol1:
            data["base_adult_price"] = st.number_input("Adult Price", min_value=0.0,
                                                        value=float(data.get("base_adult_price", 0) or 0), key="tk_adult_price")
        with pcol2:
            data["base_children_price"] = st.number_input("Child Price", min_value=0.0,
                                                           value=float(data.get("base_children_price", 0) or 0), key="tk_child_price")
        with pcol3:
            data["base_infant_price"] = st.number_input("Infant Price", min_value=0.0,
                                                         value=float(data.get("base_infant_price", 0) or 0), key="tk_infant_price")
        dcol1, dcol2 = st.columns(2)
        with dcol1:
            data["start_date"] = st.text_input("Valid From (YYYY-MM-DD)", value=data.get("start_date", ""), key="tk_start_date")
        with dcol2:
            data["end_date"] = st.text_input("Valid Until (YYYY-MM-DD)", value=data.get("end_date", ""), key="tk_end_date")
        if data.get("pricing_notes"):
            st.warning(f"⚠️ {data['pricing_notes']}")

        st.subheader("Optional Add-ons (Supplements)")
        supp_rows = [
            {"Name": s.get("name", ""), "Adult": s.get("adult_price", 0), "Child": s.get("children_price", 0),
             "Infant": s.get("infant_price", 0), "Start": s.get("travel_start_date", ""), "End": s.get("travel_end_date", "")}
            for s in data.get("supplements", [])
        ]
        supp_df = pd.DataFrame(supp_rows) if supp_rows else pd.DataFrame(columns=["Name", "Adult", "Child", "Infant", "Start", "End"])
        def _save_tk_supplements(edf, data=data):
            new_supp = []
            for _, r in edf.iterrows():
                name = str(r.get("Name", "")).strip()
                if not name:
                    continue
                new_supp.append({
                    "name": name, "adult_price": float(r.get("Adult", 0) or 0),
                    "children_price": float(r.get("Child", 0) or 0), "infant_price": float(r.get("Infant", 0) or 0),
                    "travel_start_date": str(r.get("Start", "")).strip(), "travel_end_date": str(r.get("End", "")).strip(),
                })
            data["supplements"] = new_supp
        editable_table("Supplements", supp_df, "tk_supplements", on_save=_save_tk_supplements)

        price_valid = any([data.get("base_adult_price", 0), data.get("base_children_price", 0), data.get("base_infant_price", 0)])
        if not price_valid:
            st.error("Add at least one non-zero price (Adult/Child/Infant) before continuing.")

        time_valid = bool(data.get("time_tables"))
        can_build = price_valid and time_valid

        if st.button("🔎 Resolve Geolocation & Build Payload", disabled=not can_build, key="tk_build_payload"):
            pre_config = TicketHumanPreConfig(
                supplier_id=supplier_id, ticket_code=ticket_code or existing_ticket_code or "XXX",
                currency=currency, modality_code=modality_code, on_request=on_request,
                days_available_before_release=release_days, min_passengers=min_passengers, max_passengers=max_passengers
            )
            with st.spinner("Resolving geolocation..."):
                st.session_state.tk_payloads = build_ticket_payloads(pre_config, data, client)

        # ------------------------------------------------------------------
        # TICKET STEP 6: Geolocation & Payload Preview
        # ------------------------------------------------------------------
        if st.session_state.get("tk_payloads"):
            payloads = st.session_state.tk_payloads
            st.header("Ticket — Step 6: Geolocation & Payload Preview")

            if payloads["geolocation_resolved"]:
                st.markdown(
                    f"<div style='background-color:#d4edda; color:#155724; padding:6px 12px; "
                    f"border-radius:4px;'>✅ Geolocation resolved (source: {payloads['geolocation_source']})</div>",
                    unsafe_allow_html=True
                )
            else:
                st.markdown(
                    "<div style='background-color:#f8d7da; color:#721c24; padding:6px 12px; "
                    "border-radius:4px;'>❌ Geolocation NOT resolved - the City name may not match a known "
                    "destination. Check/adjust the City field above and rebuild.</div>",
                    unsafe_allow_html=True
                )

            with st.expander("🔧 Main Ticket Payload", expanded=False):
                if payloads["main_ticket_error"]:
                    st.error(f"Invalid: {payloads['main_ticket_error']}")
                else:
                    st.json(payloads["main_ticket_payload"])
            with st.expander("🔧 Ticket Option Payload", expanded=False):
                if payloads["ticket_option_error"]:
                    st.error(f"Invalid: {payloads['ticket_option_error']}")
                else:
                    st.json(payloads["ticket_option_payload"])

            # ------------------------------------------------------------------
            # TICKET STEP 7: Publish
            # ------------------------------------------------------------------
            st.header("Ticket — Step 7: Publish")
            creating_new = publish_action == "Create a brand-new ticket (+ first option)"
            target_ticket_code = payloads["main_ticket_code"] if creating_new else existing_ticket_code
            can_publish = not payloads["main_ticket_error"] and not payloads["ticket_option_error"]

            action_descriptions = {
                "Create a brand-new ticket (+ first option)": "Will POST a new ticket, then POST a new option.",
                "Add a new option to an existing ticket": f"Will POST a new option under existing ticket `{target_ticket_code}`.",
                "Update an existing ticket's details": f"Will PUT (update) ticket `{target_ticket_code}`'s details.",
                "Update an existing ticket option": f"Will PUT (update) the option under ticket `{target_ticket_code}`.",
            }
            st.caption(action_descriptions[publish_action])

            if st.button("🚀 Publish to Travel Compositor", disabled=not can_publish, type="primary", key="tk_publish_btn"):
                with st.spinner("Publishing..."):
                    if publish_action == "Create a brand-new ticket (+ first option)":
                        creation_payload = dict(payloads["main_ticket_payload"])
                        creation_payload["active"] = True
                        result = client.create_ticket(supplier_id, creation_payload)
                        if "error" in result:
                            st.error(f"❌ Ticket creation failed: {result}")
                        else:
                            real_code = result.get("code", payloads["main_ticket_code"])
                            st.success(f"✅ Ticket created (active) with real Code: **{real_code}** — save this exact value.")

                            option_result = None
                            for attempt in range(6):
                                option_result = client.create_ticket_option(supplier_id, real_code, payloads["ticket_option_payload"])
                                if "error" not in option_result:
                                    break
                                time.sleep(2)

                            if "error" in option_result:
                                st.error(f"❌ Ticket option creation failed after 6 attempts: {option_result}\n\n"
                                        f"💡 Note: adjustments to a Ticket require it to be ACTIVE - inactive "
                                        f"tickets aren't visible via the API.")
                            else:
                                st.success("✅ Ticket option created.")
                                deactivate_payload = dict(creation_payload)
                                deactivate_payload["active"] = False
                                deactivate_payload["code"] = real_code
                                deactivate_result = client.update_ticket(supplier_id, deactivate_payload)
                                if "error" in deactivate_result:
                                    st.warning(f"⚠️ Ticket and option created successfully, but switching back "
                                              f"to inactive/draft failed: {deactivate_result}.")
                                else:
                                    st.success(f"✅ Ticket `{real_code}` switched back to inactive/draft. "
                                              f"Ready for human review — activate it inside Travel Compositor when ready.")

                    elif publish_action == "Add a new option to an existing ticket":
                        result = client.create_ticket_option(supplier_id, target_ticket_code, payloads["ticket_option_payload"])
                        if "error" in result:
                            st.error(f"❌ Failed: {result}\n\n💡 Note: adjustments require the Ticket to be "
                                    f"ACTIVE - activate `{target_ticket_code}` inside Travel Compositor first.")
                        else:
                            st.success(f"✅ New option added to ticket `{target_ticket_code}`. Verify inside Travel Compositor.")

                    elif publish_action == "Update an existing ticket's details":
                        update_payload = dict(payloads["main_ticket_payload"])
                        update_payload["code"] = target_ticket_code
                        result = client.update_ticket(supplier_id, update_payload)
                        if "error" in result:
                            st.error(f"❌ Update failed: {result}\n\n💡 Note: adjustments require the Ticket "
                                    f"to be ACTIVE - activate `{target_ticket_code}` inside Travel Compositor first.")
                        else:
                            st.success(f"✅ Ticket `{target_ticket_code}` updated.")

                    elif publish_action == "Update an existing ticket option":
                        update_option_payload = dict(payloads["ticket_option_payload"])
                        update_option_payload["code"] = modality_code
                        result = client.update_ticket_option(supplier_id, target_ticket_code, update_option_payload)
                        if "error" in result:
                            st.error(f"❌ Option update failed: {result}\n\n💡 Note: adjustments require the "
                                    f"Ticket to be ACTIVE - activate `{target_ticket_code}` inside Travel Compositor first.")
                        else:
                            st.success(f"✅ Option `{modality_code}` under ticket `{target_ticket_code}` updated.")



st.set_page_config(page_title="Momira: DMC -> Travel Compositor", layout="wide")

_defaults = {
    "client": None, "extracted": None, "raw_preview": "", "payloads": None,
    "suppliers_cache": None, "step1_confirmed": False, "step2_confirmed": False,
    "cfg_action": None, "cfg_supplier_id": None, "cfg_provider_code": "",
    "cfg_min_pax": 1, "cfg_max_pax": 9, "cfg_currency": "", "cfg_modality_code": "",
    "cfg_on_request": True, "cfg_release_days": 30, "cfg_existing_tour_code": "",
}
for _k, _v in _defaults.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

if st.session_state.client is None:
    st.session_state.client = TravelCompositorAPI()
client = st.session_state.client

st.title("DMC → Travel Compositor: Closed Tour Draft Builder")
st.caption("Build version: 2026-07-28-self-test-fixes — bump this string whenever new code is shared, so it's always obvious whether a deploy actually took effect.")
st.caption("Every publish respects the confirmed active/inactive workflow. Human verification and final activation still happen inside Travel Compositor.")


# ----------------------------------------------------------------------
# STEP 0: Product Type - Ticket or ClosedTour?
# ----------------------------------------------------------------------
if "product_type" not in st.session_state:
    st.session_state.product_type = None

if st.session_state.product_type is not None:
    ptcol1, ptcol2 = st.columns([5, 1])
    with ptcol1:
        st.success(f"✅ Working on: **{st.session_state.product_type}**")
    with ptcol2:
        if st.button("🔄 Switch"):
            for key in list(st.session_state.keys()):
                if key not in ("client", "suppliers_cache"):
                    del st.session_state[key]
            st.session_state.product_type = None
            st.rerun()

if st.session_state.product_type is None:
    st.header("Step 1 — What do you want to work on?")
    pt_choice = st.radio("Choose one:", ["ClosedTour", "Ticket"], key="pt_choice_radio")
    st.caption("ClosedTour = multi-day tour (itinerary, room-occupancy pricing). "
              "Ticket = single-destination excursion/activity, no overnight, passenger-type pricing.")
    if st.button("➡️ Continue", type="primary"):
        st.session_state.product_type = pt_choice
        st.rerun()
    st.stop()

if st.session_state.product_type == "Ticket":
    render_ticket_flow(client)
    st.stop()


# ----------------------------------------------------------------------
# STEP 2: What do you want to do? + Supplier
# ----------------------------------------------------------------------


st.header("Step 2 — What do you want to do?")

if st.session_state.step1_confirmed:
    st.success(f"✅ Action: **{ACTION_LABELS[st.session_state.cfg_action]}** | "
               f"Supplier ID: **{st.session_state.cfg_supplier_id}**")
    if st.button("🔄 Change action / supplier"):
        st.session_state.step1_confirmed = False
        st.session_state.step2_confirmed = False
        st.rerun()
else:
    action_key = st.radio(
        "Choose one:",
        list(ACTION_LABELS.keys()),
        format_func=lambda k: ACTION_LABELS[k],
        help="Travel Compositor uses POST for creating new things and PUT for updating existing ones."
    )

    if st.session_state.suppliers_cache is None:
        with st.spinner("Loading supplier list from Travel Compositor..."):
            st.session_state.suppliers_cache = client.get_all_suppliers()

    supplier_id_choice = None
    if st.session_state.suppliers_cache:
        supplier_options = {
            f"{s.get('commercialName') or s.get('legalName') or '(unnamed)'} — ID {s.get('id')}": s.get("id")
            for s in st.session_state.suppliers_cache
        }
        selected_label = st.selectbox("Supplier (select by name)", list(supplier_options.keys()))
        supplier_id_choice = str(supplier_options[selected_label])
        if st.button("🔄 Refresh supplier list"):
            st.session_state.suppliers_cache = None
            st.rerun()
    else:
        st.error("Could not load the supplier list from Travel Compositor.")
        if st.button("🔄 Try again"):
            st.rerun()
        with st.expander("⚠️ Emergency manual entry (only if the list keeps failing to load)"):
            supplier_id_choice = st.text_input("Supplier ID (numeric)", value="")

    if st.button("➡️ Continue to Step 3", type="primary", disabled=not supplier_id_choice):
        st.session_state.cfg_action = action_key
        st.session_state.cfg_supplier_id = supplier_id_choice
        st.session_state.step1_confirmed = True
        st.rerun()

    st.stop()


# ----------------------------------------------------------------------
# STEP 3: Action-specific details
# ----------------------------------------------------------------------
st.header("Step 3 — Details for this action")
action = st.session_state.cfg_action
needed = ACTION_FIELDS[action]
supplier_id = st.session_state.cfg_supplier_id

if st.session_state.step2_confirmed:
    st.success("✅ Step 3 details confirmed.")
    if st.button("🔄 Change details"):
        st.session_state.step2_confirmed = False
        st.rerun()
else:
    provider_code_in = min_pax_in = max_pax_in = currency_in = modality_code_in = existing_tour_code_in = None
    on_request_in = True
    release_days_in = 30

    if "existing_tour_code" in needed:
        prefill = st.session_state.pop("prefill_existing_tour_code", "")
        existing_tour_code_in = st.text_input(
            "Existing Tour Code",
            value=prefill,
            placeholder="e.g. BKK-1 (your own ClosedTour/Provider Code) or CLOSEDTOUR-411099",
        ).strip()
        st.caption(
            "Try your own ClosedTour/Provider Code first (e.g. 'BKK-1') - the app will "
            "automatically also try the internal 'CLOSEDTOUR-XXXXX' format as a fallback "
            "if the first attempt doesn't work."
        )

        if st.button("🔍 Check what's already online for this code", disabled=not existing_tour_code_in):
            with st.spinner("Fetching from Travel Compositor..."):
                fetched, working_code = try_code_variants(
                    lambda c: client.get_closed_tour(supplier_id, c), existing_tour_code_in
                )
                st.session_state.fetched_tour = fetched
                st.session_state.fetched_option = None
                st.session_state.working_tour_code = working_code
                if isinstance(fetched, dict) and "error" not in fetched:
                    st.session_state.fetched_tour_provider_code = fetched.get("providerCode", "")
                    st.session_state.fetched_tour_min_pax = fetched.get("minPax")
                    st.session_state.fetched_tour_max_pax = fetched.get("maxPax")
                    st.session_state.fetched_tour_currency = fetched.get("currency")

        if st.session_state.get("fetched_tour"):
            t = st.session_state.fetched_tour
            if "error" in t:
                st.error(f"Not found or error: {t.get('message', t)}")
            else:
                working_code = st.session_state.get("working_tour_code") or existing_tour_code_in
                st.success(f"Found: **{t.get('name', '(no name)')}** (using code `{working_code}`)")
                if action == "update_tour":
                    st.caption(f"Will reuse from this tour: Min Pax **{t.get('minPax')}**, "
                              f"Max Pax **{t.get('maxPax')}**, Currency **{t.get('currency')}**, "
                              f"ClosedTour Code **{t.get('providerCode')}**.")
                existing_modalities = t.get("modalityCodes", [])
                st.write(f"Existing modality codes: {existing_modalities if existing_modalities else '(none)'}")
                if existing_modalities and "modality_code" in needed:
                    check_modality = st.selectbox("Check pricing for modality:", existing_modalities, key="check_modality_pick")
                    if st.button("🔍 Fetch this modality's live pricing"):
                        with st.spinner("Fetching option..."):
                            st.session_state.fetched_option = client.get_closed_tour_option(
                                supplier_id, working_code, check_modality
                            )
                    if st.session_state.get("fetched_option"):
                        opt = st.session_state.fetched_option
                        if "error" in opt:
                            st.error(f"Could not fetch option: {opt.get('message', opt)}")
                        else:
                            with st.expander("Live pricing for this modality", expanded=True):
                                for row in opt.get("priceList", []):
                                    label = row.get("name") or ""
                                    st.write(f"**{row.get('startDate')} → {row.get('endDate')}** {label}")
                                    st.json(row.get("price", {}))

    if "provider_code" in needed:
        provider_code_in = st.text_input("ClosedTour Code", value="", placeholder="e.g. ASW-1")
    if "min_pax" in needed:
        min_pax_in = st.selectbox("Min Pax", [1, 2])
    if "max_pax" in needed:
        max_pax_in = st.selectbox("Max Pax", list(range(2, 10)), index=7)
    if "currency" in needed:
        currency_in = st.text_input("Currency", value="", placeholder="e.g. EUR")
    if "modality_code" in needed:
        default_modality = st.session_state.get("check_modality_pick", "") if action == "update_option" else ""
        label = "Modality Code to update" if action == "update_option" else "Unique Modality Code"
        modality_code_in = st.text_input(label, value=default_modality or "", placeholder="e.g. Standard Cruise")
        if any(c in (modality_code_in or "") for c in ["/", "\\", "+", "-"]):
            st.error("🚫 The Modality Code cannot contain '/', '\\\\', '+', or '-' - it becomes part of a URL, "
                    "and these characters can break lookups (confirmed: a slash already caused a real HTTP "
                    "400 error). Use spaces instead, e.g. 'Standard Cruise'.")
    if "on_request" in needed:
        on_request_in = st.checkbox("On Request", value=True)
    if "release_days" in needed:
        release_days_in = st.number_input(
            "Release Day (days before departure this tour becomes bookable)",
            min_value=0, value=30,
            help="Default 30 days before departure."
        )

    required_ok = True
    if "provider_code" in needed and not provider_code_in.strip():
        required_ok = False
    if "currency" in needed and not (currency_in or "").strip():
        required_ok = False
    if "modality_code" in needed and not (modality_code_in or "").strip():
        required_ok = False
    if "modality_code" in needed and ("/" in (modality_code_in or "") or "\\" in (modality_code_in or "") or "+" in (modality_code_in or "") or "-" in (modality_code_in or "")):
        required_ok = False
    if "existing_tour_code" in needed and not existing_tour_code_in:
        required_ok = False
    if action in ("update_tour", "add_option") and not st.session_state.get("fetched_tour_provider_code"):
        required_ok = False
        st.info("Click 'Check what's already online for this code' above first - this fetches the "
               "existing tour's Currency (and for updates, Min/Max Pax too) so you don't have to re-enter them.")

    if st.button("➡️ Continue to Step 4", type="primary", disabled=not required_ok):
        if action == "update_tour":
            min_pax_in = st.session_state.get("fetched_tour_min_pax") or 1
            max_pax_in = st.session_state.get("fetched_tour_max_pax") or 9
            currency_in = st.session_state.get("fetched_tour_currency") or ""
        elif action == "add_option":
            currency_in = st.session_state.get("fetched_tour_currency") or ""
        st.session_state.cfg_provider_code = provider_code_in or ""
        st.session_state.cfg_min_pax = min_pax_in or 1
        st.session_state.cfg_max_pax = max_pax_in or 9
        st.session_state.cfg_currency = currency_in or ""
        st.session_state.cfg_modality_code = modality_code_in or ""
        st.session_state.cfg_on_request = on_request_in
        st.session_state.cfg_release_days = release_days_in
        st.session_state.cfg_existing_tour_code = existing_tour_code_in or ""
        st.session_state.step2_confirmed = True
        st.rerun()

    if not required_ok:
        st.info("Fill in all fields above to continue.")
    st.stop()


supplier_id = st.session_state.cfg_supplier_id
provider_code = st.session_state.cfg_provider_code
min_pax = st.session_state.cfg_min_pax
max_pax = st.session_state.cfg_max_pax
currency = st.session_state.cfg_currency
modality_code = st.session_state.cfg_modality_code
on_request = st.session_state.cfg_on_request
days_available_before_release = st.session_state.cfg_release_days
existing_tour_code = st.session_state.cfg_existing_tour_code

_action_to_publish_label = {
    "create": "Create a brand-new tour (+ first option)",
    "add_option": "Add a new option to an existing tour",
    "update_tour": "Update an existing tour's details",
    "update_option": "Update an existing option",
}
publish_action = _action_to_publish_label[action]


# ----------------------------------------------------------------------
# STEP 4: Input source
# ----------------------------------------------------------------------
st.header("Step 4 — Input Source")
st.caption("Provide a URL, a document, or both. If you give both, information from each will be "
           "combined into one extraction (e.g. itinerary from a web page + hotel detail from a document).")

url = st.text_input("Product page URL (optional)")
uploaded_files = st.file_uploader(
    "Upload DMC document(s) (optional, multiple allowed)",
    type=["pdf", "docx", "xlsx"], accept_multiple_files=True
)
extraction_hint = st.text_input(
    "Extraction hint (optional)",
    placeholder="e.g. 'Use the German-language pricing table' or 'Focus on the Superior room category'",
    help="Short, specific guidance for the AI if the source is ambiguous (e.g. multiple languages, "
         "multiple room categories). Leave blank for normal extraction."
)

is_option_only = action in ("add_option", "update_option")

multi_modality_mode = False
if action == "add_option":
    multi_modality_mode = st.checkbox(
        "📦 I'm adding MULTIPLE modalities from this same source",
        help="The app will detect distinct pricing categories (e.g. Standard/Deluxe cabin) from one "
             "shared document/URL, and let you review + publish each one individually, one at a time."
    )

if multi_modality_mode:
    render_multi_modality_flow(client, url=url, uploaded_files=uploaded_files)
    st.stop()

if st.button("🔎 Extract", disabled=not (url or uploaded_files)):
    spinner_msg = "Gathering pricing/schedule content..." if is_option_only else "Gathering content and checking for multiple tour variants..."
    with st.spinner(spinner_msg):
        try:
            combined_parts = []
            doc_names = []
            if url:
                combined_parts.append(f"--- SOURCE: WEB PAGE ({url}) ---\n{get_page_text(url)}")
            doc_image_urls = []
            doc_raw_images = []  # [(filename, bytes), ...] - always kept as a guaranteed fallback
            for uploaded in (uploaded_files or []):
                doc_names.append(uploaded.name)
                suffix = os.path.splitext(uploaded.name)[1]
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(uploaded.getbuffer())
                    tmp_path = tmp.name
                combined_parts.append(f"--- SOURCE: UPLOADED DOCUMENT ({uploaded.name}) ---\n{extract_raw_text(tmp_path)}")

                embedded_images = extract_images(tmp_path)
                if embedded_images:
                    for i, (img_bytes, ext) in enumerate(embedded_images):
                        doc_raw_images.append((f"{os.path.splitext(uploaded.name)[0]}_img{i+1}.{ext or 'jpg'}", img_bytes))
                    with st.spinner(f"Trying to auto-upload {len(embedded_images)} image(s) from {uploaded.name}..."):
                        try:
                            new_urls = upload_images_freeimage(embedded_images)
                            doc_image_urls.extend(new_urls)
                            if new_urls:
                                st.caption(f"✅ Auto-uploaded {len(new_urls)}/{len(embedded_images)} image(s) from {uploaded.name}.")
                            if len(new_urls) < len(embedded_images):
                                st.caption(f"ℹ️ {len(embedded_images) - len(new_urls)} image(s) will be available to download instead (see Step 5).")
                        except Exception as e:
                            st.caption(f"ℹ️ Auto-upload unavailable ({e}) - all {len(embedded_images)} image(s) from "
                                      f"{uploaded.name} will be available to download instead (see Step 5).")

                os.remove(tmp_path)

            raw_text = "\n\n".join(combined_parts)

            if is_option_only:
                # Lightweight path: no variant detection needed - we're adding
                # pricing/schedule to an ALREADY-KNOWN modality, not identifying
                # which tour variant this is.
                data = extract_option_only_data(raw_text, human_hint=extraction_hint or None)
                st.session_state.extracted = data
                sources_desc = " + ".join(filter(None, [url] + doc_names))
                st.session_state.raw_preview = f"Source(s): {sources_desc}\n\n{raw_text}"
                st.session_state.payloads = None
                st.session_state.doc_raw_images = doc_raw_images
                st.success("Pricing/schedule extraction complete. Review and edit below.")
            else:
                variants = detect_tour_variants(raw_text)

                if variants:
                    st.session_state.pending_variants = variants
                    st.session_state.pending_raw_text = raw_text
                    st.session_state.pending_url = url or None
                    st.session_state.pending_hint = extraction_hint or None
                    st.session_state.pending_doc_images = doc_image_urls
                    st.session_state.pending_doc_raw_images = doc_raw_images
                else:
                    data = extract_structured_data(raw_text, human_hint=extraction_hint or None)
                    data["image_urls"] = (get_page_images(url) if url else []) + doc_image_urls
                    st.session_state.extracted = data
                    st.session_state.images_text_value = "\n".join(data.get("image_urls", []))
                    sources_desc = " + ".join(filter(None, [url] + doc_names))
                    st.session_state.raw_preview = f"Source(s): {sources_desc}\n\n{raw_text}"
                    st.session_state.payloads = None
                    st.session_state.doc_raw_images = doc_raw_images
                    st.success("Extraction complete. Review and edit below.")
        except Exception as e:
            st.error(f"Extraction failed: {e}")

if st.session_state.get("pending_variants") and not is_option_only:
    variants = st.session_state.pending_variants
    st.warning(f"⚠️ This content describes {len(variants)} distinct tour variants — which one do you want to add?")
    labels = [f"{v.get('label', 'Variant ' + str(i+1))} ({v.get('nights', '?')} nights)" for i, v in enumerate(variants)]
    chosen_idx = st.radio("Pick one:", range(len(labels)), format_func=lambda i: labels[i])

    if st.button("✅ Confirm and Extract Full Details"):
        with st.spinner("Extracting full details for the selected variant..."):
            try:
                chosen_label = variants[chosen_idx].get("label", "")
                data = extract_structured_data(
                    st.session_state.pending_raw_text, variant_hint=chosen_label,
                    human_hint=st.session_state.get("pending_hint")
                )

                pending_url = st.session_state.get("pending_url")
                data["image_urls"] = (get_page_images(pending_url) if pending_url else []) + st.session_state.get("pending_doc_images", [])
                preview = f"(Extracted variant: {chosen_label})\n\n{st.session_state.pending_raw_text}"

                st.session_state.extracted = data
                st.session_state.images_text_value = "\n".join(data.get("image_urls", []))
                st.session_state.raw_preview = preview
                st.session_state.payloads = None
                st.session_state.doc_raw_images = st.session_state.get("pending_doc_raw_images", [])
                st.session_state.pending_variants = None
                st.session_state.pending_raw_text = None
                st.session_state.pending_url = None
                st.rerun()
            except Exception as e:
                st.error(f"Extraction failed: {e}")


# ----------------------------------------------------------------------
# STEP 5: Side-by-side review & edit
# ----------------------------------------------------------------------
if st.session_state.extracted:
    data = st.session_state.extracted

    st.header("Step 5 — Review & Edit")
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Original Source")
        st.text_area("Raw content (read-only reference)", st.session_state.raw_preview, height=600, disabled=True)

    with col2:
        if is_option_only:
            st.subheader("Only pricing/schedule are needed for this action")
            st.caption("Tour details (name, description, hotels, itinerary, supplements) are skipped "
                      "entirely - they belong to the existing tour and aren't touched by adding/updating "
                      "a Modality. Scroll down for Departure Schedule and Pricing.")
        else:
            st.subheader("Extracted Data (click ✏️ to edit each field)")
            DEFAULT_MEETING_POINT = ("Meet your guide in the airport arrival hall or, if you are already in the "
                                     "tour's starting city, in your hotel lobby.")
            if not data.get("meeting_point"):
                data["meeting_point"] = DEFAULT_MEETING_POINT

            editable_field("Tour name", data, "tour_name", widget="text_input")
            editable_field("Description (HTML ok)", data, "description", widget="text_area", height=200)
            editable_field("Hotels", data, "hotels_text", widget="text_area", height=140)
            editable_field("Included", data, "included", widget="text_area", height=120)
            editable_field("Excluded", data, "excluded", widget="text_area", height=120)
            editable_field("Meeting point", data, "meeting_point", widget="text_input")
            editable_field("Policy remarks", data, "policy_remarks", widget="text_area", height=100)
            editable_field("Nights", data, "nights", widget="number_input")

            tcol1, tcol2 = st.columns(2)
            with tcol1:
                data["start_time"] = st.text_input("Start Time (HH:MM, optional)", value=data.get("start_time", ""), key="ct_start_time")
            with tcol2:
                data["end_time"] = st.text_input("End Time (HH:MM, optional)", value=data.get("end_time", ""), key="ct_end_time")

            dest_rows = [{"#": i + 1, "Destination": d} for i, d in enumerate(data.get("itinerary_destinations", []))]
            dest_df = pd.DataFrame(dest_rows) if dest_rows else pd.DataFrame(columns=["#", "Destination"])

            def _save_destinations(edited_df):
                data["itinerary_destinations"] = [
                    str(row["Destination"]).strip() for _, row in edited_df.iterrows()
                    if str(row.get("Destination", "")).strip()
                ]

            editable_table(
                "Itinerary destinations (in visit order)", dest_df, "destinations",
                on_save=_save_destinations,
                column_config={"#": st.column_config.NumberColumn(disabled=True)}
            )

            if "images_text_value" not in st.session_state:
                st.session_state.images_text_value = "\n".join(data.get("image_urls", []))
            if st.session_state.get("_pending_images_update") is not None:
                st.session_state.images_text_value = st.session_state._pending_images_update
                st.session_state._pending_images_update = None

            images_text = st.text_area(
                "Image URLs (one per line - documents need these added manually)",
                key="images_text_value",
                height=80
            )
            data["image_urls"] = [u.strip() for u in images_text.split("\n") if u.strip()] or [FALLBACK_IMAGE]
            if data["image_urls"] == [FALLBACK_IMAGE]:
                st.caption(f"⚠️ No real images provided - using placeholder ({FALLBACK_IMAGE}).")

            if st.session_state.get("doc_raw_images"):
                with st.expander(f"📥 Download images found in your document(s) ({len(st.session_state.doc_raw_images)} found)"):
                    st.caption("These were extracted from your document. Download any you need, host them "
                              "wherever you normally do (or wherever auto-upload didn't already work), then "
                              "paste the resulting URL into the Image URLs box above.")
                    for fname, img_bytes in st.session_state.doc_raw_images:
                        dcol1, dcol2 = st.columns([1, 3])
                        with dcol1:
                            st.download_button("⬇️ Download", data=img_bytes, file_name=fname, key=f"dl_{fname}")
                        with dcol2:
                            st.caption(fname)

            with st.expander("🖼️ Or search free stock photos (Pexels)"):
                pexels_query = st.text_input(
                    "Search term", value=data.get("tour_name", "") or (data.get("itinerary_destinations", [""])[0])
                )
                if st.button("🔍 Search Pexels"):
                    with st.spinner("Searching Pexels..."):
                        try:
                            st.session_state.pexels_results = search_images(pexels_query)
                        except Exception as e:
                            st.session_state.pexels_results = None
                            st.error(str(e))

                if st.session_state.get("pexels_results"):
                    st.caption("Select images to add, then click 'Add selected below':")
                    pexels_cols = st.columns(3)
                    selected_pexels_urls = []
                    for i, photo in enumerate(st.session_state.pexels_results):
                        with pexels_cols[i % 3]:
                            st.image(photo["thumbnail"])
                            if st.checkbox(f"Use (by {photo['photographer']})", key=f"pexels_pick_{i}"):
                                selected_pexels_urls.append(photo["url"])

                    if st.button("➕ Add selected to Image URLs") and selected_pexels_urls:
                        current = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                        new_list = current + selected_pexels_urls
                        data["image_urls"] = new_list
                        st.session_state._pending_images_update = "\n".join(new_list)
                        st.rerun()

    st.subheader("Departure Schedule")
    if data.get("schedule_notes"):
        st.info(f"🔎 AI detected this note about departure timing in the source: \"{data['schedule_notes']}\" "
                f"— use this to help set Operational Days and Stop Sales below correctly. "
                f"This is NOT applied automatically - please verify and set the fields yourself.")

    data["operational_days"] = st.multiselect(
        "Operational Days (which weekdays this tour can depart on)",
        ALL_WEEKDAYS,
        default=data.get("operational_days", ALL_WEEKDAYS)
    )

    with st.expander("Stop Sales (block specific date ranges - e.g. for monthly-only or irregular departures)"):
        st.caption("For tours that ONLY depart on specific dates (e.g. once a month), set Operational Days above "
                   "to the relevant weekday, then add Stop Sales rows here to block every date EXCEPT the ones "
                   "you want to allow.")
        stop_sales_json = st.text_area(
            "stopSales (JSON array of {\"start\": \"YYYY-MM-DD\", \"end\": \"YYYY-MM-DD\"})",
            json.dumps(data.get("stop_sales", []), indent=2),
            height=100
        )
        try:
            data["stop_sales"] = json.loads(stop_sales_json)
        except json.JSONDecodeError as e:
            st.error(f"stopSales isn't valid JSON: {e}")

    # Clear at-a-glance summary of what kind of schedule this actually is.
    num_days = len(data.get("operational_days", []))
    num_stop_sales = len(data.get("stop_sales", []))
    if num_days == 0:
        schedule_summary = ("⚠️ No Operational Days selected", "#f8d7da", "#721c24")
    elif num_days == 7 and num_stop_sales == 0:
        schedule_summary = ("🟢 DAILY departure - runs every day", "#d4edda", "#155724")
    elif num_stop_sales > 0:
        schedule_summary = (
            f"🟠 SPECIFIC DATE departure - runs on {num_days} weekday(s) MINUS {num_stop_sales} "
            f"blocked date range(s) (irregular/custom schedule)", "#fff3cd", "#856404"
        )
    else:
        schedule_summary = (
            f"🔵 WEEKLY departure - runs every {', '.join(data.get('operational_days', []))}",
            "#d1ecf1", "#0c5460"
        )
    label, bg, fg = schedule_summary
    st.markdown(
        f"<div style='background-color:{bg}; color:{fg}; padding:10px 14px; border-radius:4px; "
        f"font-weight:bold; margin-bottom:10px;'>{label}</div>",
        unsafe_allow_html=True
    )

    st.subheader("Pricing (required by Travel Compositor to publish)")
    if data.get("pricing_notes"):
        st.warning(f"⚠️ **Pricing had to be approximated to fit the 4-slot Distribution schema:**\n\n"
                  f"{data['pricing_notes']}\n\n"
                  f"Review the priceList below carefully - some information may have been "
                  f"simplified or dropped.")

    default_price_list = sorted(
        data.get("price_list") or [{
            "name": "Example row - edit or delete",
            "startDate": "2027-01-01",
            "endDate": "2027-12-31",
            "price": {
                "singlePrice": {"amount": 0, "currency": currency},
                "doublePrice": {"amount": 0, "currency": currency}
            }
        }],
        key=lambda entry: entry.get("startDate", "")
    )
    data["price_list"] = default_price_list

    price_df_rows = []
    for entry in default_price_list:
        price = entry.get("price", {}) or {}
        def _amt(key):
            block = price.get(key)
            return block.get("amount") if isinstance(block, dict) else None
        price_df_rows.append({
            "Name": entry.get("name", ""),
            "Start Date": entry.get("startDate", ""),
            "End Date": entry.get("endDate", ""),
            "Single": _amt("singlePrice"),
            "Double": _amt("doublePrice"),
            "Triple": _amt("triplePrice"),
            "Quadruple": _amt("quadruplePrice"),
        })

    st.caption(f"Prices below are in **{currency or '(set Currency in Step 3)'}**. "
              f"Leave a price blank if that occupancy isn't offered. Add/remove rows freely.")
    def _row_to_price_entry(row):
        price = {}
        for col, key in [("Single", "singlePrice"), ("Double", "doublePrice"),
                         ("Triple", "triplePrice"), ("Quadruple", "quadruplePrice")]:
            val = row.get(col)
            if val is not None and not pd.isna(val):
                price[key] = {"amount": float(val), "currency": currency}
        entry = {
            "startDate": str(row.get("Start Date", "")).strip(),
            "endDate": str(row.get("End Date", "")).strip(),
            "price": price
        }
        name = str(row.get("Name", "")).strip()
        if name:
            entry["name"] = name
        return entry

    def _save_price_list(edited_df):
        data["price_list"] = sorted(
            [
                _row_to_price_entry(row) for _, row in edited_df.iterrows()
                if str(row.get("Start Date", "")).strip() and str(row.get("End Date", "")).strip()
            ],
            key=lambda entry: entry.get("startDate", "")
        )

    price_df = pd.DataFrame(price_df_rows)
    editable_table("Pricing table", price_df, "pricing", on_save=_save_price_list)

    price_list_valid = len(data["price_list"]) > 0
    if not price_list_valid:
        st.error("Add at least one price row with both a Start Date and End Date.")

    # Detect overlapping date ranges - Travel Compositor ADDS prices together
    # for any rows with overlapping dates within one option, silently inflating
    # the total. Catch this here regardless of whether it came from AI
    # extraction or a manual edit to the table.
    def _dates_overlap(a_start, a_end, b_start, b_end):
        return a_start <= b_end and b_start <= a_end

    overlaps_found = []
    for i in range(len(data["price_list"])):
        for j in range(i + 1, len(data["price_list"])):
            r1, r2 = data["price_list"][i], data["price_list"][j]
            if _dates_overlap(r1.get("startDate", ""), r1.get("endDate", ""), r2.get("startDate", ""), r2.get("endDate", "")):
                overlaps_found.append((i, j))

    if overlaps_found:
        price_list_valid = False
        st.error(
            f"🚫 **Overlapping date ranges detected in {len(overlaps_found)} row pair(s) of the pricing "
            f"table above.** Travel Compositor ADDS TOGETHER prices from rows with overlapping dates "
            f"within one Modality - this would silently create a wrong, inflated price. Each date range "
            f"in the table should be unique/non-overlapping. If you meant to set different prices for "
            f"different occupancy (single/double/triple/quadruple) in the SAME period, that all belongs "
            f"in ONE row, not separate rows."
        )

    with st.expander("🔧 Advanced: view raw priceList JSON (for reference/copying)"):
        st.json(data["price_list"])

    if not is_option_only:
        st.subheader("Optional Add-ons / Upgrades / Excursions (Supplements)")
        st.caption("TRUE optional extras the customer only pays for if they choose them - e.g. a hotel/room "
                  "upgrade, a meal upgrade, or an optional excursion day. Leave empty if this tour has none. "
                  "For a genuinely different core product (different cabin/route with its own full pricing), "
                  "use a separate Modality instead (Publish Action 2).")
        st.caption("Every row needs a clear Name. Special Travel Date is optional - only set it if this "
                  "supplement only applies during a specific date range (e.g. a seasonal excursion).")

        default_supplements = data.get("supplements") or []
        supp_df_rows = [
            {
                "Name": s.get("name", ""),
                "Price (per person)": s.get("price", 0),
                "Per Pax": s.get("per_pax", True),
                "Mandatory": s.get("mandatory", False),
                "On Request": s.get("on_request", False),
                "Special Travel Start Date": s.get("travel_start_date", ""),
                "Special Travel End Date": s.get("travel_end_date", ""),
            }
            for s in default_supplements
        ]
        supp_df = pd.DataFrame(supp_df_rows) if supp_df_rows else pd.DataFrame(
            columns=["Name", "Price (per person)", "Per Pax", "Mandatory", "On Request",
                     "Special Travel Start Date", "Special Travel End Date"]
        )

        def _save_supplements(edited_df):
            missing_name = False
            new_supplements = []
            for _, row in edited_df.iterrows():
                name = str(row.get("Name", "")).strip()
                price_given = row.get("Price (per person)", 0)
                has_any_data = name or (price_given not in (0, "", None))
                if not name and has_any_data:
                    missing_name = True
                    continue
                if not name:
                    continue
                new_supplements.append({
                    "name": name,
                    "price": float(price_given or 0),
                    "per_pax": bool(row.get("Per Pax", True)),
                    "mandatory": bool(row.get("Mandatory", False)),
                    "on_request": bool(row.get("On Request", False)),
                    "travel_start_date": str(row.get("Special Travel Start Date", "") or "").strip(),
                    "travel_end_date": str(row.get("Special Travel End Date", "") or "").strip(),
                })
            data["supplements"] = new_supplements
            st.session_state._supplements_missing_name = missing_name

        editable_table("Supplements", supp_df, "supplements", on_save=_save_supplements)
        if st.session_state.get("_supplements_missing_name"):
            st.warning("⚠️ A supplement row has a price but no Name - it was skipped. Every supplement needs a clear Name.")

    # ----------------------------------------------------------------------
    # STEP 6: Build payloads (destination resolution happens here)
    # ----------------------------------------------------------------------
    st.subheader("🤖 Tell AI what to fix or clarify (optional)")
    st.caption("Ask a question, or tell it to fix something (e.g. 'the end date of season 1 should be "
              "Sept 30, not Oct 10'). It applies real changes when you ask for them - always shows exactly "
              "what changed so you can double-check.")
    clarify_question = st.text_input("Your message", key="clarify_question_input",
                                     placeholder="e.g. 'Fix season 1's end date to Sept 30' or 'Does this include the Junior Suite?'")
    if st.button("Send", disabled=not clarify_question.strip()):
        with st.spinner("Thinking..."):
            result = apply_clarification(st.session_state.raw_preview, data, clarify_question)
            st.session_state.clarify_result = result
            if result.get("changes"):
                for field_name, new_value in result["changes"].items():
                    data[field_name] = new_value
            st.rerun()
    if st.session_state.get("clarify_result"):
        r = st.session_state.clarify_result
        st.info(r.get("summary", ""))
        if r.get("changes"):
            st.caption(f"✅ Applied changes to: {', '.join(r['changes'].keys())} - review above before continuing.")

    if st.button("🔎 Resolve Destinations & Build Payload",
                disabled=not price_list_valid):
        _real_provider_code = st.session_state.get("fetched_tour_provider_code", "")
        pre_config = HumanPreConfig(
            supplier_id=supplier_id,
            provider_code=provider_code or _real_provider_code or "XXX-1",
            min_pax=min_pax, max_pax=max_pax, currency=currency,
            modality_code=modality_code, on_request=on_request,
            days_available_before_release=days_available_before_release
        )
        with st.spinner("Resolving destinations against Travel Compositor..."):
            st.session_state.payloads = build_closed_tour_payloads(pre_config, data, client)
            st.session_state.pre_config = pre_config

    if st.session_state.payloads:
        payloads = st.session_state.payloads

        st.header("Step 6 — Destination Resolution & Payload Preview")

        st.subheader("Destination Check — verify these against Travel Compositor before publishing")
        for res in payloads["itinerary_resolution"]:
            if res["valid"]:
                st.markdown(
                    f"<div style='background-color:#d4edda; color:#155724; padding:6px 12px; "
                    f"border-radius:4px; margin-bottom:4px;'>✅ <b>{res['input']}</b> → "
                    f"<code>{res['destination']}</code> ({res.get('resolved_name', '')})</div>",
                    unsafe_allow_html=True
                )
            else:
                st.markdown(
                    f"<div style='background-color:#f8d7da; color:#721c24; padding:6px 12px; "
                    f"border-radius:4px; margin-bottom:4px;'>❌ <b>{res['input']}</b> → NOT FOUND "
                    f"in Travel Compositor</div>",
                    unsafe_allow_html=True
                )

        if payloads["unresolved_destinations"]:
            st.error(
                f"🚫 **{len(payloads['unresolved_destinations'])} destination(s) could NOT be matched "
                f"to a real Travel Compositor location:** {', '.join(payloads['unresolved_destinations'])}\n\n"
                f"This means Travel Compositor doesn't recognize this place by that name - publishing "
                f"would fail or create a wrong/broken itinerary stop. **To fix:** go back up to Step 5's "
                f"'Itinerary destinations' box and either correct the spelling/name, or replace it with "
                f"the exact name Travel Compositor uses, then click 'Resolve Destinations & Build Payload' again."
            )

        col3, col4 = st.columns(2)
        with col3:
            if publish_action == "Create a brand-new tour (+ first option)":
                title = "Main Tour Payload (POST - Call 1)"
            elif publish_action == "Update an existing tour's details":
                title = "Main Tour Payload (PUT - update)"
            else:
                title = "Main Tour Payload (not sent this time)"
            with st.expander(f"🔧 {title}", expanded=False):
                if publish_action not in ("Create a brand-new tour (+ first option)", "Update an existing tour's details"):
                    st.caption(f"Shown for reference only — '{publish_action}' doesn't touch the main tour.")
                st.json(payloads["main_tour_payload"])
        with col4:
            if publish_action in ("Create a brand-new tour (+ first option)", "Add a new option to an existing tour"):
                title = "Tour Option Payload (POST)"
            elif publish_action == "Update an existing option":
                title = "Tour Option Payload (PUT - update)"
            else:
                title = "Tour Option Payload (not sent this time)"
            if payloads["tour_option_error"]:
                st.error(f"Invalid: {payloads['tour_option_error']}")
            else:
                with st.expander(f"🔧 {title}", expanded=False):
                    st.json(payloads["tour_option_payload"])

        # ----------------------------------------------------------------------
        # STEP 7: Publish
        # ----------------------------------------------------------------------
        st.header("Step 7 — Publish")

        creating_new_tour = publish_action == "Create a brand-new tour (+ first option)"
        target_tour_code = payloads["main_tour_code"] if creating_new_tour else existing_tour_code
        missing_existing_code = not creating_new_tour and not existing_tour_code
        missing_provider_code_for_update = (
            publish_action == "Update an existing tour's details"
            and not st.session_state.get("fetched_tour_provider_code")
        )
        if missing_provider_code_for_update:
            st.warning("⚠️ Go back to Step 3 and click 'Check what's already online for this code' first — "
                      "without it, this update could overwrite the tour's real ClosedTour Code with a placeholder.")

        can_publish = (
            not payloads["unresolved_destinations"]
            and not payloads["tour_option_error"]
            and not missing_existing_code
            and not missing_provider_code_for_update
        )

        if missing_existing_code:
            st.info("Existing Tour Code is missing - go back to Step 3.")
        elif not can_publish:
            st.info("Resolve all destinations and fix pricing above before publishing.")

        action_descriptions = {
            "Create a brand-new tour (+ first option)": "Will POST a new tour, then POST a new option.",
            "Add a new option to an existing tour": f"Will POST a new option under existing tour `{target_tour_code}`. Main tour is untouched.",
            "Update an existing tour's details": f"Will PUT (update) tour `{target_tour_code}`'s details. No option changes.",
            "Update an existing option": f"Will PUT (update) the option under tour `{target_tour_code}`.",
        }
        st.caption(action_descriptions[publish_action])

        if st.button("🚀 Publish to Travel Compositor", disabled=not can_publish, type="primary"):
            with st.spinner("Sending to Travel Compositor..."):

                if publish_action == "Create a brand-new tour (+ first option)":
                    creation_payload = dict(payloads["main_tour_payload"])
                    creation_payload["active"] = True

                    result = client.create_closed_tour(payloads["supplier_id"], creation_payload)
                    if "error" in result:
                        st.error(f"❌ Main tour creation failed: {result}")
                    else:
                        real_code = result.get('code', payloads['main_tour_code'])
                        st.success(f"✅ Main tour created (active) with real Code: **{real_code}** "
                                  f"— save this exact value, you'll need it for any future lookups, "
                                  f"updates, or adding more modalities to this tour.")

                        # Try the human-chosen ClosedTour/Provider Code first (confirmed working
                        # via direct API test), falling back to the internal 'code' if that fails -
                        # we've seen conflicting evidence about which one Travel Compositor's
                        # lookup actually uses, so don't bet everything on just one.
                        option_result = None
                        used_code = None
                        for candidate_code in [provider_code, real_code]:
                            for attempt in range(3):
                                option_result = client.create_closed_tour_option(
                                    payloads["supplier_id"], candidate_code, payloads["tour_option_payload"]
                                )
                                if "error" not in option_result:
                                    used_code = candidate_code
                                    break
                                time.sleep(2)
                            if "error" not in option_result:
                                break

                        if "error" not in option_result:
                            st.caption(f"(Option succeeded using code: `{used_code}`)")

                        if "error" in option_result:
                            st.error(f"❌ Tour option creation failed after trying both "
                                    f"`{provider_code}` and `{real_code}`: "
                                    f"{option_result}\n\n"
                                    f"💡 Note: adjustments to a ClosedTour require it to be ACTIVE - "
                                    f"inactive tours aren't visible via the API. The tour was created with "
                                    f"active:true, but if this keeps failing, check inside Travel Compositor "
                                    f"whether `{real_code}` shows as active, and try 'Add a new option to "
                                    f"an existing tour' manually once confirmed.")
                        else:
                            st.success("✅ Tour option created.")
                            deactivate_payload = dict(creation_payload)
                            deactivate_payload["active"] = False
                            deactivate_payload["code"] = real_code
                            deactivate_result = client.update_closed_tour(payloads["supplier_id"], deactivate_payload)
                            if "error" in deactivate_result:
                                st.warning(f"⚠️ Tour and option were created successfully, but switching "
                                          f"the tour back to inactive/draft failed: {deactivate_result}. "
                                          f"You may need to deactivate it manually inside Travel Compositor.")
                            else:
                                st.success(f"✅ Tour `{real_code}` switched back to inactive/draft. "
                                          f"Ready for human review — activate it inside Travel Compositor when ready to go live.")
                                st.session_state.just_published_tour_code = real_code
                                st.session_state.just_published_supplier_id = payloads["supplier_id"]

                elif publish_action == "Add a new option to an existing tour":
                    option_result, used_code = try_code_variants(
                        lambda c: client.create_closed_tour_option(payloads["supplier_id"], c, payloads["tour_option_payload"]),
                        target_tour_code
                    )
                    if "error" in option_result:
                        st.error(f"❌ Tour option creation failed (tried both `{target_tour_code}` and its "
                                f"CLOSEDTOUR- variant): {option_result}\n\n"
                                f"💡 Note: adjustments to a ClosedTour require it to be ACTIVE - "
                                f"inactive tours aren't visible via the API. Activate `{target_tour_code}` "
                                f"inside Travel Compositor first, then retry (you can switch it back "
                                f"to inactive/draft afterward).")
                    else:
                        st.success(f"✅ New option added to existing tour using code `{used_code}`. Verify inside Travel Compositor.")
                        st.session_state.just_published_tour_code = target_tour_code
                        st.session_state.just_published_supplier_id = payloads["supplier_id"]

                elif publish_action == "Update an existing tour's details":
                    update_payload = dict(payloads["main_tour_payload"])
                    update_payload["code"] = target_tour_code
                    result, used_code = try_code_variants(
                        lambda c: client.update_closed_tour(payloads["supplier_id"], {**update_payload, "code": c}),
                        target_tour_code
                    )
                    if "error" in result:
                        st.error(f"❌ Tour update failed (tried both `{target_tour_code}` and its CLOSEDTOUR- "
                                f"variant): {result}\n\n"
                                f"💡 Note: adjustments to a ClosedTour require it to be ACTIVE - "
                                f"inactive tours aren't visible via the API. Activate `{target_tour_code}` "
                                f"inside Travel Compositor first, then retry.")
                    else:
                        st.success(f"✅ Tour updated using code `{used_code}`.")
                        st.session_state.just_published_tour_code = target_tour_code
                        st.session_state.just_published_supplier_id = payloads["supplier_id"]

                elif publish_action == "Update an existing option":
                    update_option_payload = dict(payloads["tour_option_payload"])
                    update_option_payload["code"] = modality_code
                    option_result, used_code = try_code_variants(
                        lambda c: client.update_closed_tour_option(payloads["supplier_id"], c, update_option_payload),
                        target_tour_code
                    )
                    if "error" in option_result:
                        st.error(f"❌ Option update failed (tried both `{target_tour_code}` and its CLOSEDTOUR- "
                                f"variant): {option_result}\n\n"
                                f"💡 Note: adjustments to a ClosedTour require it to be ACTIVE - "
                                f"inactive tours aren't visible via the API. Activate `{target_tour_code}` "
                                f"inside Travel Compositor first, then retry.")
                    else:
                        st.success(f"✅ Option `{modality_code}` under tour (code `{used_code}`) updated.")
                        st.session_state.just_published_tour_code = target_tour_code
                        st.session_state.just_published_supplier_id = payloads["supplier_id"]

# ----------------------------------------------------------------------
# Post-publish follow-up: what next?
# ----------------------------------------------------------------------
if st.session_state.get("just_published_tour_code"):
    st.divider()
    st.subheader("✅ ClosedTour published — what would you like to do next?")
    st.write(f"Just published: **{st.session_state.just_published_tour_code}** "
            f"(Supplier {st.session_state.just_published_supplier_id})")

    fcol1, fcol2 = st.columns(2)
    with fcol1:
        if st.button("🆕 Start a new import (different ClosedTour)", type="primary"):
            keep_client = st.session_state.client
            keep_suppliers = st.session_state.suppliers_cache
            st.session_state.clear()
            st.session_state.client = keep_client
            st.session_state.suppliers_cache = keep_suppliers
            st.rerun()
    with fcol2:
        if st.button("➕ Add another Modality to this same ClosedTour"):
            prefill_tour_code = st.session_state.just_published_tour_code
            prefill_supplier_id = st.session_state.just_published_supplier_id
            keep_client = st.session_state.client
            keep_suppliers = st.session_state.suppliers_cache
            st.session_state.clear()
            st.session_state.client = keep_client
            st.session_state.suppliers_cache = keep_suppliers
            st.session_state.cfg_action = "add_option"
            st.session_state.cfg_supplier_id = prefill_supplier_id
            st.session_state.cfg_existing_tour_code = prefill_tour_code
            st.session_state.prefill_existing_tour_code = prefill_tour_code
            st.session_state.step1_confirmed = True
            st.rerun()
