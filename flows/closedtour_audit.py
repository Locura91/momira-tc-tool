"""
flows/closedtour_audit.py — Two-phase ClosedTour audit.

Phase 1: basic info check (name, nights, times, currency, active status).
Phase 2: price & modality audit (date validity, price ordering, rounding,
         child discounts, missing/zero prices, operational days).
"""

import math
from datetime import date, timedelta

import streamlit as st

from flows.flow_ui_helpers import load_momira_suppliers as _load_momira_suppliers
from list_response_utils import normalize_list_response

MODULE_BUILD = "2026-11-26-closedtour-audit"

_KEY = "cta_"
_WRAPPER_KEYS = ("items", "data", "results", "content")


# ── Phase 1: basic info ────────────────────────────────────────────────────────

def _audit_basic_info(tour: dict) -> list[str]:
    issues = []
    code = tour.get("code") or "(no code)"
    name = tour.get("name") or ""
    if not name:
        ds = tour.get("datasheets") or {}
        name = (ds.get("EN") or {}).get("name", "")
    if not name:
        issues.append(f"[{code}] Missing tour name")
    if not tour.get("currency"):
        issues.append(f"[{code}] Missing currency")
    nights = tour.get("nights")
    if nights is None or nights == 0:
        issues.append(f"[{code}] Nights is {nights} (should be >= 1)")
    if not tour.get("startTime"):
        issues.append(f"[{code}] Start time is empty")
    if not tour.get("endTime"):
        issues.append(f"[{code}] End time is empty")
    if tour.get("active") is False:
        issues.append(f"[{code}] Tour is INACTIVE")
    modality_codes = tour.get("modalityCodes") or []
    if not modality_codes:
        issues.append(f"[{code}] No modalities/options defined")
    return issues


# ── Phase 2: price & modality checks ───────────────────────────────────────────

def _get_price_amount(money_vo) -> float | None:
    if money_vo is None:
        return None
    if isinstance(money_vo, dict):
        return money_vo.get("amount")
    return None


def _audit_option(option: dict, tour_code: str) -> list[str]:
    issues = []
    ocode = option.get("code") or "(no code)"
    label = f"[{tour_code} / {ocode}]"

    op_days = option.get("operationalDays") or []
    if not op_days:
        issues.append(f"{label} No operational days set")

    price_list = option.get("priceList") or []
    if not price_list:
        issues.append(f"{label} Empty priceList - no pricing defined")
        return issues

    today_str = date.today().isoformat()
    soon_str = (date.today() + timedelta(days=60)).isoformat()

    for i, entry in enumerate(price_list):
        start = entry.get("startDate") or ""
        end = entry.get("endDate") or ""
        season_label = entry.get("name") or f"season {i + 1}"
        prefix = f"{label} {season_label} ({start} to {end})"

        # Date checks
        if end and end < today_str:
            issues.append(f"{prefix}: EXPIRED")
        elif end and end <= soon_str:
            issues.append(f"{prefix}: expires within 60 days")

        # Price checks
        price_block = entry.get("price") or {}
        amounts = {}
        for occ in ("singlePrice", "doublePrice", "triplePrice", "quadruplePrice"):
            amounts[occ] = _get_price_amount(price_block.get(occ))

        # Check for missing/zero prices
        for occ, val in amounts.items():
            occ_label = occ.replace("Price", "")
            if val is None:
                issues.append(f"{prefix}: {occ_label} price is missing")
            elif val <= 0:
                issues.append(f"{prefix}: {occ_label} price is {val} (must be > 0)")

        # Price ordering: single >= double >= triple >= quadruple
        present = {k: v for k, v in amounts.items() if v is not None and v > 0}
        order = ["singlePrice", "doublePrice", "triplePrice", "quadruplePrice"]
        vals = [present.get(k) for k in order]
        for j in range(len(vals) - 1):
            if vals[j] is not None and vals[j + 1] is not None:
                if vals[j] < vals[j + 1]:
                    left = order[j].replace("Price", "")
                    right = order[j + 1].replace("Price", "")
                    issues.append(
                        f"{prefix}: {left} ({vals[j]}) < {right} ({vals[j + 1]}) "
                        f"- violates ordering rule"
                    )

        # Rounding check: prices must be rounded up to whole numbers
        for occ, val in amounts.items():
            if val is not None and val > 0:
                rounded_up = math.ceil(val)
                if val != rounded_up:
                    occ_label = occ.replace("Price", "")
                    issues.append(
                        f"{prefix}: {occ_label} price {val} is not rounded up "
                        f"(should be {rounded_up})"
                    )

        # Child discount percentage checks
        for disc_key in (
            "singleChildPercentageDiscount",
            "doubleChildPercentageDiscount",
            "tripleChildPercentageDiscount",
            "quadrupleChildPercentageDiscount",
        ):
            disc_val = price_block.get(disc_key)
            if disc_val is not None:
                if disc_val < 0 or disc_val > 100:
                    disc_label = disc_key.replace("ChildPercentageDiscount", "")
                    issues.append(
                        f"{prefix}: {disc_label} child discount {disc_val}% "
                        f"is outside 0-100 range"
                    )

    return issues


# ── Streamlit UI ───────────────────────────────────────────────────────────────

