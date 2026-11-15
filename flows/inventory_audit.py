"""
flows/inventory_audit.py — combined "Inventory audit" entry point.

CONFIRMED PRODUCT-OWNER REQUEST (2026-11-15): extend the Transport-only inventory audit
to Tickets and Transfers. The human picks the product type first; Transport hands off
unchanged to flows/transport_inventory_audit.py, while Ticket and Transfer get their own
checks defined here.

WHY THE CHECKS DIFFER PER TYPE — this is not one rule applied three times:

  Transport  inventories[] end date AND price supplement end date must both be the house
             "always available" date. Unchanged; see transport_inventory_audit.py.

  Ticket     has NO inventories[] at all, and its modalities carry REAL seasonal date
             ranges — a ticket priced for summer legitimately ends in September. Applying
             transport's "must end 2049-12-31" rule here would flag every ticket in the
             account. Product-owner's answer (2026-11-15), asked directly: flag tickets
             whose pricing has EXPIRED or expires soon (the rate sheet needs refreshing),
             and modalities with a MISSING PRICE or that are inactive under a live ticket.

  Transfer   is a flat record with no modalities at all, so there is nothing to check for
             consistency BETWEEN modalities. It is a standing open-ended service, so its
             own endDate should be the house date. Product-owner's answer: flag endDate
             that is not the expected house end date, plus routes with no reverse-direction
             pair.

ON THE EXPECTED END DATE (product-owner, 2026-11-15, verbatim): "we will in the future add
the code for the 'real' price range. The future it will be the same as Transport 20271231
for example if the product finished 31 of dec of 2027". So the house date is NOT hardcoded
here the way it is in the transport audit — it is an input defaulting to 2049-12-31, so
that once real contract end dates are in use a supplier can be audited against its own
date instead. A transfer whose endDate is already in the PAST is reported separately as
expired, since that is a different problem from not matching the house date.
"""
import re
from datetime import date, timedelta

import streamlit as st

import transfer_gap_finder
from flows.flow_ui_helpers import load_momira_suppliers as _load_momira_suppliers
from list_response_utils import normalize_list_response

MODULE_BUILD = "2026-11-15-inventory-audit"

HOUSE_END_DATE = "2049-12-31"
_WRAPPER_FALLBACKS = ("items", "data", "results", "content")


# ── Ticket audit ─────────────────────────────────────────────────────────────

def audit_ticket(ticket: dict, modalities: list, today: str, soon_cutoff: str) -> dict:
    """Flags a ticket whose pricing has expired / expires soon, or whose modalities have
    no usable price or are inactive under a live ticket.

    `modalities` is the list of full ContractTicketModalityVO dicts for this ticket.
    `today` and `soon_cutoff` are ISO date strings; a modality ending on or before
    soon_cutoff (but after today) counts as expiring soon, not yet expired.
    """
    code = ticket.get("code") or ""
    name = (ticket.get("name")
            or ((ticket.get("datasheets") or {}).get("EN") or {}).get("name")
            or code)
    ticket_active = ticket.get("active") is not False

    issues = []
    expired = []
    expiring = []

    # Tickets MAY carry the bracketed validity code too — product owner, 2026-11-15:
    # "might be for the tickets, but this is not sure". Read it when present, never
    # require it. Unlike transfers, a ticket modality's own endDate is already a real
    # seasonal date, so the code is a second opinion rather than the only signal.
    validity = parse_validity_code(name)
    if validity is False:
        issues.append("The bracketed code in the name is not a real date — "
                      "expected a year, month and day, e.g. (20271231)")
    elif validity:
        if validity < today:
            issues.append(f"Pricing EXPIRED on {validity} per the code in the name")
            expired.append({"modality_code": "(from name)", "end": validity})
        elif validity <= soon_cutoff:
            issues.append(f"Pricing runs out on {validity} per the code in the name")
            expiring.append({"modality_code": "(from name)", "end": validity})

    if not modalities:
        issues.append("Ticket has no modalities at all — nothing is sellable")

    for m in modalities:
        mcode = m.get("code") or "(no code)"
        end = m.get("endDate") or ""
        label = f"Modality {mcode!r}"

        # Price presence. A ticket modality prices through baseAdultPrice, or through
        # occupancyPrices when it is priced per occupancy instead — either satisfies this.
        base_adult = m.get("baseAdultPrice")
        occ = [p for p in (m.get("occupancyPrices") or [])
               if isinstance(p, dict) and (p.get("amount") or 0) > 0]
        if not (base_adult and base_adult > 0) and not occ:
            issues.append(f"{label}: no usable price (baseAdultPrice is "
                          f"{base_adult!r} and no occupancy prices)")

        # An inactive modality under a live ticket is a silent hole in availability.
        if m.get("active") is False and ticket_active:
            issues.append(f"{label}: inactive, but the ticket itself is live")

        # Expiry. No end date means open-ended, which is not a problem to flag here.
        if not end:
            continue
        if end < today:
            issues.append(f"{label}: pricing EXPIRED on {end}")
            expired.append({"modality_code": mcode, "end": end})
        elif end <= soon_cutoff:
            issues.append(f"{label}: pricing expires on {end}")
            expiring.append({"modality_code": mcode, "end": end})

    return {
        "kind": "Ticket",
        "id": code,
        "name": name,
        "active": ticket_active,
        "modality_count": len(modalities),
        "expired": expired,
        "expiring": expiring,
        "issues": issues,
        "ok": not issues,
    }


