"""
Bulk hotel supplement tool — two modes:

1. CREATE a new supplement: add one to every hotel (or selected hotels) for a
   supplier, via the dedicated POST /hotel/supplement/{supplierId}/{providerCode}.
2. DELETE all supplements: wipe every supplement from every hotel for a supplier,
   via PUT /hotel/{supplierId} with an emptied supplements array.

HOTEL IS THE ODD ONE OUT on create: the other three product types carry supplements
inside the record and are written back with the record itself, while a hotel supplement
has its own creation endpoint. Deleting still goes through the full-record PUT, because
no delete endpoint exists — the supplements array is emptied and the record written back.

MODES MATCH THE TRANSFER TOOL ON PURPOSE (product owner, 2026-11-15): "if we developed
one thing for a specific product, to add it to all the other products as well".
"""
import streamlit as st
from datetime import date

import supplement_apply
from app_helpers import _ur_pick_momira_supplier
from list_response_utils import normalize_list_response

MODULE_BUILD = "2026-10-24-rollover-ext"

_WRAPPER_FALLBACKS = ("items", "data", "results", "content")

APPLY_TYPES = [
    "LODGING", "MEAL", "LODGING_AND_MEAL",
    "PER_NIGHT", "PER_NIGHT_PERSON", "PER_STAY", "PER_STAY_PERSON",
]
SUPP_TYPES = ["ABSOLUTE", "PERCENT"]
DAYS_OF_WEEK = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY",
                "SATURDAY", "SUNDAY"]


def _hotel_code(h):
    return h.get("providerCode") or h.get("code") or ""


def _load_hotels(client, supplier_id):
    """Fetch all hotels for a supplier, re-fetching each for full supplement data."""
    try:
        resp = client.get_hotels(supplier_id)
    except Exception as e:
        st.error(f"Failed to fetch hotels: {e}")
        return None
    hotels = normalize_list_response(resp, ("hotel", "hotels") + _WRAPPER_FALLBACKS)
    hotels = [h for h in hotels if isinstance(h, dict)]
    if not hotels:
        st.warning("No hotels found for this supplier.")
        return None

    full_hotels = []
    progress = st.progress(0, text="Loading hotel details…")
    for i, h in enumerate(hotels):
        try:
            full = client.get_hotel(supplier_id, _hotel_code(h))
        except Exception:
            full = None
        full_hotels.append(full if isinstance(full, dict) and "error" not in full else h)
        progress.progress((i + 1) / len(hotels))
    progress.empty()
    return full_hotels


def _supp_name(s):
    names = s.get("names") or []
    en = next((n.get("content", "") for n in names
               if isinstance(n, dict) and n.get("languageCode") == "EN"), "")
    if en:
        return en
    return (names[0].get("content", "") if names and isinstance(names[0], dict)
            else "(unnamed)")


