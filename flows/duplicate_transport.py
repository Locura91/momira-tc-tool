"""
Duplicate-and-swap Transport creation flow.

CONFIRMED PRODUCT-OWNER REQUEST (2026-09-16): "can we do the same for Transport. Changing the
Destination of the original Transport ID, adopting the Name and adopting the Description." A
direct follow-up to the Transfer version of this same feature (flows/duplicate_transfer.py,
same day) - Transport has had NO create path in this app since 2026-08-12's redesign either
("Transfer and Transport are not possible to automatically Import/upload" - see
app_helpers.render_update_refresh_flow's own docstring), so this is that missing create path
for Transport too.

KEY DIFFERENCE FROM THE TRANSFER VERSION: Transport's per-occupancy pricing lives on SEPARATE
Option sub-resources (schemas.py's ContractTransportOptionVO docstring), not one flat array on
the parent record the way Transfer's pricesByOccupancy works - so duplicating a Transport means
duplicating its parent record AND every one of its existing Options (fetched via
client.get_transport_option for each of the source's optionCodes), then publishing the parent
FIRST (to get its real new id) and each Option second, exactly mirroring how a brand-new
Transport is built everywhere else in this app (builder.build_transport_payloads' own two-phase
create sequencing). See builder.build_transport_swap_payload/build_transport_option_swap_payload
for the actual swap logic and why Transport needs an extra api_client lookup that Transfer's
version doesn't (Transport's route lives only as location CODES on its segments, not a
human-readable name field).

SIMPLIFIED (product owner, 2026-09-21): "make sure, the Transport duplicate is workking similar
like transfers duplicate. Transports are now easaliy copied, but the search is easy as transfer
but not as transport." Matches the same simplification flows/duplicate_transfer.py already went
through on 2026-09-16, for the same reasons Chris gave there: "Search by departure/arrival can be
delete in the transfer creation for swap. Not needed" (a human duplicating a specific record
already knows which one; pasting its id is the whole real use of this flow - the extra "how do
you want to find it?" radio and departure/arrival search box this screen used to have, on top of
Transfer's single paste box, was exactly the "search is easy as transfer but not as transport"
gap) and "'duplicate Check' not needed, it is always safe to duplicate" (the mandatory "Check for
a matching existing transport" step and the Publish-blocking gate that required it - Transfer's
own equivalent was removed the same day for the same reason: publishing a genuine duplicate is a
low-cost, easily-fixed mistake, not one worth a mandatory extra click on every publish).
transport_matcher.suggest_existing_transport_matches/resolve_transport_match are no longer called
from this flow at all - finding the source is a single id-paste box, same as Transfer.
"""
import pandas as pd
import streamlit as st

from builder import build_transport_option_swap_payload
from transfer_gap_finder import build_and_rewrite_transport_swap_payload
import transport_matcher
from ui_components import editable_table, _safe_float, _safe_int, _html_to_plain_for_editing, _plain_to_html_for_saving

from app_helpers import _ur_pick_momira_supplier, show_publish_error


def render_duplicate_transport_flow(client):
    st.header("🧬 New Transport — duplicate an existing one & swap destinations")
    if st.button("🔙 Back to Step 1", key="dtp_back"):
        st.session_state.product_type = None
        st.rerun()
    st.caption("For a brand-new Transport that's really the SAME route sold the other way - e.g. "
              "you already have Airport → Hotel and now need Hotel → Airport. Pick the existing "
              "one below: vehicle, price, cancellation text, images and every occupancy bracket's "
              "pricing are all copied exactly, only the route (and any name/description text that "
              "names it) is swapped. Edit anything that genuinely differs for the new direction "
              "before publishing.")

    supplier_id = _ur_pick_momira_supplier(client, "dtp")
    if not supplier_id:
        return

    if st.session_state.get("dtp_supplier_id") != supplier_id:
        # Supplier changed - drop everything picked/loaded for the previous one, same as every
        # other flow in this app does when the supplier selection changes underneath it.
        for k in ("dtp_source", "dtp_source_options", "dtp_payload", "dtp_swap_report",
                  "dtp_route_info", "dtp_options"):
            st.session_state.pop(k, None)
        st.session_state.dtp_supplier_id = supplier_id

    if not st.session_state.get("dtp_source"):
        _render_pick_source(client, supplier_id)
        return

    _render_review_and_publish(client, supplier_id)