# ── the bracketed validity code ──────────────────────────────────────────────

_VALIDITY_CODE_RE = re.compile(r"\((\d{4})[-./]?(\d{2})[-./]?(\d{2})\)")


def parse_validity_code(name: str):
    """Reads the real price-validity end date out of a bracketed code in a product name,
    e.g. "Airport → Hotel (20271231)" → "2027-12-31". Returns None when there is no code.

    PRODUCT-OWNER EXPLANATION (2026-11-15, verbatim): "the code that we will add in the
    future in the brackets with the year, month, and date, which looks like a random
    number for any person who doesn't know, will be added for the transfers and the
    transports for sure. Also, might be for the tickets, but this is not sure. And this is
    just because a transfer and transport are in the system until 2049."

    So the record's own endDate is NOT the real answer for these types — it is deliberately
    2049-12-31 ("in the system until 2049") and should stay that way. The date that
    actually matters is written into the name. An audit that only read endDate would call
    every transfer healthy right up to the day its real pricing ran out.

    Not every product carries a code yet (it is being introduced, and may never apply to
    tickets), so a missing code is NOT an error — it just means there is nothing extra to
    check beyond the endDate rule.
    """
    if not name:
        return None
    m = _VALIDITY_CODE_RE.search(name)
    if not m:
        return None
    year, month, day = m.groups()
    # A malformed code is worse than no code: report it rather than guessing a date.
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return False  # sentinel: a code is present but is not a real date


# ── shared date-range helpers ────────────────────────────────────────────────

def find_overlaps(ranges: list) -> list:
    """Given [(start, end, label), ...] ISO date strings, returns the pairs that overlap.

    Two ranges overlap when each starts on or before the other ends. Used for both the
    ClosedTour price list and Hotel seasons, where an overlap means a date is priced twice
    and which price applies is ambiguous.
    """
    usable = [r for r in ranges if r[0] and r[1]]
    out = []
    for i in range(len(usable)):
        a_start, a_end, a_label = usable[i]
        for j in range(i + 1, len(usable)):
            b_start, b_end, b_label = usable[j]
            if a_start <= b_end and b_start <= a_end:
                out.append((a_label, b_label))
    return out


def _classify_coverage(ranges: list, today: str, soon_cutoff: str):
    """Returns (latest_end, all_expired, expiring_soon) for a set of (start, end, label)."""
    ends = sorted(r[1] for r in ranges if r[1])
    if not ends:
        return None, False, False
    latest = ends[-1]
    return latest, latest < today, today <= latest <= soon_cutoff


# ── ClosedTour audit ─────────────────────────────────────────────────────────