def _render_create_mode(hotels):
    """UI for creating a new supplement on hotels."""
    st.subheader("New supplement details")

    name_en = st.text_input("Supplement name (EN)", key="hsb_c_name_en",
                            help="e.g. Christmas Supplement, Spa Access")
    if not name_en:
        st.warning("Enter a supplement name.")
        return None

    c1, c2 = st.columns(2)
    with c1:
        supp_type = st.selectbox("Type", SUPP_TYPES, key="hsb_c_type")
    with c2:
        apply_type = st.selectbox("Apply", APPLY_TYPES, key="hsb_c_apply")

    v1, v2 = st.columns(2)
    with v1:
        value = st.number_input("Adult value", min_value=0.0, step=0.5, key="hsb_c_value")
    with v2:
        child_value = st.number_input("Child value", min_value=0.0, step=0.5,
                                      key="hsb_c_child_value")

    st.markdown("**Travel window**")
    today = date.today()
    d1, d2 = st.columns(2)
    with d1:
        tw_start = st.date_input("Start", value=today, key="hsb_c_tw_start")
    with d2:
        tw_end = st.date_input("End", value=date(today.year + 1, today.month, today.day),
                               key="hsb_c_tw_end")

    bw_start = bw_end = None
    if st.checkbox("Add booking window", key="hsb_c_use_bw"):
        b1, b2 = st.columns(2)
        with b1:
            bw_start = st.date_input("Booking window start", value=today, key="hsb_c_bw_start")
        with b2:
            bw_end = st.date_input("Booking window end",
                                   value=date(today.year + 1, today.month, today.day),
                                   key="hsb_c_bw_end")

    op_days = []
    if st.checkbox("Restrict to specific days", key="hsb_c_use_days"):
        op_days = st.multiselect("Operational days", DAYS_OF_WEEK, key="hsb_c_days")

    all_room_codes = set()
    all_meal_plans = set()
    for h in hotels:
        for room in h.get("rooms") or []:
            if isinstance(room, dict) and room.get("providerRoomCode"):
                all_room_codes.add(room["providerRoomCode"])
        for mp in h.get("mealPlans") or []:
            if isinstance(mp, dict) and mp.get("mealPlan"):
                all_meal_plans.add(mp["mealPlan"])

    st.markdown("**Scoping**")
    room_codes = []
    if all_room_codes and st.checkbox("Scope to specific room codes", key="hsb_c_scope_rooms"):
        room_codes = st.multiselect("Room codes", sorted(all_room_codes), key="hsb_c_rooms")
    meal_plans = []
    if all_meal_plans and st.checkbox("Scope to specific meal plans", key="hsb_c_scope_meals"):
        meal_plans = st.multiselect("Meal plans", sorted(all_meal_plans), key="hsb_c_meals")

    st.markdown("**Stay constraints** (optional)")
    s1, s2, s3 = st.columns(3)
    with s1:
        min_stay = st.number_input("Min stay (nights)", min_value=0, step=1, key="hsb_c_min_stay")
    with s2:
        max_stay = st.number_input("Max stay (nights)", min_value=0, step=1, key="hsb_c_max_stay")
    with s3:
        release_days = st.number_input("Release days", min_value=0, step=1, key="hsb_c_release")

    st.divider()
    scope = st.radio("Add to which hotels?", ["All hotels", "Select specific hotels"],
                     key="hsb_c_scope")

    selected_codes = None
    if scope == "Select specific hotels":
        options = {_hotel_code(h): (h.get("name") or _hotel_code(h)) for h in hotels}
        picks = st.multiselect("Select hotels", list(options.keys()),
                               format_func=lambda p: f"{options[p]} ({p})",
                               key="hsb_c_hotel_pick")
        if not picks:
            st.info("Select at least one hotel.")
            return None
        selected_codes = set(picks)

    new_supp = {
        "type": supp_type,
        "apply": apply_type,
        "value": value,
        "childValue": child_value,
        "providerRoomCodes": room_codes,
        "mealPlans": meal_plans,
        "releaseDays": release_days,
        "travelWindows": [{"startDate": tw_start.isoformat(),
                           "endDate": tw_end.isoformat()}],
        "names": [{"languageCode": "EN", "content": name_en}],
    }
    if bw_start and bw_end:
        new_supp["bookingWindows"] = [{"startDate": bw_start.isoformat(),
                                       "endDate": bw_end.isoformat()}]
    if op_days:
        new_supp["operationalDays"] = op_days
    if min_stay:
        new_supp["minimumStay"] = min_stay
    if max_stay:
        new_supp["maximumStay"] = max_stay

    target_count = len(selected_codes) if selected_codes else len(hotels)

    with st.expander("Preview new supplement"):
        for k, v in new_supp.items():
            st.text(f"  {k}: {v}")
        st.caption(f"Will be added to **{target_count}** hotel(s).")

    return ("create", new_supp, selected_codes, target_count)


