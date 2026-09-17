"""
Duplicate-and-swap Transfer creation flow.

CONFIRMED PRODUCT-OWNER REQUEST (2026-09-16): "when human create a new transfer or transport,
could the app simple copy the product and just swap the destinations?" 2026-08-12's redesign
removed AI-document creation for Transfer/Transport entirely ("Transfer and Transport are not
possible to automatically Import/upload" - see app_helpers.render_update_refresh_flow's own
docstring), which left NO way at all in this app to create a brand-new Transfer - only
price_refresh.py's update-existing-only flow remained. This is that missing create path.

SCOPE (confirmed): Transfer only for now (Transport is the natural next step once this is
proven out). Finding the source product to duplicate is by pasting a known Travel Compositor id
directly - not from a picked-list of what this app has published this session.

CONFIRMED PRODUCT-OWNER FEEDBACK (2026-09-16), simplifying this flow after it was proven out:
"Search by departure/arrival can be delete in the transfer creation for swap. Not needed." - the
id-paste path alone covers this flow's real use (a human duplicating a SPECIFIC known record),
so the search-by-text picker (and its transfer_matcher.suggest_existing_transfer_matches call)
was removed. Also: "'duplicate Check' not needed, it is always safe to duplicate." - the
"Check for a matching existing transfer" step (and the Publish-blocking gate that required it)
was removed for the same reason; publishing a genuine duplicate is a low-cost, easily-fixed
mistake (delete/deactivate it in Travel Compositor), not one worth a mandatory extra click on
every single publish.

Everything about the source Transfer (vehicle, price, cancellation text, images, validity dates,
supplements) is copied exactly - see builder.build_transfer_swap_payload's own docstring for why
swapping the departure/arrival LOCATION OBJECTS wholesale (not re-typing/re-geocoding a name from
scratch) is the safer default. The human can still edit the route names, top-level price, and
the per-occupancy price table before publishing, since the new direction may genuinely differ.
"""
import pandas as pd
import streamlit as st

import transfer_matcher
from geocoding_client import geocode
from transfer_gap_finder import build_and_rewrite_transfer_swap_payload
from ui_components import (editable_table, _safe_float, _safe_int,
                            _html_to_plain_for_editing, _plain_to_html_for_saving)

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
    """Standalone entry point - picks its own supplier, then renders the pick/review/publish
    body below. Kept for backward compatibility with this module's own test suite; the combined
    Step 1 menu entry (flows/transfer_duplicate_and_create.py, 2026-09-17) instead picks ONE
    supplier shared with the automated missing-transfers scan and calls
    _render_duplicate_transfer_body(client, supplier_id) directly, so the human never sees two
    separate supplier pickers on what is now one screen."""
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

    _render_duplicate_transfer_body(client, supplier_id)


def _render_duplicate_transfer_body(client, supplier_id):
    """Everything after the supplier is already known - split out (2026-09-17) so the combined
    Transfer create screen can share ONE supplier pick with the automated missing-transfers scan
    instead of rendering two separate pickers. See render_duplicate_transfer_flow's own
    docstring."""
    if st.session_state.get("dtf_supplier_id") != supplier_id:
        # Supplier changed - drop everything picked/loaded for the previous one, same as every
        # other flow in this app does when the supplier selection changes underneath it.
        for k in ("dtf_source", "dtf_payload", "dtf_swap_report", "dtf_route_info"):
            st.session_state.pop(k, None)
        st.session_state.dtf_supplier_id = supplier_id

    if not st.session_state.get("dtf_source"):
        _render_pick_source(client, supplier_id)
        return

    _render_review_and_publish(client, supplier_id)


def _render_pick_source(client, supplier_id):
    # CONFIRMED PRODUCT-OWNER FEEDBACK (2026-09-16): "Search by departure/arrival can be delete
    # in the transfer creation for swap. Not needed." - a human duplicating a specific record
    # already knows which one; pasting its id is the whole real use of this flow.
    st.markdown("#### Find the Transfer to duplicate")
    tid = st.text_input("Transfer id (e.g. TRANSFER-412545)", key="dtf_manual_id").strip()
    if st.button("Fetch", key="dtf_fetch_manual", disabled=not tid):
        with st.spinner(f"Fetching {tid}..."):
            result = client.get_transfer(supplier_id, tid)
        if isinstance(result, dict) and "error" in result:
            st.error(f"❌ Couldn't fetch {tid}: {result.get('message', result)}")
        else:
            st.session_state.dtf_source = result
            st.rerun()


