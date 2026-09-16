"""
Duplicate-and-swap Transfer creation flow.

CONFIRMED PRODUCT-OWNER REQUEST (2026-09-16): "when human create a new transfer or transport,
could the app simple copy the product and just swap the destinations?" 2026-08-12's redesign
removed AI-document creation for Transfer/Transport entirely ("Transfer and Transport are not
possible to automatically Import/upload" - see app_helpers.render_update_refresh_flow's own
docstring), which left NO way at all in this app to create a brand-new Transfer - only
price_refresh.py's update-existing-only flow remained. This is that missing create path.

SCOPE (confirmed): Transfer only for now (Transport is the natural next step once this is
proven out). Finding the source product to duplicate is by SEARCHING (departure/arrival text
against this supplier's live Travel Compositor list, via transfer_matcher's existing similarity
scoring - or pasting a known Travel Compositor id directly), not from a picked-list of what this
app has published this session.

Everything about the source Transfer (vehicle, price, cancellation text, images, validity dates,
supplements) is copied exactly - see builder.build_transfer_swap_payload's own docstring for why
swapping the departure/arrival LOCATION OBJECTS wholesale (not re-typing/re-geocoding a name from
scratch) is the safer default. The human can still edit the route names, top-level price, and
the per-occupancy price table before publishing, since the new direction may genuinely differ.

Same duplicate-safety bar as every other create flow in this app: Publish is disabled until the
human has explicitly checked (and, if a match is found, confirmed it away) that a transfer for
the NEW swapped route doesn't already exist - see transfer_matcher.suggest_existing_transfer_matches.
"""
import pandas as pd
import streamlit as st

from builder import build_transfer_swap_payload
import transfer_matcher
from geocoding_client import geocode
from ui_components import editable_table, _safe_float, _safe_int

from app_helpers import _ur_pick_momira_supplier, show_publish_error


def _resolve_route_location(client, supplier_id, place_name, is_zone_based):
    """Re-resolves a hand-edited departure/arrival name to real coordinates - same confirmed
    TC-first order used everywhere else in this app (builder.build_transfer_payload's own
    _resolve_location): Transfer Zones first for zone-based (area) routing, then TC's raw
    geolocation, then the free OpenStreetMap geocoder as a last resort. Duplicated here (rather
    than importing builder's private closure, which isn't exposed standalone) because it's only
    needed when a human deliberately changes a name away from the safe swap default - the common
    case never calls this at all."""
    place_name = (place_name or "").strip()
    if not place_name:
        return {"name": place_name, "geolocation": None, "zoneRadius": None}
    if is_zone_based:
        zr = client.resolve_transfer_zone(supplier_id, place_name)
        if zr.get("valid"):
            return {"name": zr.get("name") or place_name,
                    "geolocation": {"latitude": zr["latitude"], "longitude": zr["longitude"]},
                    "zoneRadius": zr.get("zone_radius")}
    tz_result = client.resolve_transfer_zone_geolocation(supplier_id, place_name)
    if tz_result.get("valid"):
        return {"name": tz_result.get("name") or place_name,
                "geolocation": {"latitude": tz_result["latitude"], "longitude": tz_result["longitude"]},
                "zoneRadius": None}
    geo_result = geocode(place_name)
    if geo_result.get("valid"):
        return {"name": geo_result.get("display_name") or place_name,
                "geolocation": {"latitude": geo_result["latitude"], "longitude": geo_result["longitude"]},
                "zoneRadius": None}
    return {"name": place_name, "geolocation": None, "zoneRadius": None}


def render_duplicate_transfer_flow(client):
    st.header("🧬 New Transfer — duplicate an existing one & swap destinations")
    if st.button("🔙 Back to Step 1", key="dtf_back"):
        st.session_state.product_type = None
        st.rerun()
    st.caption("For a brand-new Transfer that's really the SAME route sold the other way - e.g. "
              "you already have Airport → Hotel and now need Hotel → Airport. Pick the existing "
              "one below: vehicle, price, cancellation text and images are all copied exactly, "
              "only departure and arrival are swapped. Edit anything that genuinely differs for "
              "the new direction before publishing.")

    supplier_id = _ur_pick_momira_supplier(client, "dtf")
    if not supplier_id:
        return

    if st.session_state.get("dtf_supplier_id") != supplier_id:
        # Supplier changed - drop everything picked/loaded for the previous one, same as every
        # other flow in this app does when the supplier selection changes underneath it.
        for k in ("dtf_source", "dtf_payload", "dtf_swap_report", "dtf_match_result",
                  "dtf_match_route_fingerprint", "dtf_search_results"):
            st.session_state.pop(k, None)
        st.session_state.dtf_supplier_id = supplier_id

    if not st.session_state.get("dtf_source"):
        _render_pick_source(client, supplier_id)
        return

    _render_review_and_publish(client, supplier_id)


