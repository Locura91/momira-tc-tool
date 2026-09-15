"""
Manual-information flow, split out of app.py (Phase 1 restructure, zero behaviour change).

render_manual_information_flow moved here verbatim. Everything it references that is defined at
app.py's own top level is imported back from app via the same late-binding pattern used by the
earlier flows modules: app.py imports this module only after all of those names are already
defined in its own namespace, so `from app import ...` resolves correctly despite the circular
import shape. Every needed internal name here is defined earlier in app.py's file order than this
function's own original position, so the import-back line stays at that original position.
"""
import streamlit as st

from date_format import to_iso_date as _iso, DISPLAY_HINT as _DATE_HINT
from ai_extractor import friendly_error_message
import price_validity
import platform_store
import service_notes
import bulk_notes
from ui_components import is_active_supplier

from app import _dmy_date_field


def render_manual_information_flow(client):
    st.header("Adding manual information to Product")
    st.caption("Information a person knows that the supplier's documents don't say — a moved "
              "pickup point, revised cancellation terms, a temporary closure. Saved against a "
              "supplier and a product type, and **added automatically to the Voucher Remarks of "
              "every service of that type you upload from then on**, including uploads done by "
              "someone who never heard about the change. Notes are always *added to* what the "
              "document said; they never replace the cancellation policy or anything extracted.")

    if not platform_store.is_durable():
        st.warning("⚠️ No `DATABASE_URL` is configured, so a note saved here is lost on the next "
                   "redeploy and will not reach future uploads.")

    if st.session_state.suppliers_cache is None:
        with st.spinner("Loading supplier list from Travel Compositor..."):
            try:
                st.session_state.suppliers_cache = client.get_all_suppliers()
            except Exception as e:
                st.error(f"❌ Couldn't load the supplier list: {friendly_error_message(e)}")
                st.session_state.suppliers_cache = []

    supplier_id = None
    momira_suppliers = [
        s for s in (st.session_state.suppliers_cache or [])
        if (s.get("commercialName") or s.get("legalName") or "").strip().lower().startswith("momira_") and is_active_supplier(s)
    ]
    if momira_suppliers:
        options = {f"{s.get('commercialName') or s.get('legalName')} — ID {s.get('id')}": s.get("id")
                   for s in momira_suppliers}
        chosen = st.selectbox("Which supplier?", list(options.keys()), key="mi_supplier_select")
        supplier_id = str(options[chosen])
        if st.button("🔄 Refresh supplier list", key="mi_refresh_suppliers"):
            st.session_state.suppliers_cache = None
            st.rerun()
    else:
        st.error("Could not load the supplier list from Travel Compositor.")
        with st.expander("⚠️ Emergency manual entry"):
            st.caption("Only use this if the supplier list above failed to load - type the numeric Travel Compositor supplier ID directly.")
            supplier_id = st.text_input("Supplier ID (numeric)", value="", key="mi_supplier_manual")

    product_type = st.radio("Which product type does this apply to?",
                            service_notes.PRODUCT_TYPES, key="mi_product_type", horizontal=True)
    st.caption("A note is scoped to one supplier AND one product type, because a change to how "
              "transfers are picked up says nothing about that supplier's hotels. To cover more "
              "than one, do it once per type.")

    if not supplier_id:
        st.info("Choose a supplier above to write a note.")
        return

    # ---- 1. What are you adding? ---------------------------------------
    # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-14): a Supplement (ClosedTour, applies to
    # every Modality; Transfer) and an Additional Service (Transfer) are useful to bulk-add
    # here too, alongside the plain-text fields this flow already handles. They're
    # structured records, not a block of text, so they get their own small form below
    # rather than the text box - see bulk_notes.STRUCTURED_TARGETS.
    structured_labels = bulk_notes.available_structured_targets(product_type)
    # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-10): "add to all Transport from supplier
    # MOMIRA_EG_FT in the Voucher the code (20270430) - if there is already a code, the update
    # has to change ONLY this code." price_validity.PRODUCT_TYPES (Ticket/Transfer/Transport)
    # is the same scope that module's own single-service and weekly-scan logic already uses.
    price_code_available = product_type in price_validity.PRODUCT_TYPES
    add_structured = False
    add_price_code = False
    structured_kind = None
    item_data = {}
    add_mode_options = ["Text into an existing field"]
    if structured_labels:
        add_mode_options.append("A new Supplement / Additional Service")
    if price_code_available:
        add_mode_options.append("A price-validity code (YYYYMMDD)")
    if len(add_mode_options) > 1:
        st.markdown("### 1. What are you adding?")
        add_mode = st.radio(
            "What are you adding?", add_mode_options,
            key="mi_add_mode", label_visibility="collapsed", horizontal=True,
        )
        add_structured = add_mode.startswith("A new")
        add_price_code = add_mode.startswith("A price-validity")

    if add_structured:
        kind_label = st.selectbox("Which one?", structured_labels, key="mi_structured_kind_label")
        structured_kind = bulk_notes.STRUCTURED_TARGETS[product_type][kind_label]
        st.caption("This ADDS a new entry to every matching service - it never edits or replaces "
                  "anything already there. A service that already has an entry with the same name "
                  "is left alone, so sending twice can't duplicate it.")

        target = kind_label
        text = None
        mode = None
        item_data = {"name": st.text_input("Name", key="mi_s_name",
                                           placeholder="e.g. Resort Fee, Child Seat")}
        if structured_kind == "closedtour_supplement":
            st.caption("Applies to every Modality on the tour - Travel Compositor has no way to "
                      "scope a ClosedTour supplement to just one cabin.")
            c1, c2 = st.columns(2)
            with c1:
                item_data["price"] = st.number_input("Price per person", min_value=0.0, step=1.0,
                                                      key="mi_s_price")
                item_data["mandatory"] = st.checkbox("Mandatory", value=False, key="mi_s_mandatory")
            with c2:
                item_data["on_request"] = st.checkbox("On request", value=False, key="mi_s_on_request")
            item_data["single_price"] = item_data["price"]
            item_data["double_price"] = item_data["price"]
        elif structured_kind == "transfer_supplement":
            st.caption("Mandatory, automatically-applied surcharges only - an optional extra "
                      "belongs under Additional Service instead.")
            # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-09): a seasonal surcharge (Christmas/
            # NYE/Easter) computed as (Transport/Transfer Price + already existing Price
            # supplement) * x% OR a flat x number, for a defined period - see
            # bulk_notes._existing_transfer_supplement_total's own docstring for why this is
            # NOT the same as just sending PERCENT straight to Travel Compositor (which only
            # ever applies a percent to the base price, ignoring whatever else is already
            # charged on top).
            item_data["compute_from_current_price"] = st.checkbox(
                "Compute from each transfer's current price + already-active supplement",
                value=True, key="mi_s_compute_from_price",
                help="ON (recommended for a seasonal surcharge): the app reads each transfer's "
                     "own live base price plus whatever mandatory surcharge is already active "
                     "today, and writes a single € amount = (price + existing supplement) * "
                     "your % - or your flat amount, unchanged. OFF: your amount/type is sent "
                     "to Travel Compositor exactly as entered (its own PERCENT semantics apply "
                     "only to the base price, same as before this option existed).")
            c1, c2 = st.columns(2)
            with c1:
                item_data["amount"] = st.number_input("Amount", min_value=0.0, step=1.0, key="mi_s_amount")
                if item_data["compute_from_current_price"]:
                    item_data["is_percent"] = st.radio(
                        "Type", ["Fixed amount", "Percent of price + existing supplement"],
                        key="mi_s_pct_type", horizontal=True) == "Percent of price + existing supplement"
                else:
                    item_data["type"] = st.radio("Type", ["ABSOLUTE", "PERCENT"], key="mi_s_type",
                                                 horizontal=True,
                                                 help="PERCENT is applied to the base price itself by "
                                                      "Travel Compositor - never pre-calculate it.")
            with c2:
                item_data["start_time"] = st.text_input("Start time (optional, HH:MM)", key="mi_s_start_time",
                                                         placeholder="22:00")
                item_data["end_time"] = st.text_input("End time (optional, HH:MM)", key="mi_s_end_time",
                                                       placeholder="08:00")
            c3, c4 = st.columns(2)
            with c3:
                item_data["start_date"] = _dmy_date_field(
                    f"Period start date {_DATE_HINT}, e.g. Christmas/NYE/Easter",
                    "mi_s_period_start", placeholder="20/12/2026")
            with c4:
                item_data["end_date"] = _dmy_date_field(
                    f"Period end date {_DATE_HINT}", "mi_s_period_end", placeholder="05/01/2027")
            st.caption("Dates left blank inherit each transfer's own validity window (i.e. "
                      "applies all year, not just the period).")
        elif structured_kind == "transport_supplement":
            st.caption("Adds a dated price supplement to EVERY occupancy bracket (modality - "
                      "Sedan, Hiace, ...) of EVERY Transport of this supplier - e.g. a Christmas/"
                      "New Year's Eve/Easter surcharge. Travel Compositor has no percent-type "
                      "surcharge for Transport (unlike Transfer), so the app always computes and "
                      "writes a plain € amount, worked out separately per bracket (each modality "
                      "has its own base price and its own existing supplement).")
            c1, c2 = st.columns(2)
            with c1:
                # CONFIRMED REAL NEED (product owner, 2026-09-10): "The Amount is only available
                # in full number, no need for 0.00." min_value/step as plain ints (not 0.0/1.0)
                # is what makes Streamlit treat this as a whole-number input with no decimals.
                item_data["amount"] = st.number_input("Amount", min_value=0, step=1, key="mi_ts_amount")
                item_data["is_percent"] = st.radio(
                    "Type", ["Absolute number", "Percent of price + existing supplement"],
                    key="mi_ts_pct_type", horizontal=True) == "Percent of price + existing supplement"
            with c2:
                # CONFIRMED BUG (product owner, 2026-09-10): "remove the calendar function within
                # adding manual information in transport, as it does not work." Reverted to a
                # plain typeable field here - the calendar popover (_dmy_date_field) stays in use
                # everywhere else it wasn't reported broken.
                item_data["start_date"] = _iso(st.text_input(
                    f"Period start date {_DATE_HINT}", key="mi_ts_period_start", placeholder="20/12/2026"))
                item_data["end_date"] = _iso(st.text_input(
                    f"Period end date {_DATE_HINT}", key="mi_ts_period_end", placeholder="05/01/2027"))
            st.caption("Percent is computed per bracket as (that bracket's base price + whatever "
                      "surcharge is in effect as of the period's start date) * your % - never "
                      "pre-calculated from one bracket and reused for the others, since brackets "
                      "do not scale together (confirmed: real examples show non-monotonic "
                      "per-bracket amounts).")
            st.caption("CORRECTED (2026-09-10): each bracket's existing standing (always-on) "
                      "supplement is automatically split around the peak period rather than "
                      "left overlapping it - the standing entry is truncated to end the day "
                      "before the period starts, the new peak entry is inserted, and a fresh "
                      "entry resuming the standing rate is added for the day after the period "
                      "ends through 2049-12-31. No two entries for the same bracket ever cover "
                      "the same day.")
        elif structured_kind == "transport_price_increase":
            st.caption("Permanently raises EVERY Transport of this supplier's own price - e.g. "
                      "current price 60 USD, +10% -> 66 USD (or +5 -> 65 USD flat) written back "
                      "to Travel Compositor. Unlike the dated supplement above, this mutates the "
                      "existing price fields in place - it does not add a new dated entry. Works "
                      "for both pricing models: a per-passenger Transport (adult/children/"
                      "infant base prices) and a per-vehicle one (a single Vehicle price, "
                      "\"Price Per Pax\" unchecked) - the app reads whichever field is really "
                      "the base price for that specific Transport.")
            # CONFIRMED REAL NEED (product owner, 2026-09-10): "adds manually amount of "
            # percentage or absolute number and this will be added to the already existing base
            # price" - same Absolute/Percent choice already offered on the dated supplement
            # target above, kept symmetrical rather than percent-only.
            item_data["is_percent"] = st.radio(
                "Type", ["Percent (%)", "Absolute number"],
                key="mi_tpi_type", horizontal=True) == "Percent (%)"
            if item_data["is_percent"]:
                item_data["amount"] = st.number_input(
                    "Increase by (%)", min_value=0.0, step=1.0, key="mi_tpi_amount",
                    help="Applied to every base price field and/or the currently-active "
                         "supplement amount, per your choice below.")
            else:
                item_data["amount"] = st.number_input(
                    "Increase by (flat amount, in that Transport's own currency)",
                    min_value=0, step=1, key="mi_tpi_amount_abs",
                    help="Added directly to every base price field and/or the currently-active "
                         "supplement amount, per your choice below. A field already at 0 is "
                         "always left at 0 in this mode too - it means \"not priced\", not "
                         "\"priced at zero\".")
            st.caption("Choose what to increase - any combination (base price only, active "
                      "supplement only, or both):")
            c1, c2 = st.columns(2)
            with c1:
                item_data["increase_base"] = st.checkbox(
                    "Increase the base price", value=True, key="mi_tpi_base",
                    help="Raises every baseAdultPrice/baseChildrenPrice/baseInfantPrice (and "
                         "round-trip equivalents) on the Transport record itself.")
            with c2:
                item_data["increase_supplement"] = st.checkbox(
                    "Increase the currently active supplement", value=False, key="mi_tpi_supp",
                    help="Raises the amount of whichever dated price-supplement entry is in "
                         "effect today, per occupancy bracket. Brackets with no active "
                         "supplement are left unchanged.")
            if not item_data["increase_base"] and not item_data["increase_supplement"]:
                st.warning("Choose at least one: base price, active supplement, or both.")
        elif structured_kind == "transfer_additional_service":
            st.caption("A genuinely optional extra the client chooses to take, e.g. a child seat.")
            c1, c2 = st.columns(2)
            with c1:
                item_data["price"] = st.number_input("Price", min_value=0.0, step=1.0, key="mi_s_svc_price")
                item_data["currency"] = st.text_input("Currency (optional - defaults to the transfer's own)",
                                                       key="mi_s_svc_currency", placeholder="EUR")
            with c2:
                item_data["max_quantity"] = st.number_input("Maximum quantity", min_value=1, step=1,
                                                             value=1, key="mi_s_svc_max")
                item_data["on_request"] = st.checkbox("On request", value=False, key="mi_s_svc_on_request")

        codes = None
        if bulk_notes.needs_manual_codes(product_type):
            st.info("Travel Compositor has no endpoint that lists closed tours, so they can't be "
                    "found automatically — paste the tour codes, one per line.")
            raw_codes = st.text_area("ClosedTour codes", key="mi_codes", height=80,
                                     placeholder="ASW-CT1\nCAI-CT2")
            codes = [c.strip() for c in (raw_codes or "").splitlines() if c.strip()]
        also_future = False
    elif add_price_code:
        st.markdown("### 2. Set the price-validity date")
        # REVERTED 2026-09-11 (real production evidence - see bulk_notes.TARGETS["Transport"]'s
        # own note): Transport has NO separate voucher-remarks field in Travel Compositor's real
        # API (confirmed via Swagger, not just a UI screenshot) - the code goes into Description
        # for Transport, same as every other piece of Transport conditions text. Every other
        # product type here still uses its real Voucher remarks field.
        target = "Description (bottom)" if product_type == "Transport" else "Voucher remarks"
        st.caption(
            f"Writes a \"(YYYYMMDD)\" code into every {product_type} service's {target}. "
            "The code means: prices are confirmed until this date. If a service ALREADY has "
            "a code, only the code itself is replaced - every other word already in the field "
            "is left exactly as it is."
        )
        pv_date = st.date_input("Prices confirmed until", key="mi_pv_date")
        text = pv_date.isoformat() if pv_date else ""
        mode = bulk_notes.MODE_PRICE_CODE
        codes = None
        also_future = False
    else:
        # ---- 1b. Where does the text go? -------------------------------
        st.markdown("### 1. Where should the text go?" if not structured_labels
                    else "### 2. Where should the text go?")
        targets = bulk_notes.available_targets(product_type)
        target = st.selectbox("Field", targets, key="mi_target")
        missing = bulk_notes.unavailable_targets(product_type)
        if missing:
            # Naming what ISN'T possible, and why, stops someone hunting for an option that
            # was never there - which is exactly what happened with the first Hotel note.
            st.caption("Not available on " + product_type + ": "
                       + "  ·  ".join(f"**{k}** — {v}" for k, v in missing.items()))

        # ---- 2. The text ------------------------------------------------
        st.markdown("### 2. What should it say?" if not structured_labels else "### 3. What should it say?")
        text = st.text_area(
            "Text to add to every one of this supplier's " + product_type + " services",
            key="mi_text", height=120,
            placeholder="e.g. All pickups now depart from the new terminal, not the old arrivals hall.",
        )
        mode_label = st.radio(
            "How should it be written?",
            ["Add at the bottom (keep what is already there)",
             "Replace the field completely"],
            key="mi_mode",
        )
        mode = (bulk_notes.MODE_REPLACE if mode_label.startswith("Replace") else bulk_notes.MODE_APPEND)
        if mode == bulk_notes.MODE_REPLACE:
            st.warning("⚠️ Replace deletes whatever is currently in that field — including text "
                       "extracted from the supplier's own contract. There is no undo in Travel "
                       "Compositor. Use it only when the old wording is genuinely superseded.")

        codes = None
        if bulk_notes.needs_manual_codes(product_type):
            st.info("Travel Compositor has no endpoint that lists closed tours, so they can't be "
                    "found automatically — paste the tour codes, one per line.")
            raw_codes = st.text_area("ClosedTour codes", key="mi_codes", height=80,
                                     placeholder="ASW-CT1\nCAI-CT2")
            codes = [c.strip() for c in (raw_codes or "").splitlines() if c.strip()]

        also_future = st.checkbox(
            f"Also attach this to every {product_type} I upload from now on",
            value=True, key="mi_also_future",
            help="Saved as a standing note. Note: on future uploads it is added to the Voucher "
                 "Remarks, which is the field the upload flows write notes into.",
        )

    # ---- 3. Preview, then send ----------------------------------------
    st.markdown("### 3. Check it, then send" if not structured_labels else "### 4. Check it, then send")
    st.caption("Preview reads Travel Compositor and shows exactly what would change. Nothing "
              "is written until you press Send.")

    # CONFIRMED PRODUCT-OWNER REQUEST (2026-08-14): "either for all Service from a supplier
    # or just a selection" - the preview list below now has a checkbox per would-change
    # service so specific ones can be deselected before Send, for both text and structured
    # additions. ClosedTour keeps its pasted-codes list as the primary way to narrow the set,
    # but the checkboxes work there too if a pasted code turns out not to need this after all.
    if add_structured:
        preview_disabled = not (item_data.get("name") or "").strip()
        current_sig = (supplier_id, product_type, structured_kind,
                       tuple(sorted((item_data or {}).items())), tuple(codes or ()))
    else:
        preview_disabled = not (text or "").strip()
        current_sig = (supplier_id, product_type, target, text, mode, tuple(codes or ()))

    pcol1, pcol2 = st.columns([1, 3])
    with pcol1:
        if st.button("🔍 Preview", key="mi_preview", disabled=preview_disabled):
            bar = st.progress(0.0, text="Reading Travel Compositor...")

            def _tick(done, total, name):
                bar.progress(min(done / max(total, 1), 1.0), text=f"Checking {name} ({done}/{total})")

            try:
                if add_structured:
                    st.session_state.mi_plan = bulk_notes.plan_structured(
                        client, supplier_id, product_type, structured_kind, item_data,
                        codes=codes, progress=_tick)
                else:
                    st.session_state.mi_plan = bulk_notes.plan(
                        client, supplier_id, product_type, target, text, mode=mode,
                        codes=codes, progress=_tick)
                # The signature includes `codes`: without it, previewing tours A+B and then
                # editing the box to C left Send armed and would have written A+B.
                st.session_state.mi_plan_sig = current_sig
            except Exception as e:
                # A failed refresh must DISARM, never leave the previous plan pressable.
                for _k in ("mi_plan", "mi_plan_sig"):
                    st.session_state.pop(_k, None)
                st.error(f"Couldn't read Travel Compositor: {friendly_error_message(e)}")
            bar.empty()
            st.session_state.pop("mi_result", None)
            st.rerun()

    planned = st.session_state.get("mi_plan")
    # A plan is only valid for the exact inputs it was built from. Editing the text after
    # previewing and then pressing Send would otherwise publish the OLD text.
    if planned and st.session_state.get("mi_plan_sig") != current_sig:
        st.info("You changed something after previewing — press Preview again to see the new result.")
        planned = None

    if planned:
        if planned.get("error"):
            st.error(planned["error"])
        noun = "service(s)" if add_structured else "already have this text or have nothing to write into"
        st.markdown(f"**{planned['will_change']} service(s) would change**, "
                    f"{planned['unchanged']} {'already have this entry' if add_structured else noun}, "
                    f"{planned['failed']} couldn't be read.")
        # CONFIRMED REAL NEED (product owner, 2026-09-10, testing the Transport high-season
        # supplement tool for the first time): "i miss a button which would exclude ALL found
        # transports and the human can select only the once that is applying" - every item
        # defaults to included, which is right for a plain text write (everyone gets the same
        # note) but wrong for a first cautious run of something like a dated supplement, where
        # the human wants to start from nothing selected and hand-pick just the few modalities
        # a peak-season change actually applies to, rather than unchecking every other one.
        will_change_ids = [it.get("id") for it in planned["items"] if it["status"] == "will_change"]
        if will_change_ids:
            scol1, scol2 = st.columns(2)
            with scol1:
                if st.button("Select all", key="mi_select_all", use_container_width=True):
                    for it in planned["items"]:
                        if it["status"] == "will_change":
                            it["_include"] = True
                            # CONFIRMED BUG FIX (product owner, 2026-09-10): "the select all or
                            # select none button do not work." Streamlit ignores a checkbox's
                            # `value=` argument once its own widget key already has an entry in
                            # session_state (see widget_state.py's own docstring on this exact
                            # bug class) - updating it["_include"] alone left every already-
                            # rendered checkbox showing its old state. Writing the checkbox's own
                            # key directly is what actually moves it.
                            st.session_state[f"mi_include_{it.get('id')}"] = True
                    st.rerun()
            with scol2:
                if st.button("Select none", key="mi_select_none", use_container_width=True):
                    for it in planned["items"]:
                        if it["status"] == "will_change":
                            it["_include"] = False
                            st.session_state[f"mi_include_{it.get('id')}"] = False
                    st.rerun()

        for it in planned["items"]:
            icon = {"will_change": "✏️", "unchanged": "➖", "failed": "❌"}[it["status"]]
            with st.expander(f"{icon} {it['name']}"
                             + (f" — {it.get('reason') or it.get('detail','')}"
                                if it["status"] != "will_change" else ""),
                             expanded=False):
                if it["status"] == "will_change":
                    # Mutating `it` in place is deliberate: `planned` IS
                    # st.session_state.mi_plan, so this checkbox's value survives reruns
                    # without a separate session key to keep in sync.
                    it["_include"] = st.checkbox(
                        "Include this service", value=it.get("_include", True),
                        key=f"mi_include_{it.get('id')}")
                    for lang, (before, after) in sorted(it["changes"].items()):
                        st.caption(f"{lang} — before")
                        st.code(before or "(empty)")
                        st.caption(f"{lang} — after")
                        st.code(after)
                else:
                    st.caption(it.get("reason") or it.get("detail") or "")

        included_count = sum(1 for it in planned["items"]
                             if it["status"] == "will_change" and it.get("_include", True))
        if planned["will_change"]:
            if included_count < planned["will_change"]:
                st.caption(f"{planned['will_change'] - included_count} deselected above — "
                          f"those will be left untouched.")
            st.warning(f"This writes to **{included_count} live service(s)** for supplier "
                       f"{supplier_id}. Travel Compositor has no undo.")
            if st.button(f"🚀 Send to {included_count} service(s)", type="primary",
                         key="mi_send", disabled=not included_count):
                bar = st.progress(0.0, text="Sending...")

                def _tick2(done, total, name):
                    bar.progress(min(done / max(total, 1), 1.0), text=f"Updating {name} ({done}/{total})")

                # Deselected items are excluded by giving them a status apply() doesn't act
                # on - apply() only ever sends items still marked "will_change", and counts
                # everything else as skipped.
                to_apply = dict(planned)
                to_apply["items"] = [
                    (dict(it, status="excluded_by_user")
                     if it["status"] == "will_change" and not it.get("_include", True) else it)
                    for it in planned["items"]
                ]
                st.session_state.mi_result = bulk_notes.apply(client, supplier_id, to_apply,
                                                              progress=_tick2)
                bar.empty()
                if also_future:
                    st.session_state.mi_future_saved = service_notes.set_standing_note(
                        supplier_id, product_type, text)
                # Drop the plan: it holds pre-modified snapshots, so leaving Send on screen
                # let a second click re-PUT them and silently revert any edit made in Travel
                # Compositor in between.
                for _k in ("mi_plan", "mi_plan_sig"):
                    st.session_state.pop(_k, None)
                st.rerun()
        else:
            st.info("Nothing to send — every live service already has this "
                    + ("entry." if add_structured else "text."))
            if also_future and st.button("💾 Save it for future uploads anyway",
                                         key="mi_save_future_only"):
                if service_notes.set_standing_note(supplier_id, product_type, text):
                    st.success("Saved. It will be added to every future upload of this type.")
                else:
                    st.error("Could not save it — it will NOT apply to future uploads.")

    result = st.session_state.get("mi_result")
    if result:
        if result["updated"]:
            st.success(f"✅ Sent. {len(result['updated'])} service(s) updated.")
            for u in result["updated"]:
                st.write(f"- {u['name']} ({', '.join(u['languages'])})")
                # CONFIRMED REAL NEED (product owner, 2026-09-11): a real Transport bulk write
                # reported success here but the field never actually showed the new value in
                # Travel Compositor - same shape as the earlier price-refresh write-not-
                # persisting investigation. Shows the exact request/response so that can be
                # checked directly instead of guessed at.
                _mi_dbg = u.get("debug")
                if _mi_dbg:
                    with st.expander(f"🔍 Raw request/response for {u['name']} (debug)"):
                        st.caption("Request body sent:")
                        st.json(_mi_dbg.get("request"))
                        st.caption("Response received:")
                        st.json(_mi_dbg.get("response"))
        if st.session_state.get("mi_future_saved") is False:
            st.error("⚠️ The live services were updated, but the note could NOT be saved for "
                     "future uploads — check the database banner at the top of the page.")
        if result["failed"]:
            st.error(f"❌ {len(result['failed'])} service(s) failed — nothing was changed on these:")
            for f in result["failed"]:
                st.write(f"- {f.get('name')}: {f.get('detail')}")
                if f.get("debug"):
                    with st.expander(f"🔍 Raw request/response for {f.get('name')} (debug)"):
                        st.json(f["debug"])
            st.caption("Re-running is safe: services already updated are detected and skipped.")
        if st.button("Clear this result", key="mi_clear_result"):
            for k in ("mi_result", "mi_plan", "mi_plan_sig", "mi_future_saved"):
                st.session_state.pop(k, None)
            st.rerun()

    st.markdown("---")
    st.subheader("📌 All standing notes currently in force")
    existing = service_notes.list_standing_notes()
    if not existing:
        st.caption("None yet. Anything saved above appears here, and applies to every future "
                  "upload of that type until you clear it.")
    else:
        # Every note here silently alters future uploads, so they all have to be visible and
        # removable in one place - otherwise a note written months ago keeps appending itself
        # to vouchers with nobody remembering it exists.
        name_by_id = {str(s.get("id")): (s.get("commercialName") or s.get("legalName") or "")
                      for s in (st.session_state.suppliers_cache or [])}
        for note in existing:
            cols = st.columns([6, 1])
            with cols[0]:
                who = name_by_id.get(note["supplier_id"], "")
                st.markdown(f"**{note['product_type']} · {who or 'supplier'} "
                            f"(ID {note['supplier_id']})**")
                st.info(note["text"])
                if note.get("updated_at"):
                    st.caption(f"Last updated {note['updated_at'][:16].replace('T', ' ')} UTC")
            with cols[1]:
                if st.button("🗑️", key=f"mi_clear_{note['supplier_id']}_{note['product_type']}",
                             help="Clear this note"):
                    service_notes.set_standing_note(note["supplier_id"], note["product_type"], "")
                    st.rerun()
