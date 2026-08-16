"""
ui_components.py — the Streamlit building blocks every product-type flow shares.

WHY THIS FILE EXISTS (product-owner request): app.py used to define these directly, and while
they were already single functions called from all five flows (ClosedTour, Ticket, Transfer,
Transport, Hotel), living inside one 10,000-line file made that easy to lose track of. Moving
them here is a pure reorganisation - same functions, same behaviour, same call signatures -
so "where do the shared widgets live" has one obvious answer instead of "somewhere in app.py".

ALSO FIXED WHILE MOVING (real duplication, not just organisation): Transfer, Transport and Hotel
each had their OWN inline cancellation-fee-tiers table - a plain pd.DataFrame with raw "days"/
"fee_percentage" columns, no expander, no caption, no sort-by-days, no clamping of the fee
percentage to 0-100 - instead of calling render_cancellation_policy_editor() below, which
ClosedTour and Ticket already used. That is exactly the failure mode the product owner flagged
("the cancellation policy editor was fixed for ClosedTour but not for Ticket"): a fix made to
the shared editor silently could not reach three of the five flows because they weren't
actually sharing it. All five flows now call the same function.
"""

# Stamped on every delivery. app.py compares this against its own build string and says
# so on screen when they differ - a partial push (one file committed, another not) used to
# surface only as a traceback whose line numbers pointed at unrelated code.
MODULE_BUILD = "2026-08-16-outreach-no-combo-cap-one-supplier-per-combo"

import re
import math
from datetime import datetime

import streamlit as st
import pandas as pd

from builder import (
    coerce_price_list_shape, _MAX_OCCUPANCY_PAX as _TICKET_MAX_OCCUPANCY_PAX,
    resolve_ticket_child_price_ratio,
)
# HOUSE RULE (product owner): "always for Date: DD/MM/YYYY". That is what a human reads and
# types; Travel Compositor only accepts the ISO wire format, so every screen converts at the
# boundary and the payload stays ISO throughout. Both helpers accept both forms - see date_format.py.
from date_format import to_iso_date as _iso, to_display_date as _disp
from web_extractor import get_page_image_bytes
from freeimage_client import (
    upload_images as upload_images_freeimage,
    upload_images_with_errors as upload_images_freeimage_with_errors,
)
from ai_extractor import friendly_error_message

SUPPLEMENT_COLUMNS = ["Name", "Price (per person)", "Single", "Double", "Triple", "Quadruple",
                      "Per Pax", "Mandatory", "On Request",
                      "Special Travel Start Date", "Special Travel End Date"]


def _safe_cell_str(value):
    """
    Safely converts a single st.data_editor/DataFrame cell value to a plain
    stripped string. CONFIRMED FIX (real production bug): a blank/new row
    added via the data_editor's "+" button holds None for every cell, but
    once that row sits in the SAME DataFrame column as other rows holding
    real strings, pandas silently promotes the blank cell's None to NaN (its
    own missing-value marker) to keep the column's dtype consistent. NaN is
    NOT falsy in Python (`nan or ""` evaluates to `nan`, not ""), so the
    previous guard pattern `str(row.get(col, "") or "")` still let a blank
    row's NaN through as the literal 4-character text "nan" - the exact
    same class of bug as the earlier "None"-string crash, just with pandas'
    own missing-value marker instead of Python's. This treats BOTH None and
    NaN as genuinely empty. Returns "" for either; str(value) unchanged
    otherwise.
    """
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value)


def _safe_float(value, fallback=0.0):
    """
    Numeric counterpart to _safe_cell_str(), for reading a NUMERIC
    data_editor cell (a price, an occupancy count, etc.) instead of a text
    one. CONFIRMED FIX (real production crash, LXR-3): "Out of range float
    values are not JSON compliant: nan" - the same blank-row NaN promotion
    _safe_cell_str() guards against for text columns also happens to numeric
    columns, but the common "value or 0" guard pattern used around the app
    does NOT catch it, because NaN is TRUTHY in Python (only 0/0.0/None/""/
    False are falsy) - float(nan or 0) still returns nan, not 0. That nan
    then survives all the way to publish time, where requests' json=
    serialization explicitly rejects it (unlike Python's own json.dumps,
    which allows NaN by default) and crashes with this exact error. This
    explicitly checks for NaN (and Infinity, equally invalid JSON) on top of
    the normal None/non-numeric cases float() itself would raise on.
    """
    if value is None:
        return fallback
    if isinstance(value, float) and pd.isna(value):
        return fallback
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    if math.isnan(result) or math.isinf(result):
        return fallback
    return result


def _safe_int(value, fallback=0):
    """Same NaN/Infinity/non-numeric safety as _safe_float, but returns an int."""
    result = _safe_float(value, fallback=None)
    return fallback if result is None else int(result)


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


