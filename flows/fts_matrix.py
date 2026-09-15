"""
FTS transfer-matrix bulk-import flow, split out of app.py (Phase 1 restructure, zero behaviour
change).

render_fts_matrix_import_flow moved here verbatim. It needs no names defined at app.py's own top
level at all - everything it references is either a stdlib/external import or comes from
fts_transfer_matrix.py, so (unlike every other flows module so far) there is no `from app import
(...)` line and no circular-import placement concern.
"""
import os
import tempfile
import streamlit as st

from schemas import TransportHumanPreConfig
from ai_extractor import friendly_error_message
from fts_transfer_matrix import (
    build_fts_matrix_candidates, match_fts_candidates_to_existing, publish_fts_candidate,
)


def render_fts_matrix_import_flow(client, supplier_id, currency, release_days):
    """Deterministic (non-AI) bulk import for FTS's own "TRANSFER MATRIX" CSV export - Sedan +
    Hiace price grids, 271 routes each in the real data. Added 2026-09-11 after the normal AI
    pipeline overflowed on this exact document ("AI's answer was too long and got cut off") and
    the user flagged it as a real blocker given ~200 more supplier sheets still to load. See
    fts_transfer_matrix.py's module docstring for the full parsing/pricing rules (Sedan = base
    price, Hiace = base + (Hiace - Sedan) supplement, per product-owner confirmation) - this
    function is UI only, all the logic lives there and is independently unit-tested.

    SCOPE (confirmed via AskUserQuestion, 2026-09-11): "Only this one supplier (FTS) uses it" -
    this is a separate, dedicated entry point, not a replacement for the AI-based flow below,
    which every other supplier's documents still go through.

    Matching (confirmed via AskUserQuestion, 2026-09-11): "it must auto match, but the human
    must verify it before update" - match_fts_candidates_to_existing auto-suggests create/update
    per route against the supplier's existing transports, but every suggested UPDATE still needs
    an explicit per-row tick below before publish will touch it; CREATE rows need no such gate
    since they can't overwrite a live record.

    3 phases via st.session_state.ftsm_phase: "upload" -> "review" (parses + auto-matches, both
    fast/local except one client.get_transports call) -> "done" (publish results).
    """
    if "ftsm_phase" not in st.session_state:
        st.session_state.ftsm_phase = "upload"

    with st.expander("📊 Bulk import: FTS Transfer Matrix (Sedan + Hiace CSVs)",
                     expanded=st.session_state.ftsm_phase != "upload"):
        st.caption(
            "For FTS's own city-to-city price-grid export ONLY (two CSVs, one per vehicle "
            "class - Sedan and Toyota Hiace, exported from their "
            "'FTS_Momira_Whole_Egypt_B2B_Catalogue.xlsx' workbook). Parses the grid directly - "
            "no AI call, so no token limit and no per-route wait, even at 271 routes. Every "
            "other supplier's documents still go through the normal flow below."
        )

        if st.session_state.ftsm_phase == "upload":
            fc1, fc2 = st.columns(2)
            with fc1:
                sedan_file = st.file_uploader("Sedan matrix CSV", type=["csv"], key="ftsm_sedan_file")
            with fc2:
                hiace_file = st.file_uploader("Hiace matrix CSV", type=["csv"], key="ftsm_hiace_file")

            if st.button("🔎 Parse Matrix", disabled=not (sedan_file and hiace_file), key="ftsm_parse_btn"):
                with st.spinner("Parsing both CSVs..."):
                    tmp_paths = []
                    try:
                        for uploaded in (sedan_file, hiace_file):
                            with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
                                tmp.write(uploaded.getbuffer())
                                tmp_paths.append(tmp.name)
                        parsed = build_fts_matrix_candidates(tmp_paths[0], tmp_paths[1], currency=currency)
                    finally:
                        for p in tmp_paths:
                            try:
                                os.remove(p)
                            except OSError:
                                pass

                if parsed["format_error"]:
                    st.error(f"❌ Couldn't read these as an FTS transfer matrix: {parsed['format_error']}")
                elif not parsed["candidates"]:
                    st.warning("⚠️ No usable routes found in these two files - check they're the right CSVs.")
                else:
                    with st.spinner("Checking for already-existing Transports on this supplier "
                                    "to auto-suggest matches..."):
                        try:
                            existing_result = client.get_transports(supplier_id)
                            existing_transports = (existing_result.get("transport", [])
                                                   if isinstance(existing_result, dict) else (existing_result or []))
                        except Exception as e:
                            st.warning(f"⚠️ Couldn't fetch existing transports ({friendly_error_message(e)}) - "
                                      f"every route will default to CREATE instead of an auto-suggested update.")
                            existing_transports = []
                    matched = match_fts_candidates_to_existing(parsed["candidates"], existing_transports)
                    for m in matched:
                        m["verified"] = False  # only meaningful for action == "update"
                        m["include"] = True
                    st.session_state.ftsm_candidates = matched
                    st.session_state.ftsm_skipped = parsed["skipped"]
                    st.session_state.ftsm_currency = currency
                    st.session_state.ftsm_phase = "review"
                    st.rerun()
            return

        if st.session_state.ftsm_phase == "review":
            candidates = st.session_state.ftsm_candidates
            skipped = st.session_state.ftsm_skipped
            creates = [c for c in candidates if c["action"] == "create"]
            updates = [c for c in candidates if c["action"] == "update"]

            st.subheader(f"{len(candidates)} route(s) parsed")
            sc1, sc2, sc3 = st.columns(3)
            sc1.metric("Create (new)", len(creates))
            sc2.metric("Update (auto-matched)", len(updates))
            sc3.metric("Skipped", len(skipped))

            if skipped:
                with st.expander(f"Skipped ({len(skipped)}) - train-only, one-sided, or genuinely no route"):
                    from collections import Counter
                    reason_counts = Counter(s["reason"] for s in skipped)
                    st.caption(", ".join(f"{v} {k}" for k, v in reason_counts.items()))
                    st.dataframe(
                        [{"Departure": s["departure_name"], "Arrival": s["arrival_name"],
                          "Reason": s["reason"]} for s in skipped],
                        use_container_width=True, hide_index=True,
                    )

            if updates:
                st.markdown(f"#### {len(updates)} route(s) auto-matched to an existing Transport")
                st.warning("⚠️ Nothing here publishes as an UPDATE until you tick **Verified** for "
                          "that row - this overwrites a live record's price, so an auto-match "
                          "alone is only a suggestion, never applied blind.")
                for i, c in enumerate(updates):
                    ucol1, ucol2 = st.columns([4, 1])
                    with ucol1:
                        st.caption(
                            f"**{c['departure_name']} → {c['arrival_name']}** "
                            f"(Sedan ${c['sedan_price']:.2f} / Hiace ${c['hiace_price']:.2f}) "
                            f"→ matches **{c['matched_transport_name'] or '(unnamed)'}** "
                            f"— `{c['matched_transport_id']}` (score {c['match_score']})"
                        )
                    with ucol2:
                        c["verified"] = st.checkbox("Verified", value=c["verified"], key=f"ftsm_verify_{i}")
                verified_count = sum(1 for c in updates if c["verified"])
                st.caption(f"{verified_count} / {len(updates)} update(s) verified and will be published; "
                          f"the rest are skipped this run (re-parse later once you've checked them).")

            if creates:
                with st.expander(f"Preview: {len(creates)} route(s) to be created as new Transports"):
                    st.dataframe(
                        [{"Departure": c["departure_name"], "Arrival": c["arrival_name"],
                          "Sedan (base) $": c["sedan_price"], "Hiace (supplement) $":
                          round(c["hiace_price"] - c["sedan_price"], 2)} for c in creates],
                        use_container_width=True, hide_index=True,
                    )

            to_publish = creates + [c for c in updates if c["verified"]]
            skipped_updates = len(updates) - sum(1 for c in updates if c["verified"])
            st.caption(f"**{len(to_publish)}** route(s) ready to publish" +
                      (f" ({skipped_updates} unverified update(s) will be skipped this run)."
                       if skipped_updates else "."))

            bcol1, bcol2 = st.columns(2)
            with bcol1:
                if st.button("🚀 Publish", type="primary", disabled=not to_publish, key="ftsm_publish_btn"):
                    pre_config = TransportHumanPreConfig(supplier_id=supplier_id, currency=currency,
                                                          days_available_before_release=release_days)
                    progress_bar = st.progress(0.0)
                    status_line = st.empty()
                    results = []
                    for i, c in enumerate(to_publish):
                        status_line.caption(f"Publishing {i + 1} / {len(to_publish)}: "
                                            f"{c['departure_name']} → {c['arrival_name']}...")
                        outcome = publish_fts_candidate(client, supplier_id, pre_config, c)
                        results.append({"route": f"{c['departure_name']} → {c['arrival_name']}",
                                       "action": c["action"], **outcome})
                        progress_bar.progress((i + 1) / len(to_publish))
                    st.session_state.ftsm_results = results
                    st.session_state.ftsm_phase = "done"
                    st.rerun()
            with bcol2:
                if st.button("🔄 Start over", key="ftsm_restart_from_review"):
                    for key in ("ftsm_phase", "ftsm_candidates", "ftsm_skipped", "ftsm_currency"):
                        st.session_state.pop(key, None)
                    st.rerun()
            return

        if st.session_state.ftsm_phase == "done":
            results = st.session_state.ftsm_results
            ok_count = sum(1 for r in results if r["ok"])
            fail_count = len(results) - ok_count
            if fail_count == 0:
                st.success(f"✅ Published all {ok_count} route(s) successfully.")
            else:
                st.warning(f"⚠️ Published {ok_count} route(s) successfully; {fail_count} failed - "
                          f"see below. Re-running this same import is safe (matched routes update "
                          f"in place rather than duplicating).")
            st.dataframe(
                [{"Route": r["route"], "Action": r["action"],
                  "Result": "✅ OK" if r["ok"] else "❌ Failed",
                  "Transport ID": r.get("transport_id") or "",
                  "Errors": "; ".join(r.get("errors") or [])} for r in results],
                use_container_width=True, hide_index=True,
            )
            if st.button("🔄 Import another matrix", key="ftsm_restart_from_done"):
                for key in ("ftsm_phase", "ftsm_candidates", "ftsm_skipped", "ftsm_currency", "ftsm_results"):
                    st.session_state.pop(key, None)
                st.rerun()
            return
