"""
Transport image bulk-upload screen — direct counterpart to flows/transfer_image_bulk.py
(2026-09-25), adapted for Transport.

CONFIRMED PRODUCT-OWNER REQUEST (2026-10-18, verbatim): "the same style must be adopted for
transport. So we can then change the by supplier, then we can choose the transport type which
will be either plane, car, van, combined. The logic itself is the same as for transfers."

Filter: Supplier (optional) + Transport Type — Car / Plane / Van / Combined (optional, from
transport_image_bulk.TRANSPORT_TYPES). At least one must be chosen before scanning — same safety
gate as the Transfer version.

The per-item full-record fetch means scanning is slower than Transfer (one GET per transport, vs
one GET per supplier for Transfer). Progress is shown to the human during the scan.
"""
import streamlit as st

from ui_components import momira_active_suppliers

MODULE_BUILD = "2026-10-20-bulk-stop-sales"

_ANY_SUPPLIER = "— Any supplier —"
_ANY_TYPE = "— Any Transport Type —"


def _load_momira_suppliers(client):
    if st.session_state.get("suppliers_cache") is None:
        with st.spinner("Loading supplier list from Travel Compositor..."):
            try:
                st.session_state.suppliers_cache = client.get_all_suppliers()
            except Exception as e:
                st.error(f"❌ Couldn't load the supplier list: {type(e).__name__}: {e}")
                st.session_state.suppliers_cache = []
    out = [
        {"id": str(s.get("id")), "name": (s.get("commercialName") or s.get("legalName") or "").strip()}
        for s in momira_active_suppliers(st.session_state.suppliers_cache)
    ]
    return sorted(out, key=lambda s: s["name"])