def render_seasonal_price_editor(label, target_data, edit_key, currency):
    """
    Renders an editable seasonal price list table (Name/Start/End/Single/
    Double/Triple/Quadruple) bound to target_data["price_list"], matching
    the exact ClosedTour pricing shape. Reusable for the main modality and
    for any additional modalities being created in the same batch.
    """
    default_price_list = sorted(
        coerce_price_list_shape(target_data.get("price_list"), currency)[0] or [{
            "name": "Example row - edit or delete", "startDate": "2027-01-01", "endDate": "2027-12-31",
            "price": {"singlePrice": {"amount": 0, "currency": currency}, "doublePrice": {"amount": 0, "currency": currency}}
        }],
        key=lambda entry: entry.get("startDate", "")   # SORT ON ISO, never the display form: "03/12" would sort before "28/01"
    )
    target_data["price_list"] = default_price_list

    price_df_rows = []
    for entry in default_price_list:
        price = entry.get("price") if isinstance(entry.get("price"), dict) else {}
        def _amt(key, price=price):
            block = price.get(key)
            if isinstance(block, dict):
                block = block.get("amount")
            try:
                return float(block) if block not in (None, "") else None
            except (TypeError, ValueError):
                return None
        price_df_rows.append({"Name": entry.get("name", ""), "Start Date": _disp(entry.get("startDate", "")),
                              "End Date": _disp(entry.get("endDate", "")), "Single": _amt("singlePrice"),
                              "Double": _amt("doublePrice"), "Triple": _amt("triplePrice"), "Quadruple": _amt("quadruplePrice")})
    price_df = pd.DataFrame(price_df_rows)

    def _save(edited_df, target_data=target_data, currency=currency):
        def _row_to_entry(row):
            price = {}
            for col, key in [("Single", "singlePrice"), ("Double", "doublePrice"), ("Triple", "triplePrice"), ("Quadruple", "quadruplePrice")]:
                val = row.get(col)
                if val is not None and not pd.isna(val):
                    price[key] = {"amount": float(val), "currency": currency}
            entry = {"startDate": _iso(_safe_cell_str(row.get("Start Date"))), "endDate": _iso(_safe_cell_str(row.get("End Date"))), "price": price}
            name = _safe_cell_str(row.get("Name")).strip()
            if name:
                entry["name"] = name
            return entry
        target_data["price_list"] = sorted(
            [_row_to_entry(r) for _, r in edited_df.iterrows() if _iso(_safe_cell_str(r.get("Start Date"))) and _iso(_safe_cell_str(r.get("End Date")))],
            key=lambda e: e.get("startDate", "")
        )

    editable_table(label, price_df, edit_key, on_save=_save)


def render_stop_sales_editor(data, key_prefix, help_text=None):
    """
    Friendly Start/End Date table for a ClosedTour Modality's stop_sales
    (blocked date ranges - ContractClosedTourOptionVO.stopSales). Replaces a
    raw JSON array text box: a typo there silently produced a JSON error
    that was easy to miss inside a collapsed expander, and it wasn't obvious
    that MULTIPLE separate blocked periods are fully supported (this is a
    list, not a single range - a tour can have several unrelated closures,
    e.g. two different maintenance windows plus a holiday blackout). Mirrors
    the same editable_table pattern already used for price_list/itinerary/
    supplements elsewhere, so adding/removing rows works the same way.
    """
    with st.expander(f"Stop Sales ({len(data.get('stop_sales') or [])} blocked date range(s))"):
        if help_text:
            st.caption(help_text)
        st.caption("Each row blocks bookings for that date range. There's no limit to how many rows you can "
                  "add - use a separate row for each distinct blocked period (e.g. a maintenance closure AND "
                  "a holiday blackout are two rows, not one).")
        stop_rows = [{"Start Date": _disp(s.get("start", "")), "End Date": _disp(s.get("end", ""))} for s in (data.get("stop_sales") or []) if isinstance(s, dict)]
        stop_df = pd.DataFrame(stop_rows) if stop_rows else pd.DataFrame(columns=["Start Date", "End Date"])

        def _save_stop_sales(edited_df, data=data):
            new_stops = []
            for _, row in edited_df.iterrows():
                start = _iso(_safe_cell_str(row.get("Start Date")))
                end = _iso(_safe_cell_str(row.get("End Date")))
                if start and end:
                    new_stops.append({"start": start, "end": end})
            data["stop_sales"] = new_stops

        editable_table("Blocked date ranges", stop_df, f"{key_prefix}_stop_sales", on_save=_save_stop_sales)


