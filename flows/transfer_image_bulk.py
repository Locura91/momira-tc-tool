"""
Transfer image bulk-upload screen — the UI half of transfer_image_bulk.py (see that module's
own docstring for the full reasoning and how it differs from supplier_images.py's per-direction
"future builds" feature).

CONFIRMED PRODUCT-OWNER REQUEST (2026-09-25, verbatim): "i need to create a mass image upload
for Transfers. The goal is that human can select the supplier if he wants or the human selects
ServiceType by Transfer(Private; Shuttle or Shared) and the existing image will be removed and
the new image will be added... We could integrate that to manage existing product." Follow-up:
"either per supplier or per transfertype or a mixture" (both filters optional, combinable), and
"the image shall come from the local PC or from another URL that the human can select."

SAFETY GATE (this file's own choice, not transfer_image_bulk.py's): at least one of supplier /
ServiceType must be picked before scanning - "every live Transfer in the whole account" is never
offered as a scope, to keep one accidental click from touching services nobody meant to touch.
"""
import streamlit as st

from ui_components import is_active_supplier

MODULE_BUILD = "2026-09-25-transfer-image-bulk-upload"

_ANY_SUPPLIER = "— Any supplier —"
_ANY_TYPE = "— Any ServiceType —"


def _load_momira_suppliers(client):
    if st.session_state.get("suppliers_cache") is None:
        with st.spinner("Loading supplier list from Travel Compositor..."):
            try:
                st.session_state.suppliers_cache = client.get_all_suppliers()
            except Exception as e:
                st.error(f"❌ Couldn't load the supplier list: {type(e).__name__}: {e}")
                st.session_state.suppliers_cache = []
    out = []
    for s in (st.session_state.suppliers_cache or []):
        name = (s.get("commercialName") or s.get("legalName") or "").strip()
        if name.lower().startswith("momira_") and is_active_supplier(s):
            out.append({"id": str(s.get("id")), "name": name})
    return sorted(out, key=lambda s: s["name"])