def render_transport_image_bulk_flow(client):
    import transport_image_bulk as tib
    import r2_client

    st.header("🖼️ Bulk-change Transport images")
    if st.button("🔙 Back to Step 1", key="tpib_back"):
        st.session_state.product_type = None
        st.session_state.pop("image_bulk_product", None)
        st.rerun()
    st.caption(
        "Replace the image on many ALREADY-LIVE Transports at once — pick a supplier, a "
        "Transport Type (Car / Plane / Van / Combined), or both, upload one new photo (from "
        "your computer, or paste a URL), and it REPLACES whatever image is currently on every "
        "matching Transport. Nothing is written until you review the list and confirm. "
        "Note: Transport records are fetched individually, so scanning may take a moment."
    )

    if st.session_state.get("tpib_results"):
        st.markdown("---")
        st.subheader("Result")
        results = st.session_state.tpib_results
        st.caption(f"{len(results['updated'])} updated · {results['skipped']} skipped · "
                  f"{len(results['failed'])} failed.")
        for r in results["updated"]:
            if r.get("write_verified") is False:
                st.warning(
                    f"⚠️ **{r['name']}** — PUT returned 200 but the image was NOT saved in TC "
                    f"(re-fetch shows: `{r.get('actual_images')}`). "
                    f"TC may not support setting images via this endpoint."
                )
            else:
                st.success(f"✅ **{r['name']}** updated and verified.")
        for r in results["failed"]:
            st.error(f"🚫 **{r['name']}** — {r['detail']}")
        if st.button("↩️ Run again / start over", key="tpib_reset"):
            for key in ("tpib_rows", "tpib_selected", "tpib_results"):
                st.session_state.pop(key, None)
            st.rerun()
        return

    momira_suppliers = _load_momira_suppliers(client)
    if not momira_suppliers:
        st.error("🚫 No suppliers starting with 'Momira_' were found in this account — can't continue.")
        return

    st.subheader("1 — Which Transports?")
    col1, col2 = st.columns(2)
    with col1:
        supplier_labels = [_ANY_SUPPLIER] + [f"{s['name']} — ID {s['id']}" for s in momira_suppliers]
        supplier_choice = st.selectbox("Supplier", supplier_labels, key="tpib_supplier_choice")
    with col2:
        type_label_to_key = {tib.TRANSPORT_TYPE_LABELS[t]: t for t in tib.TRANSPORT_TYPES}
        type_labels = [_ANY_TYPE] + list(type_label_to_key.keys())
        type_choice = st.selectbox("Transport Type", type_labels, key="tpib_type_choice")

    if supplier_choice == _ANY_SUPPLIER and type_choice == _ANY_TYPE:
        st.warning("⚠️ Pick a supplier, a Transport Type, or both — applying to every live "
                  "Transport in the whole account isn't offered here.")
        return

    scope_bits = []
    if type_choice != _ANY_TYPE:
        scope_bits.append(f"**{type_choice}**")
    scope_desc = " ".join(scope_bits) + " " if scope_bits else "every "
    if supplier_choice == _ANY_SUPPLIER:
        target_suppliers = momira_suppliers
        st.caption(f"Scope: {scope_desc}Transport across all {len(momira_suppliers)} Momira "
                   f"suppliers — this fetches every record and will take longer.")
    else:
        chosen = next(s for s in momira_suppliers if supplier_choice == f"{s['name']} — ID {s['id']}")
        target_suppliers = [chosen]
        st.caption(f"Scope: {scope_desc}Transport(s) of **{chosen['name']}**.")

    transport_type = None if type_choice == _ANY_TYPE else type_label_to_key[type_choice]

    st.subheader("2 — New image")
    source = st.radio("Image source", ["Upload from my computer", "Paste a URL"],
                      key="tpib_image_source", horizontal=True)
    resolved_url = None
    if source == "Upload from my computer":
        uploaded = st.file_uploader("Image file", type=["jpg", "jpeg", "png", "webp"],
                                    key="tpib_upload")
        if uploaded is not None:
            st.image(uploaded.getvalue(), width=200)
    else:
        pasted_url = st.text_input("Image URL", key="tpib_url",
                                   placeholder="https://...").strip()
        if pasted_url:
            st.image(pasted_url, width=200)
            resolved_url = pasted_url

    st.subheader("3 — Load matching Transports")
    can_load = (source == "Paste a URL" and bool(resolved_url)) or \
               (source == "Upload from my computer" and
                st.session_state.get("tpib_upload") is not None)
    if st.button("📥 Load", key="tpib_load", disabled=not can_load):
        final_url = resolved_url
        if source == "Upload from my computer":
            uploaded = st.session_state.get("tpib_upload")
            with st.spinner("Uploading image..."):
                try:
                    final_url = r2_client.upload_image(
                        uploaded.getvalue(),
                        filename=uploaded.name or "transport_image.jpg",
                    )
                except Exception as e:
                    st.error(f"❌ Couldn't upload the image: {type(e).__name__}: {e}")
                    return
        with st.spinner("Checking the image is actually publicly reachable..."):
            ok, reason = r2_client.verify_public_url(final_url)
        if not ok:
            st.error(f"❌ This image URL isn't usable: {reason}")
            return
        bar = st.progress(0.0, text="Scanning...")

        def _prog(done, total, label):
            bar.progress(min(done / max(total, 1), 1.0),
                         text=f"Scanning {label} ({done}/{total})")

        planned = tib.plan(client, target_suppliers, transport_type, final_url, progress=_prog)
        if planned.get("error") and not planned.get("items"):
            st.error(f"❌ {planned['error']}")
            return
        if planned.get("error"):
            st.warning(f"⚠️ Some suppliers couldn't be scanned: {planned['error']}")
        st.session_state.tpib_rows = planned
        st.session_state.tpib_selected = {
            i["id"]: True for i in planned["items"] if i["status"] == "will_change"
        }
        st.rerun()

    planned = st.session_state.get("tpib_rows")
    if planned is None:
        return
    items = planned.get("items", [])
    will_change = [i for i in items if i["status"] == "will_change"]
    unchanged = [i for i in items if i["status"] == "unchanged"]
    if not items:
        fetched = planned.get("_fetched", 0)
        type_skipped = planned.get("_type_skipped", 0)
        if fetched == 0:
            st.info("No Transports found for this supplier — either the supplier has no live "
                    "Transports, or the list could not be fetched (check for errors above).")
        elif type_skipped > 0:
            st.info(f"No matching Transports found — {fetched} transport(s) were fetched but "
                    f"all {type_skipped} were a different Transport Type than selected. "
                    f"Try selecting '— Any Transport Type —' to see all types.")
        else:
            st.info("No matching Transports found for this scope.")
        return

    st.subheader(f"4 — Review and choose which to update "
                 f"({len(will_change)} would change, {len(unchanged)} already match)")
    bcol1, bcol2 = st.columns(2)
    with bcol1:
        if st.button("Select all", key="tpib_select_all"):
            st.session_state.tpib_selected = {i["id"]: True for i in will_change}
            for i in will_change:
                st.session_state[f"tpib_pick_{i['id']}"] = True
            st.rerun()
    with bcol2:
        if st.button("Select none", key="tpib_select_none"):
            st.session_state.tpib_selected = {i["id"]: False for i in will_change}
            for i in will_change:
                st.session_state[f"tpib_pick_{i['id']}"] = False
            st.rerun()

    for item in will_change:
        type_tag = tib.TRANSPORT_TYPE_LABELS.get(item["transport_type"], item["transport_type"])
        label = (f"**{item['name']}**" +
                 (f"  ·  {type_tag}" if type_tag else "") +
                 f"  ·  id `{item['id']}`")
        st.session_state.tpib_selected[item["id"]] = st.checkbox(
            label, value=st.session_state.tpib_selected.get(item["id"], True),
            key=f"tpib_pick_{item['id']}")
        with st.expander("Details", expanded=False):
            dcol1, dcol2 = st.columns(2)
            with dcol1:
                st.caption("**Current**")
                if item["current_images"]:
                    st.image(item["current_images"][0], width=160)
                else:
                    st.caption("*(no image set)*")
            with dcol2:
                st.caption("**New**")
                st.image(item["new_images"][0], width=160)

    if unchanged:
        with st.expander(f"{len(unchanged)} already match this image — left alone",
                         expanded=False):
            for item in unchanged:
                type_tag = tib.TRANSPORT_TYPE_LABELS.get(item["transport_type"],
                                                          item["transport_type"])
                st.caption(f"{item['name']}" + (f" · {type_tag}" if type_tag else ""))

    selected_ids = {pid for pid, v in st.session_state.tpib_selected.items() if v}
    st.caption(f"{len(selected_ids)} of {len(will_change)} selected.")
    if not selected_ids:
        return

    st.subheader("5 — Apply")
    st.warning(f"⚠️ This will PUT (update) {len(selected_ids)} Transport(s) — the `images` field "
              f"only. Everything else on each record is left exactly as it is.")
    if st.button(f"🚀 Update {len(selected_ids)} Transport(s)", key="tpib_confirm",
                 type="primary"):
        bar = st.progress(0.0, text="Updating...")

        def _prog(done, total, label):
            bar.progress(min(done / max(total, 1), 1.0),
                         text=f"Updating {label} ({done}/{total})")

        with st.spinner("Updating..."):
            results = tib.apply(client, planned, selected_ids=selected_ids, progress=_prog)
        st.session_state.tpib_results = results
        st.rerun()
