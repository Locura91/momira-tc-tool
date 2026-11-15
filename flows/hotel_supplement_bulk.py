"""
Bulk hotel supplement tool — CREATE mode only (uses dedicated POST endpoint).

Adds a new supplement to every hotel (or selected ones) for a supplier via
POST /hotel/supplement/{supplierId}/{providerCode}.

Unlike tickets/closedtours/transfers where supplements are part of the full
PUT payload, hotel supplements have their own creation endpoint.
"""
import streamlit as st
from datetime import date

APPLY_TYPES = [
    "LODGING", "MEAL", "LODGING_AND_MEAL",
    "PER_NIGHT", "PER_NIGHT_PERSON", "PER_STAY", "PER_STAY_PERSON",
]
SUPP_TYPES = ["ABSOLUTE", "PERCENT"]
DAYS_OF_WEEK = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


def _load_hotels(client, supplier_id):
    """Fetch all hotels for a supplier, re-fetching each for full data."""
    try:
        resp = client.get_hotels(supplier_id)
    except Exception as e:
        st.error(f"Failed to fetch hotels: {e}")
        return None
    hotels = resp.get("hotel") or resp.get("hotels") or []
    if not hotels:
        st.warning("No hotels found for this supplier.")
        return None

    full_hotels = []
    progress = st.progress(0, text="Loading hotel details…")
    for i, h in enumerate(hotels):
        pcode = h.get("providerCode", "")
        try:
            full = client.get_hotel(supplier_id, pcode)
            full_hotels.append(full)
        except Exception:
            full_hotels.append(h)
        progress.progress((i + 1) / len(hotels))
    progress.empty()
    return full_hotels