def render_ticket_pricing_editor(data, key_prefix, currency, max_passengers):
    """
    CONFIRMED PRODUCT-OWNER REQUEST (2026-08-13): "Can we only add occupancy or per Service for
    the ticket. Always Occupancy first ... and 9 rows and in each row one pax with one price. If
    the price is always same (like Distribution, then it would just 9 times the same price added
    in each row)." Tickets used to offer 3 pricing modes (Distribution/Occupancy/Service) -
    Distribution (a flat per-person Adult/Child/Infant rate) is retired from this editor entirely:
    only Occupancy (always exactly `cap` rows, 1 through this Ticket's own Max Passengers, capped
    at the platform-wide 9) and Service (one flat total) remain, so there's only ever one pricing
    shape a human has to think about. A legacy Distribution-priced modality (from before this
    change, or fetched live from Travel Compositor for editing) is transparently converted here
    into an Occupancy table with its old flat Adult price repeated across every row - exactly what
    the product owner described as "9 times the same price added in each row".

    Mutates `data` in place (same convention as the rest of this file): sets/normalizes
    data["price_type"] and data["occupancy_prices"]/data["base_service_price"]. Call this ONCE per
    modality per render, before reading data["price_type"] elsewhere in the same flow.
    """
    cap = min(_TICKET_MAX_OCCUPANCY_PAX, _safe_int(max_passengers, fallback=_TICKET_MAX_OCCUPANCY_PAX))

    if (data.get("price_type") or "OCCUPANCY") == "DISTRIBUTION":
        flat_price = _safe_float(data.get("base_adult_price", 0))
        child_price = _safe_float(data.get("base_children_price", 0))
        infant_price = _safe_float(data.get("base_infant_price", 0))
        existing_occ = {_safe_int(o.get("occupancy", 1), fallback=1): _safe_float(o.get("amount", 0))
                       for o in (data.get("occupancy_prices") or []) if isinstance(o, dict)}
        data["occupancy_prices"] = [
            {"occupancy": n, "amount": existing_occ.get(n, flat_price)} for n in range(1, cap + 1)
        ]
        data["price_type"] = "OCCUPANCY"
        if child_price != flat_price or infant_price:
            note = (f"Converted from a flat per-person price - Adult was {flat_price}, Child was "
                   f"{child_price}, Infant was {infant_price} {currency}. The Occupancy table now "
                   f"repeats the Adult price on every row; double-check this still matches what "
                   f"the source intended if Child/Infant pricing genuinely differed.")
            existing_notes = (data.get("pricing_notes") or "").strip()
            if note not in existing_notes:
                data["pricing_notes"] = (existing_notes + " " + note).strip() if existing_notes else note

    st.caption("A Ticket Modality holds ONE price setup + ONE validity date range (not a seasonal table). "
              "For holiday/seasonal price differences, use dated Supplements below instead.")

    price_type = st.radio(
        "Pricing Mode", ["OCCUPANCY", "SERVICE"],
        index=["OCCUPANCY", "SERVICE"].index(data.get("price_type") or "OCCUPANCY"),
        format_func=lambda x: {
            "OCCUPANCY": "Occupancy - price varies by group size (infants free, not counted)",
            "SERVICE": "Service - one flat total price regardless of headcount",
        }[x],
        key=f"{key_prefix}_price_type"
    )
    data["price_type"] = price_type

    if price_type == "SERVICE":
        data["base_service_price"] = st.number_input(
            "Total Service Price (flat, regardless of group size)", min_value=0.0,
            value=float(data.get("base_service_price", 0) or 0), key=f"{key_prefix}_service_price"
        )
        return

    # OCCUPANCY - always exactly `cap` rows, 1 through cap, Pax column locked.
    st.caption(f"Always exactly {cap} row(s) - 1 through this Ticket's Max Passengers ({max_passengers}, "
              f"capped at the platform-wide 9-pax limit). Infants are always free and excluded "
              f"automatically. If your source gives one flat price regardless of group size, use the "
              f"quick-fill below to repeat it across every row.")

    # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-13): "when child age is between 2 and 12, we must
    # add a child price column next to adult price in pricing table. If not other stated, the
    # child price = adult price. If Document says child between 2 to 11.99 50% off, the child
    # price = adult price/2." Shown whenever this Ticket allows children at all - reuses the
    # already-extracted base_adult_price/base_children_price (see resolve_ticket_child_price_ratio
    # docstring for why those are reliable even for an Occupancy-priced Ticket) to derive the
    # default ratio, so a document that stated a distinct child rate doesn't need re-typing here.
    children_allowed = not bool(data.get("disallow_children", False))
    child_age_min = _safe_int(data.get("child_age_min", 2), fallback=2)
    child_age_max = _safe_int(data.get("child_age_max", 12), fallback=12)
    child_ratio = resolve_ticket_child_price_ratio(
        data.get("base_adult_price", 0), data.get("base_children_price", 0)
    )

    existing_occ = {_safe_int(o.get("occupancy", 1), fallback=1): _safe_float(o.get("amount", 0))
                   for o in (data.get("occupancy_prices") or []) if isinstance(o, dict)
                   and _safe_int(o.get("occupancy", 1), fallback=1) <= cap}
    existing_child = {_safe_int(o.get("occupancy", 1), fallback=1): o.get("child_amount")
                      for o in (data.get("occupancy_prices") or []) if isinstance(o, dict)
                      and _safe_int(o.get("occupancy", 1), fallback=1) <= cap
                      and o.get("child_amount") not in (None, "")}
    data["occupancy_prices"] = []
    for n in range(1, cap + 1):
        amount = existing_occ.get(n, 0)
        row = {"occupancy": n, "amount": amount}
        if children_allowed:
            row["child_amount"] = _safe_float(existing_child.get(n, round(amount * child_ratio, 2)))
        data["occupancy_prices"] = data["occupancy_prices"] + [row]

    with st.expander("💨 Quick-fill: same price for every row"):
        st.caption("Use this when the source gives one flat price regardless of group size - fills all "
                  "rows below with the same amount (this replaces the old Distribution mode).")
        qcol1, qcol2 = st.columns([3, 1])
        with qcol1:
            quick_price = st.number_input("Price for all rows", min_value=0.0, value=0.0, key=f"{key_prefix}_occ_quickfill_price")
        with qcol2:
            st.write("")
            if st.button("Apply to all rows", key=f"{key_prefix}_occ_quickfill_apply"):
                data["occupancy_prices"] = [
                    {"occupancy": n, "amount": quick_price,
                     **({"child_amount": round(quick_price * child_ratio, 2)} if children_allowed else {})}
                    for n in range(1, cap + 1)
                ]
                st.rerun()
        if children_allowed:
            st.caption(f"Child price default: {child_ratio:.0%} of the adult price on each row "
                      f"(age {child_age_min}-{child_age_max}) - edit any row's Child Price directly "
                      f"below to override.")
            if st.button("Reset all Child Price cells to that default", key=f"{key_prefix}_occ_child_reset"):
                data["occupancy_prices"] = [
                    {"occupancy": o["occupancy"], "amount": o["amount"],
                     "child_amount": round(o["amount"] * child_ratio, 2)}
                    for o in data["occupancy_prices"]
                ]
                st.rerun()

    if children_allowed:
        occ_df = pd.DataFrame([
            {"Pax": o["occupancy"], "Price": o["amount"], "Child Price": o.get("child_amount", o["amount"])}
            for o in data["occupancy_prices"]
        ])

        def _save_occupancy(edf, data=data, cap=cap):
            edited = {_safe_int(r.get("Pax"), 1): (_safe_float(r.get("Price")), _safe_float(r.get("Child Price")))
                     for _, r in edf.iterrows()}
            data["occupancy_prices"] = [
                {"occupancy": n, "amount": edited.get(n, (0, 0))[0], "child_amount": edited.get(n, (0, 0))[1]}
                for n in range(1, cap + 1)
            ]

        editable_table(
            f"Occupancy Price ({cap} row(s), 1-{cap} pax) - Child Price covers age {child_age_min}-{child_age_max}",
            occ_df, f"{key_prefix}_occupancy",
            on_save=_save_occupancy, num_rows="fixed",
            column_config={"Pax": st.column_config.NumberColumn(disabled=True)}
        )
    else:
        occ_df = pd.DataFrame([{"Pax": o["occupancy"], "Price": o["amount"]} for o in data["occupancy_prices"]])

        def _save_occupancy(edf, data=data, cap=cap):
            edited = {_safe_int(r.get("Pax"), 1): _safe_float(r.get("Price")) for _, r in edf.iterrows()}
            data["occupancy_prices"] = [{"occupancy": n, "amount": edited.get(n, 0)} for n in range(1, cap + 1)]

        editable_table(
            f"Occupancy Price ({cap} row(s), 1-{cap} pax)", occ_df, f"{key_prefix}_occupancy",
            on_save=_save_occupancy, num_rows="fixed",
            column_config={"Pax": st.column_config.NumberColumn(disabled=True)}
        )