def render_closedtour_audit(client):
    st.subheader("ClosedTour Audit")
    st.caption(
        "Two-phase check: first the basic tour information, "
        "then a deep dive into prices and modalities."
    )

    # Supplier selection
    suppliers = _load_momira_suppliers(client)
    if not suppliers:
        st.warning("No active suppliers found.")
        return

    supplier_options = {f"{s['name']} ({s['id']})": s["id"] for s in suppliers}
    selected = st.selectbox(
        "Select supplier",
        list(supplier_options.keys()),
        key=f"{_KEY}supplier",
    )
    supplier_id = supplier_options[selected]

    if not st.button("Run audit", type="primary", key=f"{_KEY}run"):
        return

    # Load tours
    with st.spinner("Loading closed tours..."):
        try:
            raw = client.get_closed_tours(supplier_id)
        except Exception as e:
            st.error(f"Failed to load closed tours: {e}")
            return

    tours = normalize_list_response(raw, _WRAPPER_KEYS)
    if not tours:
        st.info("This supplier has no closed tours.")
        return

    st.markdown(f"Found **{len(tours)}** closed tour(s).")

    # ── Phase 1 ──────────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Phase 1: Basic Information Check")

    phase1_all_ok = True
    phase1_issues_total = 0

    for tour in tours:
        code = tour.get("code") or "(no code)"
        name = tour.get("name") or ""
        if not name:
            ds = tour.get("datasheets") or {}
            name = (ds.get("EN") or {}).get("name", code)

        issues = _audit_basic_info(tour)
        status_icon = "+" if not issues else "!"
        modality_count = len(tour.get("modalityCodes") or [])

        with st.expander(
            f"{'OK' if not issues else 'ISSUES'} | {code} - {name} "
            f"({tour.get('nights', '?')} nights, {modality_count} modalities)",
            expanded=bool(issues),
        ):
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"**Code:** `{code}`")
                st.markdown(f"**Name:** {name}")
                st.markdown(f"**Nights:** {tour.get('nights', 'N/A')}")
                st.markdown(f"**Pax:** {tour.get('minPax', '?')}-{tour.get('maxPax', '?')}")
            with col2:
                st.markdown(f"**Currency:** {tour.get('currency', 'N/A')}")
                st.markdown(f"**Start time:** {tour.get('startTime') or 'N/A'}")
                st.markdown(f"**End time:** {tour.get('endTime') or 'N/A'}")
                active = tour.get("active")
                st.markdown(f"**Active:** {'Yes' if active else 'No'}")

            if issues:
                phase1_all_ok = False
                phase1_issues_total += len(issues)
                for iss in issues:
                    st.warning(iss)
            else:
                st.success("All basic checks passed.")

    if phase1_all_ok:
        st.success(
            "Phase 1 complete - all basic information checks passed. "
            "Proceeding to price audit..."
        )
    else:
        st.error(
            f"Phase 1 found **{phase1_issues_total}** issue(s). "
            f"Review the warnings above. Proceeding to price audit anyway..."
        )

    # ── Phase 2 ──────────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Phase 2: Price & Modality Audit")

    phase2_issues_total = 0
    tours_checked = 0
    options_checked = 0

    for tour in tours:
        code = tour.get("code") or "(no code)"
        name = tour.get("name") or ""
        if not name:
            ds = tour.get("datasheets") or {}
            name = (ds.get("EN") or {}).get("name", code)

        modality_codes = tour.get("modalityCodes") or []
        if not modality_codes:
            continue

        tours_checked += 1
        tour_issues = []

        with st.expander(
            f"{code} - {name} ({len(modality_codes)} modalities)",
            expanded=False,
        ):
            for mcode in modality_codes:
                with st.spinner(f"Fetching option {mcode}..."):
                    try:
                        option = client.get_closed_tour_option(supplier_id, code, mcode)
                    except Exception as e:
                        st.error(f"Failed to load option {mcode}: {e}")
                        tour_issues.append(f"[{code}/{mcode}] API error: {e}")
                        continue

                if not option or isinstance(option, dict) and option.get("error"):
                    st.error(f"Option {mcode}: could not load from TC")
                    tour_issues.append(f"[{code}/{mcode}] Could not load")
                    continue

                options_checked += 1
                issues = _audit_option(option, code)
                tour_issues.extend(issues)

                st.markdown(f"**Modality: `{mcode}`**")
                price_list = option.get("priceList") or []
                op_days = option.get("operationalDays") or []
                st.caption(
                    f"{len(price_list)} price season(s), "
                    f"operates {', '.join(d[:3] for d in op_days) if op_days else 'NO DAYS SET'}"
                )

                # Show current prices
                for entry in price_list:
                    season_name = entry.get("name") or "Season"
                    start = entry.get("startDate") or "?"
                    end = entry.get("endDate") or "?"
                    price_block = entry.get("price") or {}

                    prices = {}
                    for occ in ("singlePrice", "doublePrice", "triplePrice", "quadruplePrice"):
                        mv = price_block.get(occ)
                        if mv and isinstance(mv, dict):
                            prices[occ] = mv.get("amount")
                        else:
                            prices[occ] = None

                    price_str = " | ".join(
                        f"{occ.replace('Price', '')}: {v if v is not None else 'N/A'}"
                        for occ, v in prices.items()
                    )
                    st.markdown(
                        f"  - **{season_name}** ({start} to {end}): {price_str}"
                    )

                # Show issues for this option
                option_issues = [i for i in issues]
                if option_issues:
                    for iss in option_issues:
                        st.warning(iss)
                else:
                    st.success(f"Modality `{mcode}` - all price checks passed.")

                st.markdown("---")

            phase2_issues_total += len(tour_issues)

    # Summary
    st.markdown("---")
    st.markdown("### Audit Summary")
    st.markdown(f"- **Tours checked:** {len(tours)}")
    st.markdown(f"- **Options/modalities checked:** {options_checked}")

    total_issues = phase1_issues_total + phase2_issues_total
    if total_issues == 0:
        st.success("All checks passed! No issues found.")
    else:
        st.error(
            f"**{total_issues}** issue(s) found in total "
            f"({phase1_issues_total} basic info, {phase2_issues_total} pricing)."
        )