def _fetch_source_and_options(client, supplier_id, transport_id):
    """Fetches the parent record plus every one of its existing Options (iterating its own
    optionCodes list, exactly like flows/multi_transport.py's merge-on-update snapshot fetch) -
    both are needed before build_transport_swap_payload/build_transport_option_swap_payload can
    run. Returns (parent_dict, options_list) or (None, None) on a fetch error (already reported
    to the user via st.error)."""
    with st.spinner(f"Fetching {transport_id} and its occupancy brackets..."):
        parent = client.get_transport(supplier_id, transport_id)
        if isinstance(parent, dict) and "error" in parent:
            st.error(f"❌ Couldn't fetch {transport_id}: {parent.get('message', parent)}")
            return None, None
        options = []
        for opt_code in (parent.get("optionCodes") or []):
            opt = client.get_transport_option(supplier_id, transport_id, opt_code)
            if isinstance(opt, dict) and "error" not in opt:
                options.append(opt)
            else:
                st.warning(f"⚠️ Couldn't fetch occupancy bracket {opt_code} of {transport_id} - "
                          f"it won't be duplicated onto the new Transport; add it manually "
                          f"afterward if it's genuinely needed.")
    return parent, options


def _render_pick_source(client, supplier_id):
    # CONFIRMED PRODUCT-OWNER FEEDBACK (2026-09-21): "the search is easy as transfer but not as
    # transport" - matches flows/duplicate_transfer.py's own simplification (2026-09-16): a human
    # duplicating a specific record already knows which one, so pasting its id is the whole real
    # use of this flow. The "how do you want to find it?" radio and the departure/arrival search
    # box are gone - just the one paste box, same as Transfer.
    st.markdown("#### Find the Transport to duplicate")
    tid = st.text_input("Transport id (e.g. TRANSPORT-412579)", key="dtp_manual_id").strip()
    if st.button("Fetch", key="dtp_fetch_manual", disabled=not tid):
        parent, options = _fetch_source_and_options(client, supplier_id, tid)
        if parent is not None:
            st.session_state.dtp_source = parent
            st.session_state.dtp_source_options = options
            st.rerun()


