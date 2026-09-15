"""
Supplier-migration flow, split out of app.py (Phase 1 restructure, zero behaviour change).

render_supplier_migration_flow moved here verbatim. Everything it references that is defined at
app.py's own top level is imported back from app via the same late-binding pattern used by the
earlier flows modules: app.py imports this module only after all of those names are already
defined in its own namespace, so `from app import ...` resolves correctly despite the circular
import shape. Every needed internal name here is defined earlier in app.py's file order than this
function's own original position, so the import-back line stays at that original position.
"""
from datetime import datetime
import streamlit as st
import bulk_notes
import supplier_migration
import transfer_matcher

from app import _ur_pick_momira_supplier


def render_supplier_migration_flow(client):
    """Move ALL (or a chosen subset) of a supplier's services of one type to a different
    supplier.

    CONFIRMED REAL NEED (product owner, 2026-08-24, Transfer only): "If I want mass change the
    supplier A, like all Transfers from supplier must now be changed to supplier B." EXTENDED
    (product owner, 2026-09-10): "this is not only for the transfer section, it must work for
    all services." Travel Compositor has no operation that moves anything directly - supplierId
    is part of every product endpoint's URL, never a field on the payload itself, so a
    product's supplier is fixed for its whole life once created. The only way to "move" one is:
    fetch it whole from supplier A, recreate an identical copy under supplier B (Travel
    Compositor assigns the copy a brand-new identity - the old one can never be reused or
    transferred), then retire the ORIGINAL under A so the same thing can't be booked/sold
    under two suppliers at once. Nothing under A is ever deleted - none of these APIs have a
    delete endpoint at all - so the source records stay in place, just retired.

    All the actual per-type logic - the exact create sequence, and what "retire the original"
    means for each type (a real active=False for four of the five; a stop-sale close-out for
    Hotel, which has neither an active flag nor a delete endpoint) - lives in
    supplier_migration.py, not here. See that module's own docstring for the full reasoning,
    including the confirmed production failures its sequencing is built to avoid repeating.

    KNOWN LIMITATION, surfaced to the operator rather than silently copied: a Transfer using
    ZONE-based routing (departureLocationId/arrivalLocationId, from client.get_transfer_zones)
    carries a zone id that is looked up PER SUPPLIER - the same id under the destination may not
    exist, or may point at a completely different place. Any such Transfer is flagged before
    moving so a human checks the destination supplier's zones rather than trusting a silently-
    copied id that could be silently wrong.
    """
    st.header("Move a Supplier's Services to another Supplier")
    st.caption("Recreates every selected service under a different supplier, then retires the "
              "original there - what 'retires' means depends on the product type you pick "
              "below (Hotel in particular works differently - see the warning once you get to "
              "step 3).")

    product_type = st.radio(
        "Which product type?", ["Transfer", "Transport", "Ticket", "ClosedTour", "Hotel"],
        key="sm_product_type", horizontal=True)

    if st.session_state.get("sm_active_product_type") != product_type:
        # Product type changed - drop everything loaded for the previous one so nothing from a
        # different type's review screen can leak into this one.
        for key in list(st.session_state.keys()):
            if key.startswith("sm_") and key not in ("sm_product_type", "sm_active_product_type"):
                del st.session_state[key]
        st.session_state.sm_active_product_type = product_type

    st.markdown("---")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**From (current supplier)**")
        source_id = _ur_pick_momira_supplier(client, "sm_from")
    with col2:
        st.markdown("**To (new supplier)**")
        dest_id = _ur_pick_momira_supplier(client, "sm_to")

    if st.session_state.get("sm_results"):
        st.markdown("---")
        st.subheader("Result")
        results = st.session_state.sm_results
        ok = [r for r in results if r["ok"] is True]
        partial = [r for r in results if r["ok"] == "partial"]
        failed = [r for r in results if r["ok"] is False]
        st.caption(f"{len(ok)} moved cleanly · {len(partial)} partially done (needs a "
                  f"look) · {len(failed)} failed outright.")
        for r in ok:
            if r.get("moved_in_place"):
                st.success(f"✅ **{r['name']}** — moved to the new supplier (same id "
                          f"`{r.get('new_id')}`), nothing else to do.")
            else:
                st.success(f"✅ **{r['name']}** — now `{r.get('new_id')}` under the new supplier; "
                          f"original retired.")
        for r in partial:
            st.warning(f"⚠️ **{r['name']}** — {r['detail']}")
        for r in failed:
            st.error(f"🚫 **{r['name']}** — failed at the {r['stage']} step: {r['detail']}")
        if st.button("↩️ Move more / start over", key="sm_reset"):
            for key in ("sm_records", "sm_selected", "sm_results", "sm_source_id", "sm_dest_id"):
                st.session_state.pop(key, None)
            st.rerun()
        return

    if not source_id or not dest_id:
        st.info("Choose both suppliers to continue.")
        return
    if source_id == dest_id:
        st.error("🚫 Source and destination are the same supplier - nothing to move.")
        return

    codes = None
    if product_type == "ClosedTour":
        st.caption("ClosedTour has no list-all endpoint in Travel Compositor - paste the codes "
                  "to check, one per line.")
        codes_text = st.text_area("ClosedTour codes", key="sm_ct_codes", height=100)
        codes = [c.strip() for c in codes_text.splitlines() if c.strip()]

    load_disabled = product_type == "ClosedTour" and not codes
    if st.button(f"📥 Load {product_type}(s) from the source supplier", key="sm_load",
                disabled=load_disabled):
        with st.spinner(f"Loading {product_type}(s)..."):
            records, err = bulk_notes.list_services(client, source_id, product_type, codes=codes)
            if err and not records:
                st.error(f"❌ Couldn't load {product_type}(s): {err}")
            else:
                if err:
                    st.warning(f"⚠️ Some couldn't be loaded: {err}")
                st.session_state.sm_records = records
                st.session_state.sm_selected = {i: True for i in range(len(records))}
                st.session_state.sm_source_id = source_id
                st.session_state.sm_dest_id = dest_id
                st.rerun()

    records = st.session_state.get("sm_records")
    if records is None:
        return
    if st.session_state.get("sm_source_id") != source_id or st.session_state.get("sm_dest_id") != dest_id:
        st.warning(f"⚠️ The supplier selection changed since these were loaded - click 'Load "
                  f"{product_type}(s)' again to refresh the list before moving anything.")
        return
    if not records:
        st.info(f"This supplier has no {product_type}(s) to move.")
        return

    id_field = bulk_notes.PRODUCTS[product_type]["id_field"]
    st.subheader(f"2 — Choose which of {len(records)} {product_type}(s) to move")

    bcol1, bcol2 = st.columns(2)
    with bcol1:
        if st.button("Select all", key="sm_select_all"):
            st.session_state.sm_selected = {i: True for i in range(len(records))}
            # CONFIRMED BUG FIX (product owner, 2026-09-10): a checkbox ignores `value=` once
            # its own widget key already has a session_state entry (widget_state.py's own
            # docstring names this bug class) - the tracking dict alone doesn't move an
            # already-rendered checkbox, only writing its own key does.
            for i in range(len(records)):
                st.session_state[f"sm_pick_{i}"] = True
            st.rerun()
    with bcol2:
        if st.button("Select none", key="sm_select_none"):
            st.session_state.sm_selected = {i: False for i in range(len(records))}
            for i in range(len(records)):
                st.session_state[f"sm_pick_{i}"] = False
            st.rerun()

    for i, record in enumerate(records):
        name = bulk_notes.label_for(record, product_type)
        ident = record.get(id_field)
        label = f"**{name}**  ·  id `{ident}`"
        st.session_state.sm_selected[i] = st.checkbox(
            label, value=st.session_state.sm_selected.get(i, True), key=f"sm_pick_{i}")
        if product_type == "Transfer":
            is_zoned = bool(record.get("departureLocationId") or record.get("arrivalLocationId"))
            if is_zoned:
                st.caption("⚠️ Zone-based routing (departureLocationId/arrivalLocationId) - this zone id "
                          "is specific to the SOURCE supplier and may not exist, or may mean something "
                          "different, under the destination. Check the destination supplier's zones in "
                          "Travel Compositor after moving this one, before trusting it live.")

    selected_indices = [i for i, v in st.session_state.sm_selected.items() if v]
    st.caption(f"{len(selected_indices)} of {len(records)} selected.")
    if not selected_indices:
        return

    st.subheader("3 — Move")
    if product_type in ("Transfer", "Transport"):
        # CONFIRMED PRODUCT-OWNER CORRECTION (2026-09-16): "just exchanging the supplier and NOT
        # creating new services... we strictly keep them separately." See supplier_migration.py's
        # module docstring for the full reasoning - this is now a true in-place move (one PUT
        # straight to the destination supplier, same Travel Compositor id).
        st.warning(f"⚠️ This moves {len(selected_indices)} {product_type}(s) directly to the "
                  f"destination supplier - same Travel Compositor id(s), nothing new is created "
                  f"and there's nothing left to retire under the source supplier.")
    else:
        _SM_RETIRE_NOTE = {
            "Ticket": "switches the same number OFF (active = False) under the source supplier",
            "ClosedTour": "switches the same number OFF (active = False) under the source supplier",
            "Hotel": "blocks every future date on the original instead - Travel Compositor has no "
                     "active flag or delete endpoint for Hotel at all, so this is the only way to "
                     "stop it being booked (see supplier_migration.py's migrate_hotel docstring)",
        }
        st.warning(f"⚠️ This creates {len(selected_indices)} new {product_type}(s) under the "
                  f"destination supplier, and {_SM_RETIRE_NOTE[product_type]}. The new records get "
                  f"brand-new Travel Compositor identities - the old ones cannot be reused.")
    if product_type == "Hotel":
        st.caption("Hotel migration also recreates every room, meal plan, offer, supplement and "
                  "rate one at a time (Travel Compositor assigns each a brand-new code - rates "
                  "are remapped to the new codes automatically) - this can be a lot of API calls "
                  "for a hotel with many rate seasons, so it may take a while.")

    if st.button(f"🚀 Move {len(selected_indices)} {product_type.lower()}(s)", key="sm_confirm",
                type="primary"):
        results = []
        progress_bar = st.progress(0.0)
        today_iso = datetime.now().date().isoformat()
        for n, i in enumerate(selected_indices):
            record = records[i]
            name = bulk_notes.label_for(record, product_type)
            progress_bar.progress((n + 1) / len(selected_indices), text=f"Moving {name}...")
            if product_type == "Transfer":
                result = supplier_migration.migrate_transfer(
                    client, source_id, dest_id, record, transfer_matcher=transfer_matcher)
            elif product_type == "Transport":
                result = supplier_migration.migrate_transport(client, source_id, dest_id, record)
            elif product_type == "Ticket":
                result = supplier_migration.migrate_ticket(client, source_id, dest_id, record)
            elif product_type == "ClosedTour":
                result = supplier_migration.migrate_closed_tour(client, source_id, dest_id, record)
            else:
                result = supplier_migration.migrate_hotel(client, source_id, dest_id, record,
                                                           today_iso=today_iso)
            results.append(result)

        st.session_state.sm_results = results
        st.session_state.sm_records = None
        st.session_state.sm_selected = None
        st.rerun()
