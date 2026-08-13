"""
stop_sales_tool.py — the supervised Stop Sales Email Reader.

WHAT IT IS FOR: suppliers announce closures by email, in prose, at short notice. Someone
has to read each one, work out which product it means, and block the dates in Travel
Compositor. Until that happens a customer can book something that cannot be delivered.

WHY IT IS SUPERVISED, AND STAYS SUPERVISED: the two ways this goes wrong are both silent
and they point in opposite directions. Missing a stop sale sells something undeliverable.
Applying one wrongly — the wrong month from an ambiguous 01/02, a rate season misread as a
closure, a RELEASE applied as a block — quietly destroys sellable inventory, and nobody
notices, because a product that stops appearing looks exactly like a product nobody
searched for. So the AI only ever proposes. Every date is editable, every date is shown
against the wording it came from, and nothing reaches Travel Compositor until a person
presses Apply.

FIVE STEPS, mirroring the Upload & Update wizard:

    1. Paste the email, or upload a .eml
    2. Parse            - AI reads it; warnings surfaced, nothing written
    3. Match            - confirm supplier and product (auto-matched where possible)
    4. Review & edit    - live stop sales shown alongside the proposed additions
    5. Apply            - merge and write

MERGE, NEVER REPLACE. A product's live stop sales were put there by earlier emails and by
people working in Travel Compositor directly. Sending only the new ones would silently
unblock dates that are still closed, and the product would start selling again with nobody
having asked for it. Every write here reads the live record first and appends to it.

WHERE STOP SALES ACTUALLY LIVE (different for the two product types, and neither is on the
product's own top-level record):
  * ClosedTour -> on each OPTION/modality (ContractClosedTourOptionVO.stopSales), so a tour
    with three modalities needs the block written to each one the email affects.
  * Hotel      -> inside a RATE, grouped per room name
    (ContractHotelRateVO.stopSales -> ContractHotelRoomStopSalesVO), so the rate is fetched,
    merged and PUT back whole.

KNOWN UNVERIFIED ASSUMPTION, deliberately surfaced on screen rather than buried here:
ContractHotelRoomStopSalesVO.roomId is a numeric id Travel Compositor never returns
anywhere, so hotel stop sales are submitted with roomName only. schemas.py documents this
and flags that it needs a live validation test. Until that test exists the hotel path shows
a warning before Apply.
"""

# Stamped on every delivery. app.py compares this against its own build string and says
# so on screen when they differ - a partial push (one file committed, another not) used to
# surface only as a traceback whose line numbers pointed at unrelated code.
MODULE_BUILD = "2026-08-13-ticket-occupancy-only-pricing"

import json
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

import extraction_memory
import platform_store
import stop_sales_parser as ssp
from ai_extractor import friendly_error_message

_NS_PROCESSED = "processed_stop_sales"
_PREFIX = "ss_"


def _reset_run(keep: tuple = ()) -> None:
    for key in list(st.session_state.keys()):
        if key.startswith(_PREFIX) and key not in keep:
            del st.session_state[key]


def _get(key: str, default=None):
    return st.session_state[key] if key in st.session_state else default


# ======================================================================
# Processed-email log
# ======================================================================
def already_processed(fingerprint: str) -> Optional[Dict[str, Any]]:
    """The record of a previous run for this email, or None.

    Duplicates are the ordinary case, not an edge case: supplier stop-sale mails get
    forwarded between colleagues and pasted twice. Applying the same block twice is
    harmless to the DATES (merge de-duplicates) but it wastes an operator's time and, worse,
    makes them doubt whether the first one worked."""
    if not fingerprint:
        return None
    return platform_store.get(_NS_PROCESSED, fingerprint)


def mark_processed(fingerprint: str, record: Dict[str, Any]) -> bool:
    if not fingerprint:
        return False
    return platform_store.set(_NS_PROCESSED, fingerprint, record)