def render_ticket_modality_supplements_editor(data, key_prefix, help_text=None):
    """
    Friendly Name/Start/End/Adult/Child/Infant table for a Ticket Modality's
    modality_supplements (ContractTicketModalityVO.supplements - a DATED
    price change to this specific Modality, e.g. a High Season price row or
    a Tet Holiday guide surcharge).

    CORRECTED 2026-08-12 (product owner): "Main Ticket information has no
    supplement, Modality of a Ticket has their own supplement." An earlier
    version of this app treated Tickets as having no supplements at all -
    that was too broad. A genuinely different product a customer CHOOSES
    (a foreign-language guide, a Seat-in-Coach option) still becomes its own
    Modality via Extra Costs above, unchanged - this editor is only for a
    dated price bump on THIS Modality, never a product choice.

    Each row needs both a Start and End Date (TicketSupplementVO has no
    undated fallback) - a row missing either is silently dropped rather than
    published, since an undated Ticket supplement can't be told apart from a
    permanent price rise.
    """
    with st.expander(f"Seasonal / Holiday Supplements ({len(data.get('modality_supplements') or [])} dated price change(s))"):
        if help_text:
            st.caption(help_text)
        st.caption("Each row adds an EXTRA amount on top of this Modality's base price during that date "
                  "range only - e.g. a High Season row, or a holiday guide surcharge. For a genuinely "
                  "different product a customer chooses between (another guide language, Seat-in-Coach), "
                  "use Extra Costs above instead - this is only for the same product costing more on "
                  "certain dates.")
        supp_rows = [
            {"Name": s.get("name", ""), "Start Date": _disp(s.get("start_date", "")), "End Date": _disp(s.get("end_date", "")),
             "Adult Extra": s.get("adult_price_supplement", 0), "Child Extra": s.get("children_price_supplement", 0),
             "Infant Extra": s.get("infant_price_supplement", 0)}
            for s in (data.get("modality_supplements") or []) if isinstance(s, dict)
        ]
        supp_df = pd.DataFrame(supp_rows) if supp_rows else pd.DataFrame(
            columns=["Name", "Start Date", "End Date", "Adult Extra", "Child Extra", "Infant Extra"])

        def _save_modality_supplements(edited_df, data=data):
            new_supplements = []
            for _, row in edited_df.iterrows():
                start = _iso(_safe_cell_str(row.get("Start Date")))
                end = _iso(_safe_cell_str(row.get("End Date")))
                if not start or not end:
                    continue
                new_supplements.append({
                    "name": _safe_cell_str(row.get("Name")).strip() or "Seasonal surcharge",
                    "start_date": start, "end_date": end,
                    "adult_price_supplement": _safe_float(row.get("Adult Extra")),
                    "children_price_supplement": _safe_float(row.get("Child Extra")),
                    "infant_price_supplement": _safe_float(row.get("Infant Extra")),
                })
            data["modality_supplements"] = new_supplements

        editable_table("Dated price changes", supp_df, f"{key_prefix}_modality_supplements", on_save=_save_modality_supplements)


