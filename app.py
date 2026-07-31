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
import re
import copy
import tempfile
import os
from datetime import datetime
import streamlit as st
import pandas as pd

if hasattr(st, "secrets"):
    for _key in ["TRAVELC_BASE_URL", "TRAVELC_MICROSITE_ID", "TRAVELC_USERNAME",
                 "TRAVELC_PASSWORD", "ANTHROPIC_API_KEY", "PEXELS_API_KEY", "FREEIMAGE_API_KEY", "PIXABAY_API_KEY"]:
        try:
            if _key in st.secrets and _key not in os.environ:
                os.environ[_key] = st.secrets[_key]
        except Exception:
            pass

from api_client import TravelCompositorAPI
from schemas import HumanPreConfig, TicketHumanPreConfig
from builder import build_closed_tour_payloads, build_ticket_payloads
from document_reader import extract_raw_text, extract_images
from ai_extractor import extract_structured_data, extract_option_only_data, extract_modality_data, detect_tour_variants, detect_multiple_modalities, apply_clarification, extract_ticket_data, extract_ticket_option_only_data, detect_ticket_variants, friendly_error_message
from web_extractor import get_page_text, get_page_images
from pexels_client import search_images
from pixabay_client import search_images as search_images_pixabay
from freeimage_client import upload_images as upload_images_freeimage
from geocoding_client import geocode_search, geocode

FALLBACK_IMAGE = "https://multiwander.com/wp-content/uploads/2026/07/Please-load-images.png"
ALL_WEEKDAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]

# Session-state key prefixes used ONLY by the shared editable_field/
# editable_table widget helpers - never by any flow's own phase/queue
# control state (mct_phase, mm_queue, tk_..., etc. never start with any of
# these). Safe to sweep-clear in bulk: see _clear_batch_widget_state's
# docstring for why this is needed (positional-index widget keys getting
# reused by a different queue item after a skip or a fresh batch start).
SHARED_WIDGET_STATE_PREFIXES = ["_editing_", "_widgetval_", "pencil_", "save_", "editor_"]

# Currency dropdown options - EUR/USD first as the ones actually used in
# practice, then the rest of the common ISO codes a DMC document might quote
# in, alphabetically. A dropdown (instead of free-text) prevents typos like
# "EURO" or "Eur" that Travel Compositor's API would otherwise reject or
# silently mishandle.
CURRENCY_OPTIONS = [
    "EUR", "USD", "GBP", "AUD", "CAD", "CHF", "CNY", "IDR", "INR", "JPY",
    "MXN", "NZD", "SEK", "SGD", "THB", "TRY", "VND", "ZAR",
]

TICKET_ACTION_LABELS = {
    "create": "1: Create new Ticket + 1 Modality",
    "add_option": "2: Add new Modality to existing Ticket",
    "update_ticket": "3: Update existing Ticket (Not updating modality)",
    "update_option": "4: Update existing Ticket Modality",
}
TICKET_ACTION_FIELDS = {
    # NOTE: "create" deliberately does NOT include "modality_code" - Step 4's
    # queue-based flow (render_multi_ticket_flow) collects the Modality Code
    # per Ticket there instead, right next to the Ticket Code, so it's asked
    # exactly once instead of twice. See "modality_code" in ACTION_FIELDS below
    # for the identical ClosedTour case.
    "create": ["ticket_code", "min_passengers", "max_passengers", "currency", "on_request", "release_days"],
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
    # NOTE: "create" deliberately does NOT include "modality_code" - Step 4's
    # queue-based flow (render_multi_tour_flow / the "Set up this tour" screen)
    # collects the Modality Code per tour there instead, right next to the Tour
    # Code, so it's asked exactly once instead of twice (previously a human had
    # to type it here in Step 3 AND again in Step 4 - pure double work, since
    # Step 3's value was never even used).
    "create": ["provider_code", "min_pax", "max_pax", "currency", "on_request", "release_days"],
    "add_option": ["existing_tour_code", "modality_code", "on_request"],
    "update_tour": ["existing_tour_code", "release_days"],
    "update_option": ["existing_tour_code", "currency", "modality_code", "on_request"],
}

def render_seasonal_price_editor(label, target_data, edit_key, currency):
    """
    Renders an editable seasonal price list table (Name/Start/End/Single/
    Double/Triple/Quadruple) bound to target_data["price_list"], matching
    the exact ClosedTour pricing shape. Reusable for the main modality and
    for any additional modalities being created in the same batch.
    """
    default_price_list = sorted(
        target_data.get("price_list") or [{
            "name": "Example row - edit or delete", "startDate": "2027-01-01", "endDate": "2027-12-31",
            "price": {"singlePrice": {"amount": 0, "currency": currency}, "doublePrice": {"amount": 0, "currency": currency}}
        }],
        key=lambda entry: entry.get("startDate", "")
    )
    target_data["price_list"] = default_price_list

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

    def _save(edited_df, target_data=target_data, currency=currency):
        def _row_to_entry(row):
            price = {}
            for col, key in [("Single", "singlePrice"), ("Double", "doublePrice"), ("Triple", "triplePrice"), ("Quadruple", "quadruplePrice")]:
                val = row.get(col)
                if val is not None and not pd.isna(val):
                    price[key] = {"amount": float(val), "currency": currency}
            entry = {"startDate": str(row.get("Start Date", "") or "").strip(), "endDate": str(row.get("End Date", "") or "").strip(), "price": price}
            name = str(row.get("Name", "") or "").strip()
            if name:
                entry["name"] = name
            return entry
        target_data["price_list"] = sorted(
            [_row_to_entry(r) for _, r in edited_df.iterrows() if str(r.get("Start Date", "") or "").strip() and str(r.get("End Date", "") or "").strip()],
            key=lambda e: e.get("startDate", "")
        )

    editable_table(label, price_df, edit_key, on_save=_save)


def render_readonly_source(text, height):
    """
    Read-only display for the raw extracted source text. Uses st.code()
    instead of a disabled st.text_area - disabled form elements can't
    receive real browser focus, so selecting/copying text from one can leak
    keystrokes to Streamlit's global keyboard shortcuts (e.g. "c" = Clear
    Cache), popping up an unwanted dialog while copying. st.code() isn't a
    form control and has its own built-in copy button, so it isn't affected.
    Wrapped in try/except: st.code()'s `height` argument needs a fairly
    recent Streamlit version, and if that (or anything else here) isn't
    supported in this deployment, silently crashing this block would abort
    the ENTIRE page render below it - including Step 6's geolocation UI,
    blocking ticket/tour creation entirely. A working (if imperfect) display
    beats a hard-blocked page.
    """
    try:
        st.code(text, language=None, height=height)
    except Exception:
        st.text_area("Raw content (read-only reference)", text, height=height, disabled=True)


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


def render_optional_time_input(label, data_dict, field_key, widget_key, default_time_str="08:00:00"):
    """
    Easier way to enter a Start/End Time than typing "HH:MM:SS" by hand
    (which the API requires exactly, seconds included) - shows a native
    clock/time picker instead of a free-text field. The field stays
    genuinely OPTIONAL (Travel Compositor accepts blank), so a checkbox
    gates whether a time is set at all - unchecked means "leave blank",
    checked shows the picker and stores its value as "HH:MM:SS".
    """
    current_value = (data_dict.get(field_key) or "").strip()
    has_time = st.checkbox(f"Set a {label.lower()}", value=bool(current_value), key=f"{widget_key}_has")
    if not has_time:
        data_dict[field_key] = ""
        return
    try:
        parsed_default = datetime.strptime(current_value, "%H:%M:%S").time() if current_value else datetime.strptime(default_time_str, "%H:%M:%S").time()
    except ValueError:
        parsed_default = datetime.strptime(default_time_str, "%H:%M:%S").time()
    picked = st.time_input(label, value=parsed_default, key=widget_key, step=900)
    data_dict[field_key] = picked.strftime("%H:%M:%S")


def _clean_time_table_rows(edf, col="Time (HH:MM)"):
    """
    Extracts clean time strings from a data_editor DataFrame column, guarding
    against pandas NaN/None in a blank row. CONFIRMED real bug: str(None) ==
    "None" - a non-empty, truthy string - so a naive `str(val).strip()` on a
    blank row's None/NaN cell used to sail through and get sent to the API as
    a literal time value, crashing java.time.LocalTime deserialization
    server-side ("Text 'None' could not be parsed"). Check for real None/NaN
    FIRST, before ever converting to str().
    """
    times = []
    for _, r in edf.iterrows():
        val = r.get(col)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            continue
        val = str(val).strip()
        if not val or val.lower() in ("none", "nan"):
            continue
        times.append(val)
    return times


