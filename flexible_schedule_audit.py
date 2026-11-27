"""
Flexible Schedule Audit — scans ALL contracted transports across all suppliers.

Lists every ACTIVE transport and flags those with "Flexible schedule" ticked.
Flexible schedule is not usable for our operations (too many route variations),
so this audit identifies which transports need it unticked in TC.

FIELD NAME NOTE: TC's API reference (ContractTransportVO) does not list a
`flexibleSchedule` field in its published schema. This tool checks every
boolean field on the raw response and surfaces them so the correct field name
can be confirmed from a real live response. Once confirmed, the _is_flexible()
function below should be updated with the definitive field name.

Usage: wired into app.py via FLEXIBLE_SCHEDULE_AUDIT_CHOICE menu entry.
"""
import streamlit as st

MODULE_BUILD = "2026-11-27-transport-audit"

# Candidate field names for "Flexible schedule" — TC's published schema does not
# document this field, so we probe all known variants. Update once confirmed from
# a real live GET /transport response.
_FLEXIBLE_FIELD_CANDIDATES = (
    "flexibleSchedule",
    "flexible_schedule",
    "flexSchedule",
    "flexibleItinerary",
    "flexible",
    "isFlexible",
    "flexDeparture",
    "flexibleDeparture",
)


def _extract_transport_list(result):
    if isinstance(result, dict) and "error" in result:
        return None, result
    if isinstance(result, dict):
        for key in ("transport", "transports", "items", "data", "results"):
            if isinstance(result.get(key), list):
                return result[key], None
        # Maybe the whole dict is the transport
        if result.get("id"):
            return [result], None
        return [], None
    if isinstance(result, list):
        return result, None
    return [], None


def _parse_time_minutes(t_str: str | None) -> int | None:
    """Parse HH:MM or HH:MM:SS into total minutes. Returns None if unparseable."""
    if not t_str:
        return None
    try:
        parts = str(t_str).strip().split(":")
        h, m = int(parts[0]), int(parts[1])
        return h * 60 + m
    except (ValueError, IndexError, AttributeError):
        return None


def _segment_time_mismatch(seg: dict) -> tuple[bool, str]:
    """
    Returns (mismatch, description).

    When 'Flexible schedule' is ticked in TC, the segment stores:
      departureTime = earliest possible departure
      arrivalTime   = latest possible departure  (NOT the real arrival)
      durationTime  = actual journey duration

    So departureTime + durationTime ≠ arrivalTime — the window is wider
    than the journey. We flag this mismatch as the flexible-schedule signal.

    Tolerance: 5 minutes to account for rounding.
    """
    dep = _parse_time_minutes(seg.get("departureTime"))
    arr = _parse_time_minutes(seg.get("arrivalTime"))
    dur = _parse_time_minutes(seg.get("durationTime"))

    if dep is None or arr is None or dur is None:
        return False, ""  # can't evaluate without all three

    expected_arr = dep + dur
    # Allow wrap past midnight
    actual_window = (arr - dep) % (24 * 60)
    expected_window = dur % (24 * 60)

    if abs(actual_window - expected_window) > 5:
        dep_str = seg.get("departureTime", "?")
        arr_str = seg.get("arrivalTime", "?")
        dur_str = seg.get("durationTime", "?")
        return True, (
            f"dep {dep_str} + dur {dur_str} → expected arr ~{expected_arr // 60:02d}:{expected_arr % 60:02d}, "
            f"got arr {arr_str} (window {actual_window} min vs journey {dur} min)"
        )
    return False, ""


def _is_flexible(t: dict) -> tuple[bool, str]:
    """
    Returns (is_flexible, reason_string).

    Primary signal: any segment where departureTime + durationTime ≠ arrivalTime
    (the flexible-schedule pattern — TC stores earliest/latest departure as the
    dep/arr fields when flexible is on, so the window is wider than the journey).

    Fallback: explicit boolean field candidates (undocumented TC fields).
    """
    # Primary: time mismatch on any segment
    for i, seg in enumerate(t.get("segments") or []):
        mismatch, desc = _segment_time_mismatch(seg)
        if mismatch:
            return True, f"segment {i + 1} time mismatch — {desc}"

    # Fallback: explicit field names (TC schema doesn't document these but check anyway)
    for key in _FLEXIBLE_FIELD_CANDIDATES:
        if t.get(key) is True:
            return True, f"transport.{key} = true"
    for i, seg in enumerate(t.get("segments") or []):
        for key in _FLEXIBLE_FIELD_CANDIDATES:
            if seg.get(key) is True:
                return True, f"segment {i + 1}.{key} = true"

    return False, ""


def _bool_fields(d: dict, prefix: str = "") -> list[tuple[str, bool]]:
    """Returns all boolean fields in a dict with their values."""
    out = []
    for k, v in d.items():
        if isinstance(v, bool):
            out.append((f"{prefix}{k}", v))
    return out


