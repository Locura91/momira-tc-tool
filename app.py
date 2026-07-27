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
                 "TRAVELC_PASSWORD", "ANTHROPIC_API_KEY", "PEXELS_API_KEY", "IMGUR_CLIENT_ID"]:
        try:
            if _key in st.secrets and _key not in os.environ:
                os.environ[_key] = st.secrets[_key]
        except Exception:
            pass

from api_client import TravelCompositorAPI
from schemas import HumanPreConfig
from builder import build_closed_tour_payloads
from document_reader import extract_raw_text, extract_images
from ai_extractor import extract_structured_data, detect_tour_variants
from web_extractor import extract_from_url, get_page_text, get_page_images
from pexels_client import search_images
from imgur_client import upload_images

FALLBACK_IMAGE = "https://multiwander.com/wp-content/uploads/2026/07/Please-load-images.png"
ALL_WEEKDAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]

ACTION_LABELS = {
    "create": "1: Create new ClosedTour + 1 Modality",
    "add_option": "2: Add new Modality to existing ClosedTour",
    "update_tour": "3: Update existing ClosedTour",
    "update_option": "4: Update existing ClosedTour Modality",
}
ACTION_FIELDS = {
    "create": ["provider_code", "min_pax", "max_pax", "currency", "modality_code", "on_request", "release_days"],
    "add_option": ["existing_tour_code", "currency", "modality_code", "on_request"],
    "update_tour": ["existing_tour_code", "release_days"],
    "update_option": ["existing_tour_code", "currency", "modality_code", "on_request"],
}

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
st.caption("Build version: 2026-07-26-images-colors-schedule-followup — bump this string whenever new code is shared, so it's always obvious whether a deploy actually took effect.")
st.caption("Every publish respects the confirmed active/inactive workflow. Human verification and final activation still happen inside Travel Compositor.")


# ----------------------------------------------------------------------
# STEP 1: What do you want to do? + Supplier
# ----------------------------------------------------------------------


st.header("Step 1 — What do you want to do?")

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

    if st.button("➡️ Continue to Step 2", type="primary", disabled=not supplier_id_choice):
        st.session_state.cfg_action = action_key
        st.session_state.cfg_supplier_id = supplier_id_choice
        st.session_state.step1_confirmed = True
        st.rerun()

    st.stop()


# ----------------------------------------------------------------------
# STEP 2: Action-specific details
# ----------------------------------------------------------------------
st.header("Step 2 — Details for this action")
action = st.session_state.cfg_action
needed = ACTION_FIELDS[action]
supplier_id = st.session_state.cfg_supplier_id

if st.session_state.step2_confirmed:
    st.success("✅ Step 2 details confirmed.")
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
        modality_code_in = st.text_input(label, value=default_modality or "", placeholder="e.g. Standard Cruise/Tour etc.")
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
    if "existing_tour_code" in needed and not existing_tour_code_in:
        required_ok = False
    if action == "update_tour" and not st.session_state.get("fetched_tour_provider_code"):
        required_ok = False
        st.info("Click 'Check what's already online for this code' above first - this fetches the "
               "existing Min/Max Pax, Currency, and ClosedTour Code so you don't have to re-enter them.")

    if st.button("➡️ Continue to Step 3", type="primary", disabled=not required_ok):
        if action == "update_tour":
            min_pax_in = st.session_state.get("fetched_tour_min_pax") or 1
            max_pax_in = st.session_state.get("fetched_tour_max_pax") or 9
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
# STEP 3: Input source
# ----------------------------------------------------------------------
st.header("Step 3 — Input Source")
st.caption("Provide a URL, a document, or both. If you give both, information from each will be "
           "combined into one extraction (e.g. itinerary from a web page + hotel detail from a document).")

url = st.text_input("Product page URL (optional)")
uploaded = st.file_uploader("Upload a DMC document (optional)", type=["pdf", "docx", "xlsx"])
extraction_hint = st.text_input(
    "Extraction hint (optional)",
    placeholder="e.g. 'Use the German-language pricing table' or 'Focus on the Superior room category'",
    help="Short, specific guidance for the AI if the source is ambiguous (e.g. multiple languages, "
         "multiple room categories). Leave blank for normal extraction."
)

