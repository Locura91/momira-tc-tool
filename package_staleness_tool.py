"""
package_staleness_tool.py — "Packages Needing Update" list view.

Shows ALL holiday packages fetched from Travel Compositor, sorted by departure date ascending
(soonest/most-overdue first), so the human can see at a glance which packages need their
departure date updated. Mirrors the competitor's travelc_ideas_list.php?sortby=original_traveldate
view — same concept, adapted for Momira.

Status colours (confirmed departure-date rules from package_rollover_rules.py):
  🔴 OVERDUE   — departure date is in the past
  🟠 URGENT    — departure is within CLOSED_WITHIN_DAYS (14 days) — "closing soon"
  🟡 DUE SOON  — departure is within 60 days (needs attention)
  🟢 OK        — departure is 60+ days out

Packages with no recognisable departure date are listed separately at the bottom.

This screen is READ-ONLY (no PUT calls — same guardrail as package_rollover_tool.py).
It uses the same TravelCompositorAPI client as the rollover tool (shared @st.cache_resource
below) and reuses package_rollover_rules.find_departure_date() so date-field detection stays
in one place and stays in sync with the rollover tool.
"""
from datetime import date, timedelta
from typing import List, Dict, Any, Optional, Tuple

import streamlit as st

import package_rollover_rules as prr
from travelcompositor_api import TravelCompositorAPI

MODULE_BUILD = "2026-10-19-home-nav-button"

_DUE_SOON_DAYS = 60   # yellow zone: not yet urgent but worth keeping an eye on

# Status constants (text only — emoji added at render time so sorting stays numeric)
_STATUS_OVERDUE = "OVERDUE"
_STATUS_URGENT = "URGENT"
_STATUS_DUE_SOON = "DUE SOON"
_STATUS_OK = "OK"

# Sort weight — lower = higher urgency = appears first
_STATUS_SORT = {
    _STATUS_OVERDUE: 0,
    _STATUS_URGENT: 1,
    _STATUS_DUE_SOON: 2,
    _STATUS_OK: 3,
}

_STATUS_EMOJI = {
    _STATUS_OVERDUE: "🔴",
    _STATUS_URGENT: "🟠",
    _STATUS_DUE_SOON: "🟡",
    _STATUS_OK: "🟢",
}

_STATUS_COLOUR = {
    _STATUS_OVERDUE: "#ffd6d6",
    _STATUS_URGENT: "#ffe5cc",
    _STATUS_DUE_SOON: "#fffacc",
    _STATUS_OK: "#d6f5d6",
}


@st.cache_resource
def _get_api_client() -> TravelCompositorAPI:
    """Shared with package_rollover_tool.py — both use travelcompositor_api.TravelCompositorAPI
    for the Packages GET endpoints. Cached so re-runs don't re-authenticate on every click."""
    return TravelCompositorAPI()


def _classify(dep_date: Optional[date], today: date) -> str:
    if dep_date is None:
        return _STATUS_OVERDUE  # shouldn't happen in TC, treat as urgent
    delta = (dep_date - today).days
    if delta < 0:
        return _STATUS_OVERDUE
    if delta < prr.CLOSED_WITHIN_DAYS:
        return _STATUS_URGENT
    if delta < _DUE_SOON_DAYS:
        return _STATUS_DUE_SOON
    return _STATUS_OK


def _fetch_all_packages(api: TravelCompositorAPI) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Fetches ALL packages via paginated calls. Returns (packages_list, error_message).
    The TC API returns {"pagination": {..., "totalResults": N, "pageSize": M}, "package": [...]}.
    We keep fetching with increasing page offsets until we've got everything."""
    all_packages: List[Dict[str, Any]] = []
    page = 1
    page_size = 50

    while True:
        result = api.get_holiday_packages(
            api.microsite_id,
            lang="EN",
            page=page,
            pageSize=page_size,
        )
        if isinstance(result, dict) and "error" in result:
            return [], str(result["error"])

        packages = result.get("package", []) if isinstance(result, dict) else []
        if not isinstance(packages, list):
            break
        all_packages.extend(packages)

        pagination = result.get("pagination", {}) if isinstance(result, dict) else {}
        total = pagination.get("totalResults") or pagination.get("total") or 0
        if not total or len(all_packages) >= int(total):
            break
        page += 1
        # Safety cap — no infinite loops on unexpected pagination shapes
        if page > 200:
            break

    return all_packages, None


def _build_rows(packages: List[Dict[str, Any]], today: date) -> List[Dict[str, Any]]:
    """Turns raw TC package objects into flat display rows, deduplicating by ID."""
    seen_ids: set = set()
    rows = []
    for pkg in packages:
        if not isinstance(pkg, dict):
            continue

        pkg_id = (
            pkg.get("id")
            or pkg.get("holidayPackageId")
            or pkg.get("packageId")
            or pkg.get("code")
            or "?"
        )
        if str(pkg_id) in seen_ids:
            continue
        seen_ids.add(str(pkg_id))
        # Title — try common field names
        title = (
            pkg.get("title")
            or pkg.get("name")
            or pkg.get("description")
            or ""
        )
        if isinstance(title, dict):
            # Some TC responses wrap text in {"value": "...", "lang": "EN"}
            title = title.get("value") or title.get("text") or str(title)
        title = str(title).strip() or f"Package {pkg_id}"

        _dep_field, dep_date = prr.find_departure_date(pkg)
        status = _classify(dep_date, today)

        rows.append({
            "pkg_id": str(pkg_id),
            "title": title,
            "dep_date": dep_date,
            "dep_date_display": dep_date.strftime("%d/%m/%Y") if dep_date else "—",
            "status": status,
            "_sort_key": (
                _STATUS_SORT[status],
                dep_date or date.max,   # no-date packages sort after all dated ones within their bucket
            ),
        })

    rows.sort(key=lambda r: r["_sort_key"])
    return rows