def _render_single_transport_diagnostic(client):
    """Fetch one specific transport by ID across all suppliers and show its raw response."""
    st.markdown("### 🔬 Single Transport Raw Inspection")
    st.caption(
        "Fetch a specific transport by ID to see its exact raw API response — "
        "useful for confirming which field TC uses for 'Flexible schedule'."
    )
    col1, col2 = st.columns([3, 1])
    with col1:
        t_id = st.text_input(
            "Transport ID", value="TRANSPORT-412579",
            key="fsa_diag_id", placeholder="e.g. TRANSPORT-412579"
        )
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        run_diag = st.button("Fetch", key="fsa_diag_run", type="primary")

    if not run_diag or not t_id.strip():
        return

    t_id = t_id.strip()
    with st.spinner(f"Searching for {t_id} across all suppliers…"):
        suppliers = client.get_all_suppliers()
        found = None
        found_supplier = None
        for sup in (suppliers or []):
            sid = str(sup.get("id", ""))
            try:
                raw = client.get_transport(sid, t_id)
                if isinstance(raw, dict) and raw.get("id") == t_id:
                    found = raw
                    found_supplier = sup
                    break
            except Exception:
                continue

    if not found:
        st.error(f"Could not find `{t_id}` in any supplier. Check the ID and try again.")
        return

    sname = found_supplier.get("commercialName") or found_supplier.get("legalName") or str(found_supplier.get("id"))
    st.success(f"Found `{t_id}` under supplier **{sname}** (id: {found_supplier.get('id')})")

    # Show all fields — look for anything that could be "flexible schedule"
    st.markdown("**All top-level fields:**")
    top_fields = {k: v for k, v in found.items() if k != "segments"}
    st.json(top_fields)

    segments = found.get("segments") or []
    if segments:
        st.markdown(f"**Segments ({len(segments)}):**")
        for i, seg in enumerate(segments):
            st.markdown(f"*Segment {i + 1}:*")
            st.json(seg)

            dep_m = _parse_time_minutes(seg.get("departureTime"))
            arr_m = _parse_time_minutes(seg.get("arrivalTime"))
            dur_m = _parse_time_minutes(seg.get("durationTime"))

            if dep_m is not None and arr_m is not None and dur_m is not None:
                expected = dep_m + dur_m
                window = (arr_m - dep_m) % (24 * 60)
                if abs(window - dur_m) > 5:
                    st.error(
                        f"⚠️ **Time mismatch — FLEXIBLE SCHEDULE detected** on segment {i + 1}: "
                        f"`departureTime` {seg.get('departureTime')} + `durationTime` {seg.get('durationTime')} "
                        f"= expected arrival ~{expected // 60:02d}:{expected % 60:02d}, "
                        f"but `arrivalTime` = {seg.get('arrivalTime')} "
                        f"(window is {window} min, journey is {dur_m} min)"
                    )
                else:
                    st.success(
                        f"✅ Segment {i + 1} times are consistent: "
                        f"{seg.get('departureTime')} + {seg.get('durationTime')} ≈ {seg.get('arrivalTime')}"
                    )
            elif dep_m is not None and arr_m is not None and dur_m is None:
                st.warning(f"Segment {i + 1}: no `durationTime` — cannot evaluate mismatch.")

    # Specifically flag if any field value looks like "flexible"
    import json as _json
    raw_str = _json.dumps(found, default=str).lower()
    flex_hits = [k for k in found if "flex" in k.lower()]
    for seg in segments:
        flex_hits += [f"segment.{k}" for k in seg if "flex" in k.lower()]
    if flex_hits:
        st.warning(f"Fields containing 'flex': `{flex_hits}`")
    else:
        st.info("No fields containing 'flex' found in the raw response. "
                "Look at the segment time fields above — a range (earliest + latest) "
                "vs a single time is likely the flexible-schedule signal.")


