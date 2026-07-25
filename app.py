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
                 "TRAVELC_PASSWORD", "ANTHROPIC_API_KEY"]:
        try:
            if _key in st.secrets and _key not in os.environ:
                os.environ[_key] = st.secrets[_key]
        except Exception:
            pass  # no secrets.toml present (e.g. running fully locally with .env) - fine, ignore

from api_client import TravelCompositorAPI
from schemas import HumanPreConfig
from builder import build_closed_tour_payloads
from document_reader import extract_raw_text
from ai_extractor import extract_structured_data
from web_extractor import extract_from_url

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
st.sidebar.header("Step 1 — Pre-Configuration")
supplier_id = st.sidebar.text_input("Supplier ID", value="48940")
provider_code = st.sidebar.text_input("Provider Code (XXX-Number)", value="ASW-1")
min_pax = st.sidebar.selectbox("Min Pax", [1, 2])
max_pax = st.sidebar.selectbox("Max Pax", list(range(2, 10)), index=7)  # defaults to 9
currency = st.sidebar.text_input("Currency (ISO 3-letter)", value="EUR")
modality_code = st.sidebar.text_input("Modality / Option Code", value="STANDARD_CABIN")

st.sidebar.divider()
st.sidebar.subheader("Multiple modalities?")
adding_to_existing = st.sidebar.checkbox(
    "Add this as a NEW modality/option to an ALREADY-PUBLISHED tour",
    value=False,
    help="Use this to add a second (or third...) pricing option to a tour you already created. "
         "Skips creating a new main tour and just adds the option below to the existing one."
)
existing_tour_code = None
if adding_to_existing:
    existing_tour_code = st.sidebar.text_input(
        "Existing Tour Code (from Travel Compositor)",
        placeholder="e.g. TOUR-PEK-5"
    )
on_request = st.sidebar.checkbox("On Request", value=True)


# ----------------------------------------------------------------------
# Step 2: Input source
# ----------------------------------------------------------------------
st.header("Step 2 — Input Source")
source_type = st.radio("Source type", ["Web link", "Document upload (PDF / Word / Excel)"], horizontal=True)

if source_type == "Web link":
    url = st.text_input("Product page URL")
    if st.button("🔗 Extract from URL", disabled=not url):
        with st.spinner("Scraping page and resolving destinations..."):
            try:
                data = extract_from_url(url, client)
                st.session_state.extracted = data
                st.session_state.raw_preview = f"Source URL:\n{url}\n\n(Original page content was scraped directly - see extracted fields on the right.)"
                st.session_state.payloads = None
                st.success("Extraction complete. Review and edit below.")
            except Exception as e:
                st.error(f"Extraction failed: {e}")

else:
    uploaded = st.file_uploader("Upload a DMC document", type=["pdf", "docx", "xlsx"])
    if uploaded and st.button("📄 Extract from Document"):
        with st.spinner("Reading document and running AI extraction (this calls the Claude API)..."):
            try:
                # Save to a real temp file since our readers work on file paths
                suffix = os.path.splitext(uploaded.name)[1]
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(uploaded.getbuffer())
                    tmp_path = tmp.name

                raw_text = extract_raw_text(tmp_path)
                st.session_state.raw_preview = raw_text
                data = extract_structured_data(raw_text)
                data["image_urls"] = []  # documents don't have hosted URLs - human adds these below
                st.session_state.extracted = data
                st.session_state.payloads = None
                os.remove(tmp_path)
                st.success("Extraction complete. Review and edit below.")
            except Exception as e:
                st.error(f"Extraction failed: {e}")


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
        data["meeting_point"] = st.text_input("Meeting point", data.get("meeting_point", ""))
        data["policy_remarks"] = st.text_area("Policy remarks", data.get("policy_remarks", ""), height=80)
        data["nights"] = st.number_input("Nights", min_value=1, value=int(data.get("nights", 1)))

        dest_text = st.text_area(
            "Itinerary destinations (one per line, in visit order)",
            "\n".join(data.get("itinerary_destinations", [])),
            height=120
        )
        data["itinerary_destinations"] = [d.strip() for d in dest_text.split("\n") if d.strip()]

        images_text = st.text_area(
            "Image URLs (one per line - documents need these added manually)",
            "\n".join(data.get("image_urls", [])),
            height=80
        )
        data["image_urls"] = [u.strip() for u in images_text.split("\n") if u.strip()] or [FALLBACK_IMAGE]
        if data["image_urls"] == [FALLBACK_IMAGE]:
            st.caption(f"⚠️ No real images provided - using placeholder ({FALLBACK_IMAGE}).")

    st.subheader("Pricing (required by Travel Compositor to publish)")
    price_list_json = st.text_area(
        "priceList (JSON array)",
        json.dumps(data.get("price_list", []), indent=2),
        height=150
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
            if adding_to_existing:
                st.subheader("Main Tour Payload (not sent - adding to existing tour)")
                st.caption("Shown for reference only. Since 'Add to existing tour' is checked, Call 1 is skipped.")
            else:
                st.subheader("Main Tour Payload (Call 1)")
            st.json(payloads["main_tour_payload"])
        with col4:
            st.subheader("Tour Option Payload (Call 2)")
            if payloads["tour_option_error"]:
                st.error(f"Invalid: {payloads['tour_option_error']}")
            else:
                st.json(payloads["tour_option_payload"])

        # ----------------------------------------------------------------------
        # Step 5: Publish
        # ----------------------------------------------------------------------
        st.header("Step 5 — Publish")

        target_tour_code = existing_tour_code if adding_to_existing else payloads["main_tour_code"]
        missing_existing_code = adding_to_existing and not existing_tour_code

        can_publish = (
            not payloads["unresolved_destinations"]
            and not payloads["tour_option_error"]
            and not missing_existing_code
        )

        if missing_existing_code:
            st.info("Enter the Existing Tour Code in the sidebar before publishing.")
        elif not can_publish:
            st.info("Resolve all destinations and fix pricing above before publishing.")

        if adding_to_existing:
            st.caption(f"This will ONLY create a new option under tour code: `{target_tour_code}`. "
                       f"It will NOT create a new main tour.")

        if st.button("🚀 Publish Draft to Travel Compositor", disabled=not can_publish, type="primary"):
            with st.spinner("Publishing draft (active: false)..."):
                if adding_to_existing:
                    # Skip Call 1 entirely - just add the new option to the existing tour
                    option_result = client.create_closed_tour_option(
                        payloads["supplier_id"], target_tour_code, payloads["tour_option_payload"]
                    )
                    if "error" in option_result:
                        st.error(f"❌ Tour option creation failed: {option_result}")
                    else:
                        st.success(f"✅ New modality/option added to existing tour `{target_tour_code}`. "
                                  f"Verify it inside Travel Compositor.")
                else:
                    result = client.create_closed_tour(payloads["supplier_id"], payloads["main_tour_payload"])
                    if "error" in result:
                        st.error(f"❌ Main tour creation failed: {result}")
                    else:
                        st.success(f"✅ Main tour created: {result.get('code', payloads['main_tour_code'])}")
                        option_result = client.create_closed_tour_option(
                            payloads["supplier_id"], payloads["main_tour_code"], payloads["tour_option_payload"]
                        )
                        if "error" in option_result:
                            st.error(f"❌ Tour option creation failed: {option_result}")
                        else:
                            st.success("✅ Tour option created. Draft is ready — verify and activate it inside Travel Compositor.")