def audit_closed_tour(tour: dict, options: list, today: str, soon_cutoff: str) -> dict:
    """Flags a closed tour whose seasonal price list has expired, expires soon, or breaks
    the house no-overlap rule.

    CLAUDE.md states the invariant this enforces, verbatim: "Date ranges within one
    modality must NEVER overlap — overlapping ranges require separate modalities." That is
    a documented house rule with no API-side enforcement, so an overlap can sit unnoticed
    in a live tour until a booking prices wrong. `options` is the list of full
    ContractClosedTourOptionVO dicts for this tour.
    """
    code = tour.get("code") or tour.get("providerCode") or ""
    name = (tour.get("name")
            or ((tour.get("datasheets") or {}).get("EN") or {}).get("name")
            or code)

    issues = []
    expired = []
    expiring = []

    if not options:
        issues.append("Tour has no modalities at all — nothing is sellable")

    for opt in options:
        ocode = opt.get("code") or "(no code)"
        price_list = [p for p in (opt.get("priceList") or []) if isinstance(p, dict)]
        label = f"Modality {ocode!r}"

        if not price_list:
            issues.append(f"{label}: no price list — nothing is priced")
            continue

        ranges = [(p.get("startDate") or "", p.get("endDate") or "",
                   p.get("name") or f"{p.get('startDate')}→{p.get('endDate')}")
                  for p in price_list]

        for a_label, b_label in find_overlaps(ranges):
            issues.append(f"{label}: price periods '{a_label}' and '{b_label}' OVERLAP — "
                          f"each date must be priced once; split them into separate "
                          f"modalities")

        latest, all_expired, soon = _classify_coverage(ranges, today, soon_cutoff)
        if latest is None:
            issues.append(f"{label}: price periods have no end dates")
        elif all_expired:
            issues.append(f"{label}: all pricing EXPIRED on {latest}")
            expired.append({"modality_code": ocode, "end": latest})
        elif soon:
            issues.append(f"{label}: pricing runs out on {latest}")
            expiring.append({"modality_code": ocode, "end": latest})

    return {
        "kind": "ClosedTour",
        "id": code,
        "name": name,
        "active": tour.get("active") is not False,
        "modality_count": len(options),
        "expired": expired,
        "expiring": expiring,
        "issues": issues,
        "ok": not issues,
    }


# ── Hotel audit ──────────────────────────────────────────────────────────────

def audit_hotel(hotel: dict, today: str, soon_cutoff: str) -> dict:
    """Flags a hotel whose rate seasons have expired, expire soon, or overlap.

    Hotel rates carry seasons, each with its own dateRanges. The same reasoning as the
    ClosedTour price list applies: two seasons covering the same date leave it ambiguous
    which price wins, and a hotel whose last season has run out cannot be booked.
    """
    code = hotel.get("providerCode") or hotel.get("code") or ""
    name = hotel.get("name") or code

    issues = []
    expired = []
    expiring = []

    rates = [r for r in (hotel.get("rates") or []) if isinstance(r, dict)]
    if not rates:
        issues.append("Hotel has no rates at all — nothing is sellable")

    for rate in rates:
        rname = rate.get("name") or rate.get("id") or "(unnamed rate)"
        seasons = [s for s in (rate.get("seasons") or []) if isinstance(s, dict)]
        label = f"Rate {rname!r}"

        if not seasons:
            issues.append(f"{label}: no seasons — nothing is priced")
            continue

        ranges = []
        for s in seasons:
            sname = s.get("name") or "(unnamed season)"
            for dr in (s.get("dateRanges") or []):
                if isinstance(dr, dict):
                    ranges.append((dr.get("start") or "", dr.get("end") or "", sname))

        if not ranges:
            issues.append(f"{label}: seasons have no date ranges")
            continue

        for a_label, b_label in find_overlaps(ranges):
            if a_label == b_label:
                continue  # two ranges of the SAME season are a normal split season
            issues.append(f"{label}: seasons '{a_label}' and '{b_label}' OVERLAP — "
                          f"each date must fall in one season only")

        latest, all_expired, soon = _classify_coverage(ranges, today, soon_cutoff)
        if all_expired:
            issues.append(f"{label}: all seasons EXPIRED on {latest}")
            expired.append({"rate": str(rname), "end": latest})
        elif soon:
            issues.append(f"{label}: seasons run out on {latest}")
            expiring.append({"rate": str(rname), "end": latest})

    return {
        "kind": "Hotel",
        "id": code,
        "name": name,
        "active": hotel.get("active") is not False,
        "expired": expired,
        "expiring": expiring,
        "issues": issues,
        "ok": not issues,
    }


