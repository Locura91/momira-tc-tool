"""
flows/transport_segment_model_clear.py

One-shot bulk tool: scan all transports for a supplier (or all Momira_
suppliers) and clear the "Model" field on every segment where it is set.

The TC segment "model" field (vehicle/aircraft model name) is frequently
populated with values like "Private" or "Private with Guide" that are
wrong and must be blank. This tool finds and removes them in bulk.
"""

import streamlit as st

MODULE_BUILD = "2026-11-27-segment-model-clear-v1"

_KEY = "tsmc_"


def render_transport_segment_model_clear(client):
    from app_helpers import momira_active_suppliers

    st.subheader("Clear Transport Segment Model field")
    st.caption(
        "Scans all transports and removes anything stored in the **Model** field "
        "on each segment (e.g. \"Private\", \"Private with Guide\"). "
        "The field should always be blank."
    )
    st.caption(f"Build: `{MODULE_BUILD}`")

    if st.button("🔙 Back", key=f"{_KEY}back"):
        st.session_state.bulk_action = None
        st.rerun()

    # ── Supplier picker ────────────────────────────────────────────────────────
    if not st.session_state.get(f"{_KEY}suppliers"):
        with st.spinner("Loading suppliers…"):
            try:
                st.session_state[f"{_KEY}suppliers"] = momira_active_suppliers(
                    client.get_all_suppliers()
                )
            except Exception as e:
                st.error(f"Could not load suppliers: {e}")
                return

    suppliers = st.session_state.get(f"{_KEY}suppliers", [])
    if not suppliers:
        st.error("No Momira_ suppliers found.")
        return

    sup_opts = {"— All Momira_ suppliers —": None}
    for s in suppliers:
        label = f"{s.get('commercialName') or s.get('legalName')} — ID {s.get('id')}"
        sup_opts[label] = str(s.get("id"))

    chosen_label = st.selectbox("Supplier", list(sup_opts.keys()), key=f"{_KEY}sup_select")
    chosen_id = sup_opts[chosen_label]

    target_suppliers = (
        [{"id": chosen_id, "name": chosen_label}]
        if chosen_id
        else [{"id": str(s.get("id")),
               "name": s.get("commercialName") or s.get("legalName") or str(s.get("id"))}
              for s in suppliers]
    )

    # ── Scan ──────────────────────────────────────────────────────────────────
    if not st.session_state.get(f"{_KEY}scan_done"):
        if not st.button("🔍 Scan for non-empty Model fields", type="primary",
                         key=f"{_KEY}scan_btn"):
            return

        findings = []
        progress = st.progress(0.0, text="Scanning…")
        total_sups = len(target_suppliers)

        for si, sup in enumerate(target_suppliers):
            sid = sup["id"]
            sname = sup["name"]
            progress.progress((si + 0.5) / total_sups, text=f"Scanning {sname}…")

            try:
                raw = client.get_transports(sid)
            except Exception as e:
                st.warning(f"Could not load transports for {sname}: {e}")
                continue

            transports = raw if isinstance(raw, list) else (raw or {}).get("items") or []

            for t in transports:
                t_id = t.get("id", "?")
                try:
                    full = client.get_transport(sid, t_id)
                    if not isinstance(full, dict) or not full.get("id"):
                        full = t
                except Exception:
                    full = t

                segments = full.get("segments") or []
                bad_segs = [
                    {"idx": i, "model_val": seg.get("model")}
                    for i, seg in enumerate(segments)
                    if seg.get("model")
                ]
                if bad_segs:
                    findings.append({
                        "supplier_id": sid,
                        "supplier_name": sname,
                        "transport_id": t_id,
                        "transport_name": full.get("name") or t.get("name") or t_id,
                        "full": full,
                        "bad_segs": bad_segs,
                    })

            progress.progress((si + 1) / total_sups, text=f"Done {sname}")

        progress.empty()
        st.session_state[f"{_KEY}findings"] = findings
        st.session_state[f"{_KEY}scan_done"] = True
        st.rerun()

    # ── Show findings ──────────────────────────────────────────────────────────
    findings = st.session_state.get(f"{_KEY}findings", [])

    if not findings:
        st.success("✅ No transports have a non-empty segment Model field. Nothing to clear.")
        if st.button("🔁 Scan again", key=f"{_KEY}rescan"):
            for k in [f"{_KEY}scan_done", f"{_KEY}findings", f"{_KEY}applied"]:
                st.session_state.pop(k, None)
            st.rerun()
        return

    st.warning(
        f"Found **{len(findings)}** transport(s) with a non-empty Model field on at least one segment."
    )

    for f in findings:
        lines = [f"Segment {b['idx'] + 1}: `{b['model_val']}`" for b in f["bad_segs"]]
        st.markdown(
            f"- **{f['transport_name']}** (ID `{f['transport_id']}`, supplier: {f['supplier_name']})  \n"
            + "  \n".join(f"  → {l}" for l in lines)
        )

    st.markdown("---")

    if st.session_state.get(f"{_KEY}applied"):
        st.success("✅ All Model fields cleared.")
        if st.button("🔁 Scan again", key=f"{_KEY}rescan2"):
            for k in [f"{_KEY}scan_done", f"{_KEY}findings", f"{_KEY}applied"]:
                st.session_state.pop(k, None)
            st.rerun()
        return

    if not st.button(
        f"🗑️ Clear Model field on all {len(findings)} transport(s)",
        type="primary", key=f"{_KEY}apply_btn"
    ):
        return

    # ── Apply ──────────────────────────────────────────────────────────────────
    errors = []
    progress2 = st.progress(0.0, text="Clearing…")

    for fi, f in enumerate(findings):
        progress2.progress(fi / len(findings), text=f"Updating {f['transport_name']}…")
        full = f["full"]
        segments = full.get("segments") or []
        for seg in segments:
            if seg.get("model"):
                seg["model"] = None

        try:
            resp = client.update_transport(f["supplier_id"], full)
            if isinstance(resp, dict) and "error" in resp:
                errors.append(f"{f['transport_name']}: {resp.get('message') or resp}")
        except Exception as e:
            errors.append(f"{f['transport_name']}: {e}")

    progress2.empty()

    if errors:
        st.error("Some transports could not be updated:")
        for err in errors:
            st.error(err)
    else:
        st.session_state[f"{_KEY}applied"] = True
        st.rerun()