def _render_pick_source(client, supplier_id):
    st.markdown("#### Find the Transfer to duplicate")
    pick_mode = st.radio(
        "How do you want to find it?",
        ["Search by departure/arrival", "I already know its Travel Compositor id"],
        horizontal=True, key="dtf_pick_mode")

    if pick_mode == "I already know its Travel Compositor id":
        tid = st.text_input("Transfer id (e.g. TRANSFER-412545)", key="dtf_manual_id").strip()
        if st.button("Fetch", key="dtf_fetch_manual", disabled=not tid):
            with st.spinner(f"Fetching {tid}..."):
                result = client.get_transfer(supplier_id, tid)
            if isinstance(result, dict) and "error" in result:
                st.error(f"❌ Couldn't fetch {tid}: {result.get('message', result)}")
            else:
                st.session_state.dtf_source = result
                st.rerun()
        return

    scol1, scol2 = st.columns(2)
    with scol1:
        dep_search = st.text_input("Departure (or part of it)", key="dtf_search_dep")
    with scol2:
        arr_search = st.text_input("Arrival (or part of it)", key="dtf_search_arr")
    if st.button("🔎 Search", key="dtf_search_btn", disabled=not (dep_search or arr_search)):
        with st.spinner("Fetching this supplier's existing transfers..."):
            result = client.get_transfers(supplier_id)
        if isinstance(result, dict) and "error" in result:
            st.error(f"❌ Couldn't fetch this supplier's transfers: {result.get('message', result)}")
            st.session_state.dtf_search_results = []
        else:
            existing = result.get("transfer", []) if isinstance(result, dict) else (result or [])
            st.session_state.dtf_search_results = transfer_matcher.suggest_existing_transfer_matches(
                dep_search or "", arr_search or "", existing, top_n=10)

    results = st.session_state.get("dtf_search_results")
    if results:
        options = [f"{r['name'] or '(unnamed)'} — {r['departure_name']!r} → {r['arrival_name']!r} "
                  f"({r['transfer_id']}, match {r['score']})" for r in results]
        picked = st.radio("Pick the one to duplicate:", options, key="dtf_search_pick")
        picked_idx = options.index(picked)
        if st.button("Use this one", key="dtf_use_picked"):
            tid = results[picked_idx]["transfer_id"]
            with st.spinner(f"Fetching {tid}..."):
                result = client.get_transfer(supplier_id, tid)
            if isinstance(result, dict) and "error" in result:
                st.error(f"❌ Couldn't fetch {tid}: {result.get('message', result)}")
            else:
                st.session_state.dtf_source = result
                st.rerun()
    elif results == []:
        st.info("No existing transfers found for this supplier - nothing to duplicate yet. "
                "Create the first one for this route directly in Travel Compositor, then this "
                "tool can clone it for the return direction.")