# ── Transfer audit ───────────────────────────────────────────────────────────

def audit_transfer(transfer: dict, expected_end: str, today: str,
                   missing_reverse: bool, soon_cutoff: str = "") -> dict:
    """Flags a transfer whose endDate is not the expected house date (or is already past),
    whose bracketed validity code says its real pricing has run out, and optionally that
    it has no reverse-direction pair.

    Two different dates are in play and they are not interchangeable — see
    parse_validity_code. endDate is the house "in the system until 2049" date; the
    bracketed code in the name is when the pricing actually stops being valid.
    """
    tid = transfer.get("id") or ""
    name = transfer.get("name") or tid
    end = transfer.get("endDate") or ""

    issues = []
    end_date_fix = None

    if not end:
        issues.append("No end date set at all")
    elif end < today:
        issues.append(f"End date {end} is in the PAST — this transfer is no longer sellable")
        end_date_fix = {"current_end": end}
    elif end != expected_end:
        issues.append(f"End date is {end} — expected {expected_end}")
        end_date_fix = {"current_end": end}

    # The real price validity, when the name carries a code for it.
    validity = parse_validity_code(name)
    validity_end = None
    expired = False
    expiring = False
    if validity is False:
        issues.append("The bracketed code in the name is not a real date — "
                      "expected a year, month and day, e.g. (20271231)")
    elif validity:
        validity_end = validity
        if validity < today:
            issues.append(f"Pricing EXPIRED on {validity} per the code in the name "
                          f"(the {end} end date is the house 2049 date, not the real one)")
            expired = True
        elif soon_cutoff and validity <= soon_cutoff:
            issues.append(f"Pricing runs out on {validity} per the code in the name")
            expiring = True

    if missing_reverse:
        issues.append("No reverse-direction transfer exists for this route")

    return {
        "kind": "Transfer",
        "id": tid,
        "name": name,
        "active": transfer.get("active") is not False,
        "current_end": end,
        "validity_end": validity_end,
        "expired": [{"end": validity_end}] if expired else [],
        "expiring": [{"end": validity_end}] if expiring else [],
        "end_date_fix": end_date_fix,
        "missing_reverse": missing_reverse,
        "issues": issues,
        "ok": not issues,
        "raw": transfer,
    }


def apply_transfer_end_date_fixes(client, supplier_id: str, rows: list,
                                  expected_end: str) -> list:
    """GET each transfer fresh, set endDate to expected_end, PUT it back.

    Re-fetches rather than reusing the scanned copy so a change made in TC between the
    scan and the fix is not silently overwritten with stale fields.
    """
    results = []
    for row in rows:
        tid = row["id"]
        try:
            rec = client.get_transfer(supplier_id, tid)
        except Exception as e:
            results.append({"id": tid, "name": row["name"], "ok": False,
                            "detail": f"GET failed: {type(e).__name__}: {e}"})
            continue
        if isinstance(rec, dict) and "error" in rec:
            results.append({"id": tid, "name": row["name"], "ok": False,
                            "detail": str(rec.get("message") or rec.get("error"))})
            continue

        if (rec.get("endDate") or "") == expected_end:
            results.append({"id": tid, "name": row["name"], "ok": True,
                            "detail": "already correct (no change needed)"})
            continue

        payload = dict(rec)
        payload["endDate"] = expected_end
        try:
            resp = client.update_transfer(supplier_id, payload)
        except Exception as e:
            results.append({"id": tid, "name": row["name"], "ok": False,
                            "detail": f"PUT failed: {type(e).__name__}: {e}"})
            continue
        if isinstance(resp, dict) and "error" in resp:
            results.append({"id": tid, "name": row["name"], "ok": False,
                            "detail": str(resp.get("message") or resp.get("error"))})
        else:
            results.append({"id": tid, "name": row["name"], "ok": True,
                            "detail": f"endDate changed to {expected_end}"})
    return results


# ── scan drivers ─────────────────────────────────────────────────────────────