def render_cancellation_policy_editor(data, key_prefix, help_text=None):
    """
    Friendly Days/Fee% table for the tour's/ticket's/transfer's/transport's/
    hotel's cancellation policy (cancellation_policy_tiers - see
    ai_extractor.py's extraction rule and builder.py's
    _cancellation_ranges_from_tiers for how this gets converted into Travel
    Compositor's cancellationRanges field).

    CONFIRMED REAL RULE (human feedback): this used to be silently hardcoded
    to a flat 30-days/100%-refund default for every tour/ticket regardless
    of what the supplier's own contract said - now the AI extracts the
    supplier's own specific policy when the source states one, but a human
    should always review/adjust it here before publishing since this
    directly affects real money.

    Rows are entered the way a supplier's contract normally states them (a
    cancellation FEE % charged from N days before arrival) - the fee-to-
    refund-percentage conversion for Travel Compositor's own API field
    happens later in builder.py, not here, so what's shown here matches
    what's actually in the source document.

    ONE EDITOR FOR ALL FIVE PRODUCT TYPES. Transfer, Transport and Hotel used to each carry
    their own inline copy of this table (raw "days"/"fee_percentage" columns, no expander, no
    sort, no 0-100 clamp on the fee) instead of calling this function - so a fix made here for
    ClosedTour/Ticket silently never reached those three. There is now exactly one
    implementation, used by every flow.
    """
    with st.expander(f"💰 Cancellation Policy ({len(data.get('cancellation_policy_tiers') or [])} tier(s) - leave empty for the default 30 days / no fee)"):
        if help_text:
            st.caption(help_text)
        st.caption("Each row: from this many days before arrival (or more), this cancellation fee % applies. "
                  "Leave the table empty to use the standard default (free cancellation 30+ days before "
                  "arrival, 100% fee after). Add one row per tier from the supplier's contract, e.g. a row "
                  "with Days=91 and Fee=25 means \"91+ days before arrival: 25% cancellation fee\".")
        tier_rows = [
            {"Days before arrival (or more)": t.get("days"), "Cancellation Fee %": t.get("fee_percentage")}
            for t in (data.get("cancellation_policy_tiers") or []) if isinstance(t, dict)
        ]
        tier_df = pd.DataFrame(tier_rows) if tier_rows else pd.DataFrame(columns=["Days before arrival (or more)", "Cancellation Fee %"])

        def _save_cancellation_tiers(edited_df, data=data):
            new_tiers = []
            for _, row in edited_df.iterrows():
                days_val = row.get("Days before arrival (or more)")
                fee_val = row.get("Cancellation Fee %")
                if days_val is None or (isinstance(days_val, float) and pd.isna(days_val)):
                    continue
                new_tiers.append({
                    "days": _safe_int(days_val, fallback=0),
                    "fee_percentage": max(0.0, min(100.0, _safe_float(fee_val, fallback=0.0))),
                })
            new_tiers.sort(key=lambda t: t["days"], reverse=True)
            data["cancellation_policy_tiers"] = new_tiers

        editable_table("Cancellation fee tiers", tier_df, f"{key_prefix}_cancellation_tiers",
                       on_save=_save_cancellation_tiers,
                       column_config={
                           "Days before arrival (or more)": st.column_config.NumberColumn(min_value=0, step=1),
                           "Cancellation Fee %": st.column_config.NumberColumn(min_value=0, max_value=100, step=1),
                       })


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