def _render_review_and_publish(client, supplier_id):
    source = st.session_state.dtf_source
    if "dtf_payload" not in st.session_state:
        st.session_state.dtf_payload, st.session_state.dtf_swap_report = build_transfer_swap_payload(source)

    payload = st.session_state.dtf_payload
    swap_report = st.session_state.get("dtf_swap_report") or {}
    src_dep = (source.get("departure") or {}).get("name", "?")
    src_arr = (source.get("arrival") or {}).get("name", "?")
    st.success(f"Duplicating **{source.get('name') or '(unnamed)'}** ({source.get('id')}): "
              f"**{src_dep} → {src_arr}**.")

    if st.button("↩️ Pick a different Transfer to duplicate", key="dtf_restart"):
        for k in ("dtf_source", "dtf_payload", "dtf_swap_report", "dtf_match_result",
                  "dtf_match_route_fingerprint", "dtf_search_results"):
            st.session_state.pop(k, None)
        st.rerun()

    st.markdown("#### Route (already swapped — edit if the new direction needs different wording)")
    rcol1, rcol2 = st.columns(2)
    with rcol1:
        new_dep_name = st.text_input("New departure", value=(payload.get("departure") or {}).get("name", ""), key="dtf_dep_name")
    with rcol2:
        new_arr_name = st.text_input("New arrival", value=(payload.get("arrival") or {}).get("name", ""), key="dtf_arr_name")

    dep_changed = new_dep_name != (payload.get("departure") or {}).get("name", "")
    arr_changed = new_arr_name != (payload.get("arrival") or {}).get("name", "")
    is_zone_based = bool((payload.get("departureLocationId") or payload.get("arrivalLocationId")))
    if dep_changed or arr_changed:
        st.warning("⚠️ You changed a location name - it still carries the OLD (swapped) "
                  "coordinates until you re-resolve it, or this would publish at the wrong spot.")
        if st.button("🔎 Re-resolve both locations now", key="dtf_reresolve"):
            with st.spinner("Resolving..."):
                payload["departure"] = _resolve_route_location(client, supplier_id, new_dep_name, is_zone_based)
                payload["arrival"] = _resolve_route_location(client, supplier_id, new_arr_name, is_zone_based)
            st.rerun()
    payload.setdefault("departure", {})["name"] = new_dep_name
    payload.setdefault("arrival", {})["name"] = new_arr_name

    st.markdown("#### Name")
    payload["name"] = st.text_input("Transfer name", value=payload.get("name", ""), key="dtf_name")
    datasheets = dict(payload.get("datasheets") or {})
    en = dict(datasheets.get("EN") or {})
    en["name"] = st.text_input("Datasheet name (customer-facing)", value=en.get("name", ""), key="dtf_datasheet_name")

    if en.get("description") is not None or swap_report.get("description") is not None:
        if swap_report.get("description") is False:
            st.warning("⚠️ Couldn't auto-swap the description - it doesn't literally contain "
                      "both original location names, so it's copied unchanged below. Check it "
                      "reads correctly for the new direction before publishing.")
        en["description"] = st.text_area("Description", value=en.get("description", ""), key="dtf_description")

    if en.get("pickupDescription") is not None or swap_report.get("pickupDescription") is not None:
        if swap_report.get("pickupDescription") is False:
            st.warning("⚠️ Couldn't auto-swap the pickup information - it doesn't literally "
                      "contain both original location names, so it's copied unchanged below. "
                      "Check it reads correctly for the new direction before publishing.")
        en["pickupDescription"] = st.text_area("Pickup information", value=en.get("pickupDescription", ""), key="dtf_pickup_description")

    datasheets["EN"] = en
    payload["datasheets"] = datasheets

    st.markdown("#### Price")
    currency = payload.get("currency", "EUR")
    payload["basePrice"] = st.number_input(
        f"Default price ({currency})", min_value=0.0, value=_safe_float(payload.get("basePrice", 0.0)),
        key="dtf_baseprice",
        help="Copied from the original - edit if the new direction is genuinely priced differently.")

    occ_rows = []
    for t in payload.get("pricesByOccupancy") or []:
        if not isinstance(t, dict):
            continue
        occ_rows.append({
            "occupancy": t.get("occupancy"),
            "base_price": (t.get("basePrice") or {}).get("amount"),
            "child_price": (t.get("childPrice") or {}).get("amount"),
            "infant_price": (t.get("infantPrice") or {}).get("amount"),
        })
    occ_df = pd.DataFrame(occ_rows or [{"occupancy": 1, "base_price": payload.get("basePrice", 0.0),
                                        "child_price": None, "infant_price": None}])
    for col in ["occupancy", "base_price", "child_price", "infant_price"]:
        if col not in occ_df.columns:
            occ_df[col] = None

    def _save_occ(edited_df):
        rows = []
        for _, row in edited_df.iterrows():
            if pd.isna(row.get("occupancy")) or pd.isna(row.get("base_price")):
                continue
            rows.append({
                "occupancy": _safe_int(row.get("occupancy"), fallback=1),
                "basePrice": {"amount": _safe_float(row.get("base_price"), fallback=0.0), "currency": currency},
                "childPrice": ({"amount": _safe_float(row.get("child_price"), fallback=0.0), "currency": currency}
                              if not pd.isna(row.get("child_price")) else {"amount": 0.0, "currency": currency}),
                "infantPrice": ({"amount": _safe_float(row.get("infant_price"), fallback=0.0), "currency": currency}
                               if not pd.isna(row.get("infant_price")) else {"amount": 0.0, "currency": currency}),
                "priceByPax": payload.get("priceByPax", True),
            })
        payload["pricesByOccupancy"] = rows

    editable_table("Occupancy price tiers", occ_df, "dtf_occ", on_save=_save_occ)

    with st.expander("🔎 Everything else, copied exactly from the original (edit later in Travel "
                     "Compositor if the new direction genuinely differs)"):
        st.json({k: v for k, v in payload.items()
                if k not in ("departure", "arrival", "name", "datasheets", "basePrice", "pricesByOccupancy")})

    st.markdown("#### Duplicate check")
    st.caption("Same safeguard every other create flow here has - confirms a Transfer for THIS "
              "new (swapped) route doesn't already exist before you publish another one.")
    current_route_fingerprint = f"{new_dep_name}::{new_arr_name}"
    if st.session_state.get("dtf_match_route_fingerprint") != current_route_fingerprint:
        st.session_state.dtf_match_result = None
        st.session_state.dtf_match_route_fingerprint = current_route_fingerprint

    if st.button("🔎 Check for a matching existing transfer", key="dtf_checkmatch"):
        with st.spinner("Checking..."):
            st.session_state.dtf_match_result = transfer_matcher.resolve_transfer_match(
                client, supplier_id, new_dep_name, new_arr_name)
            st.session_state.dtf_match_route_fingerprint = current_route_fingerprint

    match_result = st.session_state.get("dtf_match_result")
    match_checked = match_result is not None
    blocks_as_duplicate = False
    if match_result:
        if match_result.get("fetch_error"):
            st.warning(f"⚠️ Couldn't fetch this supplier's existing transfers to check for a "
                      f"match: {match_result['fetch_error'].get('message', match_result['fetch_error'])}.")
        elif match_result.get("tracked_id"):
            st.error(f"🚫 This app already tracks a Transfer for this exact route: "
                    f"**{match_result['tracked_id']}**. Duplicating would create a second, "
                    f"conflicting record - go update that one instead (Step 1 → Price update to "
                    f"existing Products), or change the route text above if this is genuinely a "
                    f"different one.")
            blocks_as_duplicate = True
        elif match_result.get("fallback_candidates"):
            best = match_result["fallback_candidates"][0]
            if best["score"] >= 0.85:
                st.warning(f"⚠️ A very similar Transfer already exists: **{best['name'] or '(unnamed)'}** "
                          f"({best['transfer_id']}) — {best['departure_name']!r} → {best['arrival_name']!r} "
                          f"(match {best['score']}). Double-check this isn't the same route before publishing.")
            else:
                st.info(f"No close match found for this route (best similarity: {best['score']}) - "
                        f"safe to publish as new.")
        else:
            st.info("No existing transfers found for this supplier - safe to publish as new.")

    if not match_checked:
        st.warning("⚠️ Click **Check for a matching existing transfer** above before publishing.")

    st.markdown("#### Publish")
    with st.expander("🔎 Preview full payload"):
        st.json(payload)

    dates_ok = bool((payload.get("startDate") or "").strip()) and bool((payload.get("endDate") or "").strip())
    geoloc_ok = bool((payload.get("departure") or {}).get("geolocation")) and bool((payload.get("arrival") or {}).get("geolocation"))
    if not geoloc_ok:
        st.warning("⚠️ Departure and/or arrival couldn't be resolved to real coordinates - fix "
                  "the names above and re-resolve before publishing.")

    publish_disabled = not match_checked or blocks_as_duplicate or not dates_ok or not geoloc_ok
    if st.button("🚀 Publish — CREATE new transfer", type="primary", key="dtf_publish", disabled=publish_disabled):
        with st.spinner("Publishing to Travel Compositor..."):
            try:
                result = client.create_transfer(supplier_id, payload)
                if isinstance(result, dict) and "error" in result:
                    show_publish_error(f"publish transfer **{payload.get('name') or '(unnamed)'}**", result)
                else:
                    new_id = result.get("id") if isinstance(result, dict) else None
                    if new_id:
                        transfer_matcher.remember_transfer_id(supplier_id, new_dep_name, new_arr_name, new_id)
                    st.success(f"✅ Published successfully (id: {new_id or 'unknown'}).")
                    for k in ("dtf_source", "dtf_payload", "dtf_swap_report", "dtf_match_result",
                              "dtf_match_route_fingerprint", "dtf_search_results"):
                        st.session_state.pop(k, None)
            except Exception as e:
                show_publish_error(f"publish transfer **{payload.get('name') or '(unnamed)'}**", str(e))