def _scan_tickets(client, supplier_id: str, soon_days: int):
    try:
        data = client.get_tickets(supplier_id, first=0, limit=200)
    except Exception as e:
        st.error(f"Could not fetch tickets: {type(e).__name__}: {e}")
        return None
    tickets = normalize_list_response(data, ("ticket", "tickets") + _WRAPPER_FALLBACKS)
    tickets = [t for t in tickets if isinstance(t, dict)]
    if not tickets:
        st.info("No tickets found for this supplier.")
        return []

    today = date.today().isoformat()
    soon_cutoff = (date.today() + timedelta(days=soon_days)).isoformat()

    results = []
    prog = st.progress(0, text="Scanning tickets…")
    for i, t in enumerate(tickets):
        prog.progress((i + 1) / len(tickets), text=f"Ticket {i + 1} / {len(tickets)}")
        mods = []
        for mcode in t.get("modalityCodes") or []:
            try:
                full = client.get_ticket_option(supplier_id, t.get("code") or "", mcode)
            except Exception:
                continue
            if isinstance(full, dict) and "error" not in full:
                mods.append(full)
        results.append(audit_ticket(t, mods, today, soon_cutoff))
    prog.empty()
    return results


def _scan_closed_tours(client, supplier_id: str, soon_days: int):
    try:
        data = client.get_closed_tours(supplier_id, first=0, limit=200)
    except Exception as e:
        st.error(f"Could not fetch closed tours: {type(e).__name__}: {e}")
        return None
    tours = normalize_list_response(data, ("closedTour", "closedTours") + _WRAPPER_FALLBACKS)
    tours = [t for t in tours if isinstance(t, dict)]
    if not tours:
        st.info("No closed tours found for this supplier.")
        return []

    today = date.today().isoformat()
    soon_cutoff = (date.today() + timedelta(days=soon_days)).isoformat()

    results = []
    prog = st.progress(0, text="Scanning closed tours…")
    for i, t in enumerate(tours):
        prog.progress((i + 1) / len(tours), text=f"Closed tour {i + 1} / {len(tours)}")
        code = t.get("code") or t.get("providerCode") or ""
        opts = []
        for ocode in t.get("modalityCodes") or []:
            try:
                full = client.get_closed_tour_option(supplier_id, code, ocode)
            except Exception:
                continue
            if isinstance(full, dict) and "error" not in full:
                opts.append(full)
        results.append(audit_closed_tour(t, opts, today, soon_cutoff))
    prog.empty()
    return results


def _scan_hotels(client, supplier_id: str, soon_days: int):
    try:
        data = client.get_hotels(supplier_id)
    except Exception as e:
        st.error(f"Could not fetch hotels: {type(e).__name__}: {e}")
        return None
    hotels = normalize_list_response(data, ("hotel", "hotels") + _WRAPPER_FALLBACKS)
    hotels = [h for h in hotels if isinstance(h, dict)]
    if not hotels:
        st.info("No hotels found for this supplier.")
        return []

    today = date.today().isoformat()
    soon_cutoff = (date.today() + timedelta(days=soon_days)).isoformat()

    results = []
    prog = st.progress(0, text="Scanning hotels…")
    for i, h in enumerate(hotels):
        prog.progress((i + 1) / len(hotels), text=f"Hotel {i + 1} / {len(hotels)}")
        # The listing is a summary; rates and their seasons only come back from the
        # per-hotel GET, so re-fetch before auditing.
        pcode = h.get("providerCode") or h.get("code") or ""
        try:
            full = client.get_hotel(supplier_id, pcode)
        except Exception:
            full = None
        rec = full if isinstance(full, dict) and "error" not in full else h
        results.append(audit_hotel(rec, today, soon_cutoff))
    prog.empty()
    return results


def _scan_transfers(client, supplier_id: str, expected_end: str, check_reverse: bool,
                    soon_days: int = 60):
    try:
        data = client.get_transfers(supplier_id)
    except Exception as e:
        st.error(f"Could not fetch transfers: {type(e).__name__}: {e}")
        return None
    transfers = normalize_list_response(data, ("transfer", "transfers") + _WRAPPER_FALLBACKS)
    transfers = [t for t in transfers if isinstance(t, dict)]
    if not transfers:
        st.info("No transfers found for this supplier.")
        return []

    gap_ids = set()
    if check_reverse:
        # Reuses the same gap finder the "create missing reverse" tool uses, so the two
        # screens can never disagree about what counts as a gap.
        try:
            for gap in transfer_gap_finder.find_missing_reverse_transfers(transfers):
                src = gap.get("source") or {}
                if src.get("id"):
                    gap_ids.add(src["id"])
        except Exception as e:
            st.warning(f"Reverse-direction check skipped: {type(e).__name__}: {e}")

    today = date.today().isoformat()
    soon_cutoff = (date.today() + timedelta(days=soon_days)).isoformat()
    active = [t for t in transfers if t.get("active") is not False]
    return [audit_transfer(t, expected_end, today, t.get("id") in gap_ids,
                           soon_cutoff=soon_cutoff)
            for t in active]