def _render_review_and_publish(client, supplier_id):
    source = st.session_state.dtp_source
    source_options = st.session_state.get("dtp_source_options") or []
    if "dtp_payload" not in st.session_state:
        payload, swap_report, route_info = build_and_rewrite_transport_swap_payload(source, client)
        st.session_state.dtp_payload = payload
        st.session_state.dtp_swap_report = swap_report
        st.session_state.dtp_route_info = route_info
        duplicated_options = [
            build_transport_option_swap_payload(
                opt, route_info["new_departure_name"], route_info["new_arrival_name"])
            for opt in source_options
        ]
        st.session_state.dtp_options = duplicated_options
        # CONFIRMED REAL PRODUCTION ERROR (product owner, 2026-09-16): "java.lang.
        # IllegalArgumentException: An instance of a null PK has been incorrectly provided for
        # this find operation" on the PARENT create call. Root cause: the deepcopy in
        # build_transport_swap_payload carries over the OLD transport's optionCodes (e.g.
        # ["AC First Class Seat"], a code that belongs to the OLD transport id and will never
        # exist under the brand-new one about to be created) unchanged - Travel Compositor tries
        # to resolve that stale reference on create and fails. build_transport_payloads' own
        # create path (the normal, non-duplicate way a Transport gets built in this app) never
        # hits this because it always sets optionCodes to the SAME freshly-generated codes it's
        # about to create as Options right after - matching that here fixes it the same way.
        payload["optionCodes"] = [opt.get("code") for opt in duplicated_options if opt.get("code")]
        st.session_state.dtp_payload = payload

    payload = st.session_state.dtp_payload
    swap_report = st.session_state.get("dtp_swap_report") or {}
    route_info = st.session_state.get("dtp_route_info") or {}
    options = st.session_state.dtp_options

    # CONFIRMED PRODUCT-OWNER FEEDBACK (2026-09-16): the original wording here showed the
    # UNCHANGED original route ("Aswan Train Station → Alexandria Train Station"), which read as
    # if nothing had actually been swapped yet - "wrong in the order as nothing changed in this
    # field, which could cause a misunderstanding". Now states both directions explicitly, with
    # an arrow icon on the new one so it's unmistakable which is which.
    st.success(f"Duplicating **{source.get('name') or '(unnamed)'}** ({source.get('id')}) "
              f"({len(options)} occupancy bracket(s)).\n\n"
              f"Original route: {route_info.get('old_departure_name', '?')} → "
              f"{route_info.get('old_arrival_name', '?')}\n\n"
              f"🔁 New route being created: **{route_info.get('new_departure_name', '?')} → "
              f"{route_info.get('new_arrival_name', '?')}**")

    if st.button("↩️ Pick a different Transport to duplicate", key="dtp_restart"):
        for k in ("dtp_source", "dtp_source_options", "dtp_payload", "dtp_swap_report",
                  "dtp_route_info", "dtp_options"):
            st.session_state.pop(k, None)
        st.rerun()

    st.markdown("#### Route (already swapped — edit if the new direction needs different wording)")
    rcol1, rcol2 = st.columns(2)
    with rcol1:
        new_dep_name = st.text_input("New departure", value=route_info.get("new_departure_name", ""), key="dtp_dep_name")
    with rcol2:
        new_arr_name = st.text_input("New arrival", value=route_info.get("new_arrival_name", ""), key="dtp_arr_name")

    dep_changed = new_dep_name != route_info.get("new_departure_name", "")
    arr_changed = new_arr_name != route_info.get("new_arrival_name", "")
    segments = payload.get("segments") or []
    if len(segments) > 1:
        st.caption("⚠️ This route has more than one segment - re-resolving below only updates the "
                  "OVERALL departure (first segment) and arrival (last segment). Check the "
                  "interior legs in the payload preview further down before publishing.")
    if dep_changed or arr_changed:
        st.warning("⚠️ You changed a location name - it still carries the OLD (swapped) "
                  "Transport Base code until you re-resolve it, or this would publish at the "
                  "wrong spot.")
        if st.button("🔎 Re-resolve both locations now", key="dtp_reresolve"):
            with st.spinner("Resolving..."):
                dep_result = client.resolve_transport_base(new_dep_name)
                arr_result = client.resolve_transport_base(new_arr_name)
            if segments:
                segments = [dict(s) for s in segments]
                if dep_result.get("valid"):
                    segments[0]["departureLocationCode"] = dep_result["code"]
                else:
                    st.warning(f"⚠️ Couldn't resolve '{new_dep_name}' to a known Transport Base - "
                              f"the old code was left in place.")
                if arr_result.get("valid"):
                    segments[-1]["arrivalLocationCode"] = arr_result["code"]
                else:
                    st.warning(f"⚠️ Couldn't resolve '{new_arr_name}' to a known Transport Base - "
                              f"the old code was left in place.")
                payload["segments"] = segments
            route_info["new_departure_name"] = new_dep_name
            route_info["new_arrival_name"] = new_arr_name
            st.session_state.dtp_route_info = route_info
            st.rerun()

    st.markdown("#### Name")
    payload["name"] = st.text_input("Transport name", value=payload.get("name", ""), key="dtp_name")
    datasheets = dict(payload.get("datasheets") or {})
    en = dict(datasheets.get("EN") or {})
    en["name"] = st.text_input("Datasheet name (customer-facing)", value=en.get("name", ""), key="dtp_datasheet_name")

    if en.get("description") is not None or swap_report.get("description") is not None:
        # CONFIRMED PRODUCT-OWNER FEEDBACK (2026-09-17): "description must be rewritten by AI" -
        # same gap Transfer's duplicate flow already closed on 2026-09-16 (its own docstring has
        # the full history). The AI rewrite now runs automatically (see
        # transfer_gap_finder.build_and_rewrite_transport_swap_payload, called by the payload-
        # build block above) whenever the literal swap couldn't confidently handle this field -
        # swap_report["description"] == "ai" means that already happened, so this just informs
        # the human rather than asking them to click a button. Only the rare case where the AI
        # rewrite ITSELF made no change (still False) shows the original "couldn't auto-swap"
        # warning, so nothing is silently lost.
        if swap_report.get("description") == "ai":
            st.info("✨ Rewritten automatically by AI for the new direction - double-check it "
                    "reads correctly before publishing.")
        elif swap_report.get("description") is False:
            st.warning("⚠️ Couldn't auto-swap the description (even the AI rewrite made no "
                      "change) - check it reads correctly for the new direction before "
                      "publishing.")
        # CONFIRMED PRODUCT-OWNER FEEDBACK (2026-09-16, screenshot): the stored description is
        # real HTML (<p>, <ul><li>, ...) - showing that raw markup in the box read as "coding
        # lines" a non-technical human wouldn't understand. Same fix already used everywhere else
        # in this app for an HTML-backed description field (see ui_components.editable_field's
        # own "html_text_area" widget and its docstring for the full history): show/edit plain,
        # human-friendly text, and convert it back to the same HTML shape automatically on save -
        # the human never sees or types a tag.
        st.caption("Formatting (paragraphs, bullet points) is handled automatically - just write "
                  "plain text, with a blank line between paragraphs and one item per line for a "
                  "list.")
        new_plain_description = st.text_area(
            "Description", value=_html_to_plain_for_editing(en.get("description", "")), key="dtp_description")
        en["description"] = _plain_to_html_for_saving(new_plain_description)

    datasheets["EN"] = en
    payload["datasheets"] = datasheets

    st.markdown("#### Price")
    currency = payload.get("currency", "EUR")
    # CONFIRMED REAL PRODUCTION BUG (product owner, 2026-09-17, screenshots comparing a real
    # per-vehicle Transport in Travel Compositor's admin UI - "Vehicle: 220.00 USD" - against this
    # screen): "In travel c is per vehicle 220 USD and a supplement. But on the app nothing is
    # seen and all prices are 0 USD, which is the biggest issue." Root cause: this section only
    # ever showed baseAdultPrice/baseChildrenPrice/baseInfantPrice, which are legitimately 0.0 for
    # a per-vehicle transport (pricePerPax=False) - the real price lives in the SEPARATE
    # vehiclePrice field instead (schemas.py's ContractTransportVO docstring; same pricePerPax
    # convention already established and relied on elsewhere in this app - price_refresh.py,
    # bulk_notes.py both branch on `bool(payload.get("pricePerPax", True))` the same way), which
    # this screen never displayed anywhere except buried, unlabeled, inside the "Everything else"
    # JSON expander further down. build_transport_swap_payload/build_transport_option_swap_payload
    # both deepcopy the source untouched, so vehiclePrice and every occupancy bracket's supplement
    # WERE already correct in the payload the whole time - this was purely a display gap, not a
    # data-loss bug. Fixed by branching the same way the rest of the app already does: show
    # vehiclePrice as the one editable price for a per-vehicle transport, the three base fields
    # for a per-pax one.
    per_pax = bool(payload.get("pricePerPax", True))
    if per_pax:
        pcol1, pcol2, pcol3 = st.columns(3)
        with pcol1:
            payload["baseAdultPrice"] = st.number_input(
                f"Base adult price ({currency})", min_value=0.0,
                value=_safe_float(payload.get("baseAdultPrice", 0.0)), key="dtp_base_adult")
        with pcol2:
            payload["baseChildrenPrice"] = st.number_input(
                f"Base children price ({currency})", min_value=0.0,
                value=_safe_float(payload.get("baseChildrenPrice", 0.0)), key="dtp_base_children")
        with pcol3:
            payload["baseInfantPrice"] = st.number_input(
                f"Base infant price ({currency})", min_value=0.0,
                value=_safe_float(payload.get("baseInfantPrice", 0.0)), key="dtp_base_infant")
        st.caption("Copied from the original - edit if the new direction is genuinely priced "
                  "differently. Every occupancy bracket below is an ADDITIONAL supplement on top "
                  "of this base, same as the original.")
    else:
        payload["vehiclePrice"] = st.number_input(
            f"Vehicle price ({currency})", min_value=0.0,
            value=_safe_float(payload.get("vehiclePrice", 0.0)), key="dtp_vehicle_price")
        st.caption("This is a per-vehicle transport (\"Price Per Pax\" is off) - the whole "
                  "vehicle is priced once here, copied from the original. Every occupancy "
                  "bracket below is an ADDITIONAL supplement on top of this vehicle price, same "
                  "as the original.")

    opt_rows = []
    for opt in options:
        first_price = next(iter(opt.get("prices") or []), {})
        if per_pax:
            opt_rows.append({
                "min_passengers": opt.get("minPassengers"), "max_passengers": opt.get("maxPassengers"),
                "adult_supplement": first_price.get("adultPriceSupplement", 0.0),
                "children_supplement": first_price.get("childrenPriceSupplement", 0.0),
                "infant_supplement": first_price.get("infantPriceSupplement", 0.0),
            })
        else:
            # CONFIRMED CONVENTION (builder.build_transport_payloads' own docstring):
            # ContractTransportOptionPriceVO has no generic "vehicle" supplement field, so a
            # per-vehicle bracket's delta is written into adultPriceSupplement the same way a
            # per-pax bracket's is - it's the only numeric delta field the schema offers. Shown
            # here as a single "price_supplement" column instead of three identical-looking ones,
            # so it doesn't look like a per-passenger price that was never meant to exist.
            opt_rows.append({
                "min_passengers": opt.get("minPassengers"), "max_passengers": opt.get("maxPassengers"),
                "price_supplement": first_price.get("adultPriceSupplement", 0.0),
            })
    opt_df = pd.DataFrame(opt_rows or ([{"min_passengers": 1, "max_passengers": 1,
                                        "adult_supplement": 0.0, "children_supplement": 0.0,
                                        "infant_supplement": 0.0}] if per_pax else
                                       [{"min_passengers": 1, "max_passengers": 1,
                                        "price_supplement": 0.0}]))

    def _save_options(edited_df):
        rows = list(edited_df.to_dict("records"))
        for i, opt in enumerate(options):
            if i >= len(rows):
                break
            row = rows[i]
            if per_pax:
                adult = _safe_float(row.get("adult_supplement"), fallback=0.0)
                children = _safe_float(row.get("children_supplement"), fallback=0.0)
                infant = _safe_float(row.get("infant_supplement"), fallback=0.0)
            else:
                # Per-vehicle: one number, written into adultPriceSupplement (see the matching
                # comment above building opt_rows for why).
                adult = _safe_float(row.get("price_supplement"), fallback=0.0)
                children = 0.0
                infant = 0.0
            # CONFIRMED CONVENTION (see builder.build_transport_payloads): a bracket that costs
            # exactly the base rate has NO price entries at all, never a redundant zero entry.
            if adult == 0 and children == 0 and infant == 0:
                opt["prices"] = []
            else:
                existing_first = next(iter(opt.get("prices") or []), {})
                opt["prices"] = [{
                    "name": existing_first.get("name"),
                    "startDate": existing_first.get("startDate") or payload.get("startDate") or "",
                    "endDate": existing_first.get("endDate") or "2049-12-31",
                    "adultPriceSupplement": adult, "childrenPriceSupplement": children,
                    "infantPriceSupplement": infant,
                    "adultRTPriceSupplement": existing_first.get("adultRTPriceSupplement", 0.0),
                    "childrenRTPriceSupplement": existing_first.get("childrenRTPriceSupplement", 0.0),
                    "infantRTPriceSupplement": existing_first.get("infantRTPriceSupplement", 0.0),
                }]
        st.session_state.dtp_options = options

    editable_table("Occupancy brackets (supplement on top of the base price above)",
                   opt_df, "dtp_occ", on_save=_save_options, num_rows="fixed")
    st.caption("Each row is a real, separate occupancy bracket copied from the original (min/max "
              "passengers can't be changed here) - only the supplement amounts are editable. To "
              "add or remove a whole bracket, do that directly in Travel Compositor after "
              "publishing.")

    with st.expander("🔎 Everything else, copied exactly from the original (edit later in Travel "
                     "Compositor if the new direction genuinely differs)"):
        st.json({k: v for k, v in payload.items()
                if k not in ("segments", "name", "datasheets", "baseAdultPrice",
                             "baseChildrenPrice", "baseInfantPrice", "vehiclePrice")})

    # CONFIRMED PRODUCT-OWNER FEEDBACK (2026-09-21, matching flows/duplicate_transfer.py's own
    # 2026-09-16 simplification): "'duplicate Check' not needed, it is always safe to duplicate."
    # The "Check for a matching existing transport" step and the Publish-blocking gate that
    # required it are gone - transport_matcher.resolve_transport_match is no longer called from
    # this flow at all.
    st.markdown("#### Publish")
    with st.expander("🔎 Preview full parent payload"):
        st.json(payload)
    with st.expander(f"🔎 Preview {len(options)} occupancy bracket payload(s)"):
        st.json(options)

    dates_ok = bool((payload.get("startDate") or "").strip()) and bool((payload.get("endDate") or "").strip())
    segments_ok = bool(segments) and all(
        s.get("departureLocationCode") and s.get("arrivalLocationCode") for s in segments)
    if not segments_ok:
        st.warning("⚠️ Departure and/or arrival couldn't be resolved to a real Transport Base - "
                  "fix the names above and re-resolve before publishing.")

    publish_disabled = not dates_ok or not segments_ok
    if st.button("🚀 Publish — CREATE new transport", type="primary", key="dtp_publish", disabled=publish_disabled):
        # CONFIRMED REAL PRODUCTION BUG (product owner, 2026-09-16): the SAME "java.lang.
        # IllegalArgumentException: An instance of a null PK has been incorrectly provided for
        # this find operation" error kept happening even after the optionCodes-regeneration fix
        # AND the companyName fix - diagnosed live via screen-share a second time: the actual
        # submitted parent payload had every schema field present with sane values (including a
        # correctly-matching, freshly-generated optionCodes: ["ALEASW14"]) and still failed on
        # the PARENT create call itself, before any Option was ever submitted. The one thing left
        # that's genuinely different from the WORKING path: build_transport_payloads' own create
        # path (flows/multi_transport.py) has only ever been exercised going through
        # update_transport for a real, already-existing Transport (Transport has had NO working
        # create path in this app since 2026-08-12 per this file's own module docstring) - so a
        # brand-new Transport's optionCodes pointing at Options that DON'T EXIST YET has likely
        # never actually been proven to work on a real CREATE call at all, only assumed to mirror
        # the update case. Travel Compositor's create endpoint appears to try to resolve/look up
        # each optionCodes entry as a real entity even on create, and a code that doesn't exist
        # yet resolves to a null PK. FIX: create the parent with optionCodes EMPTY (nothing to
        # resolve), create every Option under the new id (exactly as before), THEN a follow-up
        # PUT sets the parent's optionCodes to the now-real codes - same two-step shape Hotel's
        # two-phase build already uses for its own analogous forward-reference problem (room
        # providerCodes only exist after the parent's first create response).
        # CONFIRMED REAL PRODUCTION BUG (product owner, 2026-09-18): "Couldn't publish transport
        # ... modalityAvailableWhenActive:You must add at least one modality!" - a SECOND, separate
        # Travel Compositor validation rule from the null-PK one above, hit only once that one was
        # fixed: a Transport parent create with active=true is rejected outright if it has zero
        # Options (Modalities) yet - which every brand-new Transport genuinely does, at the exact
        # moment of this first create call, since Options can only be created AFTER the parent has
        # a real id. Same two-step shape as the optionCodes fix just above, extended one field
        # further: the parent is created with active=FALSE (nothing to violate the "must have a
        # modality" rule), Options are created under the new id exactly as before, and the SAME
        # follow-up PUT that links their real optionCodes also flips active back to True - by then
        # the Transport genuinely does have at least one modality, so Travel Compositor accepts it.
        create_payload = dict(payload)
        create_payload["optionCodes"] = []
        create_payload["active"] = False
        with st.spinner("Publishing parent transport to Travel Compositor..."):
            try:
                result = client.create_transport(supplier_id, create_payload)
            except Exception as e:
                show_publish_error(f"publish transport **{payload.get('name') or '(unnamed)'}**", str(e))
                result = None
        if isinstance(result, dict) and "error" in result:
            show_publish_error(f"publish transport **{payload.get('name') or '(unnamed)'}**", result)
        elif result is not None:
            new_id = result.get("id") if isinstance(result, dict) else None
            if not new_id:
                st.error("❌ Transport was created but no id came back - can't create its "
                        "occupancy brackets. Check Travel Compositor directly.")
            else:
                failed_options = []
                created_codes = []
                with st.spinner(f"Publishing {len(options)} occupancy bracket(s)..."):
                    for opt in options:
                        opt_result = client.create_transport_option(supplier_id, new_id, opt)
                        if isinstance(opt_result, dict) and "error" in opt_result:
                            failed_options.append((opt.get("code"), opt_result))
                        else:
                            created_codes.append(opt.get("code"))
                if created_codes:
                    with st.spinner("Linking occupancy bracket(s) to the new transport..."):
                        link_payload = dict(payload)
                        link_payload["id"] = new_id
                        link_payload["optionCodes"] = created_codes
                        # The parent was deliberately created with active=False above (see that
                        # block's comment) because it had zero modalities at that point - now that
                        # at least one Option genuinely exists, this same follow-up PUT that links
                        # it also flips active back to True. `payload` already carries active=True
                        # (build_transport_swap_payload's own default for a duplicated record), so
                        # this is just making that explicit rather than relying on it silently.
                        link_payload["active"] = True
                        link_result = client.update_transport(supplier_id, link_payload)
                    if isinstance(link_result, dict) and "error" in link_result:
                        st.warning(f"⚠️ Published (id: {new_id}) with {len(created_codes)} "
                                  f"occupancy bracket(s), but couldn't link them to the parent "
                                  f"record ({link_result.get('message', link_result)}) - open "
                                  f"the transport in Travel Compositor and set its optionCodes "
                                  f"manually: " + ", ".join(created_codes))
                elif options:
                    # Every occupancy bracket failed to publish - the parent is still sitting at
                    # active=False (correctly, per Travel Compositor's own rule: it genuinely has
                    # no modalities yet) and was never re-linked/activated. Say so explicitly, so
                    # this doesn't look like a silently-broken, invisible-to-the-human record.
                    st.warning(f"⚠️ Published (id: {new_id}), but every occupancy bracket failed - "
                              f"the transport was left **inactive** in Travel Compositor (it can't "
                              f"be active with no modalities). Add at least one occupancy bracket "
                              f"manually in Travel Compositor, then activate it there.")
                transport_matcher.remember_transport_id(supplier_id, new_dep_name, new_arr_name, new_id)
                if failed_options:
                    st.warning(f"⚠️ Published (id: {new_id}), but {len(failed_options)} of "
                              f"{len(options)} occupancy bracket(s) failed to publish - add "
                              f"them manually in Travel Compositor: " +
                              ", ".join(code or "?" for code, _err in failed_options))
                elif created_codes:
                    st.success(f"✅ Published successfully (id: {new_id}) with all {len(options)} "
                              f"occupancy bracket(s).")
                for k in ("dtp_source", "dtp_source_options", "dtp_payload", "dtp_swap_report",
                          "dtp_route_info", "dtp_options"):
                    st.session_state.pop(k, None)