def _html_to_plain_for_editing(html):
    """
    Converts the stored HTML (<p>...</p> paragraphs, <strong>/<em> inline
    formatting, <p><br></p> spacer paragraphs between days) into plain,
    human-friendly text for editing - no raw HTML tags shown. Bold/italic
    become **bold**/*italic* markdown-style markers so the human can still
    see and edit that emphasis without ever looking at a tag.
    """
    if not html:
        return ""
    text = html
    # Spacer paragraphs (used between day blocks) become a blank line.
    text = re.sub(r"<p>\s*<br\s*/?>\s*</p>", "\n", text, flags=re.I)
    text = re.sub(r"<(strong|b)>(.*?)</\1>", r"**\2**", text, flags=re.I | re.S)
    text = re.sub(r"<(em|i)>(.*?)</\1>", r"*\2*", text, flags=re.I | re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<p>(.*?)</p>", r"\1\n\n", text, flags=re.I | re.S)
    # Strip any remaining stray tags (e.g. <ul><li> bullet lists) rather than
    # showing raw code - fall back to plain line-per-item for bullets.
    text = re.sub(r"</li>\s*<li>", "\n", text, flags=re.I)
    text = re.sub(r"<[uo]l>", "", text, flags=re.I)
    text = re.sub(r"</[uo]l>", "\n", text, flags=re.I)
    text = re.sub(r"<li>", "", text, flags=re.I)
    text = re.sub(r"</li>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _plain_to_html_for_saving(text):
    """
    Reverses _html_to_plain_for_editing(): plain paragraphs (separated by a
    blank line) become <p>...</p> blocks with <p><br></p> spacers between
    them (matching the original day-by-day template), single newlines within
    a paragraph become <br>, and **bold**/*italic* markers become
    <strong>/<em> tags - all done automatically in the background so the
    human editing the field never has to type or see raw HTML.
    """
    if not text or not text.strip():
        return ""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    html_parts = []
    for i, para in enumerate(paragraphs):
        para_html = para.replace("\n", "<br>")
        para_html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", para_html)
        para_html = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", para_html)
        html_parts.append(f"<p>{para_html}</p>")
        if i < len(paragraphs) - 1:
            html_parts.append("<p><br></p>")
    return "".join(html_parts)


def editable_field(label, data_dict, field_key, widget="text_input", height=None, default_value="", key_suffix=""):
    """
    Renders a field in READ-ONLY display mode by default, with a small
    pencil button to switch it into an editable widget. Saving switches
    back to display mode. Mutates data_dict[field_key] directly on save.

    `key_suffix` MUST be a unique identifier (e.g. f"_{idx}") whenever this
    is used inside a queue/batch review loop (multiple different items
    reusing the same field_key, like "city" or "tour_name", across several
    tickets/tours one at a time). CONFIRMED REAL BUG: Streamlit widgets with
    a fixed `key` ignore the `value=` argument on every render after the
    first and just keep showing whatever was last typed under that key - so
    without a per-item suffix, opening the editor for ticket #2's City after
    having edited ticket #1's City showed ticket #1's stale typed text
    instead of ticket #2's actual city, making it look like the field
    couldn't be changed at all. Single-item flows (one tour/ticket at a
    time, no loop) can safely omit this.
    """
    edit_flag_key = f"_editing_{field_key}{key_suffix}"
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
            if st.button("✏️", key=f"pencil_{field_key}{key_suffix}", help=f"Edit {label}"):
                st.session_state[edit_flag_key] = True
                st.rerun()
    else:
        widget_key = f"_widgetval_{field_key}{key_suffix}"
        if widget == "html_text_area":
            # Show/edit as plain human-friendly text - the underlying HTML
            # (<p>, <strong>, etc.) is converted automatically in the
            # background on the way in and out, so the human never sees or
            # types raw markup.
            st.caption("Formatting (bold, paragraphs) is handled automatically - just write plain text. "
                      "Use **word** for bold if you want to keep a day title bold, and a blank line "
                      "between paragraphs/days.")
            new_plain_value = st.text_area(label, value=_html_to_plain_for_editing(current_value),
                                           height=height or 120, key=widget_key)
        elif widget == "text_area":
            new_value = st.text_area(label, value=current_value, height=height or 120, key=widget_key)
        elif widget == "number_input":
            new_value = st.number_input(label, min_value=1, value=int(current_value or 1), key=widget_key)
        else:
            new_value = st.text_input(label, value=current_value, key=widget_key)
        if st.button("✅ Save", key=f"save_{field_key}{key_suffix}", type="primary"):
            if widget == "html_text_area":
                data_dict[field_key] = _plain_to_html_for_saving(new_plain_value)
            else:
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
                    st.error(f"Extraction failed: {friendly_error_message(e)}")
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

        render_skip_item_button(
            current['code'], queue, idx,
            "mm_queue", "mm_queue_index",
            ["mm_phase", "mm_raw_text", "mm_candidates", "mm_queue", "mm_queue_index"],
            button_key=f"mm_skip_{idx}",
            widget_state_prefixes=SHARED_WIDGET_STATE_PREFIXES
        )

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
                entry = {"startDate": str(row.get("Start Date", "") or "").strip(), "endDate": str(row.get("End Date", "") or "").strip(), "price": price}
                name = str(row.get("Name", "") or "").strip()
                if name:
                    entry["name"] = name
                return entry
            data["price_list"] = sorted(
                [_row_to_entry(r) for _, r in edited_df.iterrows() if str(r.get("Start Date", "") or "").strip() and str(r.get("End Date", "") or "").strip()],
                key=lambda e: e.get("startDate", "")
            )

        editable_table(f"Pricing - {current['code']}", price_df, f"mm_pricing_{idx}", on_save=_save_mm_price_list)

        st.subheader(f"🤖 Tell AI what to fix - {current['code']}")
        st.caption("Ask a question, or tell it to fix something (e.g. 'the price should be x3 for 3 "
                  "nights, not the per-night rate'). Applies real changes when you ask for them.")
        mm_clarify_q = st.text_input("Your message", key=f"mm_clarify_input_{idx}")
        if st.button("Send", disabled=not mm_clarify_q.strip(), key=f"mm_clarify_send_{idx}"):
            with st.spinner("Thinking..."):
                result = apply_clarification(st.session_state.mm_raw_text, data, mm_clarify_q)
                st.session_state[f"mm_clarify_result_{idx}"] = result
                if result.get("changes"):
                    for field_name, new_value in result["changes"].items():
                        data[field_name] = new_value
                    if "price_list" in result["changes"]:
                        st.session_state[f"_editing_table_mm_pricing_{idx}"] = False
                    if "operational_days" in result["changes"]:
                        st.session_state.pop(f"mm_days_{idx}", None)
                    if "stop_sales" in result["changes"]:
                        st.session_state.pop(f"mm_stops_{idx}", None)
                st.rerun()
        if st.session_state.get(f"mm_clarify_result_{idx}"):
            r = st.session_state[f"mm_clarify_result_{idx}"]
            st.info(r.get("summary", ""))
            if r.get("changes"):
                st.caption(f"✅ Applied changes to: {', '.join(r['changes'].keys())} - review above before continuing.")

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
                    try:
                        pre_config = HumanPreConfig(
                            supplier_id=supplier_id,
                            provider_code=st.session_state.get("fetched_tour_provider_code") or "XXX-1",
                            min_pax=1, max_pax=9, currency=currency,
                            modality_code=q["code"], on_request=on_request
                        )
                        payloads = build_closed_tour_payloads(pre_config, q["data"], client)
                        if payloads["tour_option_error"]:
                            show_publish_error(f"prepare **{q['code']}**'s payload", payloads['tour_option_error'])
                            continue
                        result, used_code = try_code_variants(
                            lambda c: client.create_closed_tour_option(supplier_id, c, payloads["tour_option_payload"]),
                            existing_tour_code
                        )
                        if "error" in result:
                            show_publish_error(f"publish **{q['code']}**", result)
                        else:
                            st.success(f"✅ **{q['code']}**: published successfully (code `{used_code}`).")
                    except Exception as e:
                        show_publish_error(f"publish **{q['code']}** (unexpected error - skipped, rest of batch continues)", str(e))
                        continue

        if st.button("🆕 Start a new batch"):
            for key in ["mm_phase", "mm_raw_text", "mm_candidates", "mm_queue", "mm_queue_index"]:
                st.session_state.pop(key, None)
            # Also clear per-item widget state (see _clear_batch_widget_state) -
            # otherwise a fresh batch's first item (always idx==0) can inherit
            # leftover edited values from the PREVIOUS batch's idx==0 item.
            _clear_batch_widget_state(SHARED_WIDGET_STATE_PREFIXES)
            st.rerun()
        return


def _reset_mct_state():
    """Clears all state for the single-ClosedTour create flow, ready to start over."""
    for key in ["mct_phase", "mct_raw_text", "mct_candidates", "mct_doc_raw_images",
               "mct_hosted_image_candidates", "mct_tour"]:
        st.session_state.pop(key, None)
    _clear_batch_widget_state(SHARED_WIDGET_STATE_PREFIXES)


def _new_mct_tour(candidate, tour_code):
    """Builds the single-tour state dict once the human has picked (or auto-picked,
    if only one was ever detected) which ClosedTour to create."""
    return {
        "label": candidate.get("label", ""),
        "nights_hint": candidate.get("nights"),
        "is_genuine_variant": candidate.get("is_genuine_variant", False),
        "tour_code": tour_code,
        "main_data": None,
        "modality_candidates": None,
        "modalities": [],
        "modality_index": 0,
    }


def render_multi_tour_flow(client, supplier_id, currency, on_request, release_days, url, uploaded_files,
                          min_pax=1, max_pax=9, default_tour_code="", extraction_hint=None):
    """
    Single-ClosedTour create flow (CONFIRMED REDESIGN - replaces the old
    multi-tour batch/queue flow, which let several AI-detected "variants"
    each become their own separate ClosedTour in one pass - that's exactly
    what caused real confusion in practice: a document describing ONE tour
    with two Modalities ("Standard | English" / "Superior | English", same
    itinerary) got misdetected as "2 variants", and the old flow tried to
    create TWO separate ClosedTours for it.

    New design: only ONE ClosedTour is ever created per run through this
    flow. If the AI detects what might be multiple distinct ClosedTours in
    the source, the human picks exactly ONE to proceed with - Tour Code and
    Modality Code are NOT collected at that stage (Tour Code already came
    from Step 3; Modality Code isn't relevant until Modalities are set up
    later). Step by step:
      1. gather            - reuse URL/document(s) from Step 4, detect distinct ClosedTours
      2. select_tour       - human picks ONE (skipped automatically if only one was found)
      3. reviewing_main    - review the TOUR-level info only (name, description, itinerary,
                              hotels, included/excluded, meeting point, policy, images) - no
                              pricing/supplements/operational days here, those are per-Modality
      4. select_modalities - AI detects distinct Modalities (room/cabin/pricing categories);
                              human confirms which to include (min. 1 required), can add more
      5. reviewing_modality - EACH Modality reviewed individually and sequentially: its own
                              focused extraction (with an editable AI hint), operational days,
                              stop sales, pricing, and supplements - a universal supplement
                              entered on Modality 1 is carried forward as an editable starting
                              point for the rest, instead of being retyped every time
      6. final_review      - recap of the main tour info + every Modality, with "Edit" links
                              back into any earlier step
      7. publishing        - unchanged from before: create the tour + first Modality's option
                              (active), then each remaining Modality's option, then deactivate
                              if the human chose draft/inactive
    """
    if "mct_phase" not in st.session_state:
        st.session_state.mct_phase = "gather"

    # ------------------------------------------------------------------
    # PHASE 1: detect distinct ClosedTours from the source already provided above
    # ------------------------------------------------------------------
    if st.session_state.mct_phase == "gather":
        if not (url or uploaded_files):
            st.info("Provide a URL and/or upload document(s) above, then click below.")
        if st.button("🔎 Detect ClosedTour(s)", disabled=not (url or uploaded_files)):
            with st.spinner("Gathering content and detecting distinct ClosedTours..."):
                try:
                    combined_parts = []
                    doc_raw_images = []
                    doc_image_urls = []
                    seen_image_hashes = set()
                    if url:
                        combined_parts.append(f"--- SOURCE: WEB PAGE ({url}) ---\n{get_page_text(url)}")
                    for uploaded in (uploaded_files or []):
                        suffix = os.path.splitext(uploaded.name)[1]
                        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                            tmp.write(uploaded.getbuffer())
                            tmp_path = tmp.name
                        combined_parts.append(f"--- SOURCE: UPLOADED DOCUMENT ({uploaded.name}) ---\n{extract_raw_text(tmp_path)}")
                        remaining_budget = 12 - len(doc_raw_images)
                        embedded_images = extract_images(tmp_path, max_images=remaining_budget, seen_hashes=seen_image_hashes) if remaining_budget > 0 else []
                        if embedded_images:
                            for i, (img_bytes, ext) in enumerate(embedded_images):
                                doc_raw_images.append((f"{os.path.splitext(uploaded.name)[0]}_img{i+1}.{ext or 'jpg'}", img_bytes))
                            try:
                                doc_image_urls.extend(upload_images_freeimage(embedded_images))
                            except Exception:
                                pass
                        os.remove(tmp_path)

                    raw_text = "\n\n".join(combined_parts)
                    detected = detect_tour_variants(raw_text)

                    candidates = []
                    for v in detected:
                        candidates.append({
                            "label": v.get("label", ""), "nights": v.get("nights"),
                            # This label came from a REAL AI-detected candidate, so it's safe
                            # to later tell the extraction "only extract this one, ignore the
                            # rest" - see is_genuine_variant usage in PHASE 3 below.
                            "is_genuine_variant": True,
                        })
                    if not candidates:
                        candidates = [{"label": "", "nights": None, "is_genuine_variant": False}]

                    # Only offer "needs hosting" for images that DIDN'T get auto-uploaded -
                    # if every image already got a real URL, showing them again in a second
                    # section would just be a confusing, redundant duplicate of "Images found" above.
                    if len(doc_image_urls) >= len(doc_raw_images):
                        doc_raw_images = []

                    st.session_state.mct_raw_text = raw_text
                    st.session_state.mct_candidates = candidates
                    st.session_state.mct_doc_raw_images = doc_raw_images
                    st.session_state.mct_hosted_image_candidates = list(dict.fromkeys((get_page_images(url) if url else []) + doc_image_urls))
                    st.session_state.mct_phase = "select_tour"
                    st.rerun()
                except Exception as e:
                    st.error(f"Detection failed: {friendly_error_message(e)}")
        return

    # ------------------------------------------------------------------
    # PHASE 2: human picks exactly ONE ClosedTour to create (only shown if
    # more than one was detected - otherwise auto-proceeds with the one found)
    # ------------------------------------------------------------------
    if st.session_state.mct_phase == "select_tour":
        candidates = st.session_state.mct_candidates

        if len(candidates) <= 1:
            chosen = candidates[0] if candidates else {"label": "", "nights": None, "is_genuine_variant": False}
            st.session_state.mct_tour = _new_mct_tour(chosen, default_tour_code)
            st.session_state.mct_phase = "reviewing_main"
            st.rerun()
            return

        st.subheader(f"{len(candidates)} possible ClosedTours detected - choose ONE to create")
        st.caption("This document seems to describe more than one distinct tour product (different "
                  "length/itinerary) - only ONE ClosedTour is created per run through this flow. Pick "
                  "the one you want below; run this again with a different Tour Code (Step 3) for any "
                  "others. Tour Code and Modality Code aren't needed here - Modalities are set up in "
                  "the next steps, after the main tour info is confirmed.")

        # SAFETY NET (confirmed real case): genuine different ClosedTours almost
        # always differ in duration - that's usually the whole point of them being
        # different products. If every candidate here reports the SAME nights,
        # that's a strong signal the AI actually found different Modalities/room-
        # categories (e.g. separate "Standard | English" / "Superior | English"
        # pricing+accommodation blocks for the SAME itinerary), not different tour
        # products - in that case just pick any one below (they're really the same
        # tour) and add the others as Modalities in the next steps.
        distinct_nights = {c.get("nights") for c in candidates if c.get("nights") is not None}
        if len(distinct_nights) <= 1:
            st.warning(
                "🤔 These all report the same length - that often means this is really ONE tour with "
                "different Modalities (e.g. 'Standard' vs 'Superior' pricing/accommodation for the same "
                "itinerary), not genuinely different tour products. If so, just pick any one below - "
                "you'll be able to add the others as Modalities of this same tour in the next steps."
            )

        labels = [
            (c.get("label") or "(unnamed)") + (f" ({c['nights']} nights)" if c.get("nights") else "")
            for c in candidates
        ]
        choice_idx = st.radio("Which ClosedTour do you want to create?", list(range(len(candidates))),
                             format_func=lambda i: labels[i], key="mct_tour_choice")

        if st.button("➡️ Start Reviewing", type="primary"):
            st.session_state.mct_tour = _new_mct_tour(candidates[choice_idx], default_tour_code)
            st.session_state.mct_phase = "reviewing_main"
            st.rerun()

        with st.expander("Not what you wanted?"):
            if st.button("🔙 Start over", key="mct_cancel_select"):
                _reset_mct_state()
                st.rerun()
        return

    # ------------------------------------------------------------------
    # PHASE 3: review the TOUR-LEVEL info only (no pricing/supplements here -
    # those are handled per-Modality in the phases below)
    # ------------------------------------------------------------------
    if st.session_state.mct_phase == "reviewing_main":
        tour = st.session_state.mct_tour

        st.subheader(f"Tour details: {tour['label'] or tour['tour_code'] or '(new tour)'}")
        with st.expander("Not what you wanted?"):
            if st.button("🔙 Start over", key="mct_cancel_main"):
                _reset_mct_state()
                st.rerun()

        if tour["main_data"] is None:
            # CONFIRMED BUG FIX: only pass a variant_hint when this label came from a
            # REAL AI-detected candidate (is_genuine_variant). Passing a human-typed
            # label as a variant_hint told the AI "extract ONLY the variant named X,
            # ignore everything else" - if the source has no variant literally named
            # that, the AI finds no match and returns an empty extraction.
            variant_hint = tour["label"] if tour.get("is_genuine_variant") else None
            with st.spinner(f"Extracting tour details{f' focused on ' + repr(tour['label']) if variant_hint else ''}..."):
                try:
                    tour["main_data"] = extract_structured_data(
                        st.session_state.mct_raw_text, variant_hint=variant_hint, human_hint=extraction_hint
                    )
                    tour["main_data"]["image_urls"] = [FALLBACK_IMAGE]
                except Exception as e:
                    st.error(f"⚠️ Couldn't extract tour details: {friendly_error_message(e)}")
                    if st.button("🔄 Retry extraction", key="mct_retry_main"):
                        st.rerun()
                    return

        data = tour["main_data"]
        if not data.get("meeting_point"):
            data["meeting_point"] = ("Meet your guide in the airport arrival hall or, if you are already in "
                                     "the tour's starting city, in your hotel lobby.")

        tour["tour_code"] = st.text_input(
            "Tour Code", value=tour["tour_code"], key="mct_tour_code",
            help="Your own reference code for this tour, e.g. 'BKK-1' - carried over from Step 3, edit "
                 "here if you want to change it."
        )

        editable_field("Tour name", data, "tour_name", widget="text_input", key_suffix="_main")
        editable_field("Description", data, "description", widget="html_text_area", height=150, key_suffix="_main")
        editable_field("Hotels", data, "hotels_text", widget="text_area", height=100, key_suffix="_main")
        editable_field("Included", data, "included", widget="text_area", height=100, key_suffix="_main")
        editable_field("Excluded", data, "excluded", widget="text_area", height=100, key_suffix="_main")
        editable_field("Meeting point", data, "meeting_point", widget="text_input", key_suffix="_main")
        editable_field("Policy remarks", data, "policy_remarks", widget="text_area", height=80, key_suffix="_main")
        editable_field("Nights", data, "nights", widget="number_input", key_suffix="_main")

        tcol1, tcol2 = st.columns(2)
        with tcol1:
            render_optional_time_input("Start Time", data, "start_time", "mct_start_time_main")
        with tcol2:
            render_optional_time_input("End Time", data, "end_time", "mct_end_time_main", default_time_str="18:00:00")

        acol1, acol2 = st.columns(2)
        with acol1:
            data["min_child_age"] = st.number_input("Min Child Age", min_value=0, max_value=17,
                                                     value=int(data.get("min_child_age", 2) or 2), key="mct_min_child_age_main")
        with acol2:
            data["max_child_age"] = st.number_input("Max Child Age", min_value=0, max_value=17,
                                                     value=int(data.get("max_child_age", 12) or 12), key="mct_max_child_age_main")

        dest_rows = [{"#": i + 1, "Destination": d} for i, d in enumerate(data.get("itinerary_destinations", []))]
        dest_df = pd.DataFrame(dest_rows) if dest_rows else pd.DataFrame(columns=["#", "Destination"])

        def _save_mct_destinations(edited_df, data=data):
            data["itinerary_destinations"] = [
                str(row.get("Destination") or "").strip() for _, row in edited_df.iterrows()
                if str(row.get("Destination", "") or "").strip()
            ]
        editable_table(
            "Itinerary destinations (in visit order)", dest_df, "mct_destinations_main",
            on_save=_save_mct_destinations,
            column_config={"#": st.column_config.NumberColumn(disabled=True)}
        )

        st.markdown("**Images**")
        if data.get("image_urls") == [FALLBACK_IMAGE] or not data.get("image_urls"):
            st.caption("⚠️ No real image picked yet - using a generic placeholder. Pick at least one real image below.")
        else:
            st.caption(f"{len([u for u in data.get('image_urls', []) if u != FALLBACK_IMAGE])} image(s) selected.")

        def _mct_add_url_images():
            selected = render_url_image_picker(st.session_state.mct_hosted_image_candidates, "mct_found_main")
            if selected:
                current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                data["image_urls"] = current_imgs + selected
                return len(selected)
            return 0

        render_closable_image_section(
            bool(st.session_state.get("mct_hosted_image_candidates")),
            f"🖼️ Images found in your document/page ({len(st.session_state.get('mct_hosted_image_candidates') or [])})",
            "mct_found_main_closed", _mct_add_url_images
        )

        def _mct_add_doc_image():
            added = render_doc_image_picker(st.session_state.mct_doc_raw_images, "mct_doc_main")
            if added:
                current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                data["image_urls"] = current_imgs + [added]
                return 1
            return 0

        render_closable_image_section(
            bool(st.session_state.get("mct_doc_raw_images")),
            f"📥 Images needing hosting ({len(st.session_state.get('mct_doc_raw_images') or [])})",
            "mct_doc_main_closed", _mct_add_doc_image
        )

        mct_default_query = tour["label"] or data.get("tour_name", "") or (data.get("itinerary_destinations", [""])[0] if data.get("itinerary_destinations") else "")

        def _mct_add_pexels():
            selected = render_stock_photo_picker("Pexels", search_images, mct_default_query, "mct_pexels_main")
            if selected:
                current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                data["image_urls"] = current_imgs + selected
                return len(selected)
            return 0

        render_closable_image_section(True, "🖼️ Search free stock photos (Pexels)", "mct_pexels_main_closed", _mct_add_pexels)

        def _mct_add_pixabay():
            selected = render_stock_photo_picker("Pixabay", search_images_pixabay, mct_default_query, "mct_pixabay_main")
            if selected:
                current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                data["image_urls"] = current_imgs + selected
                return len(selected)
            return 0

        render_closable_image_section(True, "🖼️ Search free stock photos (Pixabay)", "mct_pixabay_main_closed", _mct_add_pixabay)

        st.markdown("**🤖 Tell AI what to fix**")
        mct_clarify_q = st.text_input("Your message", key="mct_clarify_input_main")
        if st.button("Send", disabled=not mct_clarify_q.strip(), key="mct_clarify_send_main"):
            with st.spinner("Thinking..."):
                result = apply_clarification(st.session_state.mct_raw_text, data, mct_clarify_q)
                st.session_state["mct_clarify_result_main"] = result
                if result.get("changes"):
                    for field_name, new_value in result["changes"].items():
                        data[field_name] = new_value
                st.rerun()
        if st.session_state.get("mct_clarify_result_main"):
            r = st.session_state["mct_clarify_result_main"]
            st.info(r.get("summary", ""))
            if r.get("changes"):
                st.caption(f"✅ Applied changes to: {', '.join(r['changes'].keys())} - review above before continuing.")

        ready = bool((data.get("tour_name") or "").strip()) and bool((tour["tour_code"] or "").strip())
        if st.button("✅ Confirm main tour info & Continue to Modalities", type="primary", disabled=not ready):
            st.session_state.mct_phase = "select_modalities"
            st.rerun()
        if not ready:
            st.info("Tour name and Tour Code are required before continuing to Modalities.")
        return

    # ------------------------------------------------------------------
    # PHASE 4: AI detects distinct Modalities - human confirms which to include
    # ------------------------------------------------------------------
    if st.session_state.mct_phase == "select_modalities":
        tour = st.session_state.mct_tour
        main_name = (tour["main_data"] or {}).get("tour_name") or tour["tour_code"]
        st.subheader(f"Modalities for: {main_name}")

        if tour["modality_candidates"] is None:
            with st.spinner("Detecting pricing categories (Modalities)..."):
                try:
                    detected = detect_multiple_modalities(st.session_state.mct_raw_text)
                except Exception:
                    detected = []  # best-effort - human can still add Modalities manually below
                candidates = []
                for m in detected:
                    label = (m.get("label") or "").strip()
                    raw_code = (m.get("suggested_code") or label or "").strip()
                    # CONFIRMED FIX (real production failure): this code gets sent straight
                    # to Travel Compositor's API - "." used to slip through here (stripped
                    # were only / \ + -), and an AI-suggested code with extra descriptive
                    # text (e.g. "Standard English min. 2 people") got rejected outright by
                    # the real API ("Modality code ... not found in contract modalities").
                    # Strip periods too, and flag anything still suspicious below.
                    clean_code = "".join(c for c in raw_code if c not in "/\\+-.")
                    candidates.append({"code": clean_code, "hint": label, "selected": True})
                if not candidates:
                    candidates = [{"code": "", "hint": "", "selected": True}]
                tour["modality_candidates"] = candidates

        candidates = tour["modality_candidates"]
        st.caption("Auto-detected from your document where possible - untick any you don't want, edit the "
                  "code/hint, or add more manually. At least one Modality is required (a 'Modality' is "
                  "Travel Compositor's own term for the pricing option, e.g. 'Standard' or 'Deluxe').")

        def _mct_modality_code_suspicious(code):
            # CONFIRMED FIX (real production failure): a Modality Code is only ever safe
            # if it's the short category name itself - anything with a stray junk word or
            # unusually long text is almost certainly going to be rejected by the real API
            # the same way "Standard English min. 2 people" was.
            c = (code or "")
            if len(c) > 24:
                return True
            lowered = c.lower()
            return any(junk in lowered for junk in ("people", " pax", "person", " min ", " max ", "min ", "max "))

        suspicious_codes = [c["code"] for c in candidates if c["selected"] and _mct_modality_code_suspicious(c["code"])]
        if suspicious_codes:
            st.warning(
                "🤔 These Modality Codes look unusually long/descriptive for a real code, which has "
                "caused real publish failures before (Travel Compositor rejects anything that isn't "
                "the short category name itself, e.g. 'Standard' not 'Standard English min. 2 people') "
                "- please shorten them to just the core category name: " + ", ".join(f"'{c}'" for c in suspicious_codes)
            )

        for i, cand in enumerate(candidates):
            c1, c2, c3, c4 = st.columns([1, 2, 3, 1])
            with c1:
                cand["selected"] = st.checkbox("Include", value=cand["selected"], key=f"mct_modcand_sel_{i}")
            with c2:
                cand["code"] = st.text_input(
                    "Modality Code", value=cand["code"], key=f"mct_modcand_code_{i}",
                    help="Just the short category name, e.g. 'Standard' or 'Deluxe' - cannot contain the "
                         "characters / \\ + - or . (Travel Compositor's system rejects those), and should "
                         "NOT include descriptive text like the language or a minimum-pax note."
                )
            with c3:
                cand["hint"] = st.text_input("AI focus hint (optional)", value=cand["hint"], key=f"mct_modcand_hint_{i}")
            with c4:
                st.write("")
                if st.button("🗑️", key=f"mct_modcand_remove_{i}", help="Remove this Modality"):
                    candidates.pop(i)
                    st.rerun()

            if any(c in (cand["code"] or "") for c in ["/", "\\", "+", "-", "."]):
                st.error(f"🚫 Modality Code '{cand['code']}' contains invalid characters (/, \\, +, -, .).")

        if st.button("➕ Add another Modality manually"):
            candidates.append({"code": "", "hint": "", "selected": True})
            st.rerun()

        selected = [c for c in candidates if c["selected"]]
        invalid = [c["code"] for c in selected if any(ch in (c["code"] or "") for ch in "/\\+-.")]
        missing = [c for c in selected if not (c["code"] or "").strip()]
        seen = {}
        for c in selected:
            seen.setdefault((c["code"] or "").strip(), []).append(c)
        dup_codes = {code: v for code, v in seen.items() if code and len(v) > 1}

        if missing:
            st.error("🚫 Every included Modality needs a Modality Code.")
        if dup_codes:
            st.error(f"🚫 Duplicate Modality Codes: {list(dup_codes.keys())} - each Modality needs its own unique code.")
        if not selected:
            st.info("Include at least one Modality to continue.")

        ready = bool(selected) and not invalid and not missing and not dup_codes
        if st.button("➡️ Start Reviewing Modalities", type="primary", disabled=not ready):
            tour["modalities"] = [
                {"code": c["code"].strip(), "hint": c["hint"], "data": None, "confirmed": False}
                for c in selected
            ]
            tour["modality_index"] = 0
            st.session_state.mct_phase = "reviewing_modality"
            st.rerun()

        with st.expander("Not what you wanted?"):
            wcol1, wcol2 = st.columns(2)
            with wcol1:
                if st.button("🔙 Back to main tour info", key="mct_back_to_main"):
                    st.session_state.mct_phase = "reviewing_main"
                    st.rerun()
            with wcol2:
                if st.button("🔙 Start over", key="mct_cancel_modsel"):
                    _reset_mct_state()
                    st.rerun()
        return

    # ------------------------------------------------------------------
    # PHASE 5: review EACH Modality individually, one at a time
    # ------------------------------------------------------------------
    if st.session_state.mct_phase == "reviewing_modality":
        tour = st.session_state.mct_tour
        modalities = tour["modalities"]
        midx = tour["modality_index"]
        mod = modalities[midx]

        st.subheader(f"Modality {midx + 1} of {len(modalities)}: **{mod['code']}**")
        st.progress(midx / len(modalities))

        mod["hint"] = st.text_input(
            "AI focus hint (optional - helps the AI find the right pricing table for this Modality)",
            value=mod.get("hint", ""), key=f"mct_mod_hint_{midx}"
        )

        if mod["data"] is None:
            with st.spinner(f"Extracting pricing/schedule for '{mod['code']}'..."):
                try:
                    tour_nights = (tour["main_data"] or {}).get("nights")
                    mod["data"] = extract_modality_data(
                        st.session_state.mct_raw_text, tour_nights=tour_nights,
                        human_hint=mod["hint"] or mod["code"]
                    )
                except Exception as e:
                    st.error(f"⚠️ Couldn't extract pricing for '{mod['code']}': {friendly_error_message(e)}")
                    if st.button("🔄 Retry extraction", key=f"mct_mod_retry_{midx}"):
                        st.rerun()
                    return

                # CONFIRMED (per your answer): a universal supplement (e.g. an
                # airport transfer upgrade that applies to every Modality) is
                # carried forward from Modality 1 as an editable starting point
                # for every Modality after it, instead of making the human
                # retype a shared surcharge on every single Modality's screen.
                # Still fully editable/removable per Modality below - this is
                # just a convenience starting point, not a hard link between
                # them (editing Modality 2's copy never touches Modality 1's).
                if midx > 0 and modalities[0]["data"]:
                    mod["data"]["supplements"] = copy.deepcopy(modalities[0]["data"].get("supplements", []))

        if st.button("🔄 Re-extract with updated hint", key=f"mct_mod_reextract_{midx}"):
            mod["data"] = None
            st.rerun()

        data = mod["data"]

        data["operational_days"] = st.multiselect(
            "Operational Days", ALL_WEEKDAYS, default=data.get("operational_days", ALL_WEEKDAYS), key=f"mct_mod_days_{midx}"
        )
        with st.expander("Stop Sales"):
            mct_stop_sales_json = st.text_area(
                "stopSales (JSON array)", json.dumps(data.get("stop_sales", []), indent=2), key=f"mct_mod_stops_{midx}"
            )
            try:
                data["stop_sales"] = json.loads(mct_stop_sales_json)
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

        def _save_mct_price_list(edited_df, data=data, currency=currency):
            def _row_to_entry(row):
                price = {}
                for col, key in [("Single", "singlePrice"), ("Double", "doublePrice"), ("Triple", "triplePrice"), ("Quadruple", "quadruplePrice")]:
                    val = row.get(col)
                    if val is not None and not pd.isna(val):
                        price[key] = {"amount": float(val), "currency": currency}
                entry = {"startDate": str(row.get("Start Date", "") or "").strip(), "endDate": str(row.get("End Date", "") or "").strip(), "price": price}
                name = str(row.get("Name", "") or "").strip()
                if name:
                    entry["name"] = name
                return entry
            data["price_list"] = sorted(
                [_row_to_entry(r) for _, r in edited_df.iterrows() if str(r.get("Start Date", "") or "").strip() and str(r.get("End Date", "") or "").strip()],
                key=lambda e: e.get("startDate", "")
            )
        editable_table(f"Pricing - {mod['code']}", price_df, f"mct_mod_pricing_{midx}", on_save=_save_mct_price_list)

        st.markdown(f"**Optional Add-ons / Upgrades / Excursions (Supplements) - {mod['code']}**")
        st.caption("TRUE optional extras the customer only pays for if they choose them - e.g. a hotel/room "
                  "upgrade, a meal upgrade, or an optional excursion day, OR a peak-season surcharge, "
                  "SPECIFIC TO THIS MODALITY. Leave empty if this Modality has none. Every row needs a "
                  "clear Name. **Single/Double/Triple/Quadruple** only matter for a surcharge quoted 'per "
                  "room' (e.g. 'USD 71.00 per room per night') - since that flat per-room charge must be "
                  "split by how many people share the room, these 4 columns hold the resulting per-person "
                  "amount for each occupancy. For a normal per-person add-on, just fill 'Price (per "
                  "person)' - the 4 occupancy columns default to matching it.")
        if midx > 0:
            st.caption("💡 Pre-filled below with whatever was entered for Modality 1's supplements, as a "
                      "starting point - edit or remove any that don't actually apply to this Modality.")

        mct_default_supplements = data.get("supplements") or []
        mct_supp_df_rows = [
            {
                "Name": s.get("name", ""),
                "Price (per person)": s.get("price", 0),
                "Single": s.get("single_price", s.get("price", 0)),
                "Double": s.get("double_price", s.get("price", 0)),
                "Triple": s.get("triple_price", s.get("price", 0)),
                "Quadruple": s.get("quadruple_price", s.get("price", 0)),
                "Per Pax": s.get("per_pax", True),
                "Mandatory": s.get("mandatory", False),
                "On Request": s.get("on_request", False),
                "Special Travel Start Date": s.get("travel_start_date", ""),
                "Special Travel End Date": s.get("travel_end_date", ""),
            }
            for s in mct_default_supplements
        ]
        mct_supp_df = pd.DataFrame(mct_supp_df_rows) if mct_supp_df_rows else pd.DataFrame(
            columns=["Name", "Price (per person)", "Single", "Double", "Triple", "Quadruple",
                     "Per Pax", "Mandatory", "On Request", "Special Travel Start Date", "Special Travel End Date"]
        )

        def _save_mct_supplements(edited_df, data=data, midx=midx):
            missing_name = False
            new_supplements = []
            for _, row in edited_df.iterrows():
                name = str(row.get("Name", "") or "").strip()
                price_given = row.get("Price (per person)", 0)
                has_any_data = name or (price_given not in (0, "", None))
                if not name and has_any_data:
                    missing_name = True
                    continue
                if not name:
                    continue
                flat_price = float(price_given or 0)

                def _occ(col, fallback=flat_price):
                    val = row.get(col)
                    if val is None or (isinstance(val, float) and pd.isna(val)):
                        return fallback
                    return float(val)

                new_supplements.append({
                    "name": name,
                    "price": flat_price,
                    "single_price": _occ("Single"),
                    "double_price": _occ("Double"),
                    "triple_price": _occ("Triple"),
                    "quadruple_price": _occ("Quadruple"),
                    "per_pax": bool(row.get("Per Pax", True)),
                    "mandatory": bool(row.get("Mandatory", False)),
                    "on_request": bool(row.get("On Request", False)),
                    "travel_start_date": str(row.get("Special Travel Start Date", "") or "").strip(),
                    "travel_end_date": str(row.get("Special Travel End Date", "") or "").strip(),
                })
            data["supplements"] = new_supplements
            st.session_state[f"_mct_mod_supplements_missing_name_{midx}"] = missing_name

        editable_table(f"Supplements - {mod['code']}", mct_supp_df, f"mct_mod_supplements_{midx}", on_save=_save_mct_supplements)
        if st.session_state.get(f"_mct_mod_supplements_missing_name_{midx}"):
            st.warning("⚠️ A supplement row has a price but no Name - it was skipped. Every supplement needs a clear Name.")

        st.markdown(f"**🤖 Tell AI what to fix - {mod['code']}**")
        mct_mod_clarify_q = st.text_input("Your message", key=f"mct_mod_clarify_input_{midx}")
        if st.button("Send", disabled=not mct_mod_clarify_q.strip(), key=f"mct_mod_clarify_send_{midx}"):
            with st.spinner("Thinking..."):
                result = apply_clarification(st.session_state.mct_raw_text, data, mct_mod_clarify_q)
                st.session_state[f"mct_mod_clarify_result_{midx}"] = result
                if result.get("changes"):
                    for field_name, new_value in result["changes"].items():
                        data[field_name] = new_value
                    # Reset the affected widgets' state so they immediately reflect the
                    # AI's change instead of showing stale previously-typed/edited values -
                    # same fix applied to the main tour info's own clarify box.
                    if "price_list" in result["changes"]:
                        st.session_state[f"_editing_table_mct_mod_pricing_{midx}"] = False
                    if "supplements" in result["changes"]:
                        st.session_state[f"_editing_table_mct_mod_supplements_{midx}"] = False
                    if "operational_days" in result["changes"]:
                        st.session_state.pop(f"mct_mod_days_{midx}", None)
                    if "stop_sales" in result["changes"]:
                        st.session_state.pop(f"mct_mod_stops_{midx}", None)
                st.rerun()
        if st.session_state.get(f"mct_mod_clarify_result_{midx}"):
            r = st.session_state[f"mct_mod_clarify_result_{midx}"]
            st.info(r.get("summary", ""))
            if r.get("changes"):
                st.caption(f"✅ Applied changes to: {', '.join(r['changes'].keys())} - review above before continuing.")

        is_last = midx == len(modalities) - 1
        btn_label = "✅ Confirm this Modality & Finish Modalities" if is_last else "✅ Confirm this Modality & Continue →"
        if st.button(btn_label, type="primary", disabled=not data.get("price_list")):
            mod["confirmed"] = True
            if is_last:
                st.session_state.mct_phase = "final_review"
            else:
                tour["modality_index"] += 1
            st.rerun()
        if not data.get("price_list"):
            st.info("Add at least one price row before continuing.")

        with st.expander("Not what you wanted?"):
            ncol1, ncol2, ncol3 = st.columns(3)
            with ncol1:
                if midx > 0 and st.button("⬅️ Previous Modality", key=f"mct_mod_prev_{midx}"):
                    tour["modality_index"] -= 1
                    st.rerun()
            with ncol2:
                if st.button("🔙 Back to Modality selection", key=f"mct_mod_back_{midx}"):
                    st.session_state.mct_phase = "select_modalities"
                    st.rerun()
            with ncol3:
                if st.button("🔙 Start over", key=f"mct_mod_cancel_{midx}"):
                    _reset_mct_state()
                    st.rerun()
        return

    # ------------------------------------------------------------------
    # PHASE 6: final recap of the tour + all Modalities, still editable
    # ------------------------------------------------------------------
    if st.session_state.mct_phase == "final_review":
        tour = st.session_state.mct_tour
        main_data = tour["main_data"]

        st.subheader(f"Final review: {main_data.get('tour_name') or tour['tour_code']}")
        st.caption("Review everything below before publishing - click 'Edit' on any section to go back "
                  "and adjust it, your other progress is kept.")

        with st.expander("📋 Main tour info", expanded=False):
            st.write(f"**Tour Code:** {tour['tour_code']}")
            st.write(f"**Name:** {main_data.get('tour_name')}")
            st.write(f"**Nights:** {main_data.get('nights')}")
            st.write(f"**Itinerary:** {', '.join(main_data.get('itinerary_destinations', [])) or '(none)'}")
            st.write(f"**Images:** {len([u for u in main_data.get('image_urls', []) if u != FALLBACK_IMAGE])} selected")
        if st.button("✏️ Edit main tour info"):
            st.session_state.mct_phase = "reviewing_main"
            st.rerun()

        for i, mod in enumerate(tour["modalities"]):
            mdata = mod["data"] or {}
            with st.expander(f"📋 Modality: {mod['code']}", expanded=False):
                st.write(f"**Price rows:** {len(mdata.get('price_list', []))}")
                st.write(f"**Supplements:** {len(mdata.get('supplements', []))}")
                st.write(f"**Operational Days:** {', '.join(mdata.get('operational_days', []))}")
            if st.button(f"✏️ Edit Modality '{mod['code']}'", key=f"mct_final_edit_mod_{i}"):
                tour["modality_index"] = i
                st.session_state.mct_phase = "reviewing_modality"
                st.rerun()

        if st.button("➕ Add another Modality"):
            st.session_state.mct_phase = "select_modalities"
            st.rerun()

        if st.button("✅ Confirm this tour & Finish Review", type="primary"):
            st.session_state.mct_phase = "publishing"
            st.rerun()

        with st.expander("Not what you wanted?"):
            if st.button("🔙 Start over", key="mct_cancel_final"):
                _reset_mct_state()
                st.rerun()
        return

    # ------------------------------------------------------------------
    # PHASE 7: publish - unchanged sequence (create tour + first Modality's
    # option active, then each remaining Modality's option, then deactivate
    # if the human chose draft/inactive)
    # ------------------------------------------------------------------
    if st.session_state.mct_phase == "publishing":
        tour = st.session_state.mct_tour
        main_data = tour["main_data"]
        modalities = tour["modalities"]
        st.subheader(f"Ready to publish: {main_data.get('tour_name') or tour['tour_code']}")

        # Merge every Modality's own supplements into ONE list for the main tour
        # payload, each explicitly tagged to its own Modality Code via "applies_to"
        # (builder.py turns this into SupplementVO.modalityCodes) - no guessing
        # needed, since each Modality's supplements were reviewed on its own
        # dedicated screen and are already correctly scoped by construction.
        merged_supplements = []
        for m in modalities:
            for s in (m["data"].get("supplements") or []):
                s_copy = dict(s)
                s_copy["applies_to"] = m["code"]
                merged_supplements.append(s_copy)

        combined_data = dict(main_data)
        combined_data.update(modalities[0]["data"])
        combined_data["supplements"] = merged_supplements

        extra_note = f" + {len(modalities) - 1} more Modalit{'y' if len(modalities) == 2 else 'ies'}" if len(modalities) > 1 else ""
        with st.expander(f"**{tour['tour_code']}** - Modality: {modalities[0]['code']}{extra_note}", expanded=True):
            dup_warning = check_duplicate_tour_name(client, supplier_id, main_data.get("tour_name"))
            if dup_warning:
                st.warning(dup_warning)
            preview_payloads = None
            try:
                preview_pre_config = HumanPreConfig(
                    supplier_id=supplier_id, provider_code=tour["tour_code"],
                    min_pax=min_pax, max_pax=max_pax, currency=currency,
                    modality_code=modalities[0]["code"], on_request=on_request,
                    days_available_before_release=release_days
                )
                preview_payloads = build_closed_tour_payloads(preview_pre_config, combined_data, client)
            except Exception as e:
                st.error(f"⚠️ Couldn't preview this tour's destinations - most likely the Tour Code "
                        f"`{tour['tour_code']}` doesn't match the required format (3 letters, a dash, then "
                        f"a number, e.g. 'BKK-1'). Details: {str(e)[:300]}. Publishing below will also fail "
                        f"until fixed - go back and correct the Tour Code.")
            if preview_payloads:
                for res in preview_payloads.get("itinerary_resolution", []):
                    if res["valid"]:
                        st.markdown(
                            f"<div style='background-color:#d4edda; color:#155724; padding:4px 10px; "
                            f"border-radius:4px; margin-bottom:2px; font-size:0.9em;'>✅ <b>{res['input']}</b> → "
                            f"<code>{res['destination']}</code> ({res.get('resolved_name', '')})</div>",
                            unsafe_allow_html=True
                        )
                    else:
                        st.markdown(
                            f"<div style='background-color:#f8d7da; color:#721c24; padding:4px 10px; "
                            f"border-radius:4px; margin-bottom:2px; font-size:0.9em;'>❌ <b>{res['input']}</b> → "
                            f"NOT FOUND in Travel Compositor</div>",
                            unsafe_allow_html=True
                        )

        mct_activation_choice = st.radio(
            "After publishing, should this Tour be Active or Inactive (draft)?",
            ["Inactive (draft) - recommended, review inside Travel Compositor before it goes live",
             "Active - live immediately"],
            index=0, key="mct_activation_choice"
        )
        mct_publish_as_active = mct_activation_choice.startswith("Active")

        if st.button("🚀 Publish", type="primary"):
            with st.spinner(f"Publishing '{tour['tour_code']}'..."):
                try:
                    pre_config = HumanPreConfig(
                        supplier_id=supplier_id, provider_code=tour["tour_code"],
                        min_pax=min_pax, max_pax=max_pax, currency=currency,
                        modality_code=modalities[0]["code"], on_request=on_request,
                        days_available_before_release=release_days
                    )
                    payloads = build_closed_tour_payloads(pre_config, combined_data, client)
                    if payloads.get("main_tour_error"):
                        show_publish_error(f"prepare **{tour['tour_code']}**'s payload", payloads["main_tour_error"])
                    elif payloads["tour_option_error"]:
                        show_publish_error(f"prepare **{tour['tour_code']}**'s payload", payloads["tour_option_error"])
                    elif payloads["unresolved_destinations"]:
                        st.error(f"❌ Couldn't resolve destination(s) {payloads['unresolved_destinations']} - "
                                f"fix the itinerary destinations and try again.")
                    else:
                        # CONFIRMED ROOT CAUSE (3 real production failures, KNO-1 - traced against the
                        # real Swagger, which shows modalityCodes/supplements[].modalityCodes as plain
                        # freeform [string] with NO enum/pattern - so "not found in contract modalities"
                        # is a runtime check, not a schema one. It kept failing even for a single, clean,
                        # self-consistent Modality Code, which rules out "declare more codes" fixes - the
                        # only reading left is that a code must correspond to an OPTION THAT ALREADY
                        # EXISTS for this tour at the moment it's referenced. At tour-CREATE time NO
                        # option exists yet for ANY Modality, so declaring modalityCodes (or supplements
                        # referencing a Modality via SupplementVO.modalityCodes) at that point always
                        # fails. FIX: mirror the existing "active" 2-phase pattern already used below -
                        # create the tour bare (no modalityCodes, no supplements), create every option
                        # (which is what actually registers each Modality code), THEN a follow-up PUT
                        # declares modalityCodes + supplements now that they genuinely refer to options
                        # that exist, and sets the final active/inactive state in the same call.
                        creation_payload = dict(payloads["main_tour_payload"])
                        creation_payload["active"] = True
                        creation_payload["modalityCodes"] = []
                        creation_payload["supplements"] = []
                        result = client.create_closed_tour(supplier_id, creation_payload)
                        if "error" in result:
                            show_publish_error(f"create **{tour['tour_code']}**", result)
                        else:
                            real_code = result.get("code", payloads["main_tour_code"])
                            created_modality_codes = []

                            # api_client.py's _request() already retries each individual POST
                            # attempt up to 6 times internally - this loop just still tries BOTH
                            # candidate codes (genuinely two different possible identifiers).
                            option_result = None
                            used_code = None
                            for candidate_code in [tour["tour_code"], real_code]:
                                option_result = client.create_closed_tour_option(supplier_id, candidate_code, payloads["tour_option_payload"])
                                if "error" not in option_result:
                                    used_code = candidate_code
                                    break
                            if "error" in option_result:
                                show_publish_error(f"create **{tour['tour_code']}**'s option (created as `{real_code}`)", option_result)
                            else:
                                st.success(f"✅ **{tour['tour_code']}**: base modality '{modalities[0]['code']}' created (option code used: `{used_code}`).")
                                created_modality_codes.append(modalities[0]["code"])

                            for m in modalities[1:]:
                                with st.spinner(f"Creating '{tour['tour_code']}' modality '{m['code']}'..."):
                                    try:
                                        mod_pre_config = HumanPreConfig(
                                            supplier_id=supplier_id, provider_code=tour["tour_code"],
                                            min_pax=min_pax, max_pax=max_pax, currency=currency,
                                            modality_code=m["code"], on_request=on_request,
                                            days_available_before_release=release_days
                                        )
                                        mod_payloads = build_closed_tour_payloads(mod_pre_config, m["data"], client)
                                        if mod_payloads["tour_option_error"]:
                                            show_publish_error(f"prepare **{tour['tour_code']}** modality '{m['code']}'", mod_payloads["tour_option_error"])
                                            continue
                                        mod_result, mod_used_code = try_code_variants(
                                            lambda c: client.create_closed_tour_option(supplier_id, c, mod_payloads["tour_option_payload"]),
                                            [tour["tour_code"], real_code]
                                        )
                                        if "error" in mod_result:
                                            show_publish_error(f"create **{tour['tour_code']}** modality '{m['code']}'", mod_result)
                                        else:
                                            st.success(f"✅ **{tour['tour_code']}**: modality '{m['code']}' created (code used: `{mod_used_code}`).")
                                            created_modality_codes.append(m["code"])
                                    except Exception as e:
                                        show_publish_error(f"create **{tour['tour_code']}** modality '{m['code']}' (unexpected error - skipped, rest continues)", str(e))
                                        continue

                            # Now that every successfully-created option genuinely exists, declare
                            # modalityCodes for real and restore the (already correctly-scoped)
                            # supplements list - but only keep supplements whose Modality actually
                            # got created above, so a failed Modality can't drag this PUT down too.
                            finalize_payload = dict(payloads["main_tour_payload"])
                            finalize_payload["code"] = real_code
                            finalize_payload["active"] = mct_publish_as_active
                            finalize_payload["modalityCodes"] = created_modality_codes
                            finalize_payload["supplements"] = [
                                s for s in payloads["main_tour_payload"].get("supplements", [])
                                if not s.get("modalityCodes") or all(c in created_modality_codes for c in s["modalityCodes"])
                            ]
                            if created_modality_codes:
                                finalize_result = client.update_closed_tour(supplier_id, finalize_payload)
                                if "error" in finalize_result:
                                    st.warning(f"⚠️ **{tour['tour_code']}**: tour and option(s) were created, but the "
                                              f"follow-up update (registering Modality codes/supplements and setting "
                                              f"the final active state) failed - {finalize_result}. The tour exists "
                                              f"in Travel Compositor but may need this finished manually.")
                                else:
                                    state_label = "ACTIVE" if mct_publish_as_active else "inactive/draft"
                                    st.success(f"✅ **{tour['tour_code']}** published successfully as `{real_code}` ({state_label}).")
                            else:
                                st.warning(f"⚠️ **{tour['tour_code']}**: no Modality options were created successfully - "
                                          f"skipped the follow-up update. Fix the error(s) above and try again.")
                except Exception as e:
                    show_publish_error(f"publish **{tour['tour_code']}** (unexpected error)", str(e))

        if st.button("🆕 Start a new ClosedTour"):
            _reset_mct_state()
            st.rerun()
        return



def render_closable_image_section(condition, header, closed_key, picker_call):
    """
    Wraps ONE image-picker section (stock photo search, "found in your
    document/page", etc) so that once the human clicks "Add selected"
    inside it, the section visibly collapses into a plain confirmation line
    instead of staying open - which used to leave it ambiguous whether the
    click actually worked. Streamlit's native st.expander(expanded=...) only
    sets the widget's INITIAL open/closed state; once a human has manually
    toggled it open in the browser, the script can't reliably force it shut
    again on a later rerun. This sidesteps that entirely by managing its own
    open/closed flag in session_state and simply not re-rendering the
    interactive picker once something's been added.
    `picker_call` is a zero-arg callable that renders the picker AND applies
    any newly-selected URLs to the caller's data dict itself, returning how
    many it just added (0/None if nothing new this run).
    """
    if not condition:
        return
    if st.session_state.get(closed_key):
        added_n = st.session_state.get(f"{closed_key}_count", 0)
        col_a, col_b = st.columns([5, 1])
        with col_a:
            st.success(f"✅ {header} — {added_n} image(s) added.")
        with col_b:
            if st.button("➕ Add more", key=f"{closed_key}_reopen"):
                st.session_state[closed_key] = False
                st.rerun()
        return
    with st.expander(header, expanded=True):
        added = picker_call()
    if added:
        st.session_state[closed_key] = True
        st.session_state[f"{closed_key}_count"] = st.session_state.get(f"{closed_key}_count", 0) + added
        st.rerun()


def render_url_image_picker(image_urls, state_prefix):
    """
    Shows a thumbnail grid + checkboxes for images that are ALREADY hosted
    URLs (e.g. scraped from a web page) - same picker pattern as the stock
    photo search, just without a search step since the URLs are already known.
    Returns the list of newly selected URLs if 'Add selected' was clicked
    this run, otherwise None.
    """
    if not image_urls:
        return None
    st.caption("Select images to add, then click 'Add selected':")
    cols = st.columns(3)
    selected_urls = []
    for i, url in enumerate(image_urls):
        photo_key = abs(hash(url))  # content-based, never collides across different sets of results
        with cols[i % 3]:
            st.image(url)
            if st.checkbox("Use this image", value=False, key=f"{state_prefix}_pick_{photo_key}"):
                selected_urls.append(url)
    if st.button("➕ Add selected to Image URLs", key=f"{state_prefix}_add_btn") and selected_urls:
        return selected_urls
    return None


def render_doc_image_picker(doc_raw_images, state_prefix):
    """
    Shows a thumbnail grid for images extracted from an uploaded document
    (raw bytes, not yet hosted anywhere). Each has its own 'Upload & Add'
    button (uploads via freeimage.host, then adds the resulting URL) plus a
    download button as a guaranteed fallback if upload isn't set up/fails.
    Returns a newly-added URL if an upload just succeeded this run, else None.
    """
    if not doc_raw_images:
        return None
    st.caption("Images found in your document(s). Upload one to host it and add the URL automatically, "
              "or download it to host manually elsewhere.")
    cols = st.columns(3)
    newly_added_url = None
    for i, (fname, img_bytes) in enumerate(doc_raw_images):
        photo_key = abs(hash(fname + str(len(img_bytes))))  # content-based, stable per unique image
        with cols[i % 3]:
            st.image(img_bytes, caption=fname)
            if st.button("☁️ Upload & Add", key=f"{state_prefix}_upload_{photo_key}"):
                try:
                    url = upload_images_freeimage([(img_bytes, fname.rsplit(".", 1)[-1] if "." in fname else "jpg")])
                    if url:
                        newly_added_url = url[0]
                        st.success("Uploaded!")
                    else:
                        st.error("Upload returned no URL.")
                except Exception as e:
                    st.error(f"Upload failed: {friendly_error_message(e)}")
            st.download_button("⬇️ Download", data=img_bytes, file_name=fname, key=f"{state_prefix}_dl_{photo_key}")
    return newly_added_url


def render_stock_photo_picker(source_label, search_fn, default_query, state_prefix):
    """
    Renders search input + button + thumbnail grid + selection checkboxes
    for a stock photo source (Pexels, Pixabay, etc). Returns the list of
    newly selected URLs if 'Add selected' was just clicked this run,
    otherwise None - caller decides how to merge/apply (different products
    use slightly different underlying image_urls update patterns).
    """
    query = st.text_input("Search term", value=default_query, key=f"{state_prefix}_query")
    if st.button(f"🔍 Search {source_label}", key=f"{state_prefix}_search_btn"):
        with st.spinner(f"Searching {source_label}..."):
            try:
                # Clear any previous selection checkboxes before showing new
                # results - otherwise a checkbox key reused at the same grid
                # position could inherit a stale "checked" state from an
                # earlier, completely different search result.
                for key in list(st.session_state.keys()):
                    if key.startswith(f"{state_prefix}_pick_"):
                        del st.session_state[key]
                st.session_state[f"{state_prefix}_results"] = search_fn(query)
            except Exception as e:
                st.session_state[f"{state_prefix}_results"] = None
                st.error(str(e))

    if st.session_state.get(f"{state_prefix}_results"):
        st.caption("Select images to add, then click 'Add selected below':")
        cols = st.columns(3)
        selected_urls = []
        for i, photo in enumerate(st.session_state[f"{state_prefix}_results"]):
            photo_key = abs(hash(photo["url"]))  # content-based, not position-based - never collides across different searches
            with cols[i % 3]:
                st.image(photo["thumbnail"])
                if st.checkbox(f"Use (by {photo['photographer']})", value=False, key=f"{state_prefix}_pick_{photo_key}"):
                    selected_urls.append(photo["url"])

        if st.button("➕ Add selected to Image URLs", key=f"{state_prefix}_add_btn") and selected_urls:
            return selected_urls
    return None


def show_publish_error(context_label, raw_error):
    """
    Shows a simple, human-readable error summary by default - extracted from
    Travel Compositor's own nested error message when possible - with the
    full raw technical detail available in an expander for anyone who needs
    to see or report the exact API response.
    """
    extracted_detail = None
    try:
        if isinstance(raw_error, dict) and "message" in raw_error:
            inner = raw_error["message"]
            if isinstance(inner, str):
                try:
                    inner_parsed = json.loads(inner)
                    if isinstance(inner_parsed, dict) and "error" in inner_parsed:
                        errs = inner_parsed["error"]
                        extracted_detail = " / ".join(str(e) for e in errs) if isinstance(errs, list) else str(errs)
                except (json.JSONDecodeError, TypeError):
                    extracted_detail = inner
            else:
                extracted_detail = str(inner)
        elif isinstance(raw_error, str):
            # e.g. a Pydantic validation error - often multi-line, so keep the summary to the first line
            first_line = raw_error.strip().split("\n")[0]
            extracted_detail = first_line + ("..." if "\n" in raw_error.strip() else "")
    except Exception:
        pass

    if extracted_detail:
        st.error(f"❌ Couldn't {context_label}: {extracted_detail}")
    else:
        st.error(f"❌ Couldn't {context_label}.")

    with st.expander("🔧 Technical details"):
        st.code(str(raw_error))


def get_existing_tour_names(client, supplier_id):
    """
    Fetches the list of ClosedTours already published for this supplier, so
    a new tour's name can be checked against them before uploading - catches
    accidental duplicate uploads (e.g. re-running the same document twice).
    Cached per-supplier in session_state for the rest of the session (cleared
    on demand via the "Refresh" control next to the duplicate-name warning).
    Returns (names_list, error_message). error_message is None on success -
    if the API call fails or the response shape isn't recognized, this
    returns ([], "reason") so the caller can skip the check gracefully
    instead of blocking publishing over a check that couldn't run.
    """
    if "_existing_tours_cache" not in st.session_state:
        st.session_state._existing_tours_cache = {}
    cache = st.session_state._existing_tours_cache
    if supplier_id in cache:
        return cache[supplier_id]

    try:
        result = client.get_closed_tours(supplier_id, first=0, limit=200)
    except Exception as e:
        cache[supplier_id] = ([], friendly_error_message(e))
        return cache[supplier_id]

    if isinstance(result, dict) and "error" in result:
        cache[supplier_id] = ([], "couldn't reach Travel Compositor to check existing tours")
        return cache[supplier_id]

    # Normalize whatever shape came back - a bare list, or a dict wrapping
    # the list under one of a few likely keys - into a flat list of items.
    items = []
    if isinstance(result, list):
        items = result
    elif isinstance(result, dict):
        for key in ("closedTour", "closedTours", "items", "data", "results", "content"):
            if isinstance(result.get(key), list):
                items = result[key]
                break

    names = []
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            names.append({"name": item["name"], "code": item.get("code", "")})

    if not items and not names:
        cache[supplier_id] = ([], "no existing tours found (or couldn't recognize the response format)")
    else:
        cache[supplier_id] = (names, None)
    return cache[supplier_id]


def check_duplicate_tour_name(client, supplier_id, tour_name):
    """
    Returns a human-readable warning string if `tour_name` (case/whitespace-
    insensitive) matches an existing tour already published for this
    supplier, else None. Never raises - a failed lookup just means no
    warning is shown, since this is a helpful heads-up, not a hard gate.
    """
    clean_name = (tour_name or "").strip().lower()
    if not clean_name:
        return None
    names, _ = get_existing_tour_names(client, supplier_id)
    for existing in names:
        if existing["name"].strip().lower() == clean_name:
            return (f"⚠️ A tour named **'{existing['name']}'** already exists for this supplier "
                    f"(code: `{existing['code']}`). Double-check this isn't a duplicate upload before publishing.")
    return None


def check_code_availability(client, kind, supplier_id, code):
    """
    Directly asks Travel Compositor (GET by exact code) whether a
    ClosedTour/Ticket CODE already exists for this supplier - this is the
    real, authoritative check for the "code already exists" publish error
    (different from - and more definitive than - the name-based duplicate
    check above, since the actual API rejection is keyed on the code, not
    the name). `kind` is "tour" or "ticket". Cached per (kind, supplier_id,
    code) in session_state so re-checking the same code (e.g. re-rendering
    on every keystroke elsewhere on the page) costs nothing extra.
    Returns {"exists": bool, "name": str|None} on a successful lookup, or
    None if the check itself couldn't be completed (e.g. API/network
    issue) - callers should treat None as "couldn't verify" rather than
    either a pass or a fail.
    """
    clean_code = (code or "").strip()
    if not clean_code:
        return None
    if "_code_exists_cache" not in st.session_state:
        st.session_state._code_exists_cache = {}
    cache = st.session_state._code_exists_cache
    cache_key = (kind, supplier_id, clean_code)
    if cache_key in cache:
        return cache[cache_key]

    try:
        result = client.get_closed_tour(supplier_id, clean_code) if kind == "tour" else client.get_ticket(supplier_id, clean_code)
    except Exception:
        return None

    if not isinstance(result, dict):
        return None
    if "error" in result:
        # A clean "not found" - some accounts return 404, treat any error
        # response here as "doesn't exist" rather than "couldn't check",
        # since that's what get_closed_tour/get_ticket already normalize to.
        outcome = {"exists": False, "name": None}
    else:
        outcome = {"exists": True, "name": result.get("name")}
    cache[cache_key] = outcome
    return outcome


def render_code_availability_check(client, kind, supplier_id, code, label):
    """
    Shows an immediate, automatic (no button needed) availability check
    right under a Tour/Ticket Code input field - so a human sees "this code
    is already taken" the moment they type it, instead of only discovering
    it after filling out the entire form and pressing Publish.
    """
    result = check_code_availability(client, kind, supplier_id, code)
    if result is None:
        return
    if result["exists"]:
        st.error(f"🚫 `{(code or '').strip()}` is ALREADY TAKEN by an existing {label} "
                f"(\"{result.get('name') or '(unnamed)'}\"). Choose a different code, or use an "
                f"Update/Add-modality action instead if you meant to add to this existing one.")
    else:
        st.success(f"✅ `{(code or '').strip()}` is available.")


def _diff_tour_price_list(old_list, new_list):
    """
    Compares two ClosedTour price_list arrays (each entry: startDate/endDate/
    price{singlePrice,doublePrice,triplePrice,quadruplePrice}), matched by
    (startDate, endDate) - not by 'name', since that's just a free-text label
    that can differ without the actual price changing. Returns only the
    periods that actually differ: [{"period": str, "status": "added"|
    "removed"|"changed", "old": dict|None, "new": dict|None}] - unchanged
    periods are omitted so the human only sees what matters.
    """
    def _key(row):
        return (row.get("startDate", ""), row.get("endDate", ""))

    def _amounts(row):
        price = row.get("price") or {}
        out = {}
        for k, label in [("singlePrice", "Single"), ("doublePrice", "Double"),
                         ("triplePrice", "Triple"), ("quadruplePrice", "Quad")]:
            block = price.get(k)
            if isinstance(block, dict) and block.get("amount") is not None:
                out[label] = block["amount"]
        return out

    old_by_key = {_key(r): r for r in (old_list or [])}
    new_by_key = {_key(r): r for r in (new_list or [])}
    changes = []
    for key in sorted(set(old_by_key) | set(new_by_key)):
        old_row, new_row = old_by_key.get(key), new_by_key.get(key)
        period = f"{key[0]} → {key[1]}"
        if old_row and not new_row:
            changes.append({"period": period, "status": "removed", "old": _amounts(old_row), "new": None})
        elif new_row and not old_row:
            changes.append({"period": period, "status": "added", "old": None, "new": _amounts(new_row)})
        else:
            old_amt, new_amt = _amounts(old_row), _amounts(new_row)
            if old_amt != new_amt:
                changes.append({"period": period, "status": "changed", "old": old_amt, "new": new_amt})
    return changes


def _map_fetched_supplements(fetched_supplements):
    """
    Best-effort reverse mapping of GET-response SupplementVO dicts back into
    the internal editing shape (name/price/single_price/.../applies_to/
    travel_start_date/travel_end_date) used throughout the review UI and by
    build_closed_tour_payloads(). Some detail (e.g. exactly how per_pax was
    originally set) isn't recoverable from the GET response, so this
    defaults conservatively - always double-check supplements on the review
    screen after they're pulled in this way.
    """
    mapped = []
    for s in (fetched_supplements or []):
        if not isinstance(s, dict):
            continue
        translations = s.get("translations") or {}
        name = (translations.get("EN") or {}).get("name", "")
        price = s.get("price") or {}
        modality_codes = s.get("modalityCodes") or []
        if not modality_codes:
            applies_to = "All Modalities"
        elif len(modality_codes) == 1:
            applies_to = modality_codes[0]
        else:
            applies_to = modality_codes[0]  # editing UI only supports one code per row - keep the first, flag via name
            name = f"{name} (also applies to: {', '.join(modality_codes[1:])})".strip()
        windows = s.get("travelWindows") or []
        travel_start = (windows[0] or {}).get("start", "") if windows else ""
        travel_end = (windows[0] or {}).get("end", "") if windows else ""
        flat_price = price.get("singlePrice", 0) or 0
        mapped.append({
            "name": name,
            "price": flat_price,
            "single_price": price.get("singlePrice", flat_price),
            "double_price": price.get("doublePrice", flat_price),
            "triple_price": price.get("triplePrice", flat_price),
            "quadruple_price": price.get("quadruplePrice", flat_price),
            "per_pax": True,
            "mandatory": s.get("mandatory", False),
            "on_request": s.get("onRequest", False),
            "applies_to": applies_to,
            "travel_start_date": travel_start,
            "travel_end_date": travel_end,
        })
    return mapped


def _map_fetched_tour_to_data(fetched):
    """
    CONFIRMED FIX (real near-data-loss report): "Update an existing tour's
    details" used to require a FRESH extraction from a newly-uploaded
    document/URL before Step 5 (the review/edit screen) would render at
    all - if the human didn't have a new source handy (e.g. they just
    wanted to tweak one field), every field started completely BLANK, and
    nothing stopped them from publishing that blank data straight over the
    real, live tour.

    This builds the SAME internal `data` shape extract_structured_data()
    produces, but sourced from the tour's OWN currently-live GET response
    (already fetched in Step 3's "Check what's already online for this
    code") - so the review screen always starts from the tour's real
    values, never blank ones. If the human also runs a fresh extraction
    from a newly-uploaded document, that gets merged ON TOP of this
    baseline (see _merge_extraction_over_baseline) rather than replacing it
    outright, so an incomplete new extraction can't blank out real fields
    the fresh source just didn't happen to mention.

    price_list/operational_days/stop_sales are intentionally left at their
    empty defaults here - those live on the OPTION, not the tour, and this
    action ("update tour details") never touches them.
    """
    if not isinstance(fetched, dict) or "error" in fetched:
        return {}
    datasheet = (fetched.get("datasheets") or {}).get("EN", {}) or {}
    itinerary = fetched.get("itinerary") or []
    return {
        "tour_name": datasheet.get("name", "") or fetched.get("name", ""),
        "description": datasheet.get("description", ""),
        "hotels_text": datasheet.get("hotels", ""),
        "hotels_count": fetched.get("hotels", 1),
        "supplements": _map_fetched_supplements(fetched.get("supplements")),
        "included": datasheet.get("included", ""),
        "excluded": datasheet.get("excluded", ""),
        "meeting_point": datasheet.get("meetingPoint", ""),
        "policy_remarks": datasheet.get("remarksDescription", ""),
        "itinerary_destinations": [d.get("destination", "") for d in itinerary if isinstance(d, dict) and d.get("destination")],
        "nights": fetched.get("nights", 0),
        "start_time": fetched.get("startTime", ""),
        "end_time": fetched.get("endTime", ""),
        "min_child_age": fetched.get("minChildAge", 2),
        "max_child_age": fetched.get("maxChildAge", 12),
        "image_urls": fetched.get("images") or [FALLBACK_IMAGE],
        "operational_days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
        "schedule_notes": "",
        "pricing_notes": "",
        "price_list": [],
        "release_days_mentions": [],
    }


def _map_fetched_ticket_to_data(fetched):
    """
    Ticket equivalent of _map_fetched_tour_to_data() - see that function for
    the full rationale. Pre-fills "Update an existing ticket's details" from
    the ticket's own live GET response instead of leaving every field blank
    until a fresh document/URL is extracted.
    """
    if not isinstance(fetched, dict) or "error" in fetched:
        return {}
    datasheet = (fetched.get("datasheets") or {}).get("EN", {}) or {}
    geoloc = fetched.get("geolocation") or {}
    return {
        "ticket_name": datasheet.get("name", "") or fetched.get("name", ""),
        "description": datasheet.get("description", ""),
        "city": fetched.get("city", "") or geoloc.get("name", ""),
        "includes": datasheet.get("includes") or [],
        "excludes": datasheet.get("excludes") or [],
        "meeting_points": [],
        "meeting_point_summary": datasheet.get("meetingPoint", ""),
        "duration": fetched.get("duration", 0),
        "duration_type": fetched.get("durationType", "HOURS"),
        "activity_type": datasheet.get("activityType") or "",
        "is_private": False,
        "image_urls": fetched.get("imageUrls") or [FALLBACK_IMAGE],
    }


def _merge_extraction_over_baseline(baseline, fresh):
    """
    Merges a freshly-extracted dict ON TOP of an existing baseline (e.g. a
    tour/ticket's real live values pulled via _map_fetched_tour_to_data /
    _map_fetched_ticket_to_data) - keeps the baseline's value for any field
    the fresh extraction left empty/default, instead of letting an
    incomplete new extraction silently blank out real data that was already
    correctly pre-filled. Only used for "update" actions; "create" always
    uses the fresh extraction as-is (no baseline exists to merge over).
    """
    if not baseline:
        return fresh
    merged = dict(baseline)
    empty_values = (None, "", [], {}, 0)
    for k, v in (fresh or {}).items():
        if v not in empty_values:
            merged[k] = v
    return merged


def render_tour_update_comparison(publish_action, data, payloads, client, supplier_id,
                                  existing_tour_code, working_tour_code, modality_code):
    """
    For "Update an existing tour's details" / "Update an existing option":
    fetches what's CURRENTLY live on Travel Compositor (already cached in
    st.session_state.fetched_tour from Step 3's "Check what's already
    online") and compares it against the freshly-extracted new data, so a
    human sees exactly what's changing before publishing an update instead
    of blindly overwriting whatever was there.

    CONFIRMED RULE #1: a ClosedTour's number of NIGHTS is a structural fact
    about the product, not a detail that gets "updated" - if the new source
    describes a different night count than what's currently live, this is a
    DIFFERENT tour, not a revision of the same one (the itinerary/pricing
    structure is built around a fixed night count). Returns True if this
    hard block applies - the caller must then refuse to let the human
    publish, since Travel Compositor's PUT is meant for genuine detail
    corrections, not restructuring the whole product.
    """
    st.subheader("🔄 Comparing with what's already online")
    blocks_publish = False
    old = st.session_state.get("fetched_tour")
    have_old_tour = isinstance(old, dict) and "error" not in old

    if publish_action == "Update an existing tour's details":
        if not have_old_tour:
            st.info("ℹ️ No 'what's already online' data was fetched for this tour - skipping the "
                   "before/after comparison. Go back to Step 3 and click 'Check what's already online "
                   "for this code' to compare against what's currently live before publishing this update.")
            return False

        old_nights, new_nights = old.get("nights"), data.get("nights")
        if old_nights is not None and new_nights is not None and int(old_nights) != int(new_nights):
            blocks_publish = True
            st.error(
                f"🚫 **Number of nights changed: {old_nights} → {new_nights}.** This is treated as a "
                f"DIFFERENT tour, not an update of `{existing_tour_code}` - the itinerary and pricing "
                f"structure is built around a fixed night count, so pushing this through as an update "
                f"would corrupt the existing tour rather than genuinely revise it.\n\n"
                f"**What to do instead:** go back to Step 1 and choose **'Create a brand-new tour "
                f"(+ first option)'**, with a NEW ClosedTour Code and Modality Code for this "
                f"{new_nights}-night variant."
            )
        else:
            st.caption(f"✅ Nights unchanged ({new_nights}) - safe to update in place.")

        old_name, new_name = old.get("name"), data.get("tour_name")
        if old_name and new_name and old_name.strip() != new_name.strip():
            st.info(f"✏️ Name changing: **{old_name}** → **{new_name}**")

        old_stops, new_stops = len(old.get("itinerary") or []), len(data.get("itinerary_destinations") or [])
        if old_stops and new_stops and old_stops != new_stops:
            st.warning(f"🗺️ Itinerary stop count changing: **{old_stops}** → **{new_stops}** stops - "
                      f"double-check the new itinerary reflects a genuine route change, not a misread "
                      f"source document.")

        old_hotels, new_hotels = old.get("hotels"), data.get("hotels_count")
        if old_hotels is not None and new_hotels is not None and old_hotels != new_hotels:
            st.info(f"🏨 Hotel count changing: **{old_hotels}** → **{new_hotels}**")

    elif publish_action == "Update an existing option":
        cache_key = f"_cmp_fetched_option_{modality_code}"
        if cache_key not in st.session_state:
            with st.spinner("Fetching current live pricing for this modality..."):
                st.session_state[cache_key] = client.get_closed_tour_option(
                    supplier_id, working_tour_code or existing_tour_code, modality_code
                )
        old_option = st.session_state[cache_key]
        if isinstance(old_option, dict) and "error" not in old_option:
            old_price_list = old_option.get("priceList", [])
            new_price_list = (payloads.get("tour_option_payload") or {}).get("priceList", [])
            changes = _diff_tour_price_list(old_price_list, new_price_list)
            if not changes:
                st.success("✅ No pricing changes detected for this modality vs. what's currently live.")
            else:
                st.write(f"**{len(changes)} price period(s) changing:**")
                for c in changes:
                    if c["status"] == "changed":
                        st.markdown(f"- 🔁 **{c['period']}**: {c['old']} → **{c['new']}**")
                    elif c["status"] == "added":
                        st.markdown(f"- ➕ **{c['period']}** (new): **{c['new']}**")
                    else:
                        st.markdown(f"- ➖ **{c['period']}** (removed, was {c['old']})")
        else:
            err_detail = old_option.get("message", old_option) if isinstance(old_option, dict) else old_option
            st.warning(f"⚠️ Couldn't fetch this modality's live pricing for comparison: {err_detail}")

    return blocks_publish


def _diff_ticket_option_pricing(old_option, new_payload):
    """
    Compares an existing (GET) ContractTicketModalityVO dict against a
    freshly-built new one (same field names, confirmed against the real
    GET response) - returns a list of human-readable "field: old → new"
    strings for whichever priced fields actually changed. Handles all three
    pricing modes (Distribution/Occupancy/Service).
    """
    changes = []
    old_type = old_option.get("priceType", "DISTRIBUTION")
    new_type = new_payload.get("priceType", "DISTRIBUTION")
    if old_type != new_type:
        changes.append(f"Pricing mode: **{old_type}** → **{new_type}**")

    for field, label in [("baseAdultPrice", "Adult price"), ("baseChildrenPrice", "Child price"),
                         ("baseInfantPrice", "Infant price"), ("baseServicePrice", "Service price")]:
        old_val, new_val = old_option.get(field), new_payload.get(field)
        if old_val is not None and new_val is not None and float(old_val) != float(new_val):
            changes.append(f"{label}: **{old_val}** → **{new_val}**")

    old_occ = {o.get("occupancy"): o.get("amount") for o in (old_option.get("occupancyPrices") or [])}
    new_occ = {o.get("occupancy"): o.get("amount") for o in (new_payload.get("occupancyPrices") or [])}
    if old_occ != new_occ:
        for k in sorted(set(old_occ) | set(new_occ), key=lambda x: (x is None, x)):
            if old_occ.get(k) != new_occ.get(k):
                changes.append(f"Occupancy {k} pax: **{old_occ.get(k, '-')}** → **{new_occ.get(k, '-')}**")

    old_dates = (old_option.get("startDate"), old_option.get("endDate"))
    new_dates = (new_payload.get("startDate"), new_payload.get("endDate"))
    if old_dates != new_dates:
        changes.append(f"Validity dates: **{old_dates[0]} → {old_dates[1]}** → **{new_dates[0]} → {new_dates[1]}**")

    return changes


def render_ticket_update_comparison(publish_action, data, payloads, client, supplier_id,
                                    existing_ticket_code, modality_code):
    """
    Ticket equivalent of render_tour_update_comparison() - see that function
    for the full rationale. Tickets have no "nights" concept (single-day
    excursions), so there's no hard-block rule here - just a clear
    before/after comparison so an update is never a silent overwrite.
    Always returns False (nothing about a Ticket update is hard-blocked).
    """
    st.subheader("🔄 Comparing with what's already online")
    old = st.session_state.get("tk_fetched_ticket")
    have_old_ticket = isinstance(old, dict) and "error" not in old

    if publish_action == "Update an existing ticket's details":
        if not have_old_ticket:
            st.info("ℹ️ No 'what's already online' data was fetched for this ticket - skipping the "
                   "before/after comparison. Go back to Step 3 and click 'Check what's already online "
                   "for this code' to compare against what's currently live before publishing this update.")
            return False

        old_name, new_name = old.get("name"), data.get("ticket_name")
        if old_name and new_name and old_name.strip() != new_name.strip():
            st.info(f"✏️ Name changing: **{old_name}** → **{new_name}**")

        old_duration, new_duration = old.get("duration"), data.get("duration")
        if old_duration is not None and new_duration is not None and old_duration != new_duration:
            st.warning(f"⏱️ Duration changing: **{old_duration}** → **{new_duration}** "
                      f"({data.get('duration_type', '')}) - double-check this is a genuine change, not a "
                      f"misread source value.")

        # The real GET response's geolocation only has latitude/longitude (no city name stored) -
        # compare coordinates instead, with a loose threshold since minor geocoding rounding
        # shouldn't itself read as "the city changed".
        old_geo = old.get("geolocation") or {}
        old_lat, old_lng = old_geo.get("latitude"), old_geo.get("longitude")
        new_lat, new_lng = payloads.get("geolocation_latitude"), payloads.get("geolocation_longitude")
        if None not in (old_lat, old_lng, new_lat, new_lng):
            moved_far = abs(old_lat - new_lat) > 0.05 or abs(old_lng - new_lng) > 0.05  # roughly > ~5km
            if moved_far:
                st.warning(f"📍 Location moved noticeably: was ({old_lat:.4f}, {old_lng:.4f}), now resolves to "
                          f"({new_lat:.4f}, {new_lng:.4f}) for city '{data.get('city', '')}' - a big location "
                          f"shift usually means a genuinely different excursion, not just a detail update. "
                          f"Double-check this is intentional.")

    elif publish_action == "Update an existing ticket option":
        cache_key = f"_cmp_fetched_tk_option_{modality_code}"
        if cache_key not in st.session_state:
            with st.spinner("Fetching current live pricing for this modality..."):
                st.session_state[cache_key] = client.get_ticket_option(supplier_id, existing_ticket_code, modality_code)
        old_option = st.session_state[cache_key]
        if isinstance(old_option, dict) and "error" not in old_option:
            changes = _diff_ticket_option_pricing(old_option, payloads.get("ticket_option_payload") or {})
            if not changes:
                st.success("✅ No pricing changes detected for this modality vs. what's currently live.")
            else:
                st.write(f"**{len(changes)} change(s):**")
                for c in changes:
                    st.markdown(f"- 🔁 {c}")
        else:
            err_detail = old_option.get("message", old_option) if isinstance(old_option, dict) else old_option
            st.warning(f"⚠️ Couldn't fetch this modality's live pricing for comparison: {err_detail}")

    return False


def _summarize_modality_pricing(kind, data, currency):
    """
    Renders a compact, read-only summary of one modality's key facts
    (pricing, operational days, stop sales) inside whatever container is
    currently open (an expander, typically). `kind` is "tour" or "ticket" -
    the two use different pricing shapes.
    """
    if not data:
        st.warning("No pricing data entered yet for this modality.")
        return

    if kind == "tour":
        price_list = data.get("price_list", []) or []
        if price_list:
            st.write(f"**{len(price_list)} price period(s):**")
            for p in price_list:
                price = p.get("price", {}) or {}
                st.caption(
                    f"{p.get('startDate', '?')} → {p.get('endDate', '?')}: "
                    f"Single {price.get('singlePrice', {}).get('amount', '-')}, "
                    f"Double {price.get('doublePrice', {}).get('amount', '-')}, "
                    f"Triple {price.get('triplePrice', {}).get('amount', '-')}, "
                    f"Quad {price.get('quadruplePrice', {}).get('amount', '-')} {currency}"
                )
        else:
            st.warning("No price rows entered yet.")
    else:  # ticket
        price_type = data.get("price_type", "DISTRIBUTION")
        if price_type == "OCCUPANCY":
            occ = data.get("occupancy_prices", []) or []
            st.write(f"**Occupancy pricing** - {len(occ)} tier(s):")
            for o in occ:
                st.caption(f"{o.get('occupancy', '?')} pax: {o.get('amount', '?')} {currency}")
        elif price_type == "SERVICE":
            st.write(f"**Flat service price:** {data.get('base_service_price', 0)} {currency}")
        else:
            st.write(f"**Adult:** {data.get('base_adult_price', 0)} · "
                    f"**Child:** {data.get('base_children_price', 0)} · "
                    f"**Infant:** {data.get('base_infant_price', 0)} {currency}")

    st.caption(f"Operational days: {', '.join(data.get('operational_days', []) or []) or '(not set)'}")
    if data.get("stop_sales"):
        st.caption(f"🚫 Stop sales: {len(data['stop_sales'])} date range(s) blocked")


def render_modalities_review(kind, base_code, base_label, base_data, extra_modalities, currency):
    """
    Consolidated "review everything before you publish" step for when a
    Ticket or ClosedTour is getting MORE THAN ONE modality/service created
    together (a base modality + any "Add another Modality" entries). Each
    extra modality was entered and edited in its own section further up the
    page, which by the time a human reaches the publish button has usually
    scrolled out of view - this shows every modality's code, label, and key
    pricing facts together in one place so nothing entered earlier gets
    forgotten or silently dropped before publishing.
    Only renders anything if there's actually more than one modality -
    a single modality is already fully visible right above the publish
    button, so a review step here would just be a redundant restatement.
    """
    all_modalities = [{"code": base_code, "label": base_label, "data": base_data}]
    for m in extra_modalities:
        all_modalities.append({"code": m.get("code"), "label": m.get("hint") or "(no label)", "data": m.get("data")})

    if len(all_modalities) <= 1:
        return

    st.subheader(f"📋 Review — {len(all_modalities)} modalities will be created together")
    st.caption("Double-check everything below before publishing - once sent, each modality is created "
              "as its own separate call to Travel Compositor.")
    for i, mod in enumerate(all_modalities):
        code_display = mod["code"] or "(code not set)"
        icon = "🟢 Base" if i == 0 else f"➕ Extra {i}"
        with st.expander(f"{icon}: `{code_display}` — {mod['label']}", expanded=False):
            _summarize_modality_pricing(kind, mod["data"], currency)


def _clear_batch_widget_state(prefixes):
    """
    Sweeps st.session_state for every key starting with any of the given
    prefixes and removes it.

    CONFIRMED REAL BUG this exists to prevent: every per-item widget in the
    three batch/queue review flows (multi-tour, multi-ticket, multi-modality)
    is keyed off the item's POSITIONAL index in the queue (e.g.
    key=f"mct_days_{idx}", editable_field's key_suffix=f"_{idx}"). Streamlit
    widgets with a fixed key ignore the value= argument after first render -
    so whenever a positional slot gets reused by a DIFFERENT item, the new
    item displays the previous occupant's stale typed/edited values instead
    of its own data. This happens in two real situations:
      1. Skipping a non-last item: queue.pop(idx) shifts every later item
         down one slot, so the item now AT that slot inherits whatever the
         skipped item's widgets held.
      2. Starting a new batch: a fresh batch's first item is always idx==0,
         so it can inherit leftover state from the PREVIOUS batch's idx==0
         item if only a few top-level keys get cleared.
    Clearing every key under the flow's own prefix(es) - not just a short
    fixed list - closes both holes: the next render has nothing stale to
    fall back on, so every widget genuinely re-reads from the (correct,
    freshly-positioned) `data` dict again.
    """
    for key in list(st.session_state.keys()):
        if any(key.startswith(p) for p in prefixes):
            st.session_state.pop(key, None)


def render_skip_item_button(item_label, queue, idx, queue_session_key, index_session_key, cleanup_keys, button_key,
                            widget_state_prefixes=None):
    """
    Lets a human bail out on ONE item mid-batch-review (e.g. after seeing the
    AI-extracted name/description and deciding "I don't want this one"),
    without having to go through the rest of that item's review (geolocation,
    pricing, etc) or cancel the WHOLE batch. Removes just this item from the
    queue and reruns; if it was the last item left, clears the batch entirely
    since there's nothing left to review or publish.

    `widget_state_prefixes`: prefixes for _clear_batch_widget_state - pass
    this whenever skipping can leave a later item sitting in a queue slot
    whose widget keys were populated by the just-removed item (see that
    function's docstring). Without it, a skip can otherwise show the human
    the WRONG item's stale edited data on the very next render.
    """
    if st.button(f"❌ Don't want this one - remove '{item_label}' from the batch", key=button_key):
        queue.pop(idx)
        if not queue:
            for key in cleanup_keys:
                st.session_state.pop(key, None)
        else:
            st.session_state[queue_session_key] = queue
            st.session_state[index_session_key] = min(idx, len(queue) - 1)
            if widget_state_prefixes:
                _clear_batch_widget_state(widget_state_prefixes)
        st.rerun()


def try_code_variants(call_fn, code):
    """
    Tries `code` (or, if a list, each code in `code`) as given, then falls back
    to toggling the 'CLOSEDTOUR-' prefix on each - we've seen conflicting
    evidence about whether Travel Compositor's lookup needs the human
    ClosedTour/Provider Code (e.g. 'DPS-3') or the internal CLOSEDTOUR-XXXXX
    code returned by creation, so try both rather than betting on just one.

    CONFIRMED FIX (real production failure): the additional-Modality creation
    loop used to call this with ONLY the internal CLOSEDTOUR-XXXXX code (never
    the human tour code) - for at least one real supplier/tour, Travel
    Compositor's lookup only recognized the human code, so every Modality
    after the first 404'd with "Closed tour not found" even though the base
    Modality (which DOES try the human code first) succeeded moments earlier.
    Accepting a list here lets every caller try every known-good candidate,
    not just one.

    Returns (result_dict, code_that_worked_or_None).
    """
    codes = code if isinstance(code, (list, tuple)) else [code]
    variants = []
    for c in codes:
        if not c or c in variants:
            continue
        variants.append(c)
        alt = c[len("CLOSEDTOUR-"):] if c.upper().startswith("CLOSEDTOUR-") else f"CLOSEDTOUR-{c}"
        if alt not in variants:
            variants.append(alt)

    result = None
    for v in variants:
        result = call_fn(v)
        if "error" not in result:
            return result, v
    return result, None


def render_multi_ticket_flow(client, supplier_id, currency, on_request, release_days, tk_url, tk_files, min_passengers=1, max_passengers=9, default_ticket_code=""):
    """
    Batch flow for creating MULTIPLE full Tickets from one document that
    describes several distinct excursions:
    1. Reuse the URL/document(s) already provided above, detect distinct
       excursions, let the human explicitly SELECT which to create + assign
       each its own Ticket Code and Modality Code
    2. Review each SELECTED one individually - its OWN focused AI extraction
       (via a per-item hint), so excursions never get mixed up
    3. Publish all of them SEQUENTIALLY - each gets its own full
       create-ticket -> create-option -> deactivate sequence, with its own
       clear success/failure status (not one opaque batch call)
    """
    if "mt_phase" not in st.session_state:
        st.session_state.mt_phase = "gather"

    # ------------------------------------------------------------------
    # PHASE 1: detect excursions from the source already provided above
    # ------------------------------------------------------------------
    if st.session_state.mt_phase == "gather":
        if not (tk_url or tk_files):
            st.info("Provide a URL and/or upload document(s) above, then click below.")
        if st.button("🔎 Detect Excursions", disabled=not (tk_url or tk_files)):
            with st.spinner("Gathering content and detecting distinct excursions..."):
                try:
                    combined_parts = []
                    doc_raw_images = []
                    doc_image_urls = []
                    seen_image_hashes = set()
                    if tk_url:
                        combined_parts.append(f"--- SOURCE: WEB PAGE ({tk_url}) ---\n{get_page_text(tk_url)}")
                    for uploaded in (tk_files or []):
                        suffix = os.path.splitext(uploaded.name)[1]
                        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                            tmp.write(uploaded.getbuffer())
                            tmp_path = tmp.name
                        combined_parts.append(f"--- SOURCE: UPLOADED DOCUMENT ({uploaded.name}) ---\n{extract_raw_text(tmp_path)}")
                        remaining_budget = 12 - len(doc_raw_images)
                        embedded_images = extract_images(tmp_path, max_images=remaining_budget, seen_hashes=seen_image_hashes) if remaining_budget > 0 else []
                        if embedded_images:
                            for i, (img_bytes, ext) in enumerate(embedded_images):
                                doc_raw_images.append((f"{os.path.splitext(uploaded.name)[0]}_img{i+1}.{ext or 'jpg'}", img_bytes))
                            try:
                                doc_image_urls.extend(upload_images_freeimage(embedded_images))
                            except Exception:
                                pass
                        os.remove(tmp_path)

                    raw_text = "\n\n".join(combined_parts)
                    detected = detect_ticket_variants(raw_text)

                    candidates = []
                    for e in detected:
                        candidates.append({
                            "label": e.get("label", ""), "ticket_code": "",
                            "modality_code": "Standard Private" if e.get("is_private") else "Standard",
                            "selected": True,
                            # Real AI-detected excursion - safe to later restrict extraction
                            # to just this one (see is_genuine_variant usage in PHASE 3 below).
                            "is_genuine_variant": True,
                        })
                    if not candidates:
                        # No real excursion variants detected (single-excursion case) - the
                        # "Ticket Name" typed in PHASE 2 is just a display label, not a real
                        # variant to filter the source by - is_genuine_variant stays False so
                        # PHASE 3 never sends it to the AI as a variant filter (doing so
                        # caused the AI to search for a nonexistent named variant and return
                        # an empty extraction - same bug as the ClosedTour flow had).
                        # Prefill the Ticket Code from what was already entered back in Step 3
                        # (default_ticket_code) so the human doesn't have to type it again here.
                        candidates = [{"label": "", "ticket_code": default_ticket_code, "modality_code": "Standard",
                                      "selected": True, "is_genuine_variant": False}]

                    if len(doc_image_urls) >= len(doc_raw_images):
                        doc_raw_images = []

                    st.session_state.mt_raw_text = raw_text
                    st.session_state.mt_candidates = candidates
                    st.session_state.mt_doc_raw_images = doc_raw_images
                    st.session_state.mt_hosted_image_candidates = list(dict.fromkeys((get_page_images(tk_url) if tk_url else []) + doc_image_urls))
                    st.session_state.mt_phase = "prepare_queue"
                    st.rerun()
                except Exception as e:
                    st.error(f"Detection failed: {friendly_error_message(e)}")
        return

    # ------------------------------------------------------------------
    # PHASE 2: explicitly SELECT which excursions to create as Tickets
    # ------------------------------------------------------------------
    if st.session_state.mt_phase == "prepare_queue":
        candidates = st.session_state.mt_candidates
        single_ticket = len(candidates) == 1

        # Same distinction as the ClosedTour batch flow: an "excursion" here
        # means a genuinely different Ticket PRODUCT, never a "Modality"
        # (the pricing option within one Ticket) - most documents describe
        # only ONE excursion, and the wording must say so plainly rather
        # than implying variants were found when none were.
        if single_ticket:
            st.subheader("Set up this Ticket")
            st.caption("This document describes one excursion - no other variants were found, so nothing "
                      "to choose between here. The Ticket Code is already carried over from Step 3 below "
                      "(edit it here if you want to change it). It still needs a Modality Code before it "
                      "can be created (a 'Modality', Travel Compositor's own term, is the pricing option "
                      "for the Ticket, e.g. 'Standard' - you can add more Modalities for this same Ticket "
                      "in the next step).")
        else:
            st.subheader(f"{len(candidates)} excursions detected - choose which ones to create as Tickets")
            st.caption("The AI found what look like several different excursions below - each ticked row "
                      "becomes its own separate Ticket. Untick any row you don't actually want. For each "
                      "ticked row, fill in the two code fields on the right (hover the ⓘ next to each for "
                      "what it means).")

        for i, cand in enumerate(candidates):
            ccol1, ccol2, ccol3, ccol4 = st.columns([1, 3, 2, 2])
            with ccol1:
                cand["selected"] = st.checkbox("Include", value=cand["selected"], key=f"mt_sel_{i}")
            with ccol2:
                label_text = "Ticket Name" if single_ticket else "Excursion"
                cand["label"] = st.text_input(label_text, value=cand["label"], key=f"mt_label_{i}")
            with ccol3:
                cand["ticket_code"] = st.text_input(
                    "Ticket Code", value=cand["ticket_code"], key=f"mt_code_{i}", placeholder="e.g. BALI-T1",
                    help="Your own reference code for THIS ticket - make it up yourself, e.g. 'BALI-T1'."
                )
            with ccol4:
                cand["modality_code"] = st.text_input(
                    "Modality Code", value=cand["modality_code"], key=f"mt_modcode_{i}",
                    help="The name of the pricing option for this ticket, e.g. 'Standard' or 'Standard "
                         "Private'. Cannot contain the characters / \\ + or - (Travel Compositor's system "
                         "rejects those in this field)."
                )

        if st.button("➕ Add another excursion manually"):
            candidates.append({"label": "", "ticket_code": "", "modality_code": "Standard",
                              "selected": True, "is_genuine_variant": False})
            st.rerun()

        invalid_codes = []
        missing_codes = []
        new_queue = []
        seen_ticket_codes = {}
        for cand in candidates:
            if not cand["selected"]:
                continue
            code = cand["ticket_code"].strip()
            mod_code = cand["modality_code"].strip()
            if not code or not mod_code:
                missing_codes.append(cand["label"] or "(unnamed excursion)")
                continue
            if any(c in mod_code for c in ["/", "\\", "+", "-"]):
                invalid_codes.append(mod_code)
                continue
            seen_ticket_codes.setdefault(code, []).append(cand["label"] or "(unnamed excursion)")
            new_queue.append({"label": cand["label"], "ticket_code": code, "modality_code": mod_code, "data": None,
                             "confirmed": False, "is_genuine_variant": cand.get("is_genuine_variant", False)})

        duplicate_codes = {code: labels for code, labels in seen_ticket_codes.items() if len(labels) > 1}

        if missing_codes:
            st.error(f"🚫 These selected excursions are missing a Ticket Code or Modality Code and were "
                    f"excluded - enter one for each before continuing: {missing_codes}")
        if invalid_codes:
            st.error(f"🚫 These Modality Codes contain invalid characters (/, \\, +, -) and were excluded: {invalid_codes}")
        if duplicate_codes:
            for code, labels in duplicate_codes.items():
                st.error(f"🚫 Ticket Code `{code}` is used by more than one selected excursion ({', '.join(labels)}) "
                        f"- each Ticket needs its own unique code.")

        for q in new_queue:
            existing_check = check_code_availability(client, "ticket", supplier_id, q["ticket_code"])
            if existing_check and existing_check["exists"]:
                st.error(f"🚫 Ticket Code `{q['ticket_code']}` ({q['label'] or '(unnamed)'}) is ALREADY TAKEN "
                        f"by an existing ticket (\"{existing_check.get('name') or '(unnamed)'}\") - choose a "
                        f"different code before publishing, or this will fail.")

        ready_to_review = new_queue and not missing_codes and not duplicate_codes
        st.caption(f"**{len(new_queue)}** ticket(s) ready to review." if ready_to_review else
                  "Fix the issues above before continuing.")

        if st.button("➡️ Start Reviewing", type="primary", disabled=not ready_to_review):
            st.session_state.mt_queue = new_queue
            st.session_state.mt_queue_index = 0
            st.session_state.mt_phase = "reviewing"
            st.rerun()
        return

    # ------------------------------------------------------------------
    # PHASE 3: review each selected ticket individually, one at a time
    # ------------------------------------------------------------------
    if st.session_state.mt_phase == "reviewing":
        idx = st.session_state.mt_queue_index
        queue = st.session_state.mt_queue
        current = queue[idx]

        st.subheader(f"Reviewing ticket {idx + 1} of {len(queue)}: **{current['label'] or current['ticket_code']}** (code: {current['ticket_code']})")
        st.progress(idx / len(queue))
        with st.expander("Not what you wanted?"):
            if st.button("🔙 Cancel this batch - return to single-Ticket flow", key=f"mt_cancel_{idx}"):
                for key in ["mt_phase", "mt_raw_text", "mt_candidates", "mt_queue", "mt_queue_index",
                           "mt_doc_raw_images", "mt_hosted_image_candidates"]:
                    st.session_state.pop(key, None)
                _clear_batch_widget_state(SHARED_WIDGET_STATE_PREFIXES)
                st.rerun()

        if current["data"] is None:
            # Same fix as the ClosedTour batch flow: only pass a variant_hint when this
            # label came from a REAL AI-detected excursion (is_genuine_variant) - the
            # human-typed "Ticket Name" (single-excursion case) is just a display label,
            # not a real variant present in the source, and passing it as a filter caused
            # the AI to find no match and return an empty extraction.
            variant_hint = current["label"] if current.get("is_genuine_variant") else None
            with st.spinner(f"Extracting details{f' focused on ' + repr(current['label']) if variant_hint else ''}..."):
                # Same crash-prevention as the ClosedTour batch flow - never leave a
                # call that can genuinely fail (rate limit, network hiccup) unguarded.
                try:
                    current["data"] = extract_ticket_data(st.session_state.mt_raw_text, variant_hint=variant_hint)
                    current["data"]["image_urls"] = [FALLBACK_IMAGE]
                except Exception as e:
                    st.error(f"⚠️ Couldn't extract details for this excursion: {friendly_error_message(e)}")
                    if st.button("🔄 Retry extraction", key=f"mt_retry_extract_{idx}"):
                        st.rerun()
                    return

        data = current["data"]

        editable_field("Ticket name", data, "ticket_name", widget="text_input", key_suffix=f"_{idx}")
        editable_field("Description", data, "description", widget="html_text_area", height=120, key_suffix=f"_{idx}")

        render_skip_item_button(
            current['label'] or current['ticket_code'], queue, idx,
            "mt_queue", "mt_queue_index",
            ["mt_phase", "mt_raw_text", "mt_candidates", "mt_queue", "mt_queue_index",
             "mt_doc_raw_images", "mt_hosted_image_candidates"],
            button_key=f"mt_skip_{idx}",
            widget_state_prefixes=SHARED_WIDGET_STATE_PREFIXES
        )

        editable_field("City", data, "city", widget="text_input", key_suffix=f"_{idx}")

        # ------------------------------------------------------------------
        # Geolocation resolve + human confirm - REQUIRED before this ticket
        # can be confirmed. Without this, an unresolved/wrong city silently
        # fails at publish time with a raw "GeolocationVO validation error"
        # and no way to fix it from inside the batch flow.
        # ------------------------------------------------------------------
        st.markdown(f"**📍 Location for {current['label'] or current['ticket_code']}**")
        mt_city = data.get("city", "")
        if data.get("manual_latitude") is not None and data.get("manual_longitude") is not None:
            mt_geo = {"latitude": data["manual_latitude"], "longitude": data["manual_longitude"],
                      "display_name": mt_city, "valid": True}
        else:
            mt_geo = geocode(mt_city)  # cached in geocoding_client - cheap to call every rerun

        if mt_geo.get("valid"):
            mt_lat, mt_lng = mt_geo["latitude"], mt_geo["longitude"]
            mt_maps_link = f"https://www.google.com/maps?q={mt_lat},{mt_lng}"
            st.markdown(
                f"<div style='background-color:#d4edda; color:#155724; padding:8px 12px; "
                f"border-radius:4px;'>📍 Resolved: <strong>{mt_geo.get('display_name') or mt_city}</strong>"
                f"<br>Coordinates: {mt_lat:.6f}, {mt_lng:.6f} — "
                f"<a href='{mt_maps_link}' target='_blank'>Open in Google Maps to verify</a></div>",
                unsafe_allow_html=True
            )
            st.caption("Geocoding data © OpenStreetMap contributors")
        else:
            st.markdown(
                "<div style='background-color:#f8d7da; color:#721c24; padding:6px 12px; "
                "border-radius:4px;'>❌ Geolocation NOT resolved - the City name may not match a known "
                "location. Search below or enter coordinates manually.</div>",
                unsafe_allow_html=True
            )

        with st.expander("🔍 Search for a better match / fix this location", expanded=not mt_geo.get("valid")):
            mt_geo_query = st.text_input("Search for a location", value=mt_city, key=f"mt_geo_query_{idx}")
            if st.button("🔎 Search", key=f"mt_geo_search_btn_{idx}"):
                with st.spinner("Searching..."):
                    current["geo_search_results"] = geocode_search(mt_geo_query, limit=5)
            if current.get("geo_search_results"):
                for gi, candidate in enumerate(current["geo_search_results"]):
                    ggcol1, ggcol2 = st.columns([4, 1])
                    with ggcol1:
                        st.write(f"**{candidate['display_name']}**")
                        st.caption(f"{candidate['latitude']:.6f}, {candidate['longitude']:.6f} ({candidate.get('type', '')})")
                    with ggcol2:
                        if st.button("Use this", key=f"mt_geo_pick_{idx}_{gi}"):
                            data["manual_latitude"] = candidate["latitude"]
                            data["manual_longitude"] = candidate["longitude"]
                            current["geo_confirmed"] = False
                            current["geo_search_results"] = None
                            st.rerun()

            st.markdown("**Or enter coordinates manually:**")
            mgcol1, mgcol2 = st.columns(2)
            with mgcol1:
                mt_man_lat = st.number_input("Latitude", value=data.get("manual_latitude"), format="%.6f", key=f"mt_geo_manlat_{idx}", placeholder="e.g. 27.394900")
            with mgcol2:
                mt_man_lng = st.number_input("Longitude", value=data.get("manual_longitude"), format="%.6f", key=f"mt_geo_manlng_{idx}", placeholder="e.g. 33.678400")
            if st.button("📍 Use these coordinates", key=f"mt_geo_manual_btn_{idx}", disabled=mt_man_lat is None or mt_man_lng is None):
                data["manual_latitude"] = mt_man_lat
                data["manual_longitude"] = mt_man_lng
                current["geo_confirmed"] = False
                st.rerun()

        current["geo_confirmed"] = st.checkbox(
            "✅ I've checked this location and it's correct for this ticket",
            value=current.get("geo_confirmed", False), key=f"mt_geo_confirm_{idx}",
            disabled=not mt_geo.get("valid")
        )
        if not mt_geo.get("valid"):
            st.info("👆 Resolve the location above before this ticket can be confirmed.")
        elif not current["geo_confirmed"]:
            st.info("👆 Please check the location above and confirm it's correct.")

        st.markdown(f"**Images for {current['label'] or current['ticket_code']}**")
        if data.get("image_urls") == [FALLBACK_IMAGE] or not data.get("image_urls"):
            st.caption("⚠️ No real image picked yet - using a generic placeholder. Pick at least one real "
                      "image below (Travel Compositor requires at least one image per Ticket).")
        else:
            st.caption(f"{len([u for u in data.get('image_urls', []) if u != FALLBACK_IMAGE])} image(s) selected.")

        def _mt_add_url_images():
            selected = render_url_image_picker(st.session_state.mt_hosted_image_candidates, f"mt_found_{idx}")
            if selected:
                current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                data["image_urls"] = current_imgs + selected
                return len(selected)
            return 0

        render_closable_image_section(
            bool(st.session_state.get("mt_hosted_image_candidates")),
            f"🖼️ Images found in your document/page ({len(st.session_state.get('mt_hosted_image_candidates') or [])})",
            f"mt_found_{idx}_closed", _mt_add_url_images
        )

        def _mt_add_doc_image():
            added = render_doc_image_picker(st.session_state.mt_doc_raw_images, f"mt_doc_{idx}")
            if added:
                current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                data["image_urls"] = current_imgs + [added]
                return 1
            return 0

        render_closable_image_section(
            bool(st.session_state.get("mt_doc_raw_images")),
            f"📥 Images needing hosting ({len(st.session_state.get('mt_doc_raw_images') or [])})",
            f"mt_doc_{idx}_closed", _mt_add_doc_image
        )

        mt_default_query = current["label"] or data.get("ticket_name", "") or data.get("city", "")

        def _mt_add_pexels():
            selected = render_stock_photo_picker("Pexels", search_images, mt_default_query, f"mt_pexels_{idx}")
            if selected:
                current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                data["image_urls"] = current_imgs + selected
                return len(selected)
            return 0

        render_closable_image_section(True, "🖼️ Search free stock photos (Pexels)", f"mt_pexels_{idx}_closed", _mt_add_pexels)

        def _mt_add_pixabay():
            selected = render_stock_photo_picker("Pixabay", search_images_pixabay, mt_default_query, f"mt_pixabay_{idx}")
            if selected:
                current_imgs = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                data["image_urls"] = current_imgs + selected
                return len(selected)
            return 0

        render_closable_image_section(True, "🖼️ Search free stock photos (Pixabay)", f"mt_pixabay_{idx}_closed", _mt_add_pixabay)

        editable_field("Duration (hours)", data, "duration", widget="number_input", key_suffix=f"_{idx}")

        acol1, acol2 = st.columns(2)
        with acol1:
            data["child_age_min"] = st.number_input("Min Child Age", min_value=0, max_value=17,
                                                     value=int(data.get("child_age_min", 2) or 2), key=f"mt_min_child_age_{idx}")
        with acol2:
            data["child_age_max"] = st.number_input("Max Child Age", min_value=0, max_value=17,
                                                     value=int(data.get("child_age_max", 12) or 12), key=f"mt_max_child_age_{idx}")

        inc_df = pd.DataFrame([{"Item": x} for x in data.get("includes", [])]) if data.get("includes") else pd.DataFrame(columns=["Item"])
        def _save_mt_includes(edf, data=data):
            data["includes"] = [str(r.get("Item") or "").strip() for _, r in edf.iterrows() if str(r.get("Item", "") or "").strip()]
        editable_table("Includes", inc_df, f"mt_includes_{idx}", on_save=_save_mt_includes)

        exc_df = pd.DataFrame([{"Item": x} for x in data.get("excludes", [])]) if data.get("excludes") else pd.DataFrame(columns=["Item"])
        def _save_mt_excludes(edf, data=data):
            data["excludes"] = [str(r.get("Item") or "").strip() for _, r in edf.iterrows() if str(r.get("Item", "") or "").strip()]
        editable_table("Excludes", exc_df, f"mt_excludes_{idx}", on_save=_save_mt_excludes)

        mp_default = [{"Description": m.get("description", "")} for m in data.get("meeting_points", [])] or [{"Description": "Hotel Lobby"}]
        mp_df = pd.DataFrame(mp_default)
        def _save_mt_mp(edf, data=data):
            data["meeting_points"] = [
                {"description": str(r.get("Description") or "").strip(), "variable_location": str(r.get("Description") or "").strip().lower() == "hotel lobby"}
                for _, r in edf.iterrows() if str(r.get("Description", "") or "").strip()
            ]
        editable_table("Meeting Points", mp_df, f"mt_mp_{idx}", on_save=_save_mt_mp)

        st.markdown("**Start Time(s)**")
        tt_df = pd.DataFrame([{"Time (HH:MM)": t} for t in data.get("time_tables", [])]) if data.get("time_tables") else pd.DataFrame(columns=["Time (HH:MM)"])
        def _save_mt_timetables(edf, data=data):
            data["time_tables"] = _clean_time_table_rows(edf)
        editable_table("Start Time(s)", tt_df, f"mt_timetables_{idx}", on_save=_save_mt_timetables)
        if not data.get("time_tables"):
            st.caption("ℹ️ No start time set yet - optional, but add one if the excursion has a fixed departure time.")

        data["operational_days"] = st.multiselect(
            "Operational Days", ALL_WEEKDAYS, default=data.get("operational_days", ALL_WEEKDAYS), key=f"mt_op_days_{idx}"
        )

        st.markdown(f"**Pricing (in {currency})**")
        st.caption("A Ticket Modality holds ONE price setup + ONE validity date range (not a seasonal table). "
                  "For holiday/seasonal price differences, use dated Supplements below instead.")
        mt_price_type = st.radio(
            "Pricing Mode", ["DISTRIBUTION", "OCCUPANCY", "SERVICE"],
            index=["DISTRIBUTION", "OCCUPANCY", "SERVICE"].index(data.get("price_type") or "OCCUPANCY"),
            format_func=lambda x: {
                "DISTRIBUTION": "Distribution - price per person (Adult/Child/Infant)",
                "OCCUPANCY": "Occupancy - price varies by group size (infants free, not counted)",
                "SERVICE": "Service - one flat total price regardless of headcount",
            }[x],
            key=f"mt_price_type_{idx}"
        )
        data["price_type"] = mt_price_type
        if mt_price_type != "DISTRIBUTION":
            st.warning("⚠️ UNCONFIRMED whether Travel Compositor's API accepts this pricing mode for "
                      "Tickets - test carefully with a real publish before relying on it.")

        if mt_price_type == "DISTRIBUTION":
            pcol1, pcol2, pcol3 = st.columns(3)
            with pcol1:
                data["base_adult_price"] = st.number_input("Adult Price", min_value=0.0, value=float(data.get("base_adult_price", 0) or 0), key=f"mt_adult_{idx}")
            with pcol2:
                data["base_children_price"] = st.number_input("Child Price", min_value=0.0, value=float(data.get("base_children_price", 0) or 0), key=f"mt_child_{idx}")
            with pcol3:
                data["base_infant_price"] = st.number_input("Infant Price", min_value=0.0, value=float(data.get("base_infant_price", 0) or 0), key=f"mt_infant_{idx}")
        elif mt_price_type == "SERVICE":
            data["base_service_price"] = st.number_input(
                "Total Service Price (flat, regardless of group size)", min_value=0.0,
                value=float(data.get("base_service_price", 0) or 0), key=f"mt_service_price_{idx}"
            )
        elif mt_price_type == "OCCUPANCY":
            st.caption("Each row is an EXACT number of paying passengers (not a range) with its price - "
                      "infants are always free and excluded automatically. If your source shows a range "
                      "like '3-5' at one price, add ONE row per exact number (3, 4, and 5) all with that "
                      "same price - use the button below to auto-expand a range for you.")
            mt_occ_rows = [{"Occupancy (exact # pax)": o.get("occupancy", 2), "Price": o.get("amount", 0)}
                          for o in data.get("occupancy_prices", [])] or [{"Occupancy (exact # pax)": 2, "Price": 0}]
            mt_occ_df = pd.DataFrame(mt_occ_rows)
            def _save_mt_occupancy(edf, data=data):
                data["occupancy_prices"] = [
                    {"occupancy": int(r.get("Occupancy (exact # pax)", 2) or 2), "amount": float(r.get("Price", 0) or 0)}
                    for _, r in edf.iterrows()
                ]
            editable_table("Occupancy Price Tiers", mt_occ_df, f"mt_occupancy_{idx}", on_save=_save_mt_occupancy)

            mt_occ_has_solo = any(o.get("occupancy") == 1 for o in data.get("occupancy_prices", []))
            if not mt_occ_has_solo:
                st.warning("⚠️ No price for **1 pax (solo traveler)** yet - this needs to be added manually. "
                          "Solo pricing is often different from the per-person rate when sharing, so it "
                          "can't be safely defaulted from the other rows - check the source or confirm "
                          "with the supplier.")

            with st.expander("🔢 Auto-expand a range (e.g. '3-5' at one price) into individual rows"):
                mrcol1, mrcol2, mrcol3 = st.columns(3)
                with mrcol1:
                    mt_range_start = st.number_input("From", min_value=1, value=1, key=f"mt_occ_range_start_{idx}")
                with mrcol2:
                    mt_range_end = st.number_input("To", min_value=1, value=1, key=f"mt_occ_range_end_{idx}")
                with mrcol3:
                    mt_range_price = st.number_input("Price (same for all)", min_value=0.0, value=0.0, key=f"mt_occ_range_price_{idx}")
                if st.button("➕ Add this range as individual rows", key=f"mt_occ_range_add_{idx}") and mt_range_end >= mt_range_start:
                    mt_existing = list(data.get("occupancy_prices", []))
                    for n in range(int(mt_range_start), int(mt_range_end) + 1):
                        mt_existing.append({"occupancy": n, "amount": mt_range_price})
                    data["occupancy_prices"] = mt_existing
                    st.rerun()

        dcol1, dcol2 = st.columns(2)
        with dcol1:
            data["start_date"] = st.text_input("Valid From (YYYY-MM-DD)", value=data.get("start_date", ""), key=f"mt_start_date_{idx}")
        with dcol2:
            data["end_date"] = st.text_input("Valid Until (YYYY-MM-DD)", value=data.get("end_date", ""), key=f"mt_end_date_{idx}")
        if data.get("pricing_notes"):
            st.warning(f"⚠️ {data['pricing_notes']}")

        with st.expander(f"Stop Sales - {current['label'] or current['ticket_code']}"):
            mt_ss_json = st.text_area(
                "stopSales (JSON array)", json.dumps(data.get("stop_sales", []), indent=2), key=f"mt_stops_{idx}"
            )
            try:
                data["stop_sales"] = json.loads(mt_ss_json)
            except json.JSONDecodeError as e:
                st.error(f"stopSales isn't valid JSON: {e}")

        st.markdown(f"**Optional Add-ons (Supplements) - {current['label'] or current['ticket_code']}**")
        st.caption("⚠️ Ticket Supplements are always independently stackable - a customer can tick ANY "
                  "combination, and prices simply add up. For anything that should be an ALTERNATIVE (only "
                  "one of several choices), or different guide languages with their own full price table, "
                  "use a separate Modality instead below, never a supplement.")
        mt_supp_rows = [
            {"Name": s.get("name", ""), "Adult": s.get("adult_price", 0), "Child": s.get("children_price", 0),
             "Infant": s.get("infant_price", 0), "Start": s.get("travel_start_date", ""), "End": s.get("travel_end_date", "")}
            for s in data.get("supplements", [])
        ]
        mt_supp_df = pd.DataFrame(mt_supp_rows) if mt_supp_rows else pd.DataFrame(columns=["Name", "Adult", "Child", "Infant", "Start", "End"])
        def _save_mt_supplements(edf, data=data):
            new_supp = []
            for _, r in edf.iterrows():
                name = str(r.get("Name", "") or "").strip()
                if not name:
                    continue
                new_supp.append({
                    "name": name, "adult_price": float(r.get("Adult", 0) or 0),
                    "children_price": float(r.get("Child", 0) or 0), "infant_price": float(r.get("Infant", 0) or 0),
                    "travel_start_date": str(r.get("Start", "") or "").strip(), "travel_end_date": str(r.get("End", "") or "").strip(),
                })
            data["supplements"] = new_supp
        editable_table(f"Supplements - {current['label'] or current['ticket_code']}", mt_supp_df, f"mt_supplements_{idx}", on_save=_save_mt_supplements)

        st.markdown(f"**➕ Additional Modalities for {current['label'] or current['ticket_code']} (optional)**")
        st.caption("Add more variants of THIS ticket now (e.g. one per guide language) - all get created "
                  "together with this ticket's single deactivation, so you don't need to manually reactivate "
                  "it afterward. Common case: different guide languages must each be their own Modality, "
                  "never a supplement.")
        if "extra_modalities" not in current:
            current["extra_modalities"] = []

        for j, mod in enumerate(current["extra_modalities"]):
            st.markdown(f"*Modality {j + 2}*")
            mcol1, mcol2, mcol3 = st.columns([2, 2, 1])
            with mcol1:
                mod["code"] = st.text_input("Modality Code", value=mod["code"], key=f"mt_extramod_code_{idx}_{j}")
            with mcol2:
                mod["hint"] = st.text_input("Focus Hint (e.g. 'German Speaking Guide')", value=mod["hint"], key=f"mt_extramod_hint_{idx}_{j}")
            with mcol3:
                st.write("")
                if st.button("🗑️ Remove", key=f"mt_extramod_remove_{idx}_{j}"):
                    current["extra_modalities"].pop(j)
                    st.rerun()

            if any(c in (mod["code"] or "") for c in ["/", "\\", "+", "-"]):
                st.error(f"🚫 Modality Code '{mod['code']}' contains invalid characters (/, \\, +, -).")

            if st.button(f"🔎 Extract pricing focused on '{mod['hint'] or mod['code'] or 'this modality'}'", key=f"mt_extramod_extract_{idx}_{j}", disabled=not mod["code"]):
                with st.spinner("Extracting..."):
                    mod["data"] = extract_ticket_option_only_data(st.session_state.mt_raw_text, human_hint=mod["hint"])
                    # This quick-add UI only shows Adult/Child/Infant fields (Distribution), same as the
                    # main batch pricing above - force it explicitly so the extraction default (Occupancy,
                    # see Feature 3) doesn't leave these prices getting zeroed out by builder.py's
                    # per-mode zeroing (Bug 2 fix).
                    mod["data"]["price_type"] = "DISTRIBUTION"
                    st.rerun()

            if mod["data"]:
                epcol1, epcol2, epcol3 = st.columns(3)
                with epcol1:
                    mod["data"]["base_adult_price"] = st.number_input("Adult Price", min_value=0.0, value=float(mod["data"].get("base_adult_price", 0) or 0), key=f"mt_extramod_adult_{idx}_{j}")
                with epcol2:
                    mod["data"]["base_children_price"] = st.number_input("Child Price", min_value=0.0, value=float(mod["data"].get("base_children_price", 0) or 0), key=f"mt_extramod_child_{idx}_{j}")
                with epcol3:
                    mod["data"]["base_infant_price"] = st.number_input("Infant Price", min_value=0.0, value=float(mod["data"].get("base_infant_price", 0) or 0), key=f"mt_extramod_infant_{idx}_{j}")
                edcol1, edcol2 = st.columns(2)
                with edcol1:
                    mod["data"]["start_date"] = st.text_input("Valid From (YYYY-MM-DD)", value=mod["data"].get("start_date", data.get("start_date", "")), key=f"mt_extramod_start_{idx}_{j}")
                with edcol2:
                    mod["data"]["end_date"] = st.text_input("Valid Until (YYYY-MM-DD)", value=mod["data"].get("end_date", data.get("end_date", "")), key=f"mt_extramod_end_{idx}_{j}")
            else:
                st.info("Click 'Extract pricing' above to get started for this modality.")
            st.divider()

        if st.button("➕ Add another Modality", key=f"mt_add_extramod_{idx}"):
            current["extra_modalities"].append({"code": "", "hint": "", "data": None})
            st.rerun()

        st.markdown(f"**🤖 Tell AI what to fix - {current['label'] or current['ticket_code']}**")
        mt_clarify_q = st.text_input("Your message", key=f"mt_clarify_input_{idx}")
        if st.button("Send", disabled=not mt_clarify_q.strip(), key=f"mt_clarify_send_{idx}"):
            with st.spinner("Thinking..."):
                result = apply_clarification(st.session_state.mt_raw_text, data, mt_clarify_q)
                st.session_state[f"mt_clarify_result_{idx}"] = result
                if result.get("changes"):
                    for field_name, new_value in result["changes"].items():
                        data[field_name] = new_value
                    if "operational_days" in result["changes"]:
                        st.session_state.pop(f"mt_op_days_{idx}", None)
                st.rerun()
        if st.session_state.get(f"mt_clarify_result_{idx}"):
            r = st.session_state[f"mt_clarify_result_{idx}"]
            st.info(r.get("summary", ""))
            if r.get("changes"):
                st.caption(f"✅ Applied changes to: {', '.join(r['changes'].keys())}")

        if mt_price_type == "SERVICE":
            price_valid = bool(data.get("base_service_price", 0))
        elif mt_price_type == "OCCUPANCY":
            price_valid = bool(data.get("occupancy_prices")) and any(o.get("amount", 0) for o in data.get("occupancy_prices", []))
        else:
            price_valid = any([data.get("base_adult_price", 0), data.get("base_children_price", 0), data.get("base_infant_price", 0)])
        can_continue = price_valid and mt_geo.get("valid") and current.get("geo_confirmed")

        is_last = idx == len(queue) - 1
        btn_label = "✅ Confirm this Ticket & Finish Review" if is_last else "✅ Confirm this Ticket & Continue →"
        if st.button(btn_label, type="primary", disabled=not can_continue):
            current["confirmed"] = True
            if is_last:
                st.session_state.mt_phase = "publishing"
            else:
                st.session_state.mt_queue_index += 1
            st.rerun()
        if not price_valid:
            st.info("Add at least one non-zero price before continuing.")
        return

    # ------------------------------------------------------------------
    # PHASE 4: publish all confirmed Tickets, ONE BY ONE
    # ------------------------------------------------------------------
    if st.session_state.mt_phase == "publishing":
        queue = st.session_state.mt_queue
        if "mt_failed_items" not in st.session_state:
            st.session_state.mt_failed_items = []
        st.subheader(f"Ready to publish {len(queue)} Tickets - one by one")
        for q in queue:
            extra_count = len(q.get("extra_modalities", []))
            extra_note = f" + {extra_count} additional modalit{'y' if extra_count == 1 else 'ies'}" if extra_count else ""
            st.write(f"- **{q['ticket_code']}** ({q['label']}) - Modality: {q['modality_code']}{extra_note}")

        mt_activation_choice = st.radio(
            "After publishing, should these Tickets be Active or Inactive (draft)?",
            ["Inactive (draft) - recommended, review inside Travel Compositor before they go live",
             "Active - live immediately"],
            index=0, key="mt_activation_choice"
        )
        mt_publish_as_active = mt_activation_choice.startswith("Active")

        if st.button("🚀 Publish all (one by one)", type="primary"):
            for q in queue:
                with st.spinner(f"Publishing '{q['ticket_code']}'..."):
                    try:
                        pre_config = TicketHumanPreConfig(
                            supplier_id=supplier_id, ticket_code=q["ticket_code"], currency=currency,
                            modality_code=q["modality_code"], on_request=on_request,
                            days_available_before_release=release_days, min_passengers=min_passengers, max_passengers=max_passengers
                        )
                        payloads = build_ticket_payloads(pre_config, q["data"], client)
                        if payloads["main_ticket_error"] or payloads["ticket_option_error"]:
                            show_publish_error(f"prepare **{q['ticket_code']}**'s payload",
                                              payloads['main_ticket_error'] or payloads['ticket_option_error'])
                            continue
                        if not payloads["geolocation_resolved"]:
                            st.error(f"❌ **{q['ticket_code']}**: geolocation not resolved - skipped. Fix the City "
                                    f"field and create this one individually via the normal Create flow instead.")
                            continue

                        creation_payload = dict(payloads["main_ticket_payload"])
                        creation_payload["active"] = True
                        result = client.create_ticket(supplier_id, creation_payload)
                        if "error" in result:
                            show_publish_error(f"create **{q['ticket_code']}**", result)
                            continue
                        real_code = result.get("code", payloads["main_ticket_code"])

                        # api_client.py's _request() already retries every write call (incl. this
                        # POST) up to 6 times internally now - no need to also loop here.
                        option_result = client.create_ticket_option(supplier_id, real_code, payloads["ticket_option_payload"])
                        if "error" in option_result:
                            show_publish_error(f"create **{q['ticket_code']}**'s option (created as `{real_code}`)", option_result)
                            # The ticket itself WAS created (real_code) and is still ACTIVE - only the
                            # option failed. Don't force the human to abandon the whole batch and start
                            # over: remember this item (with its real_code, and the SAME editable data
                            # dict) so they can adjust it and retry just this option below, without
                            # re-running the other tickets or losing their edits.
                            st.session_state.mt_failed_items.append({
                                "ticket_code": q["ticket_code"], "label": q["label"], "real_code": real_code,
                                "modality_code": q["modality_code"], "data": q["data"],
                            })
                            continue
                        else:
                            st.success(f"✅ **{q['ticket_code']}**: base modality '{q['modality_code']}' created.")

                        for mod in q.get("extra_modalities", []):
                            if not mod.get("code") or not mod.get("data"):
                                st.warning(f"⚠️ **{q['ticket_code']}**: skipped an extra modality - missing code or pricing data.")
                                continue
                            with st.spinner(f"Creating '{q['ticket_code']}' modality '{mod['code']}'..."):
                                try:
                                    mod_pre_config = TicketHumanPreConfig(
                                        supplier_id=supplier_id, ticket_code=q["ticket_code"], currency=currency,
                                        modality_code=mod["code"], on_request=on_request,
                                        days_available_before_release=release_days, min_passengers=min_passengers, max_passengers=max_passengers
                                    )
                                    mod_payloads = build_ticket_payloads(mod_pre_config, mod["data"], client)
                                    if mod_payloads["ticket_option_error"]:
                                        show_publish_error(f"prepare **{q['ticket_code']}** modality '{mod['code']}'", mod_payloads["ticket_option_error"])
                                        continue
                                    mod_option_result = client.create_ticket_option(supplier_id, real_code, mod_payloads["ticket_option_payload"])
                                    if "error" in mod_option_result:
                                        show_publish_error(f"create **{q['ticket_code']}** modality '{mod['code']}'", mod_option_result)
                                    else:
                                        st.success(f"✅ **{q['ticket_code']}**: modality '{mod['code']}' created.")
                                except Exception as e:
                                    show_publish_error(f"create **{q['ticket_code']}** modality '{mod['code']}' (unexpected error - skipped, rest continues)", str(e))
                                    continue

                        if mt_publish_as_active:
                            st.success(f"✅ **{q['ticket_code']}** published and left ACTIVE as `{real_code}` (as chosen above).")
                        else:
                            deactivate_payload = dict(creation_payload)
                            deactivate_payload["active"] = False
                            deactivate_payload["code"] = real_code
                            deactivate_result = client.update_ticket(supplier_id, deactivate_payload)
                            if "error" in deactivate_result:
                                st.warning(f"⚠️ **{q['ticket_code']}**: created and published, but switching back to "
                                          f"inactive failed - {deactivate_result}")
                            else:
                                st.success(f"✅ **{q['ticket_code']}** published successfully as `{real_code}` (inactive/draft).")
                    except Exception as e:
                        show_publish_error(f"publish **{q['ticket_code']}** (unexpected error - skipped, rest of batch continues)", str(e))
                        continue

        if st.session_state.mt_failed_items:
            st.divider()
            st.subheader(f"⚠️ {len(st.session_state.mt_failed_items)} ticket(s) created but their option failed")
            st.caption("These tickets themselves were created successfully (and are still ACTIVE) - only "
                      "the option/modality failed, so retrying 'Publish all' would try to create duplicate "
                      "tickets. Adjust whatever needs fixing below (e.g. a start time), then retry just the "
                      "option for that one ticket - no need to redo the whole batch.")
            for fi_idx, fi in enumerate(list(st.session_state.mt_failed_items)):
                with st.expander(f"🔧 {fi['ticket_code']} (created as `{fi['real_code']}`) — {fi['label']}", expanded=True):
                    fdata = fi["data"]
                    fcol1, fcol2, fcol3 = st.columns(3)
                    with fcol1:
                        fdata["base_adult_price"] = st.number_input("Adult Price", min_value=0.0, value=float(fdata.get("base_adult_price", 0) or 0), key=f"mtf_adult_{fi_idx}")
                    with fcol2:
                        fdata["base_children_price"] = st.number_input("Child Price", min_value=0.0, value=float(fdata.get("base_children_price", 0) or 0), key=f"mtf_child_{fi_idx}")
                    with fcol3:
                        fdata["base_infant_price"] = st.number_input("Infant Price", min_value=0.0, value=float(fdata.get("base_infant_price", 0) or 0), key=f"mtf_infant_{fi_idx}")
                    ftt_df = pd.DataFrame([{"Time (HH:MM)": t} for t in fdata.get("time_tables", [])]) if fdata.get("time_tables") else pd.DataFrame(columns=["Time (HH:MM)"])
                    def _save_mtf_tt(edf, fdata=fdata):
                        fdata["time_tables"] = _clean_time_table_rows(edf)
                    editable_table("Start Time(s)", ftt_df, f"mtf_tt_{fi_idx}", on_save=_save_mtf_tt)
                    fdcol1, fdcol2 = st.columns(2)
                    with fdcol1:
                        fdata["start_date"] = st.text_input("Valid From (YYYY-MM-DD)", value=fdata.get("start_date", ""), key=f"mtf_start_{fi_idx}")
                    with fdcol2:
                        fdata["end_date"] = st.text_input("Valid Until (YYYY-MM-DD)", value=fdata.get("end_date", ""), key=f"mtf_end_{fi_idx}")

                    if st.button(f"🔄 Retry option for `{fi['real_code']}`", key=f"mtf_retry_{fi_idx}", type="primary"):
                        with st.spinner(f"Retrying '{fi['ticket_code']}'..."):
                            try:
                                retry_pre_config = TicketHumanPreConfig(
                                    supplier_id=supplier_id, ticket_code=fi["real_code"], currency=currency,
                                    modality_code=fi["modality_code"], on_request=on_request,
                                    days_available_before_release=release_days, min_passengers=min_passengers, max_passengers=max_passengers
                                )
                                retry_payloads = build_ticket_payloads(retry_pre_config, fdata, client)
                                if retry_payloads["ticket_option_error"]:
                                    show_publish_error(f"prepare **{fi['ticket_code']}**'s payload", retry_payloads["ticket_option_error"])
                                elif not retry_payloads["geolocation_resolved"]:
                                    st.error("❌ Geolocation not resolved - fix the City field via the normal Create flow instead.")
                                else:
                                    retry_option_result = client.create_ticket_option(supplier_id, fi["real_code"], retry_payloads["ticket_option_payload"])
                                    if "error" in retry_option_result:
                                        show_publish_error(f"retry **{fi['ticket_code']}**'s option", retry_option_result)
                                    else:
                                        st.success(f"✅ **{fi['ticket_code']}**: option created on retry.")
                                        # Match the activation choice made above for this batch.
                                        if not mt_publish_as_active:
                                            retry_deactivate_payload = dict(retry_payloads["main_ticket_payload"])
                                            retry_deactivate_payload["active"] = False
                                            retry_deactivate_payload["code"] = fi["real_code"]
                                            retry_deactivate_result = client.update_ticket(supplier_id, retry_deactivate_payload)
                                            if "error" in retry_deactivate_result:
                                                st.warning(f"⚠️ Option created, but switching back to inactive/draft "
                                                          f"failed: {retry_deactivate_result}.")
                                        st.session_state.mt_failed_items = [
                                            x for x in st.session_state.mt_failed_items if x is not fi
                                        ]
                                        st.rerun()
                            except Exception as e:
                                show_publish_error(f"retry **{fi['ticket_code']}**'s option (unexpected error)", str(e))

        if st.button("🆕 Start a new batch"):
            for key in ["mt_phase", "mt_raw_text", "mt_candidates", "mt_queue", "mt_queue_index",
                       "mt_doc_raw_images", "mt_hosted_image_candidates", "mt_failed_items"]:
                st.session_state.pop(key, None)
            _clear_batch_widget_state(SHARED_WIDGET_STATE_PREFIXES)
            st.rerun()
        return


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
                try:
                    st.session_state.suppliers_cache = client.get_all_suppliers()
                except Exception as e:
                    # This is the very first real network call in the flow -
                    # a transient connection issue here used to crash the
                    # whole app before the human could even pick a supplier.
                    st.error(f"❌ Couldn't load the supplier list: {friendly_error_message(e)}")
                    st.session_state.suppliers_cache = []

        supplier_id_choice = None
        if st.session_state.suppliers_cache:
            # LOCKED: only "Momira_"-prefixed suppliers may be picked - forces
            # the human to explicitly choose a real Momira supplier instead of
            # any other supplier that happens to exist in the account.
            momira_suppliers = [
                s for s in st.session_state.suppliers_cache
                if (s.get("commercialName") or s.get("legalName") or "").strip().lower().startswith("momira_")
            ]
            if not momira_suppliers:
                st.error("🚫 No suppliers starting with 'Momira_' were found in this account - can't continue. "
                        "Check the supplier exists in Travel Compositor with the correct naming, or refresh below.")
            else:
                supplier_options = {
                    f"{s.get('commercialName') or s.get('legalName')} — ID {s.get('id')}": s.get("id")
                    for s in momira_suppliers
                }
                selected_label = st.selectbox("Select Supplier", list(supplier_options.keys()), key="tk_supplier_select")
                supplier_id_choice = str(supplier_options[selected_label])
            if st.button("🔄 Refresh supplier list", key="tk_refresh_suppliers"):
                st.session_state.suppliers_cache = None
                st.rerun()
        else:
            st.error("Could not load the supplier list from Travel Compositor.")
            with st.expander("⚠️ Emergency manual entry"):
                st.caption("Bypasses the Momira_ check above - only use this if you've already confirmed the "
                          "numeric ID belongs to a real Momira_ supplier.")
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
            tk_prefill = st.session_state.pop("tk_prefill_existing_ticket_code", "")
            existing_ticket_code_in = st.text_input(
                "Existing Ticket Code", value=tk_prefill, placeholder="e.g. JAP-T1", key="tk_existing_code"
            ).strip()

            if st.button("🔍 Check what's already online for this code", disabled=not existing_ticket_code_in, key="tk_check_online"):
                with st.spinner("Fetching from Travel Compositor..."):
                    fetched = client.get_ticket(supplier_id, existing_ticket_code_in)
                    st.session_state.tk_fetched_ticket = fetched
                    st.session_state.tk_fetched_option = None
                    if isinstance(fetched, dict) and "error" not in fetched:
                        st.session_state.tk_fetched_currency = fetched.get("currency")
                        # Same fix as ClosedTours: pre-fill Step 5 from this ticket's OWN live
                        # data immediately, instead of leaving it blank until a fresh document
                        # is extracted - see _map_fetched_ticket_to_data()'s docstring.
                        if action == "update_ticket":
                            st.session_state.tk_extracted = _map_fetched_ticket_to_data(fetched)
                            st.session_state.tk_raw_preview = (
                                f"(No new document/URL provided - these fields were pre-filled from "
                                f"the ticket's CURRENT live data on Travel Compositor, code "
                                f"`{existing_ticket_code_in}`. Edit below, or provide a new source and "
                                f"click Extract to bring in updates - your existing values won't be "
                                f"blanked out by an incomplete new extraction.)"
                            )
                            st.session_state.tk_payloads = None
                            st.session_state.tk_geo_confirmed = False
                            st.session_state.tk_doc_raw_images = []
                            st.session_state.tk_hosted_image_candidates = []

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
            render_code_availability_check(client, "ticket", supplier_id, ticket_code_in, "ticket")
        if "min_passengers" in needed:
            min_pass_in = st.selectbox("Min Passengers", [1, 2], key="tk_min_pass")
        if "max_passengers" in needed:
            max_pass_in = st.selectbox("Max Passengers", list(range(2, 21)), index=7, key="tk_max_pass")
        if "currency" in needed:
            currency_in = st.selectbox("Currency", CURRENCY_OPTIONS, key="tk_currency")
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

    # "Create" always routes through the batch-capable flow now, regardless
    # of how many excursions the source actually turns out to describe - it
    # transparently handles a single excursion exactly like the old
    # single-Ticket flow did (just one row to fill in), and auto-detects/
    # handles multiple excursions without the human needing to pre-declare
    # "this has several" via a checkbox first. This removes the old upfront
    # single-vs-multiple choice per the confirmed design (always
    # auto-detect, one unified queue-based UI regardless of count).
    if action == "create":
        render_multi_ticket_flow(client, supplier_id, currency, on_request, release_days, tk_url, tk_files,
                                min_passengers=min_passengers, max_passengers=max_passengers,
                                default_ticket_code=ticket_code)
        return

    if st.button("🔎 Extract", disabled=not (tk_url or tk_files), key="tk_extract_btn"):
        with st.spinner("Gathering content..."):
            try:
                combined_parts = []
                doc_raw_images = []
                doc_image_urls = []
                seen_image_hashes = set()
                if tk_url:
                    combined_parts.append(f"--- SOURCE: WEB PAGE ({tk_url}) ---\n{get_page_text(tk_url)}")
                for uploaded in (tk_files or []):
                    suffix = os.path.splitext(uploaded.name)[1]
                    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                        tmp.write(uploaded.getbuffer())
                        tmp_path = tmp.name
                    combined_parts.append(f"--- SOURCE: UPLOADED DOCUMENT ({uploaded.name}) ---\n{extract_raw_text(tmp_path)}")
                    remaining_budget = 12 - len(doc_raw_images)
                    embedded_images = extract_images(tmp_path, max_images=remaining_budget, seen_hashes=seen_image_hashes) if remaining_budget > 0 else []
                    if embedded_images:
                        for i, (img_bytes, ext) in enumerate(embedded_images):
                            doc_raw_images.append((f"{os.path.splitext(uploaded.name)[0]}_img{i+1}.{ext or 'jpg'}", img_bytes))
                        try:
                            doc_image_urls.extend(upload_images_freeimage(embedded_images))
                        except Exception:
                            pass
                    os.remove(tmp_path)

                if len(doc_image_urls) >= len(doc_raw_images):
                    doc_raw_images = []

                raw_text = "\n\n".join(combined_parts)

                if tk_is_option_only:
                    data = extract_ticket_option_only_data(raw_text, human_hint=tk_hint or None)
                    st.session_state.tk_extracted = data
                    st.session_state.tk_raw_preview = raw_text
                    st.session_state.tk_payloads = None
                    st.session_state.tk_geo_confirmed = False
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
                        data["image_urls"] = [FALLBACK_IMAGE]  # safe default - human picks below, this only stays if nothing gets chosen
                        if action == "update_ticket":
                            data = _merge_extraction_over_baseline(st.session_state.get("tk_extracted") or {}, data)
                        st.session_state.tk_extracted = data
                        st.session_state.tk_raw_preview = raw_text
                        st.session_state.tk_payloads = None
                        st.session_state.tk_geo_confirmed = False
                        st.session_state.tk_doc_raw_images = doc_raw_images
                        st.session_state.tk_hosted_image_candidates = list(dict.fromkeys((get_page_images(tk_url) if tk_url else []) + doc_image_urls))
                        st.success("Extraction complete. Review and edit below.")
            except Exception as e:
                st.error(f"Extraction failed: {friendly_error_message(e)}")

    if st.session_state.get("tk_pending_variants"):
        excursions = st.session_state.tk_pending_variants
        st.warning(f"⚠️ This content describes {len(excursions)} distinct excursions — which one(s) do you want to add?")
        st.caption("Tick just one to continue in the normal single-Ticket flow below, or tick several to create "
                  "them all as separate Tickets in one batch (you'll assign each its own Code next).")

        if "tk_pending_variant_selection" not in st.session_state:
            st.session_state.tk_pending_variant_selection = [
                {"label": e.get("label", f"Excursion {i+1}"), "selected": False,
                 "ticket_code": "", "modality_code": "Standard Private" if e.get("is_private") else "Standard"}
                for i, e in enumerate(excursions)
            ]
        tkpv_selection = st.session_state.tk_pending_variant_selection

        for i, sel in enumerate(tkpv_selection):
            sel["selected"] = st.checkbox(sel["label"], value=sel["selected"], key=f"tkpv_sel_{i}")

        tkpv_num_selected = sum(1 for s in tkpv_selection if s["selected"])

        if tkpv_num_selected > 1:
            st.caption("Multiple selected - each needs its own Ticket Code and Modality Code:")
            for i, sel in enumerate(tkpv_selection):
                if not sel["selected"]:
                    continue
                tkpvcol1, tkpvcol2 = st.columns(2)
                with tkpvcol1:
                    sel["ticket_code"] = st.text_input(f"Ticket Code — {sel['label']}", value=sel["ticket_code"], key=f"tkpv_code_{i}", placeholder="e.g. BALI-T1")
                with tkpvcol2:
                    sel["modality_code"] = st.text_input(f"Modality Code — {sel['label']}", value=sel["modality_code"], key=f"tkpv_modcode_{i}")

        tkpv_btn_label = "✅ Confirm and Extract Full Details" if tkpv_num_selected <= 1 else f"✅ Confirm and Start Batch Review ({tkpv_num_selected} tickets)"
        if st.button(tkpv_btn_label, key="tk_confirm_variant", disabled=tkpv_num_selected == 0):
            if tkpv_num_selected <= 1:
                with st.spinner("Extracting full details for the selected excursion..."):
                    try:
                        chosen = next(s for s in tkpv_selection if s["selected"])
                        chosen_label = chosen["label"]
                        data = extract_ticket_data(
                            st.session_state.tk_pending_raw_text, variant_hint=chosen_label,
                            human_hint=st.session_state.get("tk_pending_hint")
                        )
                        tk_pending_url = st.session_state.get("tk_pending_url")
                        data["image_urls"] = [FALLBACK_IMAGE]  # safe default - human picks below, this only stays if nothing gets chosen
                        if action == "update_ticket":
                            data = _merge_extraction_over_baseline(st.session_state.get("tk_extracted") or {}, data)

                        st.session_state.tk_extracted = data
                        st.session_state.tk_raw_preview = f"(Extracted excursion: {chosen_label})\n\n{st.session_state.tk_pending_raw_text}"
                        st.session_state.tk_payloads = None
                        st.session_state.tk_geo_confirmed = False
                        st.session_state.tk_doc_raw_images = st.session_state.get("tk_pending_doc_raw_images", [])
                        st.session_state.tk_hosted_image_candidates = list(dict.fromkeys((get_page_images(tk_pending_url) if tk_pending_url else []) + st.session_state.get("tk_pending_doc_images", [])))
                        st.session_state.tk_pending_variants = None
                        st.session_state.tk_pending_raw_text = None
                        st.session_state.tk_pending_variant_selection = None
                        st.rerun()
                    except Exception as e:
                        st.error(f"Extraction failed: {friendly_error_message(e)}")
            else:
                tkpv_missing = [s["label"] for s in tkpv_selection if s["selected"] and (not s["ticket_code"].strip() or not s["modality_code"].strip())]
                tkpv_invalid = [s["modality_code"] for s in tkpv_selection if s["selected"] and any(c in s["modality_code"] for c in ["/", "\\", "+", "-"])]
                tkpv_codes_seen = {}
                for s in tkpv_selection:
                    if s["selected"] and s["ticket_code"].strip():
                        tkpv_codes_seen.setdefault(s["ticket_code"].strip(), []).append(s["label"])
                tkpv_dupes = {c: labs for c, labs in tkpv_codes_seen.items() if len(labs) > 1}
                tkpv_existing = []
                for s in tkpv_selection:
                    if s["selected"] and s["ticket_code"].strip():
                        existing_check = check_code_availability(client, "ticket", supplier_id, s["ticket_code"])
                        if existing_check and existing_check["exists"]:
                            tkpv_existing.append(s["ticket_code"].strip())

                if tkpv_missing:
                    st.error(f"🚫 These selected excursions are missing a Ticket Code or Modality Code: {tkpv_missing}")
                elif tkpv_invalid:
                    st.error(f"🚫 These Modality Codes contain invalid characters (/, \\, +, -): {tkpv_invalid}")
                elif tkpv_dupes:
                    st.error(f"🚫 These Ticket Codes are used by more than one selected excursion: {list(tkpv_dupes.keys())}")
                elif tkpv_existing:
                    st.error(f"🚫 These Ticket Codes are ALREADY TAKEN by existing tickets - choose different "
                            f"ones: {tkpv_existing}")
                else:
                    tk_pending_url = st.session_state.get("tk_pending_url")
                    new_mt_queue = [
                        {"label": s["label"], "ticket_code": s["ticket_code"].strip(), "modality_code": s["modality_code"].strip(),
                         "data": None, "confirmed": False}
                        for s in tkpv_selection if s["selected"]
                    ]
                    st.session_state.mt_raw_text = st.session_state.tk_pending_raw_text
                    st.session_state.mt_doc_raw_images = st.session_state.get("tk_pending_doc_raw_images", [])
                    st.session_state.mt_hosted_image_candidates = list(dict.fromkeys((get_page_images(tk_pending_url) if tk_pending_url else []) + st.session_state.get("tk_pending_doc_images", [])))
                    st.session_state.mt_queue = new_mt_queue
                    st.session_state.mt_queue_index = 0
                    st.session_state.mt_phase = "reviewing"
                    st.session_state.tk_pending_variants = None
                    st.session_state.tk_pending_raw_text = None
                    st.session_state.tk_pending_variant_selection = None
                    st.rerun()

    # ------------------------------------------------------------------
    # TICKET STEP 5: Review & Edit
    # ------------------------------------------------------------------
    if st.session_state.get("tk_extracted"):
        data = st.session_state.tk_extracted
        st.header("Ticket — Step 5: Review & Edit")

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Original Source")
            render_readonly_source(st.session_state.tk_raw_preview, height=500)

        with col2:
            if tk_is_option_only:
                st.subheader("Only pricing/schedule needed for this action")
                st.caption("Ticket details (name, description, city, meeting points) are skipped - "
                          "they belong to the existing ticket and aren't touched here.")
            else:
                st.subheader("Extracted Data (click ✏️ to edit)")
                editable_field("Ticket name", data, "ticket_name", widget="text_input")
                editable_field("Description", data, "description", widget="html_text_area", height=150)
                editable_field("City", data, "city", widget="text_input")

                if data.get("is_private") and "private" not in (modality_code or "").lower():
                    st.info(f"💡 This excursion is described as **PRIVATE** in the source - a genuine "
                           f"selling point. Your current Modality Code is `{modality_code}` - consider "
                           f"going back to Step 3 (Details) and adding \"Private\" to it if you'd like "
                           f"this reflected there.")

                editable_field("Duration (hours)", data, "duration", widget="number_input")

                acol1, acol2 = st.columns(2)
                with acol1:
                    data["child_age_min"] = st.number_input("Min Child Age", min_value=0, max_value=17,
                                                             value=int(data.get("child_age_min", 2) or 2), key="tk_min_child_age")
                with acol2:
                    data["child_age_max"] = st.number_input("Max Child Age", min_value=0, max_value=17,
                                                             value=int(data.get("child_age_max", 12) or 12), key="tk_max_child_age")

                # Engines (Search Engines to Sell through): always ALL of them - this was
                # previously a review multiselect, but there's never a real reason to sell
                # through fewer than all engines, so it's set silently in the background and
                # not shown to the human at all (adjustable afterward in Travel Compositor
                # under Settings > Engine if ever needed).
                data["product_types"] = [
                    "MULTI", "GROUPS", "ONLY_HOTEL", "ONLY_HOUSE", "ONLY_FLIGHT", "ONLY_TRAIN",
                    "FLIGHT_HOTEL", "FLIGHT_HOUSE", "ONLY_TICKET", "EVENT_TICKET", "GOLF", "ONLY_CAR",
                    "ONLY_TRANSFER", "HOLIDAYS", "GIFTCARD", "EXTERNAL_SEARCH_BOX", "GIFT_BOX", "ROUTING",
                    "PRIVATE_TOUR", "MAGIC_BOX", "CRUISES", "AI_TRIP", "MEMBERSHIP", "ONLY_INSURANCE",
                    "ONLY_ITEM", "TRIP_PLANNER",
                ]

                inc_df = pd.DataFrame([{"Item": x} for x in data.get("includes", [])]) if data.get("includes") else pd.DataFrame(columns=["Item"])
                def _save_tk_includes(edf, data=data):
                    data["includes"] = [str(r.get("Item") or "").strip() for _, r in edf.iterrows() if str(r.get("Item", "") or "").strip()]
                editable_table("Includes", inc_df, "tk_includes", on_save=_save_tk_includes)

                exc_df = pd.DataFrame([{"Item": x} for x in data.get("excludes", [])]) if data.get("excludes") else pd.DataFrame(columns=["Item"])
                def _save_tk_excludes(edf, data=data):
                    data["excludes"] = [str(r.get("Item") or "").strip() for _, r in edf.iterrows() if str(r.get("Item", "") or "").strip()]
                editable_table("Excludes", exc_df, "tk_excludes", on_save=_save_tk_excludes)

                mp_default = [{"Description": m.get("description", "")} for m in data.get("meeting_points", [])] or [{"Description": "Hotel Lobby"}]
                mp_df = pd.DataFrame(mp_default)
                def _save_tk_mp(edf, data=data):
                    data["meeting_points"] = [
                        {"description": str(r.get("Description") or "").strip(),
                         "variable_location": str(r.get("Description") or "").strip().lower() == "hotel lobby"}
                        for _, r in edf.iterrows() if str(r.get("Description", "") or "").strip()
                    ]
                editable_table("Meeting Points", mp_df, "tk_meeting_points", on_save=_save_tk_mp)

                if "tk_images_text_value" not in st.session_state:
                    st.session_state.tk_images_text_value = "\n".join(data.get("image_urls", []))
                if st.session_state.get("_tk_pending_images_update") is not None:
                    st.session_state.tk_images_text_value = st.session_state._tk_pending_images_update
                    st.session_state._tk_pending_images_update = None

                images_text = st.text_area("Image URLs (one per line)", key="tk_images_text_value")
                data["image_urls"] = [u.strip() for u in images_text.split("\n") if u.strip()] or [FALLBACK_IMAGE]

                default_tk_img_query = data.get("ticket_name", "") or data.get("city", "")

                def _tk_add_pexels():
                    selected = render_stock_photo_picker("Pexels", search_images, default_tk_img_query, "tk_pexels")
                    if selected:
                        current = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                        new_list = current + selected
                        data["image_urls"] = new_list
                        st.session_state._tk_pending_images_update = "\n".join(new_list)
                        return len(selected)
                    return 0

                render_closable_image_section(True, "🖼️ Or search free stock photos (Pexels)", "tk_pexels_closed", _tk_add_pexels)

                def _tk_add_pixabay():
                    selected = render_stock_photo_picker("Pixabay", search_images_pixabay, default_tk_img_query, "tk_pixabay")
                    if selected:
                        current = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                        new_list = current + selected
                        data["image_urls"] = new_list
                        st.session_state._tk_pending_images_update = "\n".join(new_list)
                        return len(selected)
                    return 0

                render_closable_image_section(True, "🖼️ Or search free stock photos (Pixabay)", "tk_pixabay_closed", _tk_add_pixabay)

                def _tk_add_url_images():
                    selected = render_url_image_picker(st.session_state.tk_hosted_image_candidates, "tk_found_images")
                    if selected:
                        current = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                        new_list = current + selected
                        data["image_urls"] = new_list
                        st.session_state._tk_pending_images_update = "\n".join(new_list)
                        return len(selected)
                    return 0

                render_closable_image_section(
                    bool(st.session_state.get("tk_hosted_image_candidates")),
                    f"🖼️ Images found ({len(st.session_state.get('tk_hosted_image_candidates') or [])}) - from the page/document",
                    "tk_found_images_closed", _tk_add_url_images
                )

                def _tk_add_doc_image():
                    added = render_doc_image_picker(st.session_state.tk_doc_raw_images, "tk_doc_images")
                    if added:
                        current = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                        new_list = current + [added]
                        data["image_urls"] = new_list
                        st.session_state._tk_pending_images_update = "\n".join(new_list)
                        return 1
                    return 0

                render_closable_image_section(
                    bool(st.session_state.get("tk_doc_raw_images")),
                    f"📥 Images extracted from your document(s) ({len(st.session_state.get('tk_doc_raw_images') or [])}) - need hosting",
                    "tk_doc_images_closed", _tk_add_doc_image
                )

        st.subheader("🤖 Tell AI what to fix or clarify (optional)")
        tk_clarify_q = st.text_input("Your message", key="tk_clarify_input")
        if st.button("Send", disabled=not tk_clarify_q.strip(), key="tk_clarify_send"):
            with st.spinner("Thinking..."):
                result = apply_clarification(st.session_state.tk_raw_preview, data, tk_clarify_q)
                st.session_state.tk_clarify_result = result
                if result.get("changes"):
                    for field_name, new_value in result["changes"].items():
                        data[field_name] = new_value
                    tk_field_to_table_key = {
                        "supplements": "_editing_table_tk_supplements",
                        "includes": "_editing_table_tk_includes",
                        "excludes": "_editing_table_tk_excludes",
                        "meeting_points": "_editing_table_tk_meeting_points",
                        "time_tables": "_editing_table_tk_timetables",
                    }
                    for field_name in result["changes"]:
                        table_key = tk_field_to_table_key.get(field_name)
                        if table_key:
                            st.session_state[table_key] = False
                    if "operational_days" in result["changes"]:
                        st.session_state.pop("tk_op_days", None)
                    if "stop_sales" in result["changes"]:
                        st.session_state.pop("tk_stop_sales", None)
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
            data["time_tables"] = _clean_time_table_rows(edf)
        editable_table("Start Time(s)", tt_df, "tk_timetables", on_save=_save_tk_timetables)
        if not data.get("time_tables"):
            st.caption("ℹ️ No start time set yet - optional, but add one if the ticket has a fixed departure time.")

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

        st.subheader(f"Pricing (in {currency or '(set Currency in Step 3)'})")
        st.caption("A Ticket Modality holds ONE price setup + ONE validity date range (not a seasonal table). "
                  "For holiday/seasonal price differences, use dated Supplements below instead.")

        price_type = st.radio(
            "Pricing Mode", ["DISTRIBUTION", "OCCUPANCY", "SERVICE"],
            index=["DISTRIBUTION", "OCCUPANCY", "SERVICE"].index(data.get("price_type") or "OCCUPANCY"),
            format_func=lambda x: {
                "DISTRIBUTION": "Distribution - price per person (Adult/Child/Infant)",
                "OCCUPANCY": "Occupancy - price varies by group size (infants free, not counted)",
                "SERVICE": "Service - one flat total price regardless of headcount",
            }[x],
            key="tk_price_type"
        )
        data["price_type"] = price_type
        if price_type != "DISTRIBUTION":
            st.warning("⚠️ UNCONFIRMED whether Travel Compositor's API accepts this pricing mode for "
                      "Tickets - ClosedTours are confirmed to only work via Distribution through the API "
                      "(Occupancy there only works through their own admin UI, not the API). Test this "
                      "carefully with a real publish before relying on it.")

        if price_type == "DISTRIBUTION":
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
        elif price_type == "SERVICE":
            data["base_service_price"] = st.number_input(
                "Total Service Price (flat, regardless of group size)", min_value=0.0,
                value=float(data.get("base_service_price", 0) or 0), key="tk_service_price"
            )
        elif price_type == "OCCUPANCY":
            st.caption("Each row is an EXACT number of paying passengers (not a range) with its price - "
                      "infants are always free and excluded automatically. If your source shows a range "
                      "like '3-5' at one price, add ONE row per exact number (3, 4, and 5) all with that "
                      "same price - use the button below to auto-expand a range for you.")
            occ_rows = [{"Occupancy (exact # pax)": o.get("occupancy", 2), "Price": o.get("amount", 0)}
                       for o in data.get("occupancy_prices", [])] or [{"Occupancy (exact # pax)": 2, "Price": 0}]
            occ_df = pd.DataFrame(occ_rows)
            def _save_occupancy(edf, data=data):
                data["occupancy_prices"] = [
                    {"occupancy": int(r.get("Occupancy (exact # pax)", 2) or 2), "amount": float(r.get("Price", 0) or 0)}
                    for _, r in edf.iterrows()
                ]
            editable_table("Occupancy Price Tiers", occ_df, "tk_occupancy", on_save=_save_occupancy)

            occ_has_solo = any(o.get("occupancy") == 1 for o in data.get("occupancy_prices", []))
            if not occ_has_solo:
                st.warning("⚠️ No price for **1 pax (solo traveler)** yet - this needs to be added manually. "
                          "Solo pricing is often different from the per-person rate when sharing (sometimes "
                          "higher, sometimes not offered at all), so it can't be safely defaulted from the "
                          "other rows - check the source or confirm with the supplier.")

            with st.expander("🔢 Auto-expand a range (e.g. '3-5' at one price) into individual rows"):
                rcol1, rcol2, rcol3 = st.columns(3)
                with rcol1:
                    range_start = st.number_input("From", min_value=1, value=1, key="tk_occ_range_start")
                with rcol2:
                    range_end = st.number_input("To", min_value=1, value=1, key="tk_occ_range_end")
                with rcol3:
                    range_price = st.number_input("Price (same for all)", min_value=0.0, value=0.0, key="tk_occ_range_price")
                if st.button("➕ Add this range as individual rows", key="tk_occ_range_add") and range_end >= range_start:
                    existing = list(data.get("occupancy_prices", []))
                    for n in range(int(range_start), int(range_end) + 1):
                        existing.append({"occupancy": n, "amount": range_price})
                    data["occupancy_prices"] = existing
                    st.rerun()

        dcol1, dcol2 = st.columns(2)
        with dcol1:
            data["start_date"] = st.text_input("Valid From (YYYY-MM-DD)", value=data.get("start_date", ""), key="tk_start_date")
        with dcol2:
            data["end_date"] = st.text_input("Valid Until (YYYY-MM-DD)", value=data.get("end_date", ""), key="tk_end_date")
        if data.get("pricing_notes"):
            st.warning(f"⚠️ {data['pricing_notes']}")

        if action == "create":
            st.subheader("➕ Add more Modalities to create right away (optional)")
            st.caption("Add more ticket variants now (e.g. different guide languages or vehicle classes) - "
                      "all get created together with a SINGLE deactivation at the end, so you don't need to "
                      "manually reactivate the Ticket in Travel Compositor between each one. Uses Distribution "
                      "pricing (Adult/Child/Infant) only - use the normal single-Ticket flow afterward for "
                      "Occupancy or Service pricing on a specific one.")
            if "tk_extra_modalities" not in st.session_state:
                st.session_state.tk_extra_modalities = []

            for i, mod in enumerate(st.session_state.tk_extra_modalities):
                st.markdown(f"**Modality {i + 2}**")
                mcol1, mcol2, mcol3 = st.columns([2, 2, 1])
                with mcol1:
                    mod["code"] = st.text_input("Modality Code", value=mod["code"], key=f"tk_extramod_code_{i}")
                with mcol2:
                    mod["hint"] = st.text_input("Focus Hint (e.g. 'German guide')", value=mod["hint"], key=f"tk_extramod_hint_{i}")
                with mcol3:
                    st.write("")
                    if st.button("🗑️ Remove", key=f"tk_extramod_remove_{i}"):
                        st.session_state.tk_extra_modalities.pop(i)
                        st.rerun()

                if any(c in (mod["code"] or "") for c in ["/", "\\", "+", "-"]):
                    st.error(f"🚫 Modality Code '{mod['code']}' contains invalid characters (/, \\, +, -).")

                if st.button(f"🔎 Extract pricing focused on '{mod['hint'] or mod['code'] or 'this modality'}'", key=f"tk_extramod_extract_{i}", disabled=not mod["code"]):
                    with st.spinner("Extracting..."):
                        mod["data"] = extract_ticket_option_only_data(st.session_state.tk_raw_preview, human_hint=mod["hint"])
                        # This quick-add UI only shows Adult/Child/Infant fields (Distribution) - force it
                        # explicitly so the extraction default (Occupancy, see Feature 3) doesn't leave
                        # these prices getting zeroed out by builder.py's per-mode zeroing (Bug 2 fix).
                        mod["data"]["price_type"] = "DISTRIBUTION"
                        st.rerun()

                if mod["data"]:
                    pcol1, pcol2, pcol3 = st.columns(3)
                    with pcol1:
                        mod["data"]["base_adult_price"] = st.number_input("Adult Price", min_value=0.0, value=float(mod["data"].get("base_adult_price", 0) or 0), key=f"tk_extramod_adult_{i}")
                    with pcol2:
                        mod["data"]["base_children_price"] = st.number_input("Child Price", min_value=0.0, value=float(mod["data"].get("base_children_price", 0) or 0), key=f"tk_extramod_child_{i}")
                    with pcol3:
                        mod["data"]["base_infant_price"] = st.number_input("Infant Price", min_value=0.0, value=float(mod["data"].get("base_infant_price", 0) or 0), key=f"tk_extramod_infant_{i}")
                    dcol1x, dcol2x = st.columns(2)
                    with dcol1x:
                        mod["data"]["start_date"] = st.text_input("Valid From (YYYY-MM-DD)", value=mod["data"].get("start_date", ""), key=f"tk_extramod_start_{i}")
                    with dcol2x:
                        mod["data"]["end_date"] = st.text_input("Valid Until (YYYY-MM-DD)", value=mod["data"].get("end_date", ""), key=f"tk_extramod_end_{i}")
                    tt_df_extra = pd.DataFrame([{"Time (HH:MM)": t} for t in mod["data"].get("time_tables", [])]) if mod["data"].get("time_tables") else pd.DataFrame(columns=["Time (HH:MM)"])
                    def _save_extramod_tt(edf, mod=mod):
                        mod["data"]["time_tables"] = _clean_time_table_rows(edf)
                    editable_table(f"Start Time(s) - {mod['code']}", tt_df_extra, f"tk_extramod_tt_{i}", on_save=_save_extramod_tt)
                else:
                    st.info("Click 'Extract pricing' above to get started for this modality.")
                st.divider()

            if st.button("➕ Add another Modality", key="tk_add_extramod"):
                st.session_state.tk_extra_modalities.append({"code": "", "hint": "", "data": None})
                st.rerun()

        st.subheader("Optional Add-ons (Supplements)")
        st.caption("⚠️ Ticket Supplements are always independently stackable - a customer can tick ANY "
                  "combination, and prices simply add up. Only use this for simple add-ons everyone can "
                  "combine freely (e.g. 'Audio guide - $5'). There's no 'on request' option here either. "
                  "For anything that should be an ALTERNATIVE (only one of several choices) or needs "
                  "special/on-request handling, create it as a SEPARATE Modality instead (Action 2: Add "
                  "new Modality to existing Ticket) - never model it as a supplement. **This includes "
                  "different guide languages** - if the source shows separate full price tables per "
                  "language (English/German/French/etc.), each one is its own Modality with its own real "
                  "price, never a small add-on fee.")
        supp_rows = [
            {"Name": s.get("name", ""), "Adult": s.get("adult_price", 0), "Child": s.get("children_price", 0),
             "Infant": s.get("infant_price", 0), "Start": s.get("travel_start_date", ""), "End": s.get("travel_end_date", "")}
            for s in data.get("supplements", [])
        ]
        supp_df = pd.DataFrame(supp_rows) if supp_rows else pd.DataFrame(columns=["Name", "Adult", "Child", "Infant", "Start", "End"])
        def _save_tk_supplements(edf, data=data):
            new_supp = []
            for _, r in edf.iterrows():
                name = str(r.get("Name", "") or "").strip()
                if not name:
                    continue
                new_supp.append({
                    "name": name, "adult_price": float(r.get("Adult", 0) or 0),
                    "children_price": float(r.get("Child", 0) or 0), "infant_price": float(r.get("Infant", 0) or 0),
                    "travel_start_date": str(r.get("Start", "") or "").strip(), "travel_end_date": str(r.get("End", "") or "").strip(),
                })
            data["supplements"] = new_supp
        editable_table("Supplements", supp_df, "tk_supplements", on_save=_save_tk_supplements)

        if price_type == "SERVICE":
            price_valid = bool(data.get("base_service_price", 0))
        elif price_type == "OCCUPANCY":
            price_valid = bool(data.get("occupancy_prices")) and any(o.get("amount", 0) for o in data.get("occupancy_prices", []))
        else:
            price_valid = any([data.get("base_adult_price", 0), data.get("base_children_price", 0), data.get("base_infant_price", 0)])
        if not price_valid:
            st.error("Add at least one non-zero price (Adult/Child/Infant) before continuing.")

        can_build = price_valid

        st.subheader("🤖 Tell AI what to fix or clarify (optional)")
        st.caption("Ask a question, or tell it to fix something about the pricing/schedule above (e.g. 'the "
                  "adult price should be 89 not 79'). It applies real changes when you ask for them - always "
                  "shows exactly what changed so you can double-check.")
        tk_clarify_q2 = st.text_input("Your message", key="tk_clarify_input_pricing",
                                      placeholder="e.g. 'Fix the adult price to 89' or 'Is the child price for under 12?'")
        if st.button("Send", disabled=not tk_clarify_q2.strip(), key="tk_clarify_send_pricing"):
            with st.spinner("Thinking..."):
                result = apply_clarification(st.session_state.tk_raw_preview, data, tk_clarify_q2)
                st.session_state.tk_clarify_result_pricing = result
                if result.get("changes"):
                    for field_name, new_value in result["changes"].items():
                        data[field_name] = new_value
                    tk_field_to_table_key2 = {
                        "supplements": "_editing_table_tk_supplements",
                        "occupancy_prices": "_editing_table_tk_occupancy",
                        "time_tables": "_editing_table_tk_timetables",
                    }
                    for field_name in result["changes"]:
                        table_key = tk_field_to_table_key2.get(field_name)
                        if table_key:
                            st.session_state[table_key] = False
                    if "operational_days" in result["changes"]:
                        st.session_state.pop("tk_op_days", None)
                    if "stop_sales" in result["changes"]:
                        st.session_state.pop("tk_stop_sales", None)
                st.rerun()
        if st.session_state.get("tk_clarify_result_pricing"):
            r = st.session_state.tk_clarify_result_pricing
            st.info(r.get("summary", ""))
            if r.get("changes"):
                st.caption(f"✅ Applied changes to: {', '.join(r['changes'].keys())} - review above before continuing.")

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

            render_modalities_review(
                "ticket", modality_code, "Base Modality", data,
                st.session_state.get("tk_extra_modalities", []), currency
            )

            if not tk_is_option_only:
                if payloads["geolocation_resolved"]:
                    lat, lng = payloads["geolocation_latitude"], payloads["geolocation_longitude"]
                    maps_link = f"https://www.google.com/maps?q={lat},{lng}"
                    st.markdown(
                        f"<div style='background-color:#d4edda; color:#155724; padding:10px 14px; "
                        f"border-radius:4px;'>📍 Resolved location: <strong>{payloads['geolocation_name'] or '(no name)'}</strong>"
                        f"<br>Coordinates: {lat:.6f}, {lng:.6f} (source: {payloads['geolocation_source']})</div>",
                        unsafe_allow_html=True
                    )
                    st.markdown(f"[🗺️ Open in Google Maps to verify]({maps_link})")
                    if payloads['geolocation_source'] not in ("manual override", "not_found", None):
                        st.caption("Geocoding data © OpenStreetMap contributors")

                    with st.expander("🔍 This looks wrong or too imprecise? Search for a better match"):
                        st.caption("Broad place names (e.g. 'Bali') often resolve to the centroid of a whole "
                                  "region, which can be far from the actual location. Try something more "
                                  "specific - a landmark, neighborhood, or meeting point name - and pick the "
                                  "correct result below.")
                        tk_geo_search_query = st.text_input("Search for a location", value=data.get("city", ""), key="tk_geo_search_query")
                        if st.button("🔎 Search", key="tk_geo_search_btn"):
                            with st.spinner("Searching..."):
                                st.session_state.tk_geo_search_results = geocode_search(tk_geo_search_query, limit=5)
                        if st.session_state.get("tk_geo_search_results"):
                            for gi, candidate in enumerate(st.session_state.tk_geo_search_results):
                                gcol_info, gcol_btn = st.columns([4, 1])
                                with gcol_info:
                                    st.write(f"**{candidate['display_name']}**")
                                    st.caption(f"{candidate['latitude']:.6f}, {candidate['longitude']:.6f} ({candidate.get('type', '')})")
                                with gcol_btn:
                                    if st.button("Use this", key=f"tk_geo_pick_{gi}"):
                                        data["manual_latitude"] = candidate["latitude"]
                                        data["manual_longitude"] = candidate["longitude"]
                                        pre_config = TicketHumanPreConfig(
                                            supplier_id=supplier_id, ticket_code=ticket_code or existing_ticket_code or "XXX",
                                            currency=currency, modality_code=modality_code, on_request=on_request,
                                            days_available_before_release=release_days, min_passengers=min_passengers, max_passengers=max_passengers
                                        )
                                        st.session_state.tk_payloads = build_ticket_payloads(pre_config, data, client)
                                        st.session_state.tk_geo_confirmed = False
                                        st.session_state.tk_geo_search_results = None
                                        st.rerun()

                    if payloads.get("is_indonesia"):
                        st.info(f"🇮🇩 Indonesia detected — Vesak Day is automatically blocked as a stop-sale "
                                f"date, no excursion may start that day. {payloads.get('vesak_day_note', '')}")

                    if payloads.get("release_days_overridden"):
                        st.info(f"📅 The document mentions its own booking/release deadline, so the release "
                                f"period being used is **{payloads['effective_release_days']} days** instead of "
                                f"your default - if the source mentioned more than one deadline, the longer "
                                f"(safer) one was used.")

                    st.session_state.tk_geo_confirmed = st.checkbox(
                        "✅ I've checked this location on the map and it's correct for this ticket",
                        value=st.session_state.get("tk_geo_confirmed", False), key="tk_geo_confirm_checkbox"
                    )
                    if not st.session_state.tk_geo_confirmed:
                        st.info("👆 Please verify the location above before publishing.")
                else:
                    st.markdown(
                        "<div style='background-color:#f8d7da; color:#721c24; padding:6px 12px; "
                        "border-radius:4px;'>❌ Geolocation NOT resolved - the City name may not match a known "
                        "destination.</div>",
                        unsafe_allow_html=True
                    )
                    st.caption("Search for the correct location below (easier than looking up exact "
                              "coordinates), or enter coordinates manually if you already have them.")

                    tk_geo_search_query2 = st.text_input("Search for a location", value=data.get("city", ""), key="tk_geo_search_query2")
                    if st.button("🔎 Search", key="tk_geo_search_btn2"):
                        with st.spinner("Searching..."):
                            st.session_state.tk_geo_search_results2 = geocode_search(tk_geo_search_query2, limit=5)
                    if st.session_state.get("tk_geo_search_results2"):
                        for gi, candidate in enumerate(st.session_state.tk_geo_search_results2):
                            gcol_info, gcol_btn = st.columns([4, 1])
                            with gcol_info:
                                st.write(f"**{candidate['display_name']}**")
                                st.caption(f"{candidate['latitude']:.6f}, {candidate['longitude']:.6f} ({candidate.get('type', '')})")
                            with gcol_btn:
                                if st.button("Use this", key=f"tk_geo_pick2_{gi}"):
                                    data["manual_latitude"] = candidate["latitude"]
                                    data["manual_longitude"] = candidate["longitude"]
                                    pre_config = TicketHumanPreConfig(
                                        supplier_id=supplier_id, ticket_code=ticket_code or existing_ticket_code or "XXX",
                                        currency=currency, modality_code=modality_code, on_request=on_request,
                                        days_available_before_release=release_days, min_passengers=min_passengers, max_passengers=max_passengers
                                    )
                                    st.session_state.tk_payloads = build_ticket_payloads(pre_config, data, client)
                                    st.session_state.tk_geo_confirmed = False
                                    st.session_state.tk_geo_search_results2 = None
                                    st.rerun()

                    st.markdown("**Or enter coordinates manually:**")
                    gcol1, gcol2 = st.columns(2)
                    with gcol1:
                        manual_lat = st.number_input("Latitude", value=None, format="%.6f", key="tk_manual_lat", placeholder="e.g. 27.394900")
                    with gcol2:
                        manual_lng = st.number_input("Longitude", value=None, format="%.6f", key="tk_manual_lng", placeholder="e.g. 33.678400")
                    manual_geo_ready = manual_lat is not None and manual_lng is not None and not (manual_lat == 0 and manual_lng == 0)
                    if manual_lat == 0 and manual_lng == 0:
                        st.caption("⚠️ 0, 0 is a real point in the ocean, not a valid location - enter real coordinates.")
                    if st.button("📍 Use these coordinates & rebuild payload", key="tk_use_manual_geo", disabled=not manual_geo_ready):
                        data["manual_latitude"] = manual_lat
                        data["manual_longitude"] = manual_lng
                        pre_config = TicketHumanPreConfig(
                            supplier_id=supplier_id, ticket_code=ticket_code or existing_ticket_code or "XXX",
                            currency=currency, modality_code=modality_code, on_request=on_request,
                            days_available_before_release=release_days, min_passengers=min_passengers, max_passengers=max_passengers
                        )
                        st.session_state.tk_payloads = build_ticket_payloads(pre_config, data, client)
                        st.session_state.tk_geo_confirmed = False
                        st.rerun()
            else:
                st.info("ℹ️ This action only affects a ticket Option/Modality, which has no geolocation "
                        "of its own (geolocation lives on the main ticket only) - nothing to confirm here.")

            if publish_action in ("Update an existing ticket's details", "Update an existing ticket option"):
                render_ticket_update_comparison(
                    publish_action, data, payloads, client, supplier_id, existing_ticket_code, modality_code
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
            # Geolocation only lives on the MAIN ticket - "Add option"/"Update option" only
            # ever touch a ContractTicketModalityVO, which has no geolocation field at all.
            # Their source data always comes from extract_ticket_option_only_data(), which
            # never fills in a real city, so geolocation_resolved is always False for these
            # two actions - requiring it here would make "Add option"/"Update option"
            # permanently unpublishable. Only require geolocation confirmation when this
            # publish action actually writes the main ticket (create / update_ticket).
            can_publish = not payloads["main_ticket_error"] and not payloads["ticket_option_error"]
            if not tk_is_option_only:
                can_publish = can_publish and payloads.get("geolocation_resolved") and st.session_state.get("tk_geo_confirmed", False)
                if payloads.get("geolocation_resolved") and not st.session_state.get("tk_geo_confirmed", False):
                    st.warning("⚠️ Confirm the location above (checkbox in Step 6) before you can publish.")

            action_descriptions = {
                "Create a brand-new ticket (+ first option)": "Will POST a new ticket, then POST a new option.",
                "Add a new option to an existing ticket": f"Will POST a new option under existing ticket `{target_ticket_code}`.",
                "Update an existing ticket's details": f"Will PUT (update) ticket `{target_ticket_code}`'s details.",
                "Update an existing ticket option": f"Will PUT (update) the option under ticket `{target_ticket_code}`.",
            }
            st.caption(action_descriptions[publish_action])

            tk_publish_as_active = True
            if creating_new:
                tk_activation_choice = st.radio(
                    "After publishing, should this Ticket be Active or Inactive (draft)?",
                    ["Inactive (draft) - recommended, review inside Travel Compositor before it goes live",
                     "Active - live immediately"],
                    index=0, key="tk_activation_choice"
                )
                tk_publish_as_active = tk_activation_choice.startswith("Active")

            if st.button("🚀 Publish to Travel Compositor", disabled=not can_publish, type="primary", key="tk_publish_btn"):
                with st.spinner("Publishing..."):
                    try:
                        if publish_action == "Create a brand-new ticket (+ first option)":
                            creation_payload = dict(payloads["main_ticket_payload"])
                            creation_payload["active"] = True
                            result = client.create_ticket(supplier_id, creation_payload)
                            if "error" in result:
                                show_publish_error("create the ticket", result)
                            else:
                                real_code = result.get("code", payloads["main_ticket_code"])
                                st.success(f"✅ Ticket created (active) with real Code: **{real_code}** — save this exact value.")

                                # api_client.py's _request() already retries every write call
                                # (incl. this POST) up to 6 times internally now.
                                option_result = client.create_ticket_option(supplier_id, real_code, payloads["ticket_option_payload"])

                                if "error" in option_result:
                                    show_publish_error("create the ticket option after retrying", option_result)
                                    st.info("💡 Adjustments to a Ticket require it to be ACTIVE - inactive tickets aren't visible via the API.")
                                    # The ticket itself WAS created successfully (real_code) and is still
                                    # ACTIVE - only the option failed. Don't leave the human stuck on this
                                    # page with no way forward: surface the same "what next" block used on
                                    # success, so they can immediately retry adding the option to this
                                    # already-created ticket ("Add another Modality" below prefills exactly
                                    # that: action=add_option, existing_ticket_code=real_code) or start a
                                    # fresh import instead.
                                    st.session_state.tk_just_published_code = real_code
                                    st.session_state.tk_just_published_supplier_id = supplier_id
                                    st.session_state.tk_just_published_is_inactive = False
                                    st.session_state.tk_publish_partial_failure = True
                                else:
                                    st.success("✅ Ticket option created.")

                                    tk_extra_modalities = st.session_state.get("tk_extra_modalities", [])
                                    if tk_extra_modalities:
                                        st.markdown("**Creating additional modalities...**")
                                        for mod in tk_extra_modalities:
                                            if not mod.get("code") or not mod.get("data"):
                                                st.warning("⚠️ Skipped a modality - missing code or pricing data.")
                                                continue
                                            with st.spinner(f"Creating modality '{mod['code']}'..."):
                                                try:
                                                    mod_pre_config = TicketHumanPreConfig(
                                                        supplier_id=supplier_id, ticket_code=ticket_code, currency=currency,
                                                        modality_code=mod["code"], on_request=on_request,
                                                        days_available_before_release=release_days,
                                                        min_passengers=min_passengers, max_passengers=max_passengers
                                                    )
                                                    mod_payloads = build_ticket_payloads(mod_pre_config, mod["data"], client)
                                                    if mod_payloads["ticket_option_error"]:
                                                        show_publish_error(f"prepare modality '{mod['code']}'", mod_payloads["ticket_option_error"])
                                                        continue
                                                    mod_option_result = client.create_ticket_option(supplier_id, real_code, mod_payloads["ticket_option_payload"])
                                                    if "error" in mod_option_result:
                                                        show_publish_error(f"create modality '{mod['code']}'", mod_option_result)
                                                    else:
                                                        st.success(f"✅ Modality '{mod['code']}' created.")
                                                except Exception as e:
                                                    show_publish_error(f"create modality '{mod['code']}' (unexpected error - skipped, rest continues)", str(e))
                                                    continue

                                    if tk_publish_as_active:
                                        st.success(f"✅ Ticket `{real_code}` left ACTIVE, as chosen above - it's live now.")
                                        st.session_state.tk_just_published_code = real_code
                                        st.session_state.tk_extra_modalities = []
                                        st.session_state.tk_just_published_supplier_id = supplier_id
                                        st.session_state.tk_just_published_is_inactive = False
                                        st.session_state.tk_publish_partial_failure = False
                                    else:
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
                                            st.session_state.tk_just_published_code = real_code
                                            st.session_state.tk_extra_modalities = []
                                            st.session_state.tk_just_published_supplier_id = supplier_id
                                            st.session_state.tk_just_published_is_inactive = True
                                            st.session_state.tk_publish_partial_failure = False

                        elif publish_action == "Add a new option to an existing ticket":
                            result = client.create_ticket_option(supplier_id, target_ticket_code, payloads["ticket_option_payload"])
                            if "error" in result:
                                show_publish_error("add the option", result)
                                st.info(f"💡 Adjustments require the Ticket to be ACTIVE - activate `{target_ticket_code}` inside Travel Compositor first.")
                            else:
                                st.success(f"✅ New option added to ticket `{target_ticket_code}`. Verify inside Travel Compositor.")
                                st.session_state.tk_just_published_code = target_ticket_code
                                st.session_state.tk_just_published_supplier_id = supplier_id
                                st.session_state.tk_just_published_is_inactive = False
                                st.session_state.tk_publish_partial_failure = False

                        elif publish_action == "Update an existing ticket's details":
                            update_payload = dict(payloads["main_ticket_payload"])
                            update_payload["code"] = target_ticket_code
                            result = client.update_ticket(supplier_id, update_payload)
                            if "error" in result:
                                show_publish_error("update the ticket", result)
                                st.info(f"💡 Adjustments require the Ticket to be ACTIVE - activate `{target_ticket_code}` inside Travel Compositor first.")
                            else:
                                st.success(f"✅ Ticket `{target_ticket_code}` updated.")
                                st.session_state.tk_just_published_code = target_ticket_code
                                st.session_state.tk_just_published_supplier_id = supplier_id
                                st.session_state.tk_just_published_is_inactive = False
                                st.session_state.tk_publish_partial_failure = False

                        elif publish_action == "Update an existing ticket option":
                            update_option_payload = dict(payloads["ticket_option_payload"])
                            update_option_payload["code"] = modality_code
                            result = client.update_ticket_option(supplier_id, target_ticket_code, update_option_payload)
                            if "error" in result:
                                show_publish_error("update the option", result)
                                st.info(f"💡 Adjustments require the Ticket to be ACTIVE - activate `{target_ticket_code}` inside Travel Compositor first.")
                            else:
                                st.success(f"✅ Option `{modality_code}` under ticket `{target_ticket_code}` updated.")
                                st.session_state.tk_just_published_code = target_ticket_code
                                st.session_state.tk_just_published_supplier_id = supplier_id
                                st.session_state.tk_just_published_is_inactive = False
                                st.session_state.tk_publish_partial_failure = False
                    except Exception as e:
                        # This used to be able to crash the whole app on any
                        # unhandled exception partway through publishing -
                        # now it shows a contained error instead.
                        show_publish_error("publish the ticket (unexpected error)", str(e))

    if st.session_state.get("tk_just_published_code"):
        st.divider()
        if st.session_state.get("tk_publish_partial_failure"):
            st.subheader("⚠️ Ticket created, but the option failed — here's how to continue")
            st.write(f"The ticket itself (**{st.session_state.tk_just_published_code}**, Supplier "
                    f"{st.session_state.tk_just_published_supplier_id}) was created successfully and is "
                    f"still **ACTIVE**, but its first option/modality failed - see the error above. Don't "
                    f"retry 'Create a brand-new ticket' (that would try to create a duplicate). Instead, "
                    f"use **'Add another Modality to this same Ticket'** below to retry just the option "
                    f"against the ticket that already exists, or start a completely fresh import.")
        else:
            st.subheader("✅ Ticket published — what would you like to do next?")
            st.write(f"Just published: **{st.session_state.tk_just_published_code}** "
                    f"(Supplier {st.session_state.tk_just_published_supplier_id})")

        if st.session_state.get("tk_just_published_is_inactive"):
            st.warning("⚠️ **This Ticket is now INACTIVE.** It was created, given its first Modality, then "
                      "switched back to draft/inactive for your review — this is expected. To add more "
                      "Modalities or make further changes, first **activate it manually inside Travel "
                      "Compositor**, then come back and use 'Add new Modality to existing Ticket'.")
            if st.button("🆕 Start a new import (different Ticket)", type="primary", key="tk_new_import_inactive"):
                keep_client = st.session_state.client
                keep_suppliers = st.session_state.suppliers_cache
                keep_product_type = st.session_state.product_type
                st.session_state.clear()
                st.session_state.client = keep_client
                st.session_state.suppliers_cache = keep_suppliers
                st.session_state.product_type = keep_product_type
                st.rerun()
        else:
            fcol1, fcol2 = st.columns(2)
            with fcol1:
                if st.button("🆕 Start a new import (different Ticket)", type="primary", key="tk_new_import_active"):
                    keep_client = st.session_state.client
                    keep_suppliers = st.session_state.suppliers_cache
                    keep_product_type = st.session_state.product_type
                    st.session_state.clear()
                    st.session_state.client = keep_client
                    st.session_state.suppliers_cache = keep_suppliers
                    st.session_state.product_type = keep_product_type
                    st.rerun()
            with fcol2:
                if st.button("➕ Add another Modality to this same Ticket", key="tk_add_modality_followup"):
                    prefill_ticket_code = st.session_state.tk_just_published_code
                    prefill_supplier_id = st.session_state.tk_just_published_supplier_id
                    keep_client = st.session_state.client
                    keep_suppliers = st.session_state.suppliers_cache
                    keep_product_type = st.session_state.product_type
                    st.session_state.clear()
                    st.session_state.client = keep_client
                    st.session_state.suppliers_cache = keep_suppliers
                    st.session_state.product_type = keep_product_type
                    st.session_state.tk_cfg_action = "add_option"
                    st.session_state.tk_cfg_supplier_id = prefill_supplier_id
                    st.session_state.tk_cfg_existing_ticket_code = prefill_ticket_code
                    st.session_state.tk_step1_confirmed = True
                    st.rerun()



st.set_page_config(page_title="Momira: DMC -> Travel Compositor", layout="wide")

# Slightly larger base font app-wide for readability. Streamlit's own CSS is
# built almost entirely on rem units, so scaling the ROOT font-size (rather
# than hunting down individual elements) cleanly scales text, inputs,
# buttons, tables etc. together without breaking any layout - 106% takes the
# default 16px browser base up to ~17px.
st.markdown("<style>html { font-size: 106%; }</style>", unsafe_allow_html=True)

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
st.caption("Build version: 2026-07-29-geolocation-search-and-pick — bump this string whenever new code is shared, so it's always obvious whether a deploy actually took effect.")
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
            try:
                st.session_state.suppliers_cache = client.get_all_suppliers()
            except Exception as e:
                st.error(f"❌ Couldn't load the supplier list: {friendly_error_message(e)}")
                st.session_state.suppliers_cache = []

    supplier_id_choice = None
    if st.session_state.suppliers_cache:
        # LOCKED: only "Momira_"-prefixed suppliers may be picked - forces
        # the human to explicitly choose a real Momira supplier instead of
        # any other supplier that happens to exist in the account.
        momira_suppliers = [
            s for s in st.session_state.suppliers_cache
            if (s.get("commercialName") or s.get("legalName") or "").strip().lower().startswith("momira_")
        ]
        if not momira_suppliers:
            st.error("🚫 No suppliers starting with 'Momira_' were found in this account - can't continue. "
                    "Check the supplier exists in Travel Compositor with the correct naming, or refresh below.")
        else:
            supplier_options = {
                f"{s.get('commercialName') or s.get('legalName')} — ID {s.get('id')}": s.get("id")
                for s in momira_suppliers
            }
            selected_label = st.selectbox("Select Supplier", list(supplier_options.keys()))
            supplier_id_choice = str(supplier_options[selected_label])
        if st.button("🔄 Refresh supplier list"):
            st.session_state.suppliers_cache = None
            st.rerun()
    else:
        st.error("Could not load the supplier list from Travel Compositor.")
        if st.button("🔄 Try again"):
            st.rerun()
        with st.expander("⚠️ Emergency manual entry (only if the list keeps failing to load)"):
            st.caption("Bypasses the Momira_ check above - only use this if you've already confirmed the "
                      "numeric ID belongs to a real Momira_ supplier.")
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
                    # CONFIRMED FIX (real near-data-loss report): pre-fill the Step 5 review
                    # screen from this tour's OWN live data immediately, instead of leaving it
                    # blank until/unless a fresh document is extracted - see
                    # _map_fetched_tour_to_data()'s docstring for the full story.
                    if action == "update_tour":
                        st.session_state.extracted = _map_fetched_tour_to_data(fetched)
                        st.session_state.raw_preview = (
                            f"(No new document/URL provided - these fields were pre-filled from the "
                            f"tour's CURRENT live data on Travel Compositor, code `{existing_tour_code_in}`. "
                            f"Edit below, or provide a new source and click Extract to bring in updates - "
                            f"your existing values won't be blanked out by an incomplete new extraction.)"
                        )
                        st.session_state.payloads = None
                        st.session_state.images_text_value = ""
                        st.session_state.doc_raw_images = []
                        st.session_state.hosted_image_candidates = []

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
        render_code_availability_check(client, "tour", supplier_id, provider_code_in, "tour")
    if "min_pax" in needed:
        min_pax_in = st.selectbox("Min Pax", [1, 2])
    if "max_pax" in needed:
        max_pax_in = st.selectbox("Max Pax", list(range(2, 10)), index=7)
    if "currency" in needed:
        currency_in = st.selectbox("Currency", CURRENCY_OPTIONS)
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

# "Create" always routes through the batch-capable flow now, regardless of
# how many tour variants the source actually turns out to describe - it
# transparently handles a single variant exactly like the old single-tour
# flow did (just one row to fill in), and auto-detects/handles multiple
# variants without the human needing to pre-declare "this has several" via
# a checkbox first. This removes the old upfront single-vs-multiple choice
# per the confirmed design (always auto-detect, one unified queue-based UI
# regardless of count).
if action == "create":
    render_multi_tour_flow(client, supplier_id, currency, on_request, days_available_before_release, url, uploaded_files,
                          min_pax=min_pax, max_pax=max_pax, default_tour_code=provider_code,
                          extraction_hint=extraction_hint or None)
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
            seen_image_hashes = set()  # shared across all documents in this batch, so a logo repeated across files is only extracted once
            for uploaded in (uploaded_files or []):
                doc_names.append(uploaded.name)
                suffix = os.path.splitext(uploaded.name)[1]
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(uploaded.getbuffer())
                    tmp_path = tmp.name
                combined_parts.append(f"--- SOURCE: UPLOADED DOCUMENT ({uploaded.name}) ---\n{extract_raw_text(tmp_path)}")

                remaining_budget = 12 - len(doc_raw_images)
                embedded_images = extract_images(tmp_path, max_images=remaining_budget, seen_hashes=seen_image_hashes) if remaining_budget > 0 else []
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

            if len(doc_image_urls) >= len(doc_raw_images):
                doc_raw_images = []

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
                    data["image_urls"] = [FALLBACK_IMAGE]  # safe default - human picks below, this only stays if nothing gets chosen
                    if action == "update_tour":
                        # Merge on top of the tour's real live values (pre-filled in Step 3) rather
                        # than replacing them outright - an incomplete fresh extraction shouldn't
                        # blank out fields the new source just didn't happen to mention.
                        data = _merge_extraction_over_baseline(st.session_state.get("extracted") or {}, data)
                    st.session_state.extracted = data
                    st.session_state.images_text_value = ""
                    sources_desc = " + ".join(filter(None, [url] + doc_names))
                    st.session_state.raw_preview = f"Source(s): {sources_desc}\n\n{raw_text}"
                    st.session_state.payloads = None
                    st.session_state.doc_raw_images = doc_raw_images
                    st.session_state.hosted_image_candidates = list(dict.fromkeys((get_page_images(url) if url else []) + doc_image_urls))
                    st.success("Extraction complete. Review and edit below.")
        except Exception as e:
            st.error(f"Extraction failed: {friendly_error_message(e)}")

if st.session_state.get("pending_variants") and not is_option_only:
    variants = st.session_state.pending_variants
    st.warning(f"⚠️ This content describes {len(variants)} distinct tour variants — which one(s) do you want to add?")
    st.caption("Tick just one to continue in the normal single-tour flow below, or tick several to create "
              "them all as separate ClosedTours in one batch (you'll assign each its own Code next).")

    if "pending_variant_selection" not in st.session_state:
        st.session_state.pending_variant_selection = [
            {"label": v.get("label", f"Variant {i+1}"), "nights": v.get("nights"), "selected": False,
             "tour_code": "", "modality_code": "Standard"}
            for i, v in enumerate(variants)
        ]
    pv_selection = st.session_state.pending_variant_selection

    for i, sel in enumerate(pv_selection):
        nights_note = f" ({sel['nights']} nights)" if sel.get("nights") else ""
        sel["selected"] = st.checkbox(f"{sel['label']}{nights_note}", value=sel["selected"], key=f"pv_sel_{i}")

    pv_num_selected = sum(1 for s in pv_selection if s["selected"])

    if pv_num_selected > 1:
        st.caption("Multiple selected - each needs its own ClosedTour/Provider Code and Modality Code:")
        for i, sel in enumerate(pv_selection):
            if not sel["selected"]:
                continue
            pvcol1, pvcol2 = st.columns(2)
            with pvcol1:
                sel["tour_code"] = st.text_input(f"ClosedTour/Provider Code — {sel['label']}", value=sel["tour_code"], key=f"pv_code_{i}", placeholder="e.g. BKK-1")
            with pvcol2:
                sel["modality_code"] = st.text_input(f"Modality Code — {sel['label']}", value=sel["modality_code"], key=f"pv_modcode_{i}")

    pv_btn_label = "✅ Confirm and Extract Full Details" if pv_num_selected <= 1 else f"✅ Confirm and Start Batch Review ({pv_num_selected} tours)"
    if st.button(pv_btn_label, disabled=pv_num_selected == 0):
        if pv_num_selected <= 1:
            with st.spinner("Extracting full details for the selected variant..."):
                try:
                    chosen = next(s for s in pv_selection if s["selected"])
                    chosen_label = chosen["label"]
                    data = extract_structured_data(
                        st.session_state.pending_raw_text, variant_hint=chosen_label,
                        human_hint=st.session_state.get("pending_hint")
                    )

                    pending_url = st.session_state.get("pending_url")
                    data["image_urls"] = [FALLBACK_IMAGE]  # safe default - human picks below, this only stays if nothing gets chosen
                    preview = f"(Extracted variant: {chosen_label})\n\n{st.session_state.pending_raw_text}"
                    if action == "update_tour":
                        data = _merge_extraction_over_baseline(st.session_state.get("extracted") or {}, data)

                    st.session_state.extracted = data
                    st.session_state.images_text_value = ""
                    st.session_state.raw_preview = preview
                    st.session_state.payloads = None
                    st.session_state.doc_raw_images = st.session_state.get("pending_doc_raw_images", [])
                    st.session_state.hosted_image_candidates = list(dict.fromkeys((get_page_images(pending_url) if pending_url else []) + st.session_state.get("pending_doc_images", [])))
                    st.session_state.pending_variants = None
                    st.session_state.pending_raw_text = None
                    st.session_state.pending_url = None
                    st.session_state.pending_variant_selection = None
                    st.rerun()
                except Exception as e:
                    st.error(f"Extraction failed: {friendly_error_message(e)}")
        else:
            pv_missing = [s["label"] for s in pv_selection if s["selected"] and (not s["tour_code"].strip() or not s["modality_code"].strip())]
            pv_invalid = [s["modality_code"] for s in pv_selection if s["selected"] and any(c in s["modality_code"] for c in ["/", "\\", "+", "-"])]
            pv_codes_seen = {}
            for s in pv_selection:
                if s["selected"] and s["tour_code"].strip():
                    pv_codes_seen.setdefault(s["tour_code"].strip(), []).append(s["label"])
            pv_dupes = {c: labs for c, labs in pv_codes_seen.items() if len(labs) > 1}
            pv_existing = []
            for s in pv_selection:
                if s["selected"] and s["tour_code"].strip():
                    existing_check = check_code_availability(client, "tour", supplier_id, s["tour_code"])
                    if existing_check and existing_check["exists"]:
                        pv_existing.append(s["tour_code"].strip())
            if pv_missing:
                st.error(f"🚫 These selected variants are missing a ClosedTour Code or Modality Code: {pv_missing}")
            elif pv_invalid:
                st.error(f"🚫 These Modality Codes contain invalid characters (/, \\, +, -): {pv_invalid}")
            elif pv_dupes:
                st.error(f"🚫 These ClosedTour Codes are used by more than one selected variant: {list(pv_dupes.keys())}")
            elif pv_existing:
                st.error(f"🚫 These ClosedTour Codes are ALREADY TAKEN by existing tours - choose different "
                        f"ones: {pv_existing}")
            else:
                pending_url = st.session_state.get("pending_url")
                new_mct_queue = [
                    {"label": s["label"], "tour_code": s["tour_code"].strip(), "modality_code": s["modality_code"].strip(),
                     "data": None, "confirmed": False}
                    for s in pv_selection if s["selected"]
                ]
                st.session_state.mct_raw_text = st.session_state.pending_raw_text
                st.session_state.mct_doc_raw_images = st.session_state.get("pending_doc_raw_images", [])
                st.session_state.mct_hosted_image_candidates = list(dict.fromkeys((get_page_images(pending_url) if pending_url else []) + st.session_state.get("pending_doc_images", [])))
                st.session_state.mct_queue = new_mct_queue
                st.session_state.mct_queue_index = 0
                st.session_state.mct_phase = "reviewing"
                st.session_state.pending_variants = None
                st.session_state.pending_raw_text = None
                st.session_state.pending_url = None
                st.session_state.pending_variant_selection = None
                st.rerun()


# ----------------------------------------------------------------------
# STEP 5: Side-by-side review & edit
# ----------------------------------------------------------------------
if st.session_state.extracted:
    data = st.session_state.extracted

    st.header("Step 5 — Review & Edit")
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Original Source")
        render_readonly_source(st.session_state.raw_preview, height=600)

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
            editable_field("Description", data, "description", widget="html_text_area", height=200)
            editable_field("Hotels", data, "hotels_text", widget="text_area", height=140)
            editable_field("Included", data, "included", widget="text_area", height=120)
            editable_field("Excluded", data, "excluded", widget="text_area", height=120)
            editable_field("Meeting point", data, "meeting_point", widget="text_input")
            editable_field("Policy remarks", data, "policy_remarks", widget="text_area", height=100)
            editable_field("Nights", data, "nights", widget="number_input")

            tcol1, tcol2 = st.columns(2)
            with tcol1:
                render_optional_time_input("Start Time", data, "start_time", "ct_start_time")
            with tcol2:
                render_optional_time_input("End Time", data, "end_time", "ct_end_time", default_time_str="18:00:00")

            acol1, acol2 = st.columns(2)
            with acol1:
                data["min_child_age"] = st.number_input("Min Child Age", min_value=0, max_value=17,
                                                        value=int(data.get("min_child_age", 2) or 2), key="ct_min_child_age")
            with acol2:
                data["max_child_age"] = st.number_input("Max Child Age", min_value=0, max_value=17,
                                                        value=int(data.get("max_child_age", 12) or 12), key="ct_max_child_age")

            dest_rows = [{"#": i + 1, "Destination": d} for i, d in enumerate(data.get("itinerary_destinations", []))]
            dest_df = pd.DataFrame(dest_rows) if dest_rows else pd.DataFrame(columns=["#", "Destination"])

            def _save_destinations(edited_df):
                data["itinerary_destinations"] = [
                    str(row.get("Destination") or "").strip() for _, row in edited_df.iterrows()
                    if str(row.get("Destination", "") or "").strip()
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

            def _ct_add_url_images():
                selected = render_url_image_picker(st.session_state.hosted_image_candidates, "found_images")
                if selected:
                    current = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                    new_list = current + selected
                    data["image_urls"] = new_list
                    st.session_state._pending_images_update = "\n".join(new_list)
                    return len(selected)
                return 0

            render_closable_image_section(
                bool(st.session_state.get("hosted_image_candidates")),
                f"🖼️ Images found ({len(st.session_state.get('hosted_image_candidates') or [])}) - from the page/document",
                "found_images_closed", _ct_add_url_images
            )

            def _ct_add_doc_image():
                added = render_doc_image_picker(st.session_state.doc_raw_images, "doc_images")
                if added:
                    current = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                    new_list = current + [added]
                    data["image_urls"] = new_list
                    st.session_state._pending_images_update = "\n".join(new_list)
                    return 1
                return 0

            render_closable_image_section(
                bool(st.session_state.get("doc_raw_images")),
                f"📥 Images extracted from your document(s) ({len(st.session_state.get('doc_raw_images') or [])}) - need hosting",
                "doc_images_closed", _ct_add_doc_image
            )

            default_img_query = data.get("tour_name", "") or (data.get("itinerary_destinations")[0] if data.get("itinerary_destinations") else "")

            def _ct_add_pexels():
                selected = render_stock_photo_picker("Pexels", search_images, default_img_query, "pexels")
                if selected:
                    current = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                    new_list = current + selected
                    data["image_urls"] = new_list
                    st.session_state._pending_images_update = "\n".join(new_list)
                    return len(selected)
                return 0

            render_closable_image_section(True, "🖼️ Or search free stock photos (Pexels)", "pexels_closed", _ct_add_pexels)

            def _ct_add_pixabay():
                selected = render_stock_photo_picker("Pixabay", search_images_pixabay, default_img_query, "pixabay")
                if selected:
                    current = [u for u in data.get("image_urls", []) if u != FALLBACK_IMAGE]
                    new_list = current + selected
                    data["image_urls"] = new_list
                    st.session_state._pending_images_update = "\n".join(new_list)
                    return len(selected)
                return 0

            render_closable_image_section(True, "🖼️ Or search free stock photos (Pixabay)", "pixabay_closed", _ct_add_pixabay)

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
            "startDate": str(row.get("Start Date", "") or "").strip(),
            "endDate": str(row.get("End Date", "") or "").strip(),
            "price": price
        }
        name = str(row.get("Name", "") or "").strip()
        if name:
            entry["name"] = name
        return entry

    def _save_price_list(edited_df):
        data["price_list"] = sorted(
            [
                _row_to_price_entry(row) for _, row in edited_df.iterrows()
                if str(row.get("Start Date", "") or "").strip() and str(row.get("End Date", "") or "").strip()
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

    if action == "create":
        st.subheader("➕ Add more Modalities to create right away (optional)")
        st.caption("Add more room/cabin/product types now - all get created together with a SINGLE "
                  "deactivation at the end, so you don't need to manually reactivate the tour in Travel "
                  "Compositor between each one.")
        if "extra_modalities" not in st.session_state:
            st.session_state.extra_modalities = []

        for i, mod in enumerate(st.session_state.extra_modalities):
            st.markdown(f"**Modality {i + 2}**")
            mcol1, mcol2, mcol3 = st.columns([2, 2, 1])
            with mcol1:
                mod["code"] = st.text_input("Modality Code", value=mod["code"], key=f"extramod_code_{i}")
            with mcol2:
                mod["hint"] = st.text_input("Focus Hint (e.g. 'Deluxe Cabin')", value=mod["hint"], key=f"extramod_hint_{i}")
            with mcol3:
                st.write("")
                if st.button("🗑️ Remove", key=f"extramod_remove_{i}"):
                    st.session_state.extra_modalities.pop(i)
                    st.rerun()

            if any(c in (mod["code"] or "") for c in ["/", "\\", "+", "-"]):
                st.error(f"🚫 Modality Code '{mod['code']}' contains invalid characters (/, \\, +, -).")

            if st.button(f"🔎 Extract pricing focused on '{mod['hint'] or mod['code'] or 'this modality'}'", key=f"extramod_extract_{i}", disabled=not mod["code"]):
                with st.spinner("Extracting..."):
                    mod["data"] = extract_option_only_data(st.session_state.raw_preview, human_hint=mod["hint"])
                    st.rerun()

            if mod["data"]:
                render_seasonal_price_editor(f"Pricing - {mod['code'] or f'Modality {i + 2}'}", mod["data"], f"extramod_pricing_{i}", currency)
            else:
                st.info("Click 'Extract pricing' above to get started for this modality.")
            st.divider()

        if st.button("➕ Add another Modality"):
            st.session_state.extra_modalities.append({"code": "", "hint": "", "data": None})
            st.rerun()

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
                name = str(row.get("Name", "") or "").strip()
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
                # Force any affected table out of edit mode so it re-renders
                # fresh from the new data, rather than potentially showing a
                # stale cached data_editor state from before the AI change.
                field_to_table_key = {
                    "supplements": "_editing_table_supplements",
                    "price_list": "_editing_table_pricing",
                    "itinerary_destinations": "_editing_table_destinations",
                }
                for field_name in result["changes"]:
                    table_key = field_to_table_key.get(field_name)
                    if table_key:
                        st.session_state[table_key] = False
            st.rerun()
    if st.session_state.get("clarify_result"):
        r = st.session_state.clarify_result
        st.info(r.get("summary", ""))
        if r.get("changes"):
            st.caption(f"✅ Applied changes to: {', '.join(r['changes'].keys())} - review above before continuing.")

    if st.button("🔎 Resolve Destinations & Build Payload",
                disabled=not price_list_valid):
        _real_provider_code = st.session_state.get("fetched_tour_provider_code", "")
        with st.spinner("Resolving destinations against Travel Compositor..."):
            try:
                # HumanPreConfig() itself used to be constructed OUTSIDE this
                # try block - if provider_code didn't match the required
                # "XXX-Number" format, its pydantic validation raised
                # unguarded and crashed the whole app instead of showing a
                # contained error here. Moved inside the try so that failure
                # mode is caught too, not just failures inside
                # build_closed_tour_payloads.
                pre_config = HumanPreConfig(
                    supplier_id=supplier_id,
                    provider_code=provider_code or _real_provider_code or "XXX-1",
                    min_pax=min_pax, max_pax=max_pax, currency=currency,
                    modality_code=modality_code, on_request=on_request,
                    days_available_before_release=days_available_before_release
                )
                st.session_state.payloads = build_closed_tour_payloads(pre_config, data, client)
                st.session_state.pre_config = pre_config
            except Exception as e:
                # This used to be able to crash the whole app on a bad
                # destination/network hiccup instead of showing a contained
                # error - build_closed_tour_payloads itself now guards its
                # main construction, but keep this as a last-resort net for
                # anything upstream (e.g. the destination-resolution API
                # calls themselves).
                show_publish_error("resolve destinations / build the payload", str(e))

    if st.session_state.payloads:
        payloads = st.session_state.payloads

        st.header("Step 6 — Destination Resolution & Payload Preview")

        render_modalities_review(
            "tour", modality_code, "Base Modality", data,
            st.session_state.get("extra_modalities", []), currency
        )

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

        if payloads.get("is_indonesia"):
            st.info(f"🇮🇩 Indonesia detected in this itinerary — Vesak Day is automatically blocked as a "
                    f"stop-sale date, no excursion/tour may start that day. {payloads.get('vesak_day_note', '')}")

        if payloads.get("release_days_overridden"):
            st.info(f"📅 The document mentions its own booking/release deadline, so the release period "
                    f"being used is **{payloads['effective_release_days']} days** instead of your default - "
                    f"if the source mentioned more than one deadline, the longer (safer) one was used.")

        if payloads["unresolved_destinations"]:
            st.error(
                f"🚫 **{len(payloads['unresolved_destinations'])} destination(s) could NOT be matched "
                f"to a real Travel Compositor location:** {', '.join(payloads['unresolved_destinations'])}\n\n"
                f"This means Travel Compositor doesn't recognize this place by that name - publishing "
                f"would fail or create a wrong/broken itinerary stop. **To fix:** go back up to Step 5's "
                f"'Itinerary destinations' box and either correct the spelling/name, or replace it with "
                f"the exact name Travel Compositor uses, then click 'Resolve Destinations & Build Payload' again."
            )

        tour_update_blocks_publish = False
        if publish_action in ("Update an existing tour's details", "Update an existing option"):
            tour_update_blocks_publish = render_tour_update_comparison(
                publish_action, data, payloads, client, payloads["supplier_id"],
                existing_tour_code, st.session_state.get("working_tour_code"), modality_code
            )

        col3, col4 = st.columns(2)
        with col3:
            if publish_action == "Create a brand-new tour (+ first option)":
                title = "Main Tour Payload (POST - Call 1)"
            elif publish_action == "Update an existing tour's details":
                title = "Main Tour Payload (PUT - update)"
            else:
                title = "Main Tour Payload (not sent this time)"
            if payloads.get("main_tour_error"):
                show_publish_error("build the main tour payload", payloads["main_tour_error"])
            else:
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
                show_publish_error("build the tour option payload", payloads["tour_option_error"])
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
            and not payloads.get("main_tour_error")
            and not payloads["tour_option_error"]
            and not missing_existing_code
            and not missing_provider_code_for_update
            and not tour_update_blocks_publish
        )

        if tour_update_blocks_publish:
            st.info("Publishing is blocked until you either switch to 'Create a brand-new tour' (see the "
                   "message above) or fix the source so the night count matches what's currently live.")
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

        if creating_new_tour:
            dup_warning = check_duplicate_tour_name(client, payloads["supplier_id"], data.get("tour_name"))
            if dup_warning:
                col_dup1, col_dup2 = st.columns([5, 1])
                with col_dup1:
                    st.warning(dup_warning)
                with col_dup2:
                    if st.button("🔄 Re-check", key="recheck_dup_tour_name"):
                        st.session_state._existing_tours_cache.pop(payloads["supplier_id"], None)
                        st.rerun()

        ct_publish_as_active = True
        if creating_new_tour:
            ct_activation_choice = st.radio(
                "After publishing, should this Tour be Active or Inactive (draft)?",
                ["Inactive (draft) - recommended, review inside Travel Compositor before it goes live",
                 "Active - live immediately"],
                index=0, key="ct_activation_choice"
            )
            ct_publish_as_active = ct_activation_choice.startswith("Active")

        if st.button("🚀 Publish to Travel Compositor", disabled=not can_publish, type="primary"):
            with st.spinner("Sending to Travel Compositor..."):

                try:
                    if publish_action == "Create a brand-new tour (+ first option)":
                        creation_payload = dict(payloads["main_tour_payload"])
                        creation_payload["active"] = True
                        # CONFIRMED FIX (real production failure, KNO-1): same issue as the restructured
                        # create flow - build_closed_tour_payloads() only declares the BASE Modality's
                        # code in modalityCodes, but supplements tagged (via applies_to) to any of the
                        # OTHER queued Modalities reference codes not yet in that list, and Travel
                        # Compositor rejects the whole tour creation for it ("Modality code X not found
                        # in contract modalities"). Declare every queued Modality's code upfront.
                        _extra_mod_codes = [m.get("code") for m in st.session_state.get("extra_modalities", []) if m.get("code")]
                        creation_payload["modalityCodes"] = list(dict.fromkeys(
                            creation_payload.get("modalityCodes", []) + _extra_mod_codes
                        ))

                        result = client.create_closed_tour(payloads["supplier_id"], creation_payload)
                        if "error" in result:
                            show_publish_error("create the main tour", result)
                        else:
                            real_code = result.get('code', payloads['main_tour_code'])
                            st.success(f"✅ Main tour created (active) with real Code: **{real_code}** "
                                      f"— save this exact value, you'll need it for any future lookups, "
                                      f"updates, or adding more modalities to this tour.")

                            # Try the human-chosen ClosedTour/Provider Code first (confirmed working
                            # via direct API test), falling back to the internal 'code' if that fails -
                            # we've seen conflicting evidence about which one Travel Compositor's
                            # lookup actually uses, so don't bet everything on just one. (Each attempt
                            # below is itself already retried up to 6x internally by api_client.py's
                            # _request() - this loop is for trying the two different CODES, not retries.)
                            option_result = None
                            used_code = None
                            for candidate_code in [provider_code, real_code]:
                                option_result = client.create_closed_tour_option(
                                    payloads["supplier_id"], candidate_code, payloads["tour_option_payload"]
                                )
                                if "error" not in option_result:
                                    used_code = candidate_code
                                    break

                            if "error" not in option_result:
                                st.caption(f"(Option succeeded using code: `{used_code}`)")

                            if "error" in option_result:
                                show_publish_error(f"create the tour option after trying both `{provider_code}` and `{real_code}`", option_result)
                                st.info(f"💡 Adjustments to a ClosedTour require it to be ACTIVE - inactive tours "
                                       f"aren't visible via the API. The tour was created with active:true, but if "
                                       f"this keeps failing, check inside Travel Compositor whether `{real_code}` "
                                       f"shows as active, and try 'Add a new option to an existing tour' manually once confirmed.")
                            else:
                                st.success("✅ Tour option created.")

                                extra_modalities = st.session_state.get("extra_modalities", [])
                                if extra_modalities:
                                    st.markdown("**Creating additional modalities...**")
                                    for mod in extra_modalities:
                                        if not mod.get("code") or not mod.get("data"):
                                            st.warning("⚠️ Skipped a modality - missing code or pricing data.")
                                            continue
                                        with st.spinner(f"Creating modality '{mod['code']}'..."):
                                            try:
                                                mod_pre_config = HumanPreConfig(
                                                    supplier_id=payloads["supplier_id"], provider_code=provider_code or _real_provider_code or "XXX-1",
                                                    min_pax=min_pax, max_pax=max_pax, currency=currency,
                                                    modality_code=mod["code"], on_request=on_request,
                                                    days_available_before_release=days_available_before_release
                                                )
                                                mod_payloads = build_closed_tour_payloads(mod_pre_config, mod["data"], client)
                                                if mod_payloads["tour_option_error"]:
                                                    show_publish_error(f"prepare modality '{mod['code']}'", mod_payloads["tour_option_error"])
                                                    continue
                                                mod_result, mod_used_code = try_code_variants(
                                                    lambda c: client.create_closed_tour_option(payloads["supplier_id"], c, mod_payloads["tour_option_payload"]),
                                                    [provider_code or _real_provider_code, real_code]
                                                )
                                                if "error" in mod_result:
                                                    show_publish_error(f"create modality '{mod['code']}'", mod_result)
                                                else:
                                                    st.success(f"✅ Modality '{mod['code']}' created.")
                                            except Exception as e:
                                                show_publish_error(f"create modality '{mod['code']}' (unexpected error - skipped, rest continues)", str(e))
                                                continue

                                if ct_publish_as_active:
                                    st.success(f"✅ Tour `{real_code}` left ACTIVE, as chosen above - it's live now.")
                                    st.session_state.just_published_tour_code = real_code
                                    st.session_state.just_published_supplier_id = payloads["supplier_id"]
                                    st.session_state.just_published_is_inactive = False
                                    st.session_state.extra_modalities = []
                                else:
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
                                        st.session_state.just_published_is_inactive = True
                                        st.session_state.extra_modalities = []

                    elif publish_action == "Add a new option to an existing tour":
                        option_result, used_code = try_code_variants(
                            lambda c: client.create_closed_tour_option(payloads["supplier_id"], c, payloads["tour_option_payload"]),
                            target_tour_code
                        )
                        if "error" in option_result:
                            show_publish_error(f"add the option (tried both `{target_tour_code}` and its CLOSEDTOUR- variant)", option_result)
                            st.info(f"💡 Adjustments to a ClosedTour require it to be ACTIVE - inactive tours "
                                   f"aren't visible via the API. Activate `{target_tour_code}` inside Travel "
                                   f"Compositor first, then retry (you can switch it back to inactive/draft afterward).")
                        else:
                            st.success(f"✅ New option added to existing tour using code `{used_code}`. Verify inside Travel Compositor.")
                            st.session_state.just_published_tour_code = target_tour_code
                            st.session_state.just_published_supplier_id = payloads["supplier_id"]
                            st.session_state.just_published_is_inactive = False

                    elif publish_action == "Update an existing tour's details":
                        update_payload = dict(payloads["main_tour_payload"])
                        update_payload["code"] = target_tour_code
                        result, used_code = try_code_variants(
                            lambda c: client.update_closed_tour(payloads["supplier_id"], {**update_payload, "code": c}),
                            target_tour_code
                        )
                        if "error" in result:
                            show_publish_error(f"update the tour (tried both `{target_tour_code}` and its CLOSEDTOUR- variant)", result)
                            st.info(f"💡 Adjustments to a ClosedTour require it to be ACTIVE - inactive tours "
                                   f"aren't visible via the API. Activate `{target_tour_code}` inside Travel Compositor first, then retry.")
                        else:
                            st.success(f"✅ Tour updated using code `{used_code}`.")
                            st.session_state.just_published_tour_code = target_tour_code
                            st.session_state.just_published_supplier_id = payloads["supplier_id"]
                            st.session_state.just_published_is_inactive = False

                    elif publish_action == "Update an existing option":
                        update_option_payload = dict(payloads["tour_option_payload"])
                        update_option_payload["code"] = modality_code
                        option_result, used_code = try_code_variants(
                            lambda c: client.update_closed_tour_option(payloads["supplier_id"], c, update_option_payload),
                            target_tour_code
                        )
                        if "error" in option_result:
                            show_publish_error(f"update the option (tried both `{target_tour_code}` and its CLOSEDTOUR- variant)", option_result)
                            st.info(f"💡 Adjustments to a ClosedTour require it to be ACTIVE - inactive tours "
                                   f"aren't visible via the API. Activate `{target_tour_code}` inside Travel Compositor first, then retry.")
                        else:
                            st.success(f"✅ Option `{modality_code}` under tour (code `{used_code}`) updated.")
                            st.session_state.just_published_tour_code = target_tour_code
                            st.session_state.just_published_supplier_id = payloads["supplier_id"]
                            st.session_state.just_published_is_inactive = False
                except Exception as e:
                    # This used to be able to crash the whole app on any
                    # unhandled exception (network error, unexpected API
                    # response shape, etc.) partway through publishing -
                    # now it shows a contained error instead.
                    show_publish_error("publish the tour (unexpected error)", str(e))

# ----------------------------------------------------------------------
# Post-publish follow-up: what next?
# ----------------------------------------------------------------------
if st.session_state.get("just_published_tour_code"):
    st.divider()
    st.subheader("✅ ClosedTour published — what would you like to do next?")
    st.write(f"Just published: **{st.session_state.just_published_tour_code}** "
            f"(Supplier {st.session_state.just_published_supplier_id})")

    if st.session_state.get("just_published_is_inactive"):
        st.warning("⚠️ **This ClosedTour is now INACTIVE.** It was created, given its first Modality, then "
                  "switched back to draft/inactive for your review — this is expected. To add more "
                  "Modalities or make further changes, first **activate it manually inside Travel "
                  "Compositor**, then come back and use 'Add new Modality to existing ClosedTour'.")
        if st.button("🆕 Start a new import (different ClosedTour)", type="primary"):
            keep_client = st.session_state.client
            keep_suppliers = st.session_state.suppliers_cache
            keep_product_type = st.session_state.product_type
            st.session_state.clear()
            st.session_state.client = keep_client
            st.session_state.suppliers_cache = keep_suppliers
            st.session_state.product_type = keep_product_type
            st.rerun()
    else:
        fcol1, fcol2 = st.columns(2)
        with fcol1:
            if st.button("🆕 Start a new import (different ClosedTour)", type="primary"):
                keep_client = st.session_state.client
                keep_suppliers = st.session_state.suppliers_cache
                keep_product_type = st.session_state.product_type
                st.session_state.clear()
                st.session_state.client = keep_client
                st.session_state.suppliers_cache = keep_suppliers
                st.session_state.product_type = keep_product_type
                st.rerun()
        with fcol2:
            if st.button("➕ Add another Modality to this same ClosedTour"):
                prefill_tour_code = st.session_state.just_published_tour_code
                prefill_supplier_id = st.session_state.just_published_supplier_id
                keep_client = st.session_state.client
                keep_suppliers = st.session_state.suppliers_cache
                keep_product_type = st.session_state.product_type
                st.session_state.clear()
                st.session_state.client = keep_client
                st.session_state.suppliers_cache = keep_suppliers
                st.session_state.product_type = keep_product_type
                st.session_state.cfg_action = "add_option"
                st.session_state.cfg_supplier_id = prefill_supplier_id
                st.session_state.cfg_existing_tour_code = prefill_tour_code
                st.session_state.prefill_existing_tour_code = prefill_tour_code
                st.session_state.step1_confirmed = True
                st.rerun()