# ── UI ───────────────────────────────────────────────────────────────────────

def _render_results(client, results, supplier_id, expected_end, kind):
    total = len(results)
    flagged = [r for r in results if not r["ok"]]

    st.markdown("---")
    st.subheader("Results")
    c1, c2, c3 = st.columns(3)
    c1.metric(f"{kind}s scanned", total)
    c2.metric("OK", total - len(flagged))
    c3.metric("Issues found", len(flagged))

    if not flagged:
        st.success(f"All {kind.lower()}s look good.")
        return

    if kind in ("Ticket", "ClosedTour", "Hotel", "Transfer"):
        noun = {"Ticket": "ticket", "ClosedTour": "closed tour",
                "Hotel": "hotel", "Transfer": "transfer"}[kind]
        expired = [r for r in flagged if r["expired"]]
        expiring = [r for r in flagged if r["expiring"] and not r["expired"]]
        overlapping = [r for r in flagged
                       if any("OVERLAP" in i for i in r["issues"])]
        if expired:
            st.error(f"**{len(expired)} {noun}(s) with EXPIRED pricing** — these need a "
                     f"new rate sheet before they can be sold.")
        if expiring:
            st.warning(f"**{len(expiring)} {noun}(s) expiring soon.**")
        if overlapping:
            st.error(f"**{len(overlapping)} {noun}(s) with OVERLAPPING date periods** — "
                     f"each date must be priced once, so which price applies is ambiguous.")

    if kind == "Transfer":
        fixable = [r for r in flagged if r["end_date_fix"]]
        if fixable:
            st.markdown("---")
            st.subheader(f"End date fixes ({len(fixable)})")
            st.info(f"These transfers have an end date other than {expected_end}. "
                    f"Select the ones to fix and click Apply.")
            chosen = []
            for i, r in enumerate(fixable):
                label = f"{r['name']} — currently **{r['current_end'] or '(none)'}**"
                if st.checkbox(label, value=True, key=f"inv_fix_{i}"):
                    chosen.append(r)
            if chosen and st.button(f"Fix {len(chosen)} end date(s) to {expected_end}",
                                    key="inv_apply_fixes", type="primary"):
                with st.spinner("Applying fixes…"):
                    st.session_state.inv_fix_results = apply_transfer_end_date_fixes(
                        client, supplier_id, chosen, expected_end)

            fix_results = st.session_state.get("inv_fix_results")
            if fix_results:
                ok = [r for r in fix_results if r["ok"]]
                bad = [r for r in fix_results if not r["ok"]]
                if ok:
                    st.success(f"{len(ok)} transfer(s) updated.")
                    for r in ok:
                        st.write(f"- {r['name']}: {r['detail']}")
                if bad:
                    st.error(f"{len(bad)} transfer(s) failed:")
                    for r in bad:
                        st.write(f"- {r['name']}: {r['detail']}")

    st.markdown("---")
    st.subheader(f"All flagged {kind.lower()}s")
    for r in flagged:
        with st.expander(f"{r['name']}  (id: {r['id']})", expanded=False):
            for issue in r["issues"]:
                st.error(issue)