if st.button("🔎 Extract", disabled=not (url or uploaded)):
    with st.spinner("Gathering content and checking for multiple tour variants..."):
        try:
            combined_parts = []
            doc_image_urls = []
            if url:
                combined_parts.append(f"--- SOURCE: WEB PAGE ({url}) ---\n{get_page_text(url)}")
            if uploaded:
                suffix = os.path.splitext(uploaded.name)[1]
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(uploaded.getbuffer())
                    tmp_path = tmp.name
                combined_parts.append(f"--- SOURCE: UPLOADED DOCUMENT ({uploaded.name}) ---\n{extract_raw_text(tmp_path)}")

                embedded_images = extract_images(tmp_path)
                if embedded_images:
                    with st.spinner(f"Uploading {len(embedded_images)} image(s) found in the document..."):
                        try:
                            doc_image_urls = upload_images(embedded_images)
                            if doc_image_urls:
                                st.caption(f"✅ Uploaded {len(doc_image_urls)} image(s) from the document to Imgur.")
                        except Exception as e:
                            st.warning(f"Couldn't upload document images: {e}")

                os.remove(tmp_path)

            raw_text = "\n\n".join(combined_parts)
            variants = detect_tour_variants(raw_text)

            if variants:
                st.session_state.pending_variants = variants
                st.session_state.pending_raw_text = raw_text
                st.session_state.pending_url = url or None
                st.session_state.pending_hint = extraction_hint or None
                st.session_state.pending_doc_images = doc_image_urls
            else:
                data = extract_structured_data(raw_text, human_hint=extraction_hint or None)
                data["image_urls"] = (get_page_images(url) if url else []) + doc_image_urls
                st.session_state.extracted = data
                st.session_state.images_text_value = "\n".join(data.get("image_urls", []))
                sources_desc = " + ".join(filter(None, [url, uploaded.name if uploaded else None]))
                st.session_state.raw_preview = f"Source(s): {sources_desc}\n\n{raw_text}"
                st.session_state.payloads = None
                st.success("Extraction complete. Review and edit below.")
        except Exception as e:
            st.error(f"Extraction failed: {e}")

if st.session_state.get("pending_variants"):
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
                st.session_state.pending_variants = None
                st.session_state.pending_raw_text = None
                st.session_state.pending_url = None
                st.rerun()
            except Exception as e:
                st.error(f"Extraction failed: {e}")


