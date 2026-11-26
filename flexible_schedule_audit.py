"""
Flexible Schedule Audit — scans ALL contracted transports across all suppliers
and lists every transport that has the "Flexible schedule" checkbox ticked.

Flexible schedule is not usable for our operations (too many variations), so
this audit helps identify transports that need to be unticked manually in TC.

Usage: run via Streamlit (wired into app.py) or standalone:
    streamlit run flexible_schedule_audit.py
"""
import streamlit as st
from api_client import TravelCompositorAPI


def _extract_transport_list(result):
    if isinstance(result, dict) and "error" in result:
        return None, result
    transports = result.get("transport", []) if isinstance(result, dict) else (result or [])
    return transports, None


def render_flexible_schedule_audit(client):
    st.subheader("Flexible Schedule Audit")
    st.caption(
        "Scans all contracted transports and lists every one that has "
        "\"Flexible schedule\" ticked. These need to be unticked manually in "
        "Travel Compositor — flexible schedules create too many variations."
    )

    if st.button("🔍 Scan all transports", type="primary", key="fsa_scan"):
        with st.spinner("Loading suppliers…"):
            suppliers = client.get_all_suppliers()

        if not suppliers:
            st.error("Could not load suppliers. Please check your API connection.")
            return

        bar = st.progress(0.0, text="Scanning suppliers…")
        flexible_transports = []
        errors = []
        total = len(suppliers)

        for i, supplier in enumerate(suppliers):
            sid = supplier.get("id", "")
            sname = supplier.get("commercialName") or supplier.get("legalName") or sid
            bar.progress(min((i + 1) / total, 1.0), text=f"Scanning {sname} ({i + 1}/{total})…")

            try:
                result = client.get_transports(sid)
                transports, err = _extract_transport_list(result)
                if err:
                    errors.append(f"{sname} ({sid}): {err.get('message', err)}")
                    continue
                if not transports:
                    continue

                # On first transport, log all keys so we can confirm field names
                if transports and not flexible_transports and not st.session_state.get("fsa_logged"):
                    sample = transports[0]
                    print(f"[FSA] Sample transport keys: {list(sample.keys())}")
                    for seg in (sample.get("segments") or []):
                        print(f"[FSA] Sample segment keys: {list(seg.keys())}")
                        break
                    st.session_state.fsa_logged = True

                for t in transports:
                    is_flexible = False
                    # Check transport-level fields
                    for key in ("flexibleSchedule", "flexible_schedule", "flexSchedule",
                                "flexibleItinerary", "flexible"):
                        if t.get(key):
                            is_flexible = True
                            break

                    # Also check inside segments
                    if not is_flexible:
                        for seg in (t.get("segments") or []):
                            for key in ("flexibleSchedule", "flexible_schedule", "flexSchedule",
                                        "flexibleItinerary", "flexible"):
                                if seg.get(key):
                                    is_flexible = True
                                    break
                            if is_flexible:
                                break

                    if is_flexible:
                        flexible_transports.append({
                            "transport_id": t.get("id", "?"),
                            "name": t.get("name", "(unnamed)"),
                            "supplier": sname,
                            "supplier_id": sid,
                            "type": t.get("transportType", "?"),
                            "active": t.get("active", True),
                        })
            except Exception as e:
                errors.append(f"{sname} ({sid}): {e}")

        bar.progress(1.0, text="Done")
        bar.empty()

        st.session_state.fsa_results = flexible_transports
        st.session_state.fsa_errors = errors
        st.session_state.fsa_total_suppliers = total
        st.rerun()

    results = st.session_state.get("fsa_results")
    if results is None:
        return

    errors = st.session_state.get("fsa_errors", [])
    total_suppliers = st.session_state.get("fsa_total_suppliers", 0)

    if not results:
        st.success(f"No transports with Flexible Schedule found across {total_suppliers} suppliers.")
        if errors:
            with st.expander(f"⚠️ {len(errors)} supplier(s) could not be scanned"):
                for e in errors:
                    st.warning(e)
        if st.button("🔍 Scan again", key="fsa_rescan_empty"):
            for k in list(st.session_state.keys()):
                if k.startswith("fsa_"):
                    del st.session_state[k]
            st.rerun()
        return

    st.warning(
        f"Found **{len(results)}** transport(s) with Flexible Schedule ticked "
        f"(scanned {total_suppliers} suppliers)."
    )
    st.caption(
        "These transports need the \"Flexible schedule\" checkbox unticked in "
        "Travel Compositor. Click on a transport ID to open it directly."
    )

    # Table header
    cols = st.columns([2, 3, 2, 1, 1])
    cols[0].markdown("**Transport ID**")
    cols[1].markdown("**Name**")
    cols[2].markdown("**Supplier**")
    cols[3].markdown("**Type**")
    cols[4].markdown("**Active**")

    for r in results:
        cols = st.columns([2, 3, 2, 1, 1])
        cols[0].code(r["transport_id"])
        cols[1].write(r["name"])
        cols[2].write(r["supplier"])
        cols[3].write(r["type"])
        cols[4].write("✅" if r["active"] else "❌")

    if errors:
        with st.expander(f"⚠️ {len(errors)} supplier(s) could not be scanned"):
            for e in errors:
                st.warning(e)

    if st.button("🔍 Scan again", key="fsa_rescan"):
        for k in list(st.session_state.keys()):
            if k.startswith("fsa_"):
                del st.session_state[k]
        st.rerun()


if __name__ == "__main__":
    st.set_page_config(page_title="Flexible Schedule Audit", layout="wide")
    st.title("🔍 Flexible Schedule Audit")
    client = TravelCompositorAPI()
    client.authenticate()
    render_flexible_schedule_audit(client)
