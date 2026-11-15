"""
flows/product_image_bulk.py — UI half of product_image_bulk.py, covering the three product
types the bulk image tool did not reach before: Ticket, ClosedTour and Hotel.

See product_image_bulk.py's docstring for why these three share one engine while Transfer
and Transport keep their own flows (they carry type-only filters and behaviour earned from
live bug reports).

SAFETY GATE, same as the Transfer screen: a supplier must be chosen before anything is
scanned — "every live product in the whole account" is never offered as a scope. And the
image URL is verified publicly fetchable before anything is planned or written, because a
successful upload is not proof of that (r2_client.verify_public_url).
"""
import streamlit as st

import product_image_bulk as pib
from flows.flow_ui_helpers import load_momira_suppliers as _load_momira_suppliers

MODULE_BUILD = "2026-10-24-rollover-ext"


def render_product_image_bulk_flow(client, product_type: str):
    import r2_client

    spec = pib.PRODUCT_SPECS[product_type]
    label = spec["label"]
    ns = f"pib_{product_type}"

    st.header(f"🖼️ Bulk-change {label} images")
    st.caption(
        f"Replace the image on many ALREADY-LIVE {label}s at once. Pick a supplier, "
        f"give one new photo (upload or paste a URL), and it REPLACES whatever image is "
        f"currently on every matching {label}. Nothing is written until you review the "
        f"list and confirm."
    )

    # ── Results view ────────────────────────────────────────────────────
    if st.session_state.get(f"{ns}_results"):
        st.markdown("---")
        st.subheader("Result")
        results = st.session_state[f"{ns}_results"]
        st.caption(f"{len(results['updated'])} updated · {results['skipped']} skipped · "
                   f"{len(results['failed'])} failed.")
        for r in results["updated"]:
            st.success(f"✅ **{r['name']}** updated.")
        for r in results["failed"]:
            st.error(f"🚫 **{r['name']}** — {r['detail']}")
        if st.button("↩️ Run again / start over", key=f"{ns}_reset"):
            for k in ("_rows", "_selected", "_results"):
                st.session_state.pop(f"{ns}{k}", None)
            st.rerun()
        return

    suppliers = _load_momira_suppliers(client)
    if not suppliers:
        st.error("🚫 No suppliers starting with 'Momira_' were found in this account.")
        return

    # ── 1. Scope ────────────────────────────────────────────────────────
    st.subheader(f"1 — Which {label}s?")
    supplier_labels = [f"{s['name']} — ID {s['id']}" for s in suppliers]
    choice = st.selectbox("Supplier", [None] + supplier_labels,
                          format_func=lambda c: "— Choose a supplier —" if c is None else c,
                          key=f"{ns}_supplier")
    if choice is None:
        st.warning(f"⚠️ Pick a supplier — applying to every live {label} in the whole "
                   f"account isn't offered here.")
        return
    chosen = suppliers[supplier_labels.index(choice)]
    st.caption(f"Scope: every live {label} of **{chosen['name']}**.")

    # ── 2. New image ────────────────────────────────────────────────────
    st.subheader("2 — New image")
    source = st.radio("Image source", ["Upload from my computer", "Paste a URL"],
                      key=f"{ns}_source", horizontal=True)
    resolved_url = None
    if source == "Upload from my computer":
        uploaded = st.file_uploader("Image file", type=["jpg", "jpeg", "png", "webp"],
                                    key=f"{ns}_upload")
        if uploaded is not None:
            st.image(uploaded.getvalue(), width=200)
    else:
        pasted = (st.text_input("Image URL", key=f"{ns}_url",
                                placeholder="https://...") or "").strip()
        if pasted:
            st.image(pasted, width=200)
            resolved_url = pasted

    # ── 3. Load ─────────────────────────────────────────────────────────
    st.subheader(f"3 — Load matching {label}s")
    can_load = (source == "Paste a URL" and bool(resolved_url)) or \
               (source == "Upload from my computer"
                and st.session_state.get(f"{ns}_upload") is not None)

    if st.button("📥 Load", key=f"{ns}_load", disabled=not can_load):
        final_url = resolved_url
        if source == "Upload from my computer":
            up = st.session_state.get(f"{ns}_upload")
            with st.spinner("Uploading image…"):
                try:
                    final_url = r2_client.upload_image(
                        up.getvalue(),
                        filename=up.name or f"{product_type.lower()}_image.jpg")
                except Exception as e:
                    st.error(f"❌ Couldn't upload the image: {type(e).__name__}: {e}")
                    return
        # A successful PUT only proves the write credentials work — not that the bucket is
        # public or that R2_PUBLIC_BASE_URL points somewhere fetchable. Checked for BOTH
        # sources before anything is planned, so a broken image never reaches a live record.
        with st.spinner("Checking the image is actually publicly reachable…"):
            ok, reason = r2_client.verify_public_url(final_url)
        if not ok:
            st.error(f"❌ This image URL isn't usable: {reason}")
            return

        bar = st.progress(0.0, text="Scanning…")

        def _prog(done, total, lbl):
            bar.progress(min(done / max(total, 1), 1.0), text=f"Scanning {lbl} ({done}/{total})")

        planned = pib.plan(client, product_type, [chosen], final_url, progress=_prog)
        bar.empty()
        if planned.get("error") and not planned.get("items"):
            st.error(f"❌ {planned['error']}")
            return
        if planned.get("error"):
            st.warning(f"⚠️ {planned['error']}")
        st.session_state[f"{ns}_rows"] = planned
        st.session_state[f"{ns}_selected"] = {
            i["id"]: True for i in planned["items"] if i["status"] == "will_change"}
        st.rerun()

    planned = st.session_state.get(f"{ns}_rows")
    if planned is None:
        return
    items = planned.get("items", [])
    will_change = [i for i in items if i["status"] == "will_change"]
    unchanged = [i for i in items if i["status"] == "unchanged"]
    if not items:
        st.info(f"No {label}s found for this supplier.")
        return

    # ── 4. Review ───────────────────────────────────────────────────────
    st.subheader(f"4 — Review and choose which to update "
                 f"({len(will_change)} would change, {len(unchanged)} already match)")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Select all", key=f"{ns}_all"):
            for i in will_change:
                st.session_state[f"{ns}_pick_{i['id']}"] = True
            st.rerun()
    with c2:
        if st.button("Select none", key=f"{ns}_none"):
            for i in will_change:
                st.session_state[f"{ns}_pick_{i['id']}"] = False
            st.rerun()

    selected_ids = set()
    for item in will_change:
        if st.checkbox(f"**{item['name']}**  ·  id `{item['id']}`",
                       value=True, key=f"{ns}_pick_{item['id']}"):
            selected_ids.add(item["id"])
        with st.expander("Details", expanded=False):
            d1, d2 = st.columns(2)
            with d1:
                st.caption("**Current**")
                if item["current_images"]:
                    st.image(item["current_images"][0], width=160)
                else:
                    st.caption("*(no image set)*")
            with d2:
                st.caption("**New**")
                st.image(item["new_images"][0], width=160)

    if unchanged:
        with st.expander(f"{len(unchanged)} already match this image — left alone",
                         expanded=False):
            for item in unchanged:
                st.caption(item["name"])

    st.caption(f"{len(selected_ids)} of {len(will_change)} selected.")
    if not selected_ids:
        return

    # ── 5. Apply ────────────────────────────────────────────────────────
    st.subheader("5 — Apply")
    st.warning(f"⚠️ This will PUT (update) {len(selected_ids)} {label}(s) — the "
               f"`{spec['image_field']}` field only. Everything else on each record is "
               f"left exactly as it is.")
    if st.button(f"🚀 Update {len(selected_ids)} {label}(s)", key=f"{ns}_confirm",
                 type="primary"):
        bar = st.progress(0.0, text="Updating…")

        def _prog(done, total, lbl):
            bar.progress(min(done / max(total, 1), 1.0), text=f"Updating {lbl} ({done}/{total})")

        with st.spinner("Updating…"):
            st.session_state[f"{ns}_results"] = pib.apply(
                client, planned, selected_ids=selected_ids, progress=_prog)
        st.rerun()