def render_package_staleness_tool():
    st.title("📋 Packages Needing Update")
    st.caption(
        "All Holiday Packages sorted by departure date — soonest / most overdue first. "
        "Identifies which packages need their departure date updated in Travel Compositor. "
        "Read-only — no changes are made here."
    )

    today = date.today()

    # ---- Controls row -------------------------------------------------------
    col_refresh, col_filter = st.columns([1, 3])
    with col_refresh:
        do_fetch = st.button("🔄 Load / Refresh list", type="primary", use_container_width=True)
    with col_filter:
        status_filter = st.multiselect(
            "Show statuses",
            options=[_STATUS_OVERDUE, _STATUS_URGENT, _STATUS_DUE_SOON, _STATUS_OK],
            default=[_STATUS_OVERDUE, _STATUS_URGENT, _STATUS_DUE_SOON],
            format_func=lambda s: f"{_STATUS_EMOJI[s]} {s}",
            label_visibility="collapsed",
        )

    # ---- Fetch / cache -------------------------------------------------------
    if do_fetch or "pst_rows" not in st.session_state:
        api = _get_api_client()
        with st.spinner("Fetching all packages from Travel Compositor…"):
            packages, err = _fetch_all_packages(api)
        if err:
            st.error(f"Travel Compositor returned an error: {err}")
            st.stop()
        rows = _build_rows(packages, today)
        st.session_state.pst_rows = rows
        st.session_state.pst_fetched_at = today.isoformat()

    rows: List[Dict[str, Any]] = st.session_state.get("pst_rows", [])
    fetched_at = st.session_state.get("pst_fetched_at", "")

    if not rows:
        st.info("No packages found. Click **Load / Refresh list** to fetch from Travel Compositor.")
        return

    # ---- Summary badges -----------------------------------------------------
    counts = {s: sum(1 for r in rows if r["status"] == s)
              for s in [_STATUS_OVERDUE, _STATUS_URGENT, _STATUS_DUE_SOON, _STATUS_OK]}

    badge_cols = st.columns(4)
    for col, (status, label) in zip(badge_cols, [
        (_STATUS_OVERDUE,  "Overdue"),
        (_STATUS_URGENT,   "Urgent (≤14 d)"),
        (_STATUS_DUE_SOON, "Due soon (≤60 d)"),
        (_STATUS_OK,       "OK"),
    ]):
        col.metric(f"{_STATUS_EMOJI[status]} {label}", counts[status])

    if fetched_at:
        st.caption(f"Last fetched: {fetched_at}  ·  {len(rows)} packages total")

    st.divider()

    # ---- Filtered list -------------------------------------------------------
    visible = [r for r in rows if not status_filter or r["status"] in status_filter]
    if not visible:
        st.info("No packages match the selected status filters.")
        return

    # Render as a table-like list with per-row actions
    for idx, row in enumerate(visible):
        status = row["status"]
        bg = _STATUS_COLOUR[status]
        emoji = _STATUS_EMOJI[status]
        dep_str = row["dep_date_display"]

        if row["dep_date"]:
            delta_days = (row["dep_date"] - today).days
            if delta_days < 0:
                age_str = f"{abs(delta_days)} days ago"
            elif delta_days == 0:
                age_str = "TODAY"
            else:
                age_str = f"in {delta_days} days"
        else:
            age_str = "unknown departure"

        with st.container():
            st.markdown(
                f"<div style='background:{bg};border-radius:6px;padding:8px 12px;"
                f"margin-bottom:6px;display:flex;align-items:center;gap:12px;'>"
                f"<span style='font-size:1.2em'>{emoji}</span>"
                f"<span style='flex:1'><strong>{row['title']}</strong>"
                f"<span style='color:#555;font-size:0.85em'> — ID {row['pkg_id']}</span></span>"
                f"<span style='color:#333;font-size:0.9em;white-space:nowrap'>"
                f"🗓 {dep_str} <em>({age_str})</em></span>"
                f"</div>",
                unsafe_allow_html=True,
            )
            open_rollover = st.button(
                "🔁 Open in Package Rollover",
                key=f"pst_open_{idx}_{row['pkg_id']}",
                help=f"Prefill the Package Rollover tool with ID {row['pkg_id']}",
            )
            if open_rollover:
                # Hand off to the Package Rollover tool with this ID pre-filled
                st.session_state.pkr_prefill_id = row["pkg_id"]
                st.session_state.active_tool = "🔁 Package Rollover"
                st.rerun()