def _render_delete_mode(hotels):
    """UI for deleting every supplement from every hotel."""
    with_supps = [h for h in hotels if (h.get("supplements") or [])]
    total_supps = sum(len(h.get("supplements") or []) for h in hotels)

    if not total_supps:
        st.info("None of these hotels have any supplements — nothing to delete.")
        return None

    st.warning(
        f"This will **delete all {total_supps} supplement(s)** from "
        f"**{len(with_supps)}** hotel(s). "
        f"The remaining {len(hotels) - len(with_supps)} hotel(s) have no supplements "
        f"and will not be touched.")

    with st.expander(f"Show all supplements to be deleted ({total_supps})", expanded=False):
        for h in with_supps:
            hname = h.get("name") or _hotel_code(h)
            for s in h.get("supplements") or []:
                if isinstance(s, dict):
                    st.text(f"{hname}  |  {_supp_name(s)}  |  "
                            f"{s.get('type', '?')} {s.get('value', 0)}  |  "
                            f"apply: {s.get('apply', '')}")

    if not st.checkbox("Yes, I want to delete all supplements from all hotels for this "
                       "supplier", key="hsb_d_confirm"):
        return None

    return ("delete", len(with_supps), total_supps)


def render_hotel_supplement_bulk_flow(client):
    st.header("Bulk Hotel supplements")

    supplier_id = _ur_pick_momira_supplier(client, key_prefix="hsb")
    if not supplier_id:
        return

    if st.session_state.get("hsb_last_supplier_id") != supplier_id:
        st.session_state.hsb_last_supplier_id = supplier_id
        st.session_state.hsb_hotels = None
        st.session_state.hsb_applied_once = False

    if st.button("Load hotels", key="hsb_load"):
        with st.spinner("Fetching hotels…"):
            result = _load_hotels(client, supplier_id)
            if result:
                st.session_state.hsb_hotels = result
                st.session_state.hsb_applied_once = False
        st.rerun()

    hotels = st.session_state.get("hsb_hotels")
    if not hotels:
        return

    st.info(f"**{len(hotels)}** hotels loaded.")

    mode = st.radio("What do you want to do?",
                    ["Create a new supplement on hotels", "Delete all supplements"],
                    key="hsb_mode")
    st.divider()

    if mode == "Create a new supplement on hotels":
        result = _render_create_mode(hotels)
    else:
        result = _render_delete_mode(hotels)

    if not result:
        return

    st.divider()
    if result[0] == "create":
        _, new_supp, selected_codes, target_count = result
        label = f"Add supplement to {target_count} hotel(s)"
    else:
        _, hotels_with_supps, total_supps = result
        label = f"⚠️ Delete all {total_supps} supplement(s) from {hotels_with_supps} hotel(s)"

    if st.session_state.get("hsb_applied_once") and result[0] == "create":
        st.button(label, key="hsb_apply", type="primary", disabled=True)
        st.caption("✅ Supplement already added — reload hotels to add again or switch mode.")
        return
    if not st.button(label, key="hsb_apply", type="primary"):
        return

    # ── Execute ──────────────────────────────────────────────────────
    # Create goes through the dedicated POST endpoint; delete empties the array and PUTs
    # the record back, since hotel supplements have no delete endpoint. Both live in
    # supplement_apply.PRODUCT_SPECS.
    bar = st.progress(0, text="Applying…")

    def _prog(done, total):
        bar.progress(done / max(total, 1), text=f"Applying… ({done}/{total})")

    if result[0] == "create":
        res = supplement_apply.apply_create(
            client, supplier_id, "Hotel", hotels, new_supp,
            selected_labels=selected_codes, progress=_prog)
        if res["ok"]:
            st.session_state.hsb_applied_once = True
    else:
        res = supplement_apply.apply_delete_all(
            client, supplier_id, "Hotel", hotels, progress=_prog)

    bar.empty()
    if res["ok"]:
        st.success(f"Done — **{res['ok']}** hotel(s) updated successfully.")
    if res["failed"]:
        st.error(f"**{res['failed']}** hotel(s) failed:")
        for err in res["errors"]:
            st.text(err)