def _html_list_to_plain_for_editing(html):
    """
    Bullet-list counterpart to _html_to_plain_for_editing() - converts a
    <ul><li>item</li>...</ul> string (the format Included/Excluded are
    stored in, per ai_extractor.py's EXTRACTION_SYSTEM_PROMPT) into plain
    text, one item per line, no visible tags. CONFIRMED REAL BUG: Included/
    Excluded used to be edited with the generic "text_area" widget, which
    just showed the raw stored value - meaning a human clicking the pencil
    to edit it saw literal "<ul><li>Breakfast</li><li>Lunch</li></ul>" code
    instead of a plain list, exactly the "code language" a non-technical
    human shouldn't have to deal with.
    """
    if not html:
        return ""
    text = html
    text = re.sub(r"<(strong|b)>(.*?)</\1>", r"**\2**", text, flags=re.I | re.S)
    text = re.sub(r"<(em|i)>(.*?)</\1>", r"*\2*", text, flags=re.I | re.S)
    items = re.findall(r"<li[^>]*>(.*?)</li>", text, flags=re.I | re.S)
    if items:
        lines = [re.sub(r"<[^>]+>", "", item).strip() for item in items]
        return "\n".join(line for line in lines if line)
    # No <li> tags found (e.g. older plain-text data, or a stray format) -
    # just strip any tags rather than showing them raw, so this is always
    # safe to call regardless of what shape the stored value happens to be.
    return re.sub(r"<[^>]+>", "", text).strip()