def render_transfer_image_bulk_flow(client):
    import transfer_image_bulk as tib
    import r2_client

    st.header("🖼️ Bulk-change Transfer images")
    if st.button("🔙 Back to Step 1", key="tib_back"):
        st.session_state.product_type = None
        st.rerun()
    st.caption(
        "Replace the image on many ALREADY-LIVE Transfers at once — pick a supplier, a "
        "ServiceType (Private/Shuttle/Shared), or both together, upload one new photo (from "
        "your computer, or paste a URL), and it REPLACES whatever image is currently on every "
        "matching Transfer. Nothing is written until you review the list and confirm."
    )

    if st.session_state.get("tib_results"):
        st.markdown("---")
        st.subheader("Result")
        results = st.session_state.tib_results
        st.caption(f"{len(results['updated'])} updated · {results['skipped']} skipped · "
                  f"{len(results['failed'])} failed.")
        for r in results["updated"]:
            st.success(f"✅ **{r['name']}** updated.")
        for r in results["failed"]:
            st.error(f"🚫 **{r['name']}** — {r['detail']}")
        if st.button("↩️ Run again / start over", key="tib_reset"):
            for key in ("tib_rows", "tib_selected", "tib_results", "tib_resolved_url"):
                st.session_state.pop(key, None)
            st.rerun()
        return

    momira_suppliers = _load_momira_suppliers(client)
    if not momira_suppliers:
        st.error("🚫 No suppliers starting with 'Momira_' were found in this account - can't continue.")
        return

    st.subheader("1 — Which Transfers?")
    col1, col2 = st.columns(2)
    with col1:
        supplier_labels = [_ANY_SUPPLIER] + [f"{s['name']} — ID {s['id']}" for s in momira_suppliers]
        supplier_choice = st.selectbox("Supplier", supplier_labels, key="tib_supplier_choice")
    with col2:
        type_label_to_key = {tib.SERVICE_TYPE_LABELS[t]: t for t in tib.SERVICE_TYPES}
        type_labels = [_ANY_TYPE] + list(type_label_to_key.keys())
        type_choice = st.selectbox("ServiceType", type_labels, key="tib_type_choice")

    if supplier_choice == _ANY_SUPPLIER and type_choice == _ANY_TYPE:
        st.warning("⚠️ Pick a supplier, a ServiceType, or both — applying to every live "
                  "Transfer in the whole account isn't offered here.")
        return

    if supplier_choice == _ANY_SUPPLIER:
        target_suppliers = momira_suppliers
        st.caption(f"Scope: every **{type_choice}** Transfer across all {len(momira_suppliers)} "
                   f"Momira suppliers — this scans every supplier and will take longer.")
    else:
        chosen = next(s for s in momira_suppliers if supplier_choice == f"{s['name']} — ID {s['id']}")
        target_suppliers = [chosen]
        if type_choice == _ANY_TYPE:
            st.caption(f"Scope: every Transfer of **{chosen['name']}**.")
        else:
            st.caption(f"Scope: **{type_choice}** Transfers of **{chosen['name']}**.")

    service_type = None if type_choice == _ANY_TYPE else type_label_to_key[type_choice]

    st.subheader("2 — New image")
    source = st.radio("Image source", ["Upload from my computer", "Paste a URL"],
                      key="tib_image_source", horizontal=True)
    resolved_url = None
    if source == "Upload from my computer":
        uploaded = st.file_uploader("Image file", type=["jpg", "jpeg", "png", "webp"], key="tib_upload")
        if uploaded is not None:
            st.image(uploaded.getvalue(), width=200)
    else:
        pasted_url = st.text_input("Image URL", key="tib_url", placeholder="https://...").strip()
        if pasted_url:
            st.image(pasted_url, width=200)
            resolved_url = pasted_url

    st.subheader("3 — Load matching Transfers")
    can_load = (source == "Paste a URL" and bool(resolved_url)) or \
              (source == "Upload from my computer" and st.session_state.get("tib_upload") is not None)
    if st.button("📥 Load", key="tib_load", disabled=not can_load):
        final_url = resolved_url
        if source == "Upload from my computer":
            uploaded = st.session_state.get("tib_upload")
            with st.spinner("Uploading image..."):
                try:
                    final_url = r2_client.upload_image(
                        uploaded.getvalue(),
                        filename=uploaded.name or "transfer_image.jpg",
                    )
                except Exception as e:
                    st.error(f"❌ Couldn't upload the image: {type(e).__name__}: {e}")
                    return
        bar = st.progress(0.0, text="Scanning...")

        def _prog(done, total, label):
            bar.progress(min(done / max(total, 1), 1.0), text=f"Scanning {label} ({done}/{total})")

        planned = tib.plan(client, target_suppliers, service_type, final_url, progress=_prog)
        if planned.get("error") and not planned.get("items"):
            st.error(f"❌ {planned['error']}")
            return
        if planned.get("error"):
            st.warning(f"⚠️ Some suppliers couldn't be scanned: {planned['error']}")
        st.session_state.tib_rows = planned
        st.session_state.tib_selected = {
            i["id"]: True for i in planned["items"] if i["status"] == "will_change"
        }
        st.rerun()

    planned = st.session_state.get("tib_rows")
    if planned is None:
        return
    items = planned.get("items", [])
    will_change = [i for i in items if i["status"] == "will_change"]
    unchanged = [i for i in items if i["status"] == "unchanged"]
    if not items:
        st.info("No matching Transfers found for this scope.")
        return

    st.subheader(f"4 — Review and choose which to update "
                 f"({len(will_change)} would change, {len(unchanged)} already match)")
    bcol1, bcol2 = st.columns(2)
    with bcol1:
        if st.button("Select all", key="tib_select_all"):
            st.session_state.tib_selected = {i["id"]: True for i in will_change}
            for i in will_change:
                st.session_state[f"tib_pick_{i['id']}"] = True
            st.rerun()
    with bcol2:
        if st.button("Select none", key="tib_select_none"):
            st.session_state.tib_selected = {i["id"]: False for i in will_change}
            for i in will_change:
                st.session_state[f"tib_pick_{i['id']}"] = False
            st.rerun()

    for item in will_change:
        label = f"**{item['name']}**  ·  {tib.SERVICE_TYPE_LABELS.get(item['service_type'], item['service_type'])}  ·  id `{item['id']}`"
        st.session_state.tib_selected[item["id"]] = st.checkbox(
            label, value=st.session_state.tib_selected.get(item["id"], True),
            key=f"tib_pick_{item['id']}")
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
        with st.expander(f"{len(unchanged)} already match this image — left alone", expanded=False):
            for item in unchanged:
                st.caption(f"{item['name']} · {tib.SERVICE_TYPE_LABELS.get(item['service_type'], item['service_type'])}")

    selected_ids = {pid for pid, v in st.session_state.tib_selected.items() if v}
    st.caption(f"{len(selected_ids)} of {len(will_change)} selected.")
    if not selected_ids:
        return

    st.subheader("5 — Apply")
    st.warning(f"⚠️ This will PUT (update) {len(selected_ids)} Transfer(s) — the `images` field "
              f"only. Everything else on each record is left exactly as it is.")
    if st.button(f"🚀 Update {len(selected_ids)} Transfer(s)", key="tib_confirm", type="primary"):
        bar = st.progress(0.0, text="Updating...")

        def _prog(done, total, label):
            bar.progress(min(done / max(total, 1), 1.0), text=f"Updating {label} ({done}/{total})")

        with st.spinner("Updating..."):
            results = tib.apply(client, planned, selected_ids=selected_ids, progress=_prog)
        st.session_state.tib_results = results
        st.rerun()
