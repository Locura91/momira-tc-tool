"""
Review UI for the DMC -> Travel Compositor Closed Tour pipeline.

Implements the master plan's Step 3: side-by-side original content vs.
extracted/editable data, destination resolution status, and a single
publish button that runs the two chained API calls (main tour + option),
always as a draft ("active": false).

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
import streamlit as st

# When deployed on Streamlit Community Cloud, credentials come from
# st.secrets (entered via the dashboard) instead of a local .env file.
# This copies them into environment variables so the existing
# os.getenv() calls in api_client.py / ai_extractor.py work unchanged
# whether running locally (.env) or centrally hosted (st.secrets).
if hasattr(st, "secrets"):
    for _key in ["TRAVELC_BASE_URL", "TRAVELC_MICROSITE_ID", "TRAVELC_USERNAME",
                 "TRAVELC_PASSWORD", "ANTHROPIC_API_KEY", "PEXELS_API_KEY"]:
        try:
            if _key in st.secrets and _key not in os.environ:
                os.environ[_key] = st.secrets[_key]
        except Exception:
            pass  # no secrets.toml present (e.g. running fully locally with .env) - fine, ignore

from api_client import TravelCompositorAPI
from schemas import HumanPreConfig
from builder import build_closed_tour_payloads
from document_reader import extract_raw_text
from ai_extractor import extract_structured_data, detect_tour_variants
from web_extractor import extract_from_url, get_page_text, get_page_images
from pexels_client import search_images

FALLBACK_IMAGE = "https://multiwander.com/wp-content/uploads/2026/07/Please-load-images.png"

st.set_page_config(page_title="Momira: DMC -> Travel Compositor", layout="wide")


# ----------------------------------------------------------------------
# Session state setup
# ----------------------------------------------------------------------
if "client" not in st.session_state:
    st.session_state.client = TravelCompositorAPI()
if "extracted" not in st.session_state:
    st.session_state.extracted = None
if "raw_preview" not in st.session_state:
    st.session_state.raw_preview = ""
if "payloads" not in st.session_state:
    st.session_state.payloads = None

client = st.session_state.client

st.title("DMC → Travel Compositor: Closed Tour Draft Builder")
st.caption("Every publish is created as a draft (active: false). Human verification and final activation still happen inside Travel Compositor.")


# ----------------------------------------------------------------------
# Step 1: Human Pre-Configuration
# ----------------------------------------------------------------------
st.sidebar.markdown("## 🔴 Step 1 — Fill this in FIRST")

if "suppliers_cache" not in st.session_state:
    st.session_state.suppliers_cache = None

# Always load the supplier list automatically - human picks by name, never
# types a numeric ID directly, to avoid mistakes.
if st.session_state.suppliers_cache is None:
    with st.spinner("Loading supplier list from Travel Compositor..."):
        st.session_state.suppliers_cache = client.get_all_suppliers()

if st.session_state.suppliers_cache:
    supplier_options = {
        f"{s.get('commercialName') or s.get('legalName') or '(unnamed)'} — ID {s.get('id')}": s.get("id")
        for s in st.session_state.suppliers_cache
    }
    selected_label = st.sidebar.selectbox("Supplier (select by name)", list(supplier_options.keys()))
    supplier_id = str(supplier_options[selected_label])
    if st.sidebar.button("🔄 Refresh supplier list"):
        st.session_state.suppliers_cache = None
        st.rerun()
else:
    st.sidebar.error("Could not load the supplier list from Travel Compositor.")
    if st.sidebar.button("🔄 Try again"):
        st.rerun()
    with st.sidebar.expander("⚠️ Emergency manual entry (only if the list keeps failing to load)"):
        supplier_id = st.text_input("Supplier ID (numeric)", value="")

provider_code = st.sidebar.text_input("ClosedTour Code", value="", placeholder="e.g. ASW-1")
min_pax = st.sidebar.selectbox("Min Pax", [1, 2])
max_pax = st.sidebar.selectbox("Max Pax", list(range(2, 10)), index=7)  # defaults to 9
currency = st.sidebar.text_input("Currency (ISO 3-letter)", value="EUR")
modality_code = st.sidebar.text_input("Unique Modality Code", value="Standard")

st.sidebar.divider()
st.sidebar.subheader("Publish Action")
_publish_action_labels = {
    "Create a brand-new tour (+ first option)": "1: Create new ClosedTour + 1 Modality",
    "Add a new option to an existing tour": "2: Add new Modality to existing ClosedTour",
    "Update an existing tour's details": "3: Update existing ClosedTour",
    "Update an existing option": "4: Update existing ClosedTour Modality",
}
publish_action = st.sidebar.radio(
    "What do you want to do?",
    list(_publish_action_labels.keys()),
    format_func=lambda k: _publish_action_labels[k],
    help="Travel Compositor uses POST for creating new things and PUT for updating "
         "existing ones - this selector picks the right one automatically."
)
existing_tour_code = None
if publish_action != "Create a brand-new tour (+ first option)":
    existing_tour_code = st.sidebar.text_input(
        "Existing Tour's real Code (NOT its ClosedTour/Provider Code)",
        placeholder="e.g. CLOSEDTOUR-411099",
        help="Travel Compositor's internal 'code' is often completely different from the "
             "human-chosen ClosedTour Code (e.g. a tour with ClosedTour Code 'TNR-03' had "
             "the real code 'CLOSEDTOUR-411099'). Check inside Travel Compositor's own "
             "platform for the exact 'Code' field if unsure. For tours created through THIS "
             "app, the code shown in the success message after publishing is the one to use here."
    )

    if st.sidebar.button("🔍 Check what's already online for this code"):
        with st.spinner("Fetching from Travel Compositor..."):
            tour_data = client.get_closed_tour(supplier_id, existing_tour_code)
            st.session_state.fetched_tour = tour_data
            st.session_state.fetched_option = None  # clear any previous option fetch

    if st.session_state.get("fetched_tour"):
        t = st.session_state.fetched_tour
        if "error" in t:
            st.sidebar.error(f"Not found or error: {t.get('message', t)}")
        else:
            st.sidebar.success(f"Found: **{t.get('name', '(no name)')}**")
            existing_modalities = t.get("modalityCodes", [])
            st.sidebar.write(f"Existing modality codes: {existing_modalities if existing_modalities else '(none)'}")

            if existing_modalities:
                check_modality = st.sidebar.selectbox("Check pricing for modality:", existing_modalities)
                if st.sidebar.button("🔍 Fetch this modality's live pricing"):
                    with st.spinner("Fetching option..."):
                        st.session_state.fetched_option = client.get_closed_tour_option(
                            supplier_id, existing_tour_code, check_modality
                        )

            if st.session_state.get("fetched_option"):
                opt = st.session_state.fetched_option
                if "error" in opt:
                    st.sidebar.error(f"Could not fetch option: {opt.get('message', opt)}")
                else:
                    with st.sidebar.expander("Live pricing for this modality", expanded=True):
                        for row in opt.get("priceList", []):
                            label = row.get("name") or ""
                            st.write(f"**{row.get('startDate')} → {row.get('endDate')}** {label}")
                            st.json(row.get("price", {}))

on_request = st.sidebar.checkbox("On Request", value=True)


# ----------------------------------------------------------------------
# Step 2: Input source
# ----------------------------------------------------------------------
st.info("⬅️ **Before continuing: fill in Step 1 in the sidebar first** "
        "(Supplier, ClosedTour Code, Pax, Currency, Modality Code). "
        "Extraction works either way, but you'll need these correct before you can publish.")
st.header("Step 2 — Input Source")
st.caption("Provide a URL, a document, or both. If you give both, information from each will be "
           "combined into one extraction (e.g. itinerary from a web page + hotel detail from a document).")

url = st.text_input("Product page URL (optional)")
uploaded = st.file_uploader("Upload a DMC document (optional)", type=["pdf", "docx", "xlsx"])

if st.button("🔎 Extract", disabled=not (url or uploaded)):
    with st.spinner("Gathering content and checking for multiple tour variants..."):
        try:
            combined_parts = []
            if url:
                combined_parts.append(f"--- SOURCE: WEB PAGE ({url}) ---\n{get_page_text(url)}")
            if uploaded:
                suffix = os.path.splitext(uploaded.name)[1]
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(uploaded.getbuffer())
                    tmp_path = tmp.name
                combined_parts.append(f"--- SOURCE: UPLOADED DOCUMENT ({uploaded.name}) ---\n{extract_raw_text(tmp_path)}")
                os.remove(tmp_path)

            raw_text = "\n\n".join(combined_parts)
            variants = detect_tour_variants(raw_text)

            if variants:
                st.session_state.pending_variants = variants
                st.session_state.pending_raw_text = raw_text
                st.session_state.pending_url = url or None
            else:
                data = extract_structured_data(raw_text)
                data["image_urls"] = get_page_images(url) if url else []
                st.session_state.extracted = data
                st.session_state.images_text_value = "\n".join(data.get("image_urls", []))
                sources_desc = " + ".join(filter(None, [url, uploaded.name if uploaded else None]))
                st.session_state.raw_preview = f"Source(s): {sources_desc}\n\n{raw_text}"
                st.session_state.payloads = None
                st.success("Extraction complete. Review and edit below.")
        except Exception as e:
            st.error(f"Extraction failed: {e}")

# Shared variant picker - shown whenever a fetch/read detected multiple distinct tours
if st.session_state.get("pending_variants"):
    variants = st.session_state.pending_variants
    st.warning(f"⚠️ This content describes {len(variants)} distinct tour variants — which one do you want to add?")
    labels = [f"{v.get('label', 'Variant ' + str(i+1))} ({v.get('nights', '?')} nights)" for i, v in enumerate(variants)]
    chosen_idx = st.radio("Pick one:", range(len(labels)), format_func=lambda i: labels[i])

    if st.button("✅ Confirm and Extract Full Details"):
        with st.spinner("Extracting full details for the selected variant..."):
            chosen_label = variants[chosen_idx].get("label", "")
            data = extract_structured_data(st.session_state.pending_raw_text, variant_hint=chosen_label)

            pending_url = st.session_state.get("pending_url")
            data["image_urls"] = get_page_images(pending_url) if pending_url else []
            preview = f"(Extracted variant: {chosen_label})\n\n{st.session_state.pending_raw_text}"

            st.session_state.extracted = data
            st.session_state.images_text_value = "\n".join(data.get("image_urls", []))
            st.session_state.raw_preview = preview
            st.session_state.payloads = None
            st.session_state.pending_variants = None
            st.session_state.pending_raw_text = None
            st.session_state.pending_source = None
            st.session_state.pending_url = None
            st.rerun()


# ----------------------------------------------------------------------
# Step 3: Side-by-side review & edit
# ----------------------------------------------------------------------
if st.session_state.extracted:
    data = st.session_state.extracted

    st.header("Step 3 — Review & Edit")
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

    all_weekdays = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
    data["operational_days"] = st.multiselect(
        "Operational Days (which weekdays this tour can depart on)",
        all_weekdays,
        default=data.get("operational_days", all_weekdays)
    )

    with st.expander("Stop Sales (block specific date ranges - e.g. for monthly-only or irregular departures)"):
        st.caption("For tours that ONLY depart on specific dates (e.g. once a month), set Operational Days above "
                   "to the relevant weekday, then add Stop Sales rows here to block every date EXCEPT the ones "
                   "you want to allow. This mirrors how Travel Compositor represents irregular schedules today - "
                   "it's manual because getting this wrong silently creates wrong bookable dates.")
        stop_sales_json = st.text_area(
            "stopSales (JSON array of {\"start\": \"YYYY-MM-DD\", \"end\": \"YYYY-MM-DD\"})",
            json.dumps(data.get("stop_sales", []), indent=2),
            height=100
        )
        try:
            data["stop_sales"] = json.loads(stop_sales_json)
        except json.JSONDecodeError as e:
            st.error(f"stopSales isn't valid JSON: {e}")

    st.subheader("Pricing (required by Travel Compositor to publish)")
    default_price_list = data.get("price_list") or [{
        "name": "Example row - edit or delete",
        "startDate": "2027-01-01",
        "endDate": "2027-12-31",
        "price": {
            "singlePrice": {"amount": 0, "currency": currency},
            "doublePrice": {"amount": 0, "currency": currency}
        }
    }]
    price_list_json = st.text_area(
        "priceList (JSON array) — fields: name (optional), startDate, endDate, "
        "price.{singlePrice/doublePrice/triplePrice/quadruplePrice} each as {amount, currency}",
        json.dumps(default_price_list, indent=2),
        height=200
    )
    try:
        data["price_list"] = json.loads(price_list_json)
        price_list_valid = True
    except json.JSONDecodeError as e:
        st.error(f"priceList isn't valid JSON: {e}")
        price_list_valid = False

    # ----------------------------------------------------------------------
    # Step 4: Build payloads (destination resolution happens here)
    # ----------------------------------------------------------------------
    if st.button("🔎 Resolve Destinations & Build Payload", disabled=not price_list_valid):
        pre_config = HumanPreConfig(
            supplier_id=supplier_id, provider_code=provider_code,
            min_pax=min_pax, max_pax=max_pax, currency=currency,
            modality_code=modality_code, on_request=on_request
        )
        with st.spinner("Resolving destinations against Travel Compositor..."):
            st.session_state.payloads = build_closed_tour_payloads(pre_config, data, client)
            st.session_state.pre_config = pre_config

    if st.session_state.payloads:
        payloads = st.session_state.payloads

        st.header("Step 4 — Destination Resolution & Payload Preview")

        for item in payloads["main_tour_payload"]["itinerary"]:
            st.write(f"✅ `{item['destination']}`")

        if payloads["unresolved_destinations"]:
            st.warning(f"⚠️ Unresolved destinations (fix in Step 3 and rebuild): {payloads['unresolved_destinations']}")

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
        # Step 5: Publish
        # ----------------------------------------------------------------------
        st.header("Step 5 — Publish")

        creating_new_tour = publish_action == "Create a brand-new tour (+ first option)"
        target_tour_code = payloads["main_tour_code"] if creating_new_tour else existing_tour_code
        missing_existing_code = not creating_new_tour and not existing_tour_code

        can_publish = (
            not payloads["unresolved_destinations"]
            and not payloads["tour_option_error"]
            and not missing_existing_code
        )

        if missing_existing_code:
            st.info("Enter the Existing Tour Code in the sidebar before publishing.")
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
                    result = client.create_closed_tour(payloads["supplier_id"], payloads["main_tour_payload"])
                    if "error" in result:
                        st.error(f"❌ Main tour creation failed: {result}")
                    else:
                        real_code = result.get('code', payloads['main_tour_code'])
                        st.success(f"✅ Main tour created with real Code: **{real_code}** "
                                  f"— save this exact value, you'll need it for any future lookups, "
                                  f"updates, or adding more modalities to this tour.")
                        option_result = client.create_closed_tour_option(
                            payloads["supplier_id"], payloads["main_tour_code"], payloads["tour_option_payload"]
                        )
                        if "error" in option_result:
                            st.error(f"❌ Tour option creation failed: {option_result}")
                        else:
                            st.success("✅ Tour option created. Draft is ready — verify and activate it inside Travel Compositor.")

                elif publish_action == "Add a new option to an existing tour":
                    option_result = client.create_closed_tour_option(
                        payloads["supplier_id"], target_tour_code, payloads["tour_option_payload"]
                    )
                    if "error" in option_result:
                        st.error(f"❌ Tour option creation failed: {option_result}")
                    else:
                        st.success(f"✅ New option added to existing tour `{target_tour_code}`. Verify inside Travel Compositor.")

                elif publish_action == "Update an existing tour's details":
                    update_payload = dict(payloads["main_tour_payload"])
                    update_payload["code"] = target_tour_code  # make sure we're updating the right tour
                    result = client.update_closed_tour(payloads["supplier_id"], update_payload)
                    if "error" in result:
                        st.error(f"❌ Tour update failed: {result}")
                    else:
                        st.success(f"✅ Tour `{target_tour_code}` updated.")

                elif publish_action == "Update an existing option":
                    update_option_payload = dict(payloads["tour_option_payload"])
                    update_option_payload["code"] = modality_code  # make sure we're updating the right option
                    option_result = client.update_closed_tour_option(
                        payloads["supplier_id"], target_tour_code, update_option_payload
                    )
                    if "error" in option_result:
                        st.error(f"❌ Option update failed: {option_result}")
                    else:
                        st.success(f"✅ Option `{modality_code}` under tour `{target_tour_code}` updated.")