def _plain_list_to_html_for_saving(text):
    """
    Reverses _html_list_to_plain_for_editing(): one item per line (blank
    lines ignored) becomes a <ul><li>...</li></ul> bullet list - the EXACT
    format Travel Compositor's Included/Excluded fields require (confirmed
    against a real published tour, see ai_extractor.py's prompt) - so the
    human only ever types/sees plain lines, never HTML, while the correct
    markup still gets saved underneath automatically.
    """
    if not text or not text.strip():
        return ""
    items = [line.strip() for line in text.strip().split("\n") if line.strip()]
    if not items:
        return ""
    li_parts = []
    for item in items:
        item_html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", item)
        item_html = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", item_html)
        li_parts.append(f"<li>{item_html}</li>")
    return "<ul>" + "".join(li_parts) + "</ul>"


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

    # HOUSE RULE (product owner): dates read DD/MM/YYYY on screen. Detected from the field name
    # rather than passed in at each of the ~30 call sites, so a date field added later inherits
    # the format instead of quietly reverting to ISO. The STORED value stays ISO throughout -
    # only what is shown, and what is typed back, is converted.
    _is_date_field = field_key.endswith("_date") or field_key.endswith("_dates")
    if _is_date_field:
        current_value = _disp(current_value)

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
        elif widget == "html_list_area":
            # Bullet-list counterpart to html_text_area, for fields stored as
            # <ul><li>...</li></ul> (Included/Excluded) - one plain line per
            # bullet, no HTML ever shown to the human.
            st.caption("One item per line - each line becomes its own bullet point automatically. "
                      "Use **word** for bold if needed.")
            new_plain_value = st.text_area(label, value=_html_list_to_plain_for_editing(current_value),
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
            elif widget == "html_list_area":
                data_dict[field_key] = _plain_list_to_html_for_saving(new_plain_value)
            elif _is_date_field:
                # Typed as DD/MM/YYYY, stored as ISO. to_iso_date also accepts an ISO value,
                # so pasting one straight in still works.
                data_dict[field_key] = _iso(new_value)
            else:
                data_dict[field_key] = new_value
            st.session_state[edit_flag_key] = False
            st.rerun()

    return data_dict.get(field_key, default_value)


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


def _add_page_images_to_doc_pool(url, doc_raw_images, doc_image_urls):
    """
    Downloads any images found on `url` server-side and folds them into the
    SAME doc_raw_images/doc_image_urls pool already used for document-
    embedded images, instead of handing the browser a raw hotlink back to
    the source site.

    CONFIRMED REAL BUG (reported: images "from document and/or URL" showing
    broken in the picker): URL-scraped images used to be kept in their own
    separate list (fed straight from get_page_images()'s raw URLs into
    render_url_image_picker, which renders each via a plain <img src=...>
    that the USER'S BROWSER has to fetch directly from the ORIGINAL source
    site). Two real things break that: (1) mixed content blocking - this
    app is served over HTTPS, and browsers silently drop an http:// image
    if its auto-upgraded https:// version fails, which it does for a site
    with no valid HTTPS setup (confirmed against a real supplier site with
    a certificate hostname mismatch); (2) hotlink protection some sites
    apply against other domains. Routing through get_page_image_bytes()
    (downloads server-side, exactly like document images already do) and
    merging into the same doc_raw_images/doc_image_urls lists fixes this
    for both sources at once, and means the picker only ever needs ONE
    reliable pipeline instead of two - one solid, one fragile.

    Mutates doc_raw_images/doc_image_urls IN PLACE (extends both): every
    downloaded image goes into doc_raw_images (renders via raw bytes -
    st.image() never has the browser fetch anything, so this always works
    regardless of the source site's own HTTPS/hotlink situation) AND gets
    an upload attempt to freeimage.host so it can also show up pre-hosted,
    one click away, instead of needing a manual "Upload & Add" - matching
    the exact fallback pattern document images already use.
    """
    if not url:
        return
    try:
        page_images_bytes = get_page_image_bytes(url)
    except Exception:
        page_images_bytes = []
    if not page_images_bytes:
        return
    doc_raw_images.extend(page_images_bytes)
    try:
        doc_image_urls.extend(upload_images_freeimage(
            [(img_bytes, fname.rsplit(".", 1)[-1] if "." in fname else "jpg") for fname, img_bytes in page_images_bytes]
        ))
    except Exception:
        pass


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
            # CONFIRMED REAL BUG (production crash, PIL.UnidentifiedImageError): a document can
            # yield bytes that LOOK like an image to whatever pulled them out of the PDF/doc, but
            # aren't a format PIL can decode (a corrupted extraction, a vector/unsupported format,
            # a stray non-image blob) - st.image() used to call straight into PIL with no guard,
            # and an unreadable single thumbnail crashed the ENTIRE page render (this function is
            # called from deep inside the ClosedTour/Ticket upload flow - one bad image blocked
            # the whole batch, not just its own thumbnail). Same "one bad item must never take
            # down the whole screen" principle already applied elsewhere in this file (see
            # render_readonly_source's try/except around st.code). The Upload/Download buttons
            # still work either way - they don't need PIL to succeed.
            try:
                st.image(img_bytes, caption=fname)
            except Exception:
                st.warning(f"⚠️ '{fname}' couldn't be previewed (not a readable image format), but "
                          f"you can still download or upload it below.")
            if st.button("☁️ Upload & Add", key=f"{state_prefix}_upload_{photo_key}"):
                # CONFIRMED REAL GAP (product owner, "I can't integrate the images from the
                # document, I get an error" - but the generic "Upload returned no URL." gave no
                # way to tell what actually went wrong). upload_images_with_errors (unlike
                # upload_images) preserves the real reason for each failure instead of a bare
                # print() nobody sees on Streamlit Cloud - a human clicking this button IS
                # watching the result, so show them the actual cause (missing/invalid
                # FREEIMAGE_API_KEY, freeimage.host down, rate-limited, etc).
                url, errors = upload_images_freeimage_with_errors(
                    [(img_bytes, fname.rsplit(".", 1)[-1] if "." in fname else "jpg")])
                if url:
                    newly_added_url = url[0]
                    st.success("Uploaded!")
                elif errors:
                    # NOT friendly_error_message() here - that's written for Anthropic AI-call
                    # errors ("Something went wrong while talking to the AI service...") and
                    # would mislabel a freeimage.host hosting failure as an AI problem. The raw
                    # message from upload_image() is already written for a human to read.
                    st.error(f"Upload failed: {errors[0]}")
                else:
                    st.error("Upload returned no URL, for no reason the hosting service reported - "
                             "try again, or use Download and host it manually.")
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


def render_closedtour_supplements(data, key_prefix):
    """The ONE supplement list for a whole ClosedTour.

    CONFIRMED PRODUCT-OWNER CORRECTION: "Supplement within ClosedTour is set only once and
    applies to ALL Modalities of the ClosedTour. The supplements must be added to the main body
    of the closedtour and not to the Modalities."

    THIS REVERSES AN EARLIER DECISION OF MINE, and the earlier reasoning was wrong. Seeing the
    same supplement appear against three room categories, I concluded it was being triple-charged
    and scoped each one to a single Modality via modalityCodes. But a ClosedTour supplement is a
    property of the TOUR - one optional Abu Simbel excursion, offered whoever's cabin you booked -
    so scoping it to one Modality meant a client in any other cabin could not buy it at all.

    So supplements live on the main tour record, edited once here, and modalityCodes is left
    empty, which is how Travel Compositor spells "applies to every Modality"."""
    st.markdown("**Optional Add-ons / Upgrades / Excursions (Supplements) — the whole tour**")
    st.caption("Set **once for the entire tour**: every Modality can be sold with these. TRUE "
              "optional extras the customer only pays for if they choose them — a room upgrade, a "
              "meal upgrade, an optional excursion — or a peak-season surcharge. Leave empty if "
              "this tour has none. Every row needs a clear Name.")
    st.caption("**Single/Double/Triple/Quadruple** only matter for a surcharge quoted 'per room' "
              "(e.g. 'USD 71.00 per room per night'): that flat per-room charge has to be split by "
              "how many share the room, so those four columns hold the resulting per-person amount. "
              "For a normal per-person add-on just fill 'Price (per person)' and the four occupancy "
              "columns follow it.")
    st.caption("⚠️ **Check Mandatory and On Request on every row before publishing.** A ClosedTour "
              "supplement is often genuinely optional, so these two boxes are the difference between "
              "an add-on the client chooses and a charge they cannot avoid — the AI's guess is a "
              "starting point, not a decision. House rule: ClosedTour supplements are never "
              "refundable, and the app always publishes them that way.")

    rows = [
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
            "Special Travel Start Date": _disp(s.get("travel_start_date", "")),
            "Special Travel End Date": _disp(s.get("travel_end_date", "")),
        }
        for s in (data.get("supplements") or []) if isinstance(s, dict)
    ]
    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=SUPPLEMENT_COLUMNS)

    def _save(edited_df, data=data):
        missing_name = False
        out = []
        for _, row in edited_df.iterrows():
            name = _safe_cell_str(row.get("Name")).strip()
            price_given = row.get("Price (per person)", 0)
            blank = price_given is None or (isinstance(price_given, float) and pd.isna(price_given))
            if not name:
                if not blank and price_given not in (0, ""):
                    missing_name = True
                continue
            flat_price = _safe_float(price_given)

            def _occ(col, fallback=flat_price):
                return _safe_float(row.get(col), fallback)

            out.append({
                "name": name,
                "price": flat_price,
                "single_price": _occ("Single"),
                "double_price": _occ("Double"),
                "triple_price": _occ("Triple"),
                "quadruple_price": _occ("Quadruple"),
                "per_pax": bool(row.get("Per Pax", True)),
                "mandatory": bool(row.get("Mandatory", False)),
                "on_request": bool(row.get("On Request", False)),
                "travel_start_date": _iso(_safe_cell_str(row.get("Special Travel Start Date"))),
                "travel_end_date": _iso(_safe_cell_str(row.get("Special Travel End Date"))),
            })
        data["supplements"] = out
        st.session_state[f"_{key_prefix}_supplements_missing_name"] = missing_name

    editable_table("Supplements", df, f"{key_prefix}_supplements", on_save=_save)
    if st.session_state.get(f"_{key_prefix}_supplements_missing_name"):
        st.warning("⚠️ A supplement row has a price but no Name - it was skipped. Every supplement "
                   "needs a clear Name.")