def render_inventory_audit_flow(client):
    from flows.transport_inventory_audit import render_transport_inventory_audit_flow

    st.header("🔍 Inventory audit")

    if "inv_audit_product" not in st.session_state:
        st.session_state.inv_audit_product = None

    if st.session_state.inv_audit_product is None:
        if st.button("🔙 Back", key="inv_back"):
            st.session_state.product_type = None
            st.rerun()
        st.caption("Scan a supplier's live products and flag what looks wrong. "
                   "Each product type is checked differently — pick one:")
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("✈️ Transport", key="inv_transport", use_container_width=True,
                         type="primary"):
                st.session_state.inv_audit_product = "Transport"
                st.rerun()
            st.caption("Inventory and price supplement end dates must both be "
                       f"{HOUSE_END_DATE}. Can fix price dates directly.")
            if st.button("🗺️ Closed Tour", key="inv_closedtour", use_container_width=True,
                         type="primary"):
                st.session_state.inv_audit_product = "ClosedTour"
                st.rerun()
            st.caption("Seasonal price periods that overlap (each date must be priced "
                       "once), have run out, or run out soon.")
        with c2:
            if st.button("🎟️ Ticket", key="inv_ticket", use_container_width=True,
                         type="primary"):
                st.session_state.inv_audit_product = "Ticket"
                st.rerun()
            st.caption("Pricing that has expired or expires soon, plus modalities "
                       "with no price or inactive under a live ticket.")
            if st.button("🏨 Hotel", key="inv_hotel", use_container_width=True,
                         type="primary"):
                st.session_state.inv_audit_product = "Hotel"
                st.rerun()
            st.caption("Rate seasons that overlap, have expired, or run out soon.")
        with c3:
            if st.button("🚌 Transfer", key="inv_transfer", use_container_width=True,
                         type="primary"):
                st.session_state.inv_audit_product = "Transfer"
                st.rerun()
            st.caption("End date not matching the house date, and routes with no "
                       "reverse-direction pair. Can fix end dates directly.")
        return

    chosen_type = st.session_state.inv_audit_product

    if chosen_type == "Transport":
        render_transport_inventory_audit_flow(client)
        return

    if st.button("🔙 Pick a different product type", key="inv_change_type"):
        for k in ("inv_audit_product", "inv_results", "inv_fix_results", "inv_supplier_id"):
            st.session_state.pop(k, None)
        st.rerun()

    suppliers = _load_momira_suppliers(client)
    if not suppliers:
        st.warning("No active suppliers found.")
        return
    supplier_options = [f"{s['name']}  (id: {s['id']})" for s in suppliers]
    chosen = st.selectbox("Supplier", supplier_options, key="inv_supplier_select")
    supplier_id = suppliers[supplier_options.index(chosen)]["id"]

    expected_end = HOUSE_END_DATE
    soon_days = 60
    check_reverse = True

    if chosen_type in ("Ticket", "ClosedTour", "Hotel"):
        soon_days = st.number_input(
            "Warn when pricing ends within this many days", min_value=1, max_value=365,
            value=60, key="inv_soon_days",
            help="Anything already past its end date is reported as expired regardless.")
    else:
        expected_end = st.text_input(
            "Expected end date", value=HOUSE_END_DATE, key="inv_expected_end",
            help="Transfers sit in the system until 2049-12-31, so this normally stays as "
                 "it is. The real price validity is read separately from the bracketed "
                 "code in the product name, e.g. (20271231).")
        soon_days = st.number_input(
            "Warn when the name's validity code is within this many days", min_value=1,
            max_value=365, value=60, key="inv_soon_days_transfer",
            help="Only applies to transfers whose name carries a validity code. One "
                 "already in the past is reported as expired regardless.")
        check_reverse = st.checkbox("Also flag routes with no reverse direction",
                                    value=True, key="inv_check_reverse")

    if st.button(f"Scan {chosen_type.lower()}s", key="inv_scan", type="primary"):
        st.session_state.pop("inv_fix_results", None)
        st.session_state.inv_supplier_id = supplier_id
        if chosen_type == "Ticket":
            st.session_state.inv_results = _scan_tickets(client, supplier_id, int(soon_days))
        elif chosen_type == "ClosedTour":
            st.session_state.inv_results = _scan_closed_tours(
                client, supplier_id, int(soon_days))
        elif chosen_type == "Hotel":
            st.session_state.inv_results = _scan_hotels(client, supplier_id, int(soon_days))
        else:
            st.session_state.inv_results = _scan_transfers(
                client, supplier_id, expected_end.strip(), check_reverse,
                soon_days=int(soon_days))

    results = st.session_state.get("inv_results")
    if results:
        _render_results(client, results,
                        st.session_state.get("inv_supplier_id", supplier_id),
                        expected_end.strip() if chosen_type == "Transfer" else expected_end,
                        chosen_type)