def render_hotel_supplement_bulk_flow(client):
    st.header("Bulk Hotel supplements")

    supplier_id = st.text_input("Supplier ID", key="hsb_supplier_id")
    if not supplier_id:
        st.info("Enter a supplier ID to start.")
        return

    if st.button("Load hotels", key="hsb_load"):
        with st.spinner("Fetching hotels…"):
            result = _load_hotels(client, supplier_id)
            if result:
                st.session_state.hsb_hotels = result
        st.rerun()

    hotels = st.session_state.get("hsb_hotels")
    if not hotels:
        return

    st.info(f"**{len(hotels)}** hotels loaded.")

    # ── View existing supplements ────────────────────────────────────
    with st.expander("View existing supplements", expanded=False):
        for h in hotels:
            pcode = h.get("providerCode", "")
            hname = h.get("name") or pcode
            supps = h.get("supplements") or []
            if supps:
                st.markdown(f"**{hname}** ({pcode}) — {len(supps)} supplement(s)")
                for s in supps:
                    names = s.get("names") or []
                    sname = next((n.get("content", "") for n in names if n.get("languageCode") == "EN"), "")
                    if not sname:
                        sname = names[0].get("content", "") if names else "unnamed"
                    st.text(f"  {sname}  |  {s.get('type','?')} {s.get('value',0)}  |  "
                            f"apply: {s.get('apply','')}  |  "
                            f"rooms: {s.get('providerRoomCodes', [])}")

    # ── Create new supplement ────────────────────────────────────────
    st.subheader("New supplement details")

    name_en = st.text_input("Supplement name (EN)", key="hsb_c_name_en",
                            help="e.g. Christmas Supplement, Spa Access")

    c1, c2 = st.columns(2)
    with c1:
        supp_type = st.selectbox("Type", SUPP_TYPES, key="hsb_c_type")
    with c2:
        apply_type = st.selectbox("Apply", APPLY_TYPES, key="hsb_c_apply")

    vc1, vc2 = st.columns(2)
    with vc1:
        value = st.number_input("Adult value", min_value=0.0, step=0.5, key="hsb_c_value")
    with vc2:
        child_value = st.number_input("Child value", min_value=0.0, step=0.5, key="hsb_c_child_value")

    st.markdown("**Travel window**")
    today = date.today()
    dc1, dc2 = st.columns(2)
    with dc1:
        tw_start = st.date_input("Start", value=today, key="hsb_c_tw_start")
    with dc2:
        tw_end = st.date_input("End", value=date(today.year + 1, today.month, today.day),
                               key="hsb_c_tw_end")

    use_booking_window = st.checkbox("Add booking window", key="hsb_c_use_bw")
    bw_start = bw_end = None
    if use_booking_window:
        bc1, bc2 = st.columns(2)
        with bc1:
            bw_start = st.date_input("Booking window start", value=today, key="hsb_c_bw_start")
        with bc2:
            bw_end = st.date_input("Booking window end",
                                   value=date(today.year + 1, today.month, today.day),
                                   key="hsb_c_bw_end")

    # Operational days
    use_op_days = st.checkbox("Restrict to specific days", key="hsb_c_use_days")
    op_days = []
    if use_op_days:
        op_days = st.multiselect("Operational days", DAYS_OF_WEEK, key="hsb_c_days")

    # Room codes and meal plans scoping
    all_room_codes = set()
    all_meal_plans = set()
    for h in hotels:
        for room in h.get("rooms") or []:
            rc = room.get("providerRoomCode") or ""
            if rc:
                all_room_codes.add(rc)
        for mp in h.get("mealPlans") or []:
            mpc = mp.get("mealPlan") or ""
            if mpc:
                all_meal_plans.add(mpc)

    st.markdown("**Scoping**")
    room_codes = []
    if all_room_codes:
        scope_rooms = st.checkbox("Scope to specific room codes", key="hsb_c_scope_rooms")
        if scope_rooms:
            room_codes = st.multiselect("Room codes", sorted(all_room_codes), key="hsb_c_rooms")
    meal_plans = []
    if all_meal_plans:
        scope_meals = st.checkbox("Scope to specific meal plans", key="hsb_c_scope_meals")
        if scope_meals:
            meal_plans = st.multiselect("Meal plans", sorted(all_meal_plans), key="hsb_c_meals")

    # Stay constraints
    st.markdown("**Stay constraints** (optional)")
    sc1, sc2, sc3 = st.columns(3)
    with sc1:
        min_stay = st.number_input("Min stay (nights)", min_value=0, step=1, key="hsb_c_min_stay")
    with sc2:
        max_stay = st.number_input("Max stay (nights)", min_value=0, step=1, key="hsb_c_max_stay")
    with sc3:
        release_days = st.number_input("Release days", min_value=0, step=1, key="hsb_c_release")

    st.divider()
    hotel_scope = st.radio("Add to which hotels?",
                           ["All hotels", "Select specific hotels"],
                           key="hsb_c_scope")

    selected_pcodes = None
    if hotel_scope == "Select specific hotels":
        options = {h.get("providerCode", ""): (h.get("name") or h.get("providerCode", ""))
                   for h in hotels}
        picks = st.multiselect("Select hotels", list(options.keys()),
                               format_func=lambda p: f"{options[p]} ({p})",
                               key="hsb_c_hotel_pick")
        if not picks:
            st.info("Select at least one hotel.")
            return
        selected_pcodes = set(picks)

    target_hotels = hotels if selected_pcodes is None else [
        h for h in hotels if h.get("providerCode") in selected_pcodes
    ]

    new_supp = {
        "type": supp_type,
        "apply": apply_type,
        "value": value,
        "childValue": child_value,
        "providerRoomCodes": room_codes,
        "mealPlans": meal_plans,
        "releaseDays": release_days,
        "travelWindows": [{"startDate": tw_start.isoformat(), "endDate": tw_end.isoformat()}],
    }
    if name_en:
        new_supp["names"] = [{"languageCode": "EN", "content": name_en}]
    if bw_start and bw_end:
        new_supp["bookingWindows"] = [{"startDate": bw_start.isoformat(), "endDate": bw_end.isoformat()}]
    if op_days:
        new_supp["operationalDays"] = op_days
    if min_stay:
        new_supp["minimumStay"] = min_stay
    if max_stay:
        new_supp["maximumStay"] = max_stay

    with st.expander("Preview new supplement"):
        for k, v in new_supp.items():
            st.text(f"  {k}: {v}")
        st.caption(f"Will be added to **{len(target_hotels)}** hotel(s).")

    if not st.button(f"Add supplement to {len(target_hotels)} hotel(s)", key="hsb_apply", type="primary"):
        return

    # ── Execute ──────────────────────────────────────────────────────
    progress = st.progress(0, text="Applying…")
    ok_count = 0
    err_count = 0
    errors = []

    for i, h in enumerate(target_hotels):
        pcode = h.get("providerCode", "")
        try:
            client.add_hotel_supplement(supplier_id, pcode, new_supp)
            ok_count += 1
        except Exception as e:
            err_count += 1
            errors.append(f"{pcode}: {e}")
        progress.progress((i + 1) / len(target_hotels))

    progress.empty()
    if ok_count:
        st.success(f"Done — supplement added to **{ok_count}** hotel(s).")
    if err_count:
        st.error(f"**{err_count}** hotel(s) failed:")
        for err in errors:
            st.text(err)