def render_child_age_band(data, key_prefix, min_key="min_child_age", max_key="max_child_age"):
    """The child age band, with the consequence of changing it spelled out.

    CONFIRMED PRODUCT-OWNER RULE: "When the document says minimum child age (e.g. 7), then this
    must be added to the child age allowed. The range for children would be then 7 to 12." So a
    stated minimum replaces the house floor of 2 and the ceiling stays at 12 unless the document
    says otherwise.

    WHY THE CAPTION MATTERS: in Travel Compositor these two numbers do not merely describe the
    child band, they DEFINE where the infant band ends. Raising the minimum to 7 does not make
    under-7s unbookable - it makes them INFANTS, priced at the infant rate. If the supplier meant
    "we do not take under-7s at all", that is a different thing entirely and needs handling as a
    restriction, not an age band. Nobody can be expected to know that from two number boxes."""
    acol1, acol2 = st.columns(2)
    with acol1:
        raw_min = data.get(min_key)
        data[min_key] = st.number_input(
            "Min Child Age", min_value=0, max_value=17,
            # NOT `or 2`: a legitimate 0 is falsy, and would have been silently rewritten to 2.
            value=int(raw_min if raw_min not in (None, "") else 2), key=f"{key_prefix}_min_child_age")
    with acol2:
        raw_max = data.get(max_key)
        data[max_key] = st.number_input(
            "Max Child Age", min_value=0, max_value=17,
            value=int(raw_max if raw_max not in (None, "") else 12), key=f"{key_prefix}_max_child_age")

    low, high = data[min_key], data[max_key]
    if low > high:
        st.error(f"⚠️ Min Child Age ({low}) is above Max Child Age ({high}) - no age counts as a "
                 f"child, so every young traveller would be priced as an infant.")
    elif low == high and low > 0:
        # Usually the AI mis-reading "children from 7" as a band of exactly 7. Occasionally a
        # document really does say it. Flagged rather than auto-corrected, because only someone
        # looking at the document can tell which.
        st.warning(f"⚠️ Both ages are {low}, so **only {low}-year-olds** count as children - "
                   f"everyone from {low + 1} up pays the adult rate, everyone below pays infant. "
                   f"If the document says \"children from {low}\", the maximum should be **12**.")
    elif low > 2:
        st.caption(f"👶 Children are **{low}–{high}**, so anyone **under {low} is an infant** and pays "
                   f"the infant rate. If the document means under-{low}s cannot join this tour at all, "
                   f"that is a booking restriction rather than an age band - say so in the description, "
                   f"because these two boxes cannot express it.")
    else:
        st.caption(f"👶 Children are **{low}–{high}**; under {low} counts as an infant. Raise the "
                   f"minimum if the document states one (e.g. \"children from 7 years\" → 7).")