def render_flexible_schedule_audit(client):
    st.subheader("Transport Audit — Active Transports & Flexible Schedule")
    st.caption(
        "Lists **all active transports** across all suppliers and flags those with "
        "\"Flexible schedule\" ticked. Flexible schedule must be unticked — it creates "
        "too many route variations for our operations."
    )
    st.caption(f"Module build: `{MODULE_BUILD}`")

    _render_single_transport_diagnostic(client)
    st.markdown("---")

    if st.button("🔍 Scan all transports", type="primary", key="fsa_scan"):
        with st.spinner("Loading suppliers…"):
            suppliers = client.get_all_suppliers()

        if not suppliers:
            st.error("Could not load suppliers. Please check your API connection.")
            return

        bar = st.progress(0.0, text="Scanning suppliers…")
        all_transports = []
        flexible_transports = []
        errors = []
        total = len(suppliers)
        # Collect boolean fields from the very first transport to surface the field names
        sample_bool_fields: list[tuple[str, bool]] | None = None

        for i, supplier in enumerate(suppliers):
            sid = str(supplier.get("id", ""))
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

                for t in transports:
                    # Capture bool fields from the first transport we see
                    if sample_bool_fields is None:
                        sample_bool_fields = _bool_fields(t, "transport.")
                        for seg in (t.get("segments") or [])[:1]:
                            sample_bool_fields += _bool_fields(seg, "segment.")

                    is_active = t.get("active", True)
                    is_flex, flex_reason = _is_flexible(t)

                    row = {
                        "transport_id": t.get("id", "?"),
                        "name": t.get("name") or "(unnamed)",
                        "supplier": sname,
                        "supplier_id": sid,
                        "type": t.get("transportType", "?"),
                        "active": is_active,
                        "flexible": is_flex,
                        "flex_reason": flex_reason,
                        "option_codes": t.get("optionCodes") or [],
                        "start_date": t.get("startDate") or "",
                        "end_date": t.get("endDate") or "",
                    }
                    if is_active:
                        all_transports.append(row)
                    if is_flex:
                        flexible_transports.append(row)

            except Exception as e:
                errors.append(f"{sname} ({sid}): {e}")

        bar.progress(1.0, text="Done")
        bar.empty()

        st.session_state.fsa_all = all_transports
        st.session_state.fsa_flexible = flexible_transports
        st.session_state.fsa_errors = errors
        st.session_state.fsa_total_suppliers = total
        st.session_state.fsa_sample_bool_fields = sample_bool_fields
        st.rerun()

    if st.session_state.get("fsa_all") is None:
        return

    all_transports = st.session_state.fsa_all
    flexible_transports = st.session_state.fsa_flexible
    errors = st.session_state.get("fsa_errors", [])
    total_suppliers = st.session_state.get("fsa_total_suppliers", 0)
    sample_bool_fields = st.session_state.get("fsa_sample_bool_fields")

    # ── Field name diagnostics ────────────────────────────────────────────────
    if sample_bool_fields is not None:
        with st.expander("🔬 Boolean fields found on first transport (field name diagnostics)", expanded=not flexible_transports):
            st.caption(
                "TC's API reference does not document a `flexibleSchedule` field. "
                "These are the actual boolean fields returned by TC on the first transport scanned. "
                "The correct 'Flexible schedule' field name can be confirmed here."
            )
            if sample_bool_fields:
                for fname, fval in sorted(sample_bool_fields):
                    st.markdown(f"- `{fname}` = **{fval}**")
            else:
                st.info("No boolean fields found on the first transport.")

    st.markdown("---")

    # ── Flexible schedule results ─────────────────────────────────────────────
    if flexible_transports:
        st.error(
            f"⚠️ **{len(flexible_transports)}** active transport(s) have Flexible Schedule ticked "
            f"— these need to be fixed in TC."
        )
        st.markdown("### Transports with Flexible Schedule — fix these in TC")

        hdr = st.columns([2, 3, 2, 1, 4])
        hdr[0].markdown("**Transport ID**")
        hdr[1].markdown("**Name**")
        hdr[2].markdown("**Supplier**")
        hdr[3].markdown("**Type**")
        hdr[4].markdown("**Why flagged (time mismatch)**")

        for r in flexible_transports:
            cols = st.columns([2, 3, 2, 1, 4])
            cols[0].code(r["transport_id"])
            cols[1].write(r["name"])
            cols[2].write(r["supplier"])
            cols[3].write(r["type"])
            cols[4].caption(r["flex_reason"] or "?")
    else:
        st.success(
            f"✅ No active transports with Flexible Schedule found "
            f"(scanned {total_suppliers} supplier(s), {len(all_transports)} active transport(s))."
        )
        st.info(
            "Note: if you expected to find some, check the 🔬 diagnostics expander above — "
            "the field name TC uses for 'Flexible schedule' may not match the candidates "
            "this tool checks. Share the diagnostics output to confirm."
        )

    st.markdown("---")

    st.markdown("---")

    # ── Full list of active transports ────────────────────────────────────────
    st.markdown(f"### All active transports ({len(all_transports)} total, {total_suppliers} suppliers scanned)")
    if not all_transports:
        st.info("No active transports found.")
    else:
        hdr = st.columns([2, 3, 2, 1, 1, 2, 2])
        hdr[0].markdown("**Transport ID**")
        hdr[1].markdown("**Name**")
        hdr[2].markdown("**Supplier**")
        hdr[3].markdown("**Type**")
        hdr[4].markdown("**Options**")
        hdr[5].markdown("**Start date**")
        hdr[6].markdown("**End date**")

        for r in all_transports:
            cols = st.columns([2, 3, 2, 1, 1, 2, 2])
            cols[0].code(r["transport_id"])
            label = r["name"]
            if r["flexible"]:
                label += "  🔴 flex"
            cols[1].write(label)
            cols[2].write(r["supplier"])
            cols[3].write(r["type"])
            cols[4].write(str(len(r["option_codes"])))
            cols[5].write(r["start_date"] or "—")
            cols[6].write(r["end_date"] or "—")

    if errors:
        with st.expander(f"⚠️ {len(errors)} supplier(s) could not be scanned"):
            for e in errors:
                st.warning(e)

    if st.button("🔍 Scan again", key="fsa_rescan"):
        for k in list(st.session_state.keys()):
            if k.startswith("fsa_"):
                del st.session_state[k]
        st.rerun()

    st.markdown("---")
    _render_single_transport_diagnostic(client)