# ======================================================================
# Reading and writing the live stop sales
# ======================================================================
def fetch_closed_tour_options(client, supplier_id: str, tour_code: str) -> Dict[str, Any]:
    """The tour plus each of its options, since stop sales live on the OPTIONS."""
    tour = client.get_closed_tour(supplier_id, tour_code)
    if not isinstance(tour, dict) or "error" in tour:
        return {"error": tour if isinstance(tour, dict) else {"message": str(tour)}}
    options = []
    for code in (tour.get("modalityCodes") or []):
        opt = client.get_closed_tour_option(supplier_id, tour_code, code)
        if isinstance(opt, dict) and "error" not in opt:
            options.append(opt)
        else:
            options.append({"code": code, "_fetch_error": True, "stopSales": []})
    return {"tour": tour, "options": options}


def existing_tour_stop_sales(option: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [s for s in (option.get("stopSales") or []) if isinstance(s, dict)]


def existing_hotel_stop_sales(rate: Dict[str, Any], room_name: str = "") -> List[Dict[str, Any]]:
    """The date ranges already blocked on this rate, for one room or across all of them.

    A hotel's stop sales are grouped per room, so 'what is already blocked' only means
    something once you have said which room - or explicitly said 'all of them', which is
    what an empty room_name means here."""
    out = []
    for group in (rate.get("stopSales") or []):
        if not isinstance(group, dict):
            continue
        if room_name and str(group.get("roomName") or "") != room_name:
            continue
        for r in (group.get("stopSales") or []):
            if isinstance(r, dict) and r.get("start"):
                out.append({"start": r.get("start"), "end": r.get("end"),
                            "room": group.get("roomName") or "(all rooms)"})
    return out


def apply_to_tour_option(client, supplier_id: str, tour_code: str, option: Dict[str, Any],
                         new_ranges: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge the new ranges into ONE option and PUT it back.

    The whole option is sent, not a stopSales-only patch: Travel Compositor's PUT replaces
    the resource, so a partial body would blank the option's priceList, operational days and
    translations. The option that was just fetched is used as the base for exactly that
    reason."""
    merged = ssp.merge_stop_sales(existing_tour_stop_sales(option), new_ranges)
    if not merged["added"]:
        return {"status": "unchanged", "code": option.get("code"),
                "detail": "every date was already blocked on this modality"}
    payload = dict(option)
    payload.pop("_fetch_error", None)
    payload["stopSales"] = merged["merged"]
    try:
        res = client.update_closed_tour_option(supplier_id, tour_code, payload)
    except Exception as e:
        return {"status": "failed", "code": option.get("code"),
                "detail": friendly_error_message(e)}
    if isinstance(res, dict) and "error" in res:
        return {"status": "failed", "code": option.get("code"),
                "detail": str(res.get("message") or res.get("error"))}
    return {"status": "updated", "code": option.get("code"), "added": merged["added"]}


def apply_to_hotel_rate(client, supplier_id: str, provider_code: str, rate: Dict[str, Any],
                        new_ranges: List[Dict[str, Any]], room_names: List[str]) -> Dict[str, Any]:
    """Merge the new ranges into a rate, for each named room, and PUT the rate back.

    room_names must be explicit. Blocking 'the hotel' means blocking each of its rooms, and
    inferring that from an empty list would be the difference between one room type being
    closed and the whole property being closed - the caller decides that, on screen."""
    payload = json.loads(json.dumps(rate))          # deep copy; never mutate what was fetched
    groups = payload.get("stopSales")
    if not isinstance(groups, list):
        groups = []
    added_total = []
    for room in room_names:
        group = next((g for g in groups
                      if isinstance(g, dict) and str(g.get("roomName") or "") == room), None)
        if group is None:
            # roomId is deliberately left unset - Travel Compositor never returns it, so
            # roomName is the only handle this tool has. See the module docstring.
            group = {"roomName": room, "stopSales": []}
            groups.append(group)
        merged = ssp.merge_stop_sales(group.get("stopSales") or [], new_ranges)
        group["stopSales"] = merged["merged"]
        added_total.extend([dict(a, room=room) for a in merged["added"]])
    if not added_total:
        return {"status": "unchanged", "code": rate.get("name") or rate.get("id"),
                "detail": "every date was already blocked on this rate"}
    payload["stopSales"] = groups
    try:
        res = client.update_hotel_rates(supplier_id, provider_code, payload)
    except Exception as e:
        return {"status": "failed", "code": rate.get("name") or rate.get("id"),
                "detail": friendly_error_message(e)}
    if isinstance(res, dict) and "error" in res:
        return {"status": "failed", "code": rate.get("name") or rate.get("id"),
                "detail": str(res.get("message") or res.get("error"))}
    return {"status": "updated", "code": rate.get("name") or rate.get("id"), "added": added_total}


# ======================================================================
# UI
# ======================================================================
def _ranges_editor(parsed: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The editable table of proposed blocks. Returns what is currently on screen.

    The supplier's own wording is carried next to each row on purpose: the single most
    valuable check a human can make is 'does this date match what the email actually
    said', and that is impossible if the quote is somewhere else on the page."""
    rows = [{"Start (YYYY-MM-DD)": r["start"], "End (YYYY-MM-DD)": r["end"],
             "From the email": r.get("quote", "")}
            for r in parsed.get("stop_sales", [])]
    df = pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["Start (YYYY-MM-DD)", "End (YYYY-MM-DD)", "From the email"])
    edited = st.data_editor(df, num_rows="dynamic", use_container_width=True,
                            key="ss_ranges_editor",
                            column_config={"From the email": st.column_config.TextColumn(
                                "From the email", help="The supplier's own words these dates "
                                                       "came from — check each date against it.",
                                disabled=True)})
    out = []
    for _, row in edited.iterrows():
        start = str(row.get("Start (YYYY-MM-DD)") or "").strip()
        end = str(row.get("End (YYYY-MM-DD)") or "").strip()
        if ssp._ISO_DATE.match(start) and ssp._ISO_DATE.match(end):
            out.append({"start": start, "end": end})
    return out


def render_stop_sales_tool(client) -> None:
    st.header("📧 Stop Sales Email Reader")
    st.caption("Paste a supplier's stop-sale email. The tool reads the dates, matches the product, "
              "shows you exactly what would change, and blocks the dates only after you confirm. "
              "New blocks are **added to** what is already there — nothing live is ever removed.")

    if not platform_store.is_durable():
        st.warning("⚠️ No `DATABASE_URL` configured, so the record of which emails have already "
                   "been processed is lost on the next redeploy — the same email could be "
                   "applied twice without warning.")

    # ---------------- Step 1: the email ----------------
    st.subheader("Step 1 — The email")
    up = st.file_uploader("Upload a .eml file (optional)", type=["eml"], key="ss_eml")
    if up is not None and _get("ss_eml_name") != up.name:
        try:
            parsed_eml = ssp.parse_eml(up.getvalue())
            st.session_state.ss_subject = parsed_eml["subject"]
            st.session_state.ss_body = parsed_eml["body"]
            st.session_state.ss_sent = parsed_eml["date"]
            st.session_state.ss_message_id = parsed_eml["message_id"]
            st.session_state.ss_eml_name = up.name
            st.rerun()
        except Exception as e:
            st.error(f"Couldn't read that .eml file: {friendly_error_message(e)}")

    subject = st.text_input("Subject", value=_get("ss_subject", ""), key="ss_subject_in")
    body = st.text_area("Email body", value=_get("ss_body", ""), height=220, key="ss_body_in",
                        placeholder="Paste the supplier's email here…")
    sent = st.text_input("Date the email was sent (optional)", value=_get("ss_sent", ""),
                         key="ss_sent_in",
                         help="Supplier emails often write dates without a year. Giving the send "
                              "date lets '12–19 August' be resolved instead of guessed.")

    st.caption("Reading a mailbox automatically (IMAP) is the obvious next step, and everything "
              "below it — parsing, matching, review, apply — works the same way when it arrives. "
              "It is left out on purpose until the supervised path has been used on real emails.")

    fingerprint = ssp.email_fingerprint(subject, body, _get("ss_message_id", ""))
    seen = already_processed(fingerprint) if body.strip() else None
    if seen:
        st.warning(f"⚠️ This email was already processed on "
                   f"{str(seen.get('applied_at', ''))[:16].replace('T', ' ')} UTC — "
                   f"{seen.get('summary', 'no details recorded')}. Re-applying it is safe "
                   f"(dates already blocked are skipped), but check it is not a duplicate first.")

    # ---------------- Step 2: parse ----------------
    st.subheader("Step 2 — Read it")
    if st.button("🔍 Parse stop sales", type="primary", disabled=not body.strip(), key="ss_parse"):
        with st.spinner("Reading the email…"):
            try:
                st.session_state.ss_parsed = ssp.extract_stop_sales_from_email(
                    body, subject=subject, sent_date=sent)
                st.session_state.ss_parsed_raw = {"subject": subject, "body": body}
                st.session_state.pop("ss_result", None)
            except Exception as e:
                st.error(f"Couldn't read this email: {friendly_error_message(e)}")
        st.rerun()

    parsed = _get("ss_parsed")
    if not parsed:
        st.stop()

    if not parsed.get("is_stop_sale"):
        st.info("No stop sale found in this email. " + (parsed.get("notes") or ""))
        st.stop()

    for warning in ssp.warnings_for(parsed):
        st.warning(warning)
    if parsed.get("notes"):
        st.caption(f"AI notes: {parsed['notes']}")

    # ---------------- Step 3: match the product ----------------
    st.subheader("Step 3 — Which product?")
    hint_bits = [b for b in (parsed.get("product_identifier"), parsed.get("product_name_hint"),
                             parsed.get("supplier_name_hint")) if b]
    if hint_bits:
        st.caption("Read from the email: " + " · ".join(f"**{b}**" for b in hint_bits))

    if _get("ss_suppliers") is None:
        with st.spinner("Loading supplier list…"):
            try:
                st.session_state.ss_suppliers = client.get_all_suppliers()
            except Exception as e:
                st.error(f"Couldn't load the supplier list: {friendly_error_message(e)}")
                st.session_state.ss_suppliers = []
    momira = [s for s in (_get("ss_suppliers") or [])
              if (s.get("commercialName") or s.get("legalName") or "").strip().lower().startswith("momira_")]
    supplier_id = None
    if momira:
        options = {f"{s.get('commercialName') or s.get('legalName')} — ID {s.get('id')}": str(s.get("id"))
                   for s in momira}
        chosen = st.selectbox("Supplier", list(options.keys()), key="ss_supplier")
        supplier_id = options[chosen]
    else:
        st.error("Could not load the supplier list from Travel Compositor.")
        with st.expander("⚠️ Emergency manual entry"):
            supplier_id = st.text_input("Supplier ID (numeric)", key="ss_supplier_manual").strip()

    default_type = parsed.get("product_type") if parsed.get("product_type") in ("ClosedTour", "Hotel") else "ClosedTour"
    product_type = st.radio("Product type", ["ClosedTour", "Hotel"], horizontal=True,
                            index=["ClosedTour", "Hotel"].index(default_type), key="ss_ptype")
    product_code = st.text_input(
        f"{'Tour' if product_type == 'ClosedTour' else 'Hotel'} code",
        value=parsed.get("product_identifier", ""), key="ss_code",
        help="The code as it exists in Travel Compositor, e.g. ASW-1 or CAI-H1.").strip()

    if st.button("🔎 Load this product", disabled=not (supplier_id and product_code), key="ss_load"):
        with st.spinner("Fetching from Travel Compositor…"):
            try:
                if product_type == "ClosedTour":
                    st.session_state.ss_product = fetch_closed_tour_options(client, supplier_id, product_code)
                else:
                    st.session_state.ss_product = {"hotel": client.get_hotel(supplier_id, product_code)}
                st.session_state.ss_product_key = (supplier_id, product_type, product_code)
            except Exception as e:
                st.session_state.ss_product = {"error": {"message": friendly_error_message(e)}}
        st.rerun()

    product = _get("ss_product")
    if product and _get("ss_product_key") != (supplier_id, product_type, product_code):
        st.info("You changed the supplier or code — press **Load this product** again.")
        product = None
    if not product:
        st.stop()
    if product.get("error") or (isinstance(product.get("hotel"), dict) and "error" in product.get("hotel", {})):
        err = product.get("error") or product.get("hotel")
        st.error(f"Couldn't load that product: {err.get('message') or err.get('error')}")
        st.stop()

    # ---------------- Step 4: review ----------------
    st.subheader("Step 4 — Check the dates, then the targets")
    with st.expander("📧 The email as received", expanded=False):
        st.text((_get("ss_parsed_raw") or {}).get("body", ""))

    st.markdown("**Proposed blocks** — edit any date before applying.")
    new_ranges = _ranges_editor(parsed)
    if not new_ranges:
        st.warning("No valid date ranges. Dates must be written as YYYY-MM-DD.")
        st.stop()

    targets: List[Dict[str, Any]] = []
    if product_type == "ClosedTour":
        options = product.get("options") or []
        if not options:
            st.error("This tour has no modalities, so there is nothing to block. Stop sales live "
                     "on a tour's modalities, not on the tour itself.")
            st.stop()
        codes = [o.get("code") for o in options]
        default = [c for c in codes if c and parsed.get("affected_modality")
                   and c.lower() == parsed["affected_modality"].lower()] or codes
        picked = st.multiselect("Which modalities does this block?", codes, default=default,
                                key="ss_modalities",
                                help="The email named one if it could be identified; otherwise all "
                                     "modalities are selected, because a closure usually applies to "
                                     "the whole tour.")
        targets = [o for o in options if o.get("code") in picked]
        for opt in targets:
            live = existing_tour_stop_sales(opt)
            with st.expander(f"{opt.get('code')} — {len(live)} block(s) already live"):
                if opt.get("_fetch_error"):
                    st.error("This modality couldn't be fetched, so its existing blocks are "
                             "unknown. Applying to it would risk overwriting them — it is "
                             "excluded from Apply.")
                elif live:
                    st.dataframe(pd.DataFrame(live), use_container_width=True)
                else:
                    st.caption("Nothing blocked yet.")
        targets = [o for o in targets if not o.get("_fetch_error")]
    else:
        hotel = product.get("hotel") or {}
        rates = hotel.get("rates") or []
        rooms = [r.get("name") for r in (hotel.get("rooms") or []) if r.get("name")]
        if not rates:
            st.error("This hotel has no rates, and a hotel's stop sales live inside a rate. "
                     "Add a rate in Travel Compositor first.")
            st.stop()
        rate_labels = {f"{r.get('name') or '(unnamed)'} (id {r.get('id')})": r for r in rates}
        picked_rates = st.multiselect("Which rate(s)?", list(rate_labels.keys()),
                                      default=list(rate_labels.keys()), key="ss_rates",
                                      help="A closure normally applies to every rate on the "
                                           "property; narrow it only if the email says so.")
        default_rooms = ([parsed["affected_room"]] if parsed.get("affected_room") in rooms
                         else rooms)
        picked_rooms = st.multiselect("Which room type(s)?", rooms, default=default_rooms,
                                      key="ss_rooms",
                                      help="Blocking the whole property means blocking every room "
                                           "type. The email named one only if it said so.")
        if not picked_rooms:
            st.warning("Choose at least one room type — a hotel stop sale is stored per room.")
            st.stop()
        st.info("ℹ️ Hotel stop sales are submitted using the room NAME. Travel Compositor never "
                "returns the numeric room id, so there is nothing else to match on. This path has "
                "not yet been confirmed against the live API — check the result in Travel "
                "Compositor after the first apply.")
        targets = [rate_labels[k] for k in picked_rates]
        for rate in targets:
            live = existing_hotel_stop_sales(rate)
            with st.expander(f"{rate.get('name') or rate.get('id')} — {len(live)} block(s) already live"):
                st.dataframe(pd.DataFrame(live), use_container_width=True) if live \
                    else st.caption("Nothing blocked yet.")
        st.session_state.ss_picked_rooms = picked_rooms

    if not targets:
        st.warning("Nothing selected to apply to.")
        st.stop()

    # ---------------- Step 5: apply ----------------
    st.subheader("Step 5 — Apply")
    st.warning(f"This blocks **{len(new_ranges)} date range(s)** on **{len(targets)}** "
               f"{'modality' if product_type == 'ClosedTour' else 'rate'}(s) of live, bookable "
               f"inventory. Existing blocks are kept; these are added to them.")

    if st.button("✅ Apply stop sales to Travel Compositor", type="primary", key="ss_apply"):
        results = []
        bar = st.progress(0.0, text="Applying…")
        for i, target in enumerate(targets):
            bar.progress((i + 1) / len(targets), text=f"Updating {i + 1} of {len(targets)}…")
            if product_type == "ClosedTour":
                results.append(apply_to_tour_option(client, supplier_id, product_code,
                                                    target, new_ranges))
            else:
                results.append(apply_to_hotel_rate(client, supplier_id, product_code, target,
                                                   new_ranges, _get("ss_picked_rooms") or []))
        bar.empty()
        st.session_state.ss_result = results

        # Learn from what the human corrected. The AI's dates are compared against the ones
        # actually applied, so a supplier who always writes dates in a way that gets misread
        # is recognised next time. Recorded only on a successful apply, for the same reason
        # the upload flows only learn from what was published.
        if any(r["status"] == "updated" for r in results):
            item = {"data": {"stop_sale_dates": json.dumps(
                [r["start"] + ".." + r["end"] for r in parsed.get("stop_sales", [])])}}
            extraction_memory.prepare(supplier_id, "StopSale", item)
            item["data"]["stop_sale_dates"] = json.dumps(
                [r["start"] + ".." + r["end"] for r in new_ranges])
            extraction_memory.commit(supplier_id, "StopSale", item, product_code)
            mark_processed(fingerprint, {
                "applied_at": pd.Timestamp.utcnow().isoformat(),
                "supplier_id": supplier_id, "product_type": product_type,
                "product_code": product_code,
                "ranges": [f"{r['start']} → {r['end']}" for r in new_ranges],
                "summary": f"{len(new_ranges)} range(s) blocked on {product_code}",
            })
        st.rerun()

    results = _get("ss_result")
    if results:
        updated = [r for r in results if r["status"] == "updated"]
        unchanged = [r for r in results if r["status"] == "unchanged"]
        failed = [r for r in results if r["status"] == "failed"]
        if updated:
            st.success(f"✅ Blocked on {len(updated)} target(s): "
                       + ", ".join(str(r["code"]) for r in updated))
        if unchanged:
            st.info(f"➖ {len(unchanged)} target(s) already had every one of these dates blocked.")
        if failed:
            st.error(f"❌ {len(failed)} target(s) failed — nothing was changed on these:")
            for r in failed:
                st.write(f"- **{r['code']}**: {r['detail']}")
            st.caption("Re-running is safe: dates already blocked are detected and skipped.")
        if st.button("🆕 Read another email", key="ss_new"):
            _reset_run(keep=("ss_suppliers",))
            st.rerun()