# ----------------------------------------------------------------------
# STEP 4: Side-by-side review & edit
# ----------------------------------------------------------------------
if st.session_state.extracted:
    data = st.session_state.extracted

    st.header("Step 4 — Review & Edit")
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Original Source")
        st.text_area("Raw content (read-only reference)", st.session_state.raw_preview, height=600, disabled=True)

    with col2:
        st.subheader("Extracted Data (editable)")
        data["tour_name"] = st.text_input("Tour name", data.get("tour_name", ""))
        data["description"] = st.text_area("Description (HTML ok)", data.get("description", ""), height=140)
        data["hotels_text"] = st.text_area("Hotels", data.get("hotels_text", ""), height=100)
        data["included"] = st.text_area("Included", data.get("included", ""), height=100)
        data["excluded"] = st.text_area("Excluded", data.get("excluded", ""), height=100)
        DEFAULT_MEETING_POINT = ("Meet your guide in the airport arrival hall or, if you are already in the "
                                 "tour's starting city, in your hotel lobby.")
        data["meeting_point"] = st.text_input("Meeting point", data.get("meeting_point") or DEFAULT_MEETING_POINT)
        data["policy_remarks"] = st.text_area("Policy remarks", data.get("policy_remarks", ""), height=80)
        data["nights"] = st.number_input("Nights", min_value=1, value=int(data.get("nights", 1)))

        dest_text = st.text_area(
            "Itinerary destinations (one per line, in visit order)",
            "\n".join(data.get("itinerary_destinations", [])),
            height=120
        )
        data["itinerary_destinations"] = [d.strip() for d in dest_text.split("\n") if d.strip()]

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

    default_price_list = data.get("price_list") or [{
        "name": "Example row - edit or delete",
        "startDate": "2027-01-01",
        "endDate": "2027-12-31",
        "price": {
            "singlePrice": {"amount": 0, "currency": currency},
            "doublePrice": {"amount": 0, "currency": currency}
        }
    }]

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

    st.caption(f"Prices below are in **{currency or '(set Currency in Step 2)'}**. "
              f"Leave a price blank if that occupancy isn't offered. Add/remove rows freely.")
    price_df = pd.DataFrame(price_df_rows)
    edited_price_df = st.data_editor(
        price_df, num_rows="dynamic", use_container_width=True, key="price_editor"
    )

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

    data["price_list"] = [
        _row_to_price_entry(row) for _, row in edited_price_df.iterrows()
        if str(row.get("Start Date", "")).strip() and str(row.get("End Date", "")).strip()
    ]
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

    st.subheader("Optional Add-ons / Upgrades / Excursions (Supplements)")
    st.caption("TRUE optional extras the customer only pays for if they choose them - e.g. a hotel/room "
              "upgrade, a meal upgrade, or an optional excursion day. Leave empty if this tour has none. "
              "For a genuinely different core product (different cabin/route with its own full pricing), "
              "use a separate Modality instead (Publish Action 2).")

    default_supplements = data.get("supplements") or []
    supp_df_rows = [
        {
            "Name": s.get("name", ""),
            "Price (per person)": s.get("price", 0),
            "Mandatory": s.get("mandatory", False),
            "On Request": s.get("on_request", False),
        }
        for s in default_supplements
    ]
    supp_df = pd.DataFrame(supp_df_rows) if supp_df_rows else pd.DataFrame(
        columns=["Name", "Price (per person)", "Mandatory", "On Request"]
    )
    edited_supp_df = st.data_editor(
        supp_df, num_rows="dynamic", use_container_width=True, key="supplements_editor"
    )

    data["supplements"] = [
        {
            "name": str(row.get("Name", "")).strip(),
            "price": float(row.get("Price (per person)", 0) or 0),
            "mandatory": bool(row.get("Mandatory", False)),
            "on_request": bool(row.get("On Request", False)),
        }
        for _, row in edited_supp_df.iterrows()
        if str(row.get("Name", "")).strip()
    ]

    # ----------------------------------------------------------------------
    # STEP 5: Build payloads (destination resolution happens here)
    # ----------------------------------------------------------------------
    if st.button("🔎 Resolve Destinations & Build Payload", disabled=not price_list_valid):
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

        st.header("Step 5 — Destination Resolution & Payload Preview")

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
                f"would fail or create a wrong/broken itinerary stop. **To fix:** go back up to Step 4's "
                f"'Itinerary destinations' box and either correct the spelling/name, or replace it with "
                f"the exact name Travel Compositor uses, then click 'Resolve Destinations & Build Payload' again."
            )

        col3, col4 = st.columns(2)
        with col3:
            if publish_action == "Create a brand-new tour (+ first option)":
                st.subheader("Main Tour Payload (POST - Call 1)")
            elif publish_action == "Update an existing tour's details":
                st.subheader("Main Tour Payload (PUT - update)")
            else:
                st.subheader("Main Tour Payload (not sent this time)")
                st.caption(f"Shown for reference only — '{publish_action}' doesn't touch the main tour.")
            st.json(payloads["main_tour_payload"])
        with col4:
            if publish_action in ("Create a brand-new tour (+ first option)", "Add a new option to an existing tour"):
                st.subheader("Tour Option Payload (POST)")
            elif publish_action == "Update an existing option":
                st.subheader("Tour Option Payload (PUT - update)")
            else:
                st.subheader("Tour Option Payload (not sent this time)")
            if payloads["tour_option_error"]:
                st.error(f"Invalid: {payloads['tour_option_error']}")
            else:
                st.json(payloads["tour_option_payload"])

        # ----------------------------------------------------------------------
        # STEP 6: Publish
        # ----------------------------------------------------------------------
        st.header("Step 6 — Publish")

        creating_new_tour = publish_action == "Create a brand-new tour (+ first option)"
        target_tour_code = payloads["main_tour_code"] if creating_new_tour else existing_tour_code
        missing_existing_code = not creating_new_tour and not existing_tour_code
        missing_provider_code_for_update = (
            publish_action == "Update an existing tour's details"
            and not st.session_state.get("fetched_tour_provider_code")
        )
        if missing_provider_code_for_update:
            st.warning("⚠️ Go back to Step 2 and click 'Check what's already online for this code' first — "
                      "without it, this update could overwrite the tour's real ClosedTour Code with a placeholder.")

        can_publish = (
            not payloads["unresolved_destinations"]
            and not payloads["tour_option_error"]
            and not missing_existing_code
            and not missing_provider_code_for_update
        )

        if missing_existing_code:
            st.info("Existing Tour Code is missing - go back to Step 2.")
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

                        # DIAGNOSTIC: verify what Travel Compositor actually recorded for
                        # 'active', rather than assuming it matches what we sent.
                        diag_check = client.get_closed_tour(payloads["supplier_id"], real_code)
                        if "error" in diag_check:
                            st.warning(f"🔍 Diagnostic GET right after creation also failed: {diag_check}")
                        else:
                            st.info(f"🔍 Diagnostic: Travel Compositor reports this tour's `active` field as "
                                   f"**{diag_check.get('active')}** (we sent `true`).")

                        with st.expander("🔍 Full raw creation response (for deeper diagnosis)", expanded=True):
                            st.json(result)

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