def _render_review_and_publish(client, supplier_id):
    source = st.session_state.dtf_source
    if "dtf_payload" not in st.session_state:
        # CONFIRMED PRODUCT-OWNER FEEDBACK (2026-09-16): "the 'Rewrite with AI for the new
        # direction' works perfectly, please automatically use that already - no human must
        # click additionally on this button." transfer_gap_finder.build_and_rewrite_transfer_swap_payload
        # (shared with flows/missing_transfers.py) runs the swap AND, for any field the literal
        # swap couldn't confidently handle, the AI rewrite - never for a field that already
        # swapped cleanly for free. If the AI call itself fails or returns the text unchanged,
        # swap_report[field] stays False and the original "couldn't auto-swap" warning still
        # shows, so nothing is silently lost.
        with st.spinner("Building the swapped payload (rewriting with AI where the literal swap couldn't confidently handle it)..."):
            payload, swap_report, route_info = build_and_rewrite_transfer_swap_payload(source)

        st.session_state.dtf_payload = payload
        st.session_state.dtf_swap_report = swap_report
        st.session_state.dtf_route_info = route_info

    payload = st.session_state.dtf_payload
    swap_report = st.session_state.get("dtf_swap_report") or {}
    route_info = st.session_state.get("dtf_route_info") or {}

    # CONFIRMED PRODUCT-OWNER FEEDBACK (2026-09-16, screenshot): this banner used to show the
    # UNCHANGED original route ("Cairo Airport (CAI) → Cairo City"), reading as if nothing had
    # actually been swapped yet - same "wrong in the order... could cause a misunderstanding"
    # issue already fixed for Transport's own success banner (flows/duplicate_transport.py). Now
    # states both directions explicitly, matching that fix.
    st.success(f"Duplicating **{source.get('name') or '(unnamed)'}** ({source.get('id')}).\n\n"
              f"Original route: {route_info.get('old_departure_name', '?')} → "
              f"{route_info.get('old_arrival_name', '?')}\n\n"
              f"🔁 New route being created: **{route_info.get('new_departure_name', '?')} → "
              f"{route_info.get('new_arrival_name', '?')}**")

    if st.button("↩️ Pick a different Transfer to duplicate", key="dtf_restart"):
        for k in ("dtf_source", "dtf_payload", "dtf_swap_report", "dtf_route_info"):
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

    # CONFIRMED REAL FIELD (product owner, 2026-09-16, pasted the real Contract - Transfer
    # Swagger): "transferToHotel" is the admin UI's "Transfer IN" checkbox - true = a transfer TO
    # the accommodation. build_transfer_swap_payload already INVERTS it automatically (a transfer
    # that heads to the accommodation necessarily heads away from it once the direction is
    # swapped) - surfaced here explicitly, not left buried in the "Everything else" JSON dump,
    # since a wrong direction on this specific field is exactly the kind of mistake that's easy
    # to miss and matters operationally (product owner: "very important").
    payload["transferToHotel"] = st.checkbox(
        "Transfer IN (heads to the accommodation)", value=bool(payload.get("transferToHotel", True)),
        key="dtf_transfer_to_hotel",
        help="Automatically flipped from the original (a transfer that headed to the "
             "accommodation now heads away from it, in the swapped direction) - double-check "
             "this reads correctly before publishing.")

    st.markdown("#### Name")
    payload["name"] = st.text_input("Transfer name", value=payload.get("name", ""), key="dtf_name")
    datasheets = dict(payload.get("datasheets") or {})
    en = dict(datasheets.get("EN") or {})
    en["name"] = st.text_input("Datasheet name (customer-facing)", value=en.get("name", ""), key="dtf_datasheet_name")

    # CONFIRMED PRODUCT-OWNER FEEDBACK (2026-09-16, screenshot, red-circled): the stored
    # description/pickup text is real HTML ("<p>...</p>") - showing that raw markup read as
    # "coding lines" a non-technical human wouldn't understand. Same fix already used everywhere
    # else in this app for an HTML-backed field (see ui_components.editable_field's own
    # "html_text_area" widget, and flows/duplicate_transport.py's own identical fix): show/edit
    # plain, human-friendly text and convert it back to the same HTML shape automatically on
    # save - the human never sees or types a tag.
    if en.get("description") is not None or swap_report.get("description") is not None:
        # CONFIRMED PRODUCT-OWNER FEEDBACK (2026-09-16): the AI rewrite now runs automatically
        # (see the payload-build block above) whenever the literal swap couldn't confidently
        # handle this field - swap_report["description"] == "ai" means that already happened, so
        # this just informs the human rather than asking them to click a button. Only the rare
        # case where the AI rewrite ITSELF made no change (still False) shows the original
        # "couldn't auto-swap" warning, so nothing is silently lost.
        if swap_report.get("description") == "ai":
            st.info("✨ Rewritten automatically by AI for the new direction - double-check it "
                    "reads correctly before publishing.")
        elif swap_report.get("description") is False:
            st.warning("⚠️ Couldn't auto-swap the description (even the AI rewrite made no "
                      "change) - check it reads correctly for the new direction before "
                      "publishing.")
        st.caption("Formatting (paragraphs, bullet points) is handled automatically - just write "
                  "plain text, with a blank line between paragraphs and one item per line for a "
                  "list.")
        new_plain_description = st.text_area(
            "Description", value=_html_to_plain_for_editing(en.get("description", "")), key="dtf_description")
        en["description"] = _plain_to_html_for_saving(new_plain_description)

    if en.get("pickupDescription") is not None or swap_report.get("pickupDescription") is not None:
        if swap_report.get("pickupDescription") == "ai":
            st.info("✨ Rewritten automatically by AI for the new direction - double-check it "
                    "reads correctly before publishing.")
        elif swap_report.get("pickupDescription") is False:
            st.warning("⚠️ Couldn't auto-swap the pickup information (even the AI rewrite made "
                      "no change) - check it reads correctly for the new direction before "
                      "publishing.")
        st.caption("Formatting (paragraphs, bullet points) is handled automatically - just write "
                  "plain text, with a blank line between paragraphs and one item per line for a "
                  "list.")
        new_plain_pickup = st.text_area(
            "Pickup information", value=_html_to_plain_for_editing(en.get("pickupDescription", "")),
            key="dtf_pickup_description")
        en["pickupDescription"] = _plain_to_html_for_saving(new_plain_pickup)

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
                if k not in ("departure", "arrival", "name", "datasheets", "basePrice",
                             "pricesByOccupancy", "transferToHotel")})

    # CONFIRMED PRODUCT-OWNER FEEDBACK (2026-09-16): "'duplicate Check' not needed, it is always
    # safe to duplicate." - removed the "Check for a matching existing transfer" step and the
    # Publish-blocking gate that required it (transfer_matcher.resolve_transfer_match is no
    # longer called from this flow at all).
    st.markdown("#### Publish")
    with st.expander("🔎 Preview full payload"):
        st.json(payload)

    dates_ok = bool((payload.get("startDate") or "").strip()) and bool((payload.get("endDate") or "").strip())
    geoloc_ok = bool((payload.get("departure") or {}).get("geolocation")) and bool((payload.get("arrival") or {}).get("geolocation"))
    if not geoloc_ok:
        st.warning("⚠️ Departure and/or arrival couldn't be resolved to real coordinates - fix "
                  "the names above and re-resolve before publishing.")

    publish_disabled = not dates_ok or not geoloc_ok
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
                    for k in ("dtf_source", "dtf_payload", "dtf_swap_report", "dtf_route_info"):
                        st.session_state.pop(k, None)
            except Exception as e:
                show_publish_error(f"publish transfer **{payload.get('name') or '(unnamed)'}**", str(e))
