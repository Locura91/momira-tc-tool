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


_ALL_WEEKDAYS = {"MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"}


def _is_flexible(t: dict) -> tuple[bool, str]:
    """Returns (is_flexible, matched_field_name). Checks transport-level and segment-level."""
    for key in _FLEXIBLE_FIELD_CANDIDATES:
        if t.get(key) is True:
            return True, f"transport.{key}"
    for seg in (t.get("segments") or []):
        for key in _FLEXIBLE_FIELD_CANDIDATES:
            if seg.get(key) is True:
                return True, f"segment.{key}"
    return False, ""


def _early_departure(t: dict, hour_from: int = 4, hour_to: int = 9) -> str | None:
    """Returns the first departure time (HH:MM) that falls in [hour_from, hour_to), or None."""
    for seg in (t.get("segments") or []):
        dep = seg.get("departureTime") or ""
        try:
            h = int(dep.split(":")[0])
            if hour_from <= h < hour_to:
                return dep
        except (ValueError, IndexError):
            continue
    return None


def _all_days_operational(t: dict) -> bool:
    """True if the transport operates on all 7 days (a signal for 'runs any day = flexible)."""
    op_days = set(t.get("operationalDays") or [])
    return bool(op_days) and op_days >= _ALL_WEEKDAYS


def _bool_fields(d: dict, prefix: str = "") -> list[tuple[str, bool]]:
    """Returns all boolean fields in a dict with their values."""
    out = []
    for k, v in d.items():
        if isinstance(v, bool):
            out.append((f"{prefix}{k}", v))
    return out


def render_flexible_schedule_audit(client):
    st.subheader("Transport Audit — Active Transports & Flexible Schedule")
    st.caption(
        "Lists **all active transports** across all suppliers and flags those with "
        "\"Flexible schedule\" ticked. Flexible schedule must be unticked — it creates "
        "too many route variations for our operations."
    )
    st.caption(f"Module build: `{MODULE_BUILD}`")

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
                    is_flex, flex_field = _is_flexible(t)
                    early_dep = _early_departure(t)
                    all_days = _all_days_operational(t)

                    row = {
                        "transport_id": t.get("id", "?"),
                        "name": t.get("name") or "(unnamed)",
                        "supplier": sname,
                        "supplier_id": sid,
                        "type": t.get("transportType", "?"),
                        "active": is_active,
                        "flexible": is_flex,
                        "flex_field": flex_field,
                        "early_departure": early_dep,
                        "all_days": all_days,
                        "option_codes": t.get("optionCodes") or [],
                        "start_date": t.get("startDate") or "",
                        "end_date": t.get("endDate") or "",
                        "operational_days": t.get("operationalDays") or [],
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

        hdr = st.columns([2, 3, 2, 1, 2])
        hdr[0].markdown("**Transport ID**")
        hdr[1].markdown("**Name**")
        hdr[2].markdown("**Supplier**")
        hdr[3].markdown("**Type**")
        hdr[4].markdown("**Field that matched**")

        for r in flexible_transports:
            cols = st.columns([2, 3, 2, 1, 2])
            cols[0].code(r["transport_id"])
            cols[1].write(r["name"])
            cols[2].write(r["supplier"])
            cols[3].write(r["type"])
            cols[4].code(r["flex_field"] or "?")
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

    # ── Additional signals ────────────────────────────────────────────────────
    _early = [r for r in all_transports if r["early_departure"]]
    _all_days = [r for r in all_transports if r["all_days"]]

    if _early:
        with st.expander(f"🌅 {len(_early)} transport(s) with early departure (04:00–09:00)", expanded=False):
            st.caption("These depart between 04:00 and 09:00. If 'Flexible schedule' in TC typically appears on early-morning transports, review this list.")
            for r in _early:
                st.markdown(f"- `{r['transport_id']}` **{r['name']}** ({r['supplier']}) — departs **{r['early_departure']}**")

    if _all_days:
        with st.expander(f"📅 {len(_all_days)} transport(s) operating all 7 days (possible flexible signal)", expanded=False):
            st.caption("These transports have all 7 operational days enabled — a potential proxy for 'flexible schedule'.")
            for r in _all_days:
                st.markdown(f"- `{r['transport_id']}` **{r['name']}** ({r['supplier']})")

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
            flags = ""
            if r["flexible"]:
                flags += " 🔴flex"
            if r["early_departure"]:
                flags += f" 🌅{r['early_departure']}"
            if r["all_days"]:
                flags += " 📅7d"
            cols[0].code(r["transport_id"])
            cols[1].write(r["name"] + (f"  `{flags.strip()}`" if flags else ""))
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
