"""
bulk_notes.py — write one piece of text into every existing service of a supplier.

THE JOB: "the pickup point for all Masons transfers moved to the new terminal" and "this
supplier's cancellation terms changed" are facts about services that are ALREADY LIVE in
Travel Compositor and already bookable. Attaching text to future uploads does nothing for
them. This module fetches every existing service of one supplier and one product type,
writes the text into a chosen field, and pushes them back.

WHAT MAKES THIS DIFFERENT FROM EVERYTHING ELSE IN THE PLATFORM: every other flow publishes
one service that a human has just reviewed on screen. This one changes dozens at once,
sight unseen, in inventory customers can book, and Travel Compositor has no undo. The
design follows from that:

  * NOTHING IS WRITTEN WITHOUT A PREVIEW. plan() does every read, computes the exact
    before/after for each service, and returns it. apply() only executes a plan a human
    has already seen. The two are separate functions precisely so it is impossible to
    write first and show afterwards.

  * APPEND IS THE DEFAULT AND REPLACE IS EXPLICIT. A service's description was extracted
    from a supplier's contract; overwriting it silently destroys the only copy. Appending
    keeps it. Replace exists because a superseded cancellation policy genuinely should not
    sit next to its replacement - but it is a deliberate choice, never a default.

  * RE-SENDING THE SAME TEXT IS A NO-OP. The single most likely mistake is pressing Send
    twice, or sending a note in June that was already sent in May. Every service whose
    field already contains the text is marked "unchanged" and skipped, so the note cannot
    accumulate three times on one voucher.

  * A SERVICE IS ONLY EVER SENT BACK WHOLE. Each update PUTs the complete record that was
    just fetched, with one field changed. Travel Compositor's PUTs overwrite the resource,
    so constructing a partial payload would blank every field left out of it.

FIELD AVAILABILITY IS NOT UNIFORM, and pretending otherwise would silently do nothing:
Transport has only a name and a description - no voucher remarks, no included/excluded.
Included/Excluded exist on ClosedTour and Ticket only. Itinerary is ClosedTour only. Hotel
stores its text as {language, description} lists rather than per-language datasheets.
available_targets() is the single source of truth for what can actually be written where.
"""
import copy
from typing import Any, Callable, Dict, List, Optional, Tuple

import builder
import price_validity

# How a product type's text is stored.
#   "datasheets"       -> record["datasheets"] = {"EN": {...}, "DE": {...}}
#   "translation_list" -> record[field] = [{"language": "EN", "description": "..."}]
SHAPE_DATASHEETS = "datasheets"
SHAPE_TRANSLATION_LIST = "translation_list"

# Per product type: how to enumerate, fetch, update, and where the text lives.
#
# full_in_list says whether the list endpoint already returns complete records. Transfers,
# transports and tickets do, so a bulk run costs one request; hotels return summaries, so
# each one has to be fetched individually before it can be safely PUT back.
PRODUCTS: Dict[str, Dict[str, Any]] = {
    "Transfer": {
        "list_fn": "get_transfers", "list_keys": ("transfer",), "id_field": "id",
        "fetch_fn": "get_transfer", "update_fn": "update_transfer",
        "full_in_list": True, "shape": SHAPE_DATASHEETS,
    },
    "Transport": {
        "list_fn": "get_transports", "list_keys": ("transport",), "id_field": "id",
        "fetch_fn": "get_transport", "update_fn": "update_transport",
        "full_in_list": True, "shape": SHAPE_DATASHEETS,
    },
    "Ticket": {
        "list_fn": "get_tickets", "list_keys": ("tickets", "ticket"), "id_field": "code",
        "fetch_fn": "get_ticket", "update_fn": "update_ticket",
        "full_in_list": True, "shape": SHAPE_DATASHEETS, "paginated": True,
    },
    "Hotel": {
        "list_fn": "get_hotels", "list_keys": ("hotel",), "id_field": "providerCode",
        "fetch_fn": "get_hotel", "update_fn": "update_hotel",
        "full_in_list": False, "shape": SHAPE_TRANSLATION_LIST,
    },
    "ClosedTour": {
        # No list endpoint exists for closed tours, so they cannot be enumerated - the codes
        # have to be supplied by a human. Everything else works identically.
        "list_fn": None, "list_keys": (), "id_field": "code",
        "fetch_fn": "get_closed_tour", "update_fn": "update_closed_tour",
        # list_services() fetches each tour in full by code, so plan() must not fetch again.
        "full_in_list": True, "shape": SHAPE_DATASHEETS,
    },
}

# The human-facing target list, per product type: label -> field name in the record.
# Only fields that genuinely exist are listed. A target offered but silently unwritable
# would be worse than one that isn't offered at all.
TARGETS: Dict[str, Dict[str, str]] = {
    "ClosedTour": {
        "Remark": "remarksDescription",
        "Description (bottom)": "description",
        "Voucher remarks": "voucherRemarks",
        "Included (bottom)": "included",
        "Excluded (bottom)": "excluded",
        "Cancellation update": "voucherRemarks",
    },
    "Ticket": {
        "Description (bottom)": "description",
        "Voucher remarks": "voucherRemarks",
        "Included (bottom)": "includes",
        "Excluded (bottom)": "excludes",
        "Meeting point": "meetingPoint",
        "Cancellation update": "voucherRemarks",
    },
    "Transfer": {
        "Description (bottom)": "description",
        "Voucher remarks": "voucherRemarks",
        "Pickup description": "pickupDescription",
        "Cancellation update": "voucherRemarks",
    },
    "Transport": {
        # CONFIRMED PRODUCT-OWNER REQUEST (2026-09-08): "Voucher remarks" must be selectable
        # here too, for bulk uploads - Transport genuinely has no separate voucherRemarks
        # field in Travel Compositor (only name + description), same reason "Cancellation
        # update" already points at "description" here and the same reason price_validity.py's
        # "(YYYYMMDD)" code goes into Transport's description rather than a remarks field - so
        # this is the same target as "Description (bottom)" under a label a human looking for
        # "Voucher remarks" (as every other product type calls it) will actually find.
        "Description (bottom)": "description",
        "Voucher remarks": "description",
        "Cancellation update": "description",
    },
    "Hotel": {
        "Description (bottom)": "descriptions",
        "Voucher remarks": "voucherRemarks",
        "Cancellation update": "voucherRemarks",
    },
}

# Why a target a human might look for is missing, so the UI can say so rather than leaving
# them hunting for an option that was never there.
UNAVAILABLE_REASON: Dict[str, Dict[str, str]] = {
    "Transport": {
        "Included (bottom)": "Included/Excluded exist on ClosedTour and Ticket only.",
        "Excluded (bottom)": "Included/Excluded exist on ClosedTour and Ticket only.",
        "Remark": "Transport has no separate remark field.",
    },
    "Transfer": {
        "Included (bottom)": "Included/Excluded exist on ClosedTour and Ticket only.",
        "Excluded (bottom)": "Included/Excluded exist on ClosedTour and Ticket only.",
        "Remark": "Transfer has no separate remark field — use Voucher remarks.",
    },
    "Hotel": {
        "Included (bottom)": "A hotel contract has no included/excluded fields.",
        "Excluded (bottom)": "A hotel contract has no included/excluded fields.",
        "Remark": "A hotel contract has no separate remark field — use Voucher remarks.",
    },
    "Ticket": {
        "Remark": "A ticket's remarks live per modality, not on the ticket itself — "
                  "use Voucher remarks.",
    },
    "ClosedTour": {
        "Itinerary (bottom)": "Itinerary text is per day, so it can't be appended in bulk "
                              "to a tour as a whole.",
    },
}

MODE_APPEND = "append"
MODE_REPLACE = "replace"
# CONFIRMED PRODUCT-OWNER REQUEST (2026-09-10): "add to all Transport from supplier
# MOMIRA_EG_FT in the Voucher the code (20270430) - if there is already a code, the update has
# to change ONLY this code." Neither append (would pile up a second "(YYYYMMDD)" alongside the
# old one) nor replace (would wipe the rest of the field's text) does this - this mode instead
# calls price_validity.with_price_validity_code, which strips any existing "(YYYYMMDD)" code
# and appends the new one, leaving everything else in the field untouched. `text` for this mode
# is the ISO date the code should encode ("2027-04-30"), not literal text to add.
MODE_PRICE_CODE = "price_code"


# ----------------------------------------------------------------------
# Structured targets: Supplement / Additional Service
#
# CONFIRMED PRODUCT-OWNER REQUEST (2026-08-14): "is there a chance we can include a
# Supplement for ClosedTour... Supplement for Transfers... Additional Services for
# Transfers" in this same bulk tool. Unlike everything above, these are not one block of
# text - they're structured records (name + price/percentage + validity + mandatory/
# on-request flags) that get ADDED as a new list entry, never appended into or replacing an
# existing string. So this is a parallel mechanism, not a new TARGETS entry: plan_structured
# builds one new supplements/additionalServices entry via the SAME builder.py functions the
# single-service upload flows already use (build_supplement_vos /
# build_transfer_supplement_vos / build_transfer_additional_service_vos), so a bulk-added
# entry is never a hand-rolled approximation that could drift from the real shape.
#
# Hotel is deliberately absent here: a Hotel supplement already has its own dedicated, more
# carefully gated bulk-safe path (there is no such thing as "add this supplement to every
# hotel of a supplier" as a product-owner-confirmed request yet) - this only covers what was
# actually asked for.
#
# TRANSPORT (product owner, 2026-09-09): "Is the App able to [add a dated price supplement,
# e.g. Christmas/NYE/Easter, to every Transport of a supplier, computed as (price + already
# existing supplement) * x% or a flat x]?" ContractTransportVO itself has NO supplements
# field (see build_transport_payloads' own confirmed note) - but each Option sub-resource
# (one per occupancy bracket) carries a `prices` list of DATED, ADDITIVE surcharge entries
# (ContractTransportOptionPriceVO: startDate/endDate + adultPriceSupplement etc, additive on
# top of the parent's baseAdultPrice - confirmed real semantics). "transport_supplement"
# is a NEW ADDED entry in that list per bracket, same append-only philosophy as every other
# structured target here - never edits or replaces an existing price entry. It is planned and
# applied by _plan_transport_supplement/_apply_transport_supplement below instead of the
# generic single-field-append path, because it operates at Option (not Transport) level and
# Travel Compositor has no native PERCENT type for Transport pricing (unlike Transfer) - the
# percent-of-(price+existing supplement) math has to be done by the app itself, per bracket,
# before writing.
STRUCTURED_TARGETS: Dict[str, Dict[str, str]] = {
    "ClosedTour": {"Supplement (applies to all Modalities)": "closedtour_supplement"},
    "Transfer": {"Supplement": "transfer_supplement", "Additional Service": "transfer_additional_service"},
    "Transport": {"Price supplement (dated, e.g. Christmas/NYE/Easter)": "transport_supplement"},
}

# Which list field on the live record each structured kind writes into.
STRUCTURED_FIELD: Dict[str, str] = {
    "closedtour_supplement": "supplements",
    "transfer_supplement": "supplements",
    "transfer_additional_service": "additionalServices",
}


def available_structured_targets(product_type: str) -> List[str]:
    return list(STRUCTURED_TARGETS.get(product_type, {}).keys())


def _safe_float(value, fallback=0.0):
    try:
        if value is None or value == "":
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _today_iso():
    import datetime
    return datetime.date.today().isoformat()


def _existing_transfer_supplement_total(record: Dict[str, Any], today: Optional[str] = None) -> float:
    """The € value of whatever mandatory surcharge is ALREADY in effect today on this transfer -
    the "already existing Price supplement" half of the product owner's formula. An ABSOLUTE
    supplement counts at its own amount; a PERCENT one is converted using the transfer's own
    basePrice (the same base Travel Compositor itself would apply it to). Only supplements whose
    own date window covers today (or carry no dates at all) count - an expired or not-yet-started
    surcharge should not silently inflate a brand-new one."""
    today = today or _today_iso()
    base = _safe_float(record.get("basePrice"))
    total = 0.0
    for s in (record.get("supplements") or []):
        if not isinstance(s, dict) or not s.get("active", True):
            continue
        start, end = s.get("startDate") or "", s.get("endDate") or "2049-12-31"
        if start and start > today:
            continue
        if end and end < today:
            continue
        amount = _safe_float(s.get("amount"))
        if str(s.get("type") or "").strip().upper() == "PERCENT":
            total += base * (amount / 100.0)
        else:
            total += amount
    return round(total, 2)


def _structured_entry_name(entry: Dict[str, Any], kind: str) -> str:
    """The name of one EXISTING supplement/additionalService entry, for the duplicate check -
    both closedtour_supplement and transfer_additional_service store it under
    translations.EN.name; transfer_supplement stores it as a plain top-level "name"."""
    if not isinstance(entry, dict):
        return ""
    if kind == "transfer_supplement":
        return str(entry.get("name") or "")
    return str(((entry.get("translations") or {}).get("EN") or {}).get("name") or "")


def _build_structured_entry(kind: str, item_data: Dict[str, Any],
                            record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One new supplements/additionalServices entry, built by the SAME code the single-
    service flows use - never a parallel hand-rolled shape. Returns None if the builder
    couldn't produce an entry (e.g. a blank name or zero amount it deliberately skips)."""
    try:
        if kind == "closedtour_supplement":
            vos = builder.build_supplement_vos([item_data])
        elif kind == "transfer_supplement":
            item_data = dict(item_data)
            # CONFIRMED PRODUCT-OWNER FORMULA (2026-09-09): "(Transport/Transfer Price +
            # Already existing Price supplement) * x% OR a flat x number", computed by the
            # app rather than left to Travel Compositor's own PERCENT semantics (which only
            # ever applies a percent to the BASE price, never to price+existing-supplement
            # together - see TransferSupplementVO's own docstring). When compute_from_
            # current_price is set, "amount"/"is_percent" are the human's raw inputs; the
            # ACTUAL value written is always ABSOLUTE, computed per-transfer here since each
            # transfer's own basePrice/existing supplements differ.
            if item_data.get("compute_from_current_price"):
                base = _safe_float(record.get("basePrice"))
                existing = _existing_transfer_supplement_total(record)
                pct = _safe_float(item_data.get("amount"))
                item_data["amount"] = (round((base + existing) * (pct / 100.0), 2)
                                       if item_data.get("is_percent") else round(pct, 2))
                item_data["type"] = "ABSOLUTE"
            vos = builder.build_transfer_supplement_vos(
                [item_data],
                transfer_start_date=record.get("startDate") or "",
                transfer_end_date=record.get("endDate") or "")
        elif kind == "transfer_additional_service":
            vos = builder.build_transfer_additional_service_vos(
                [item_data], default_currency=record.get("currency") or "EUR")
        else:
            return None
    except Exception:
        return None
    if not vos:
        return None
    return vos[0].dict()


def _active_transport_price_entry(option: Dict[str, Any], today: Optional[str] = None) -> Dict[str, Any]:
    """Which of an option's dated `prices` entries is in effect today - the "already existing
    Price supplement" for THIS bracket. Falls back to the entry with the latest endDate (the
    one most likely to represent the standing/year-round rate) when none covers today, and to
    an all-zero entry when the bracket has no price entries at all (a bracket costing exactly
    the base rate, per ContractTransportOptionPriceVO's own confirmed semantics)."""
    entries = [e for e in (option.get("prices") or []) if isinstance(e, dict)]
    if not entries:
        return {}
    today = today or _today_iso()
    for e in entries:
        start, end = e.get("startDate") or "", e.get("endDate") or "2049-12-31"
        if (not start or start <= today) and (not end or end >= today):
            return e
    return sorted(entries, key=lambda e: e.get("endDate") or "")[-1]


def _plan_transport_supplement(client, supplier_id: str, name: str, start_date: str, end_date: str,
                               is_percent: bool, amount: float,
                               progress: Optional[Callable[[int, int, str], None]] = None
                               ) -> Dict[str, Any]:
    """Transport's own plan(), kept separate from plan_structured's generic single-field-append
    path because a Transport's price lives per OPTION (occupancy bracket), not on the Transport
    record itself - see STRUCTURED_TARGETS' own comment for the full "why Transport is
    different" explanation. One item per (transport, option) bracket; each item's `record` is
    the OPTION payload apply() will PUT back (via update_transport_option, not update_transport),
    and `transport_id` carries the parent id that call needs alongside it."""
    result: Dict[str, Any] = {"items": [], "error": None, "will_change": 0, "unchanged": 0,
                              "failed": 0, "product_type": "Transport", "target": "transport_supplement",
                              "structured": True, "kind": "transport_supplement"}
    name = (name or "").strip()
    if not name:
        result["error"] = "Give the supplement a name."
        return result
    if not start_date or not end_date:
        result["error"] = "Give the supplement a start and end date."
        return result
    try:
        listing = getattr(client, "get_transports")(supplier_id)
    except Exception as e:
        result["error"] = f"Could not list transports: {type(e).__name__}: {e}"
        return result
    transports = listing.get("transport") if isinstance(listing, dict) else listing
    transports = transports or []
    total = len(transports)
    for i, t_summary in enumerate(transports):
        t_id = t_summary.get("id") if isinstance(t_summary, dict) else None
        t_name = (t_summary or {}).get("name") or t_id or "?"
        if progress:
            progress(i + 1, total, t_name)
        if not t_id:
            continue
        try:
            t = client.get_transport(supplier_id, t_id)
        except Exception as e:
            result["failed"] += 1
            result["items"].append({"id": t_id, "name": t_name, "status": "failed",
                                    "detail": f"couldn't fetch transport: {e}", "changes": {}})
            continue
        base = {
            "adult": _safe_float(t.get("baseAdultPrice")),
            "children": _safe_float(t.get("baseChildrenPrice")),
            "infant": _safe_float(t.get("baseInfantPrice")),
        }
        for code in (t.get("optionCodes") or []):
            item_id = f"{t_id}:{code}"
            try:
                opt = client.get_transport_option(supplier_id, t_id, code)
            except Exception as e:
                result["failed"] += 1
                result["items"].append({
                    "id": item_id, "name": f"{t_name} — {code}", "status": "failed",
                    "detail": f"couldn't fetch option: {e}", "changes": {},
                })
                continue
            existing_names = {_norm(e.get("name")) for e in (opt.get("prices") or [])
                              if isinstance(e, dict)
                              and e.get("startDate") == start_date and e.get("endDate") == end_date}
            bracket_label = f"{t_name} — {code} ({opt.get('minPassengers', '?')}-{opt.get('maxPassengers', '?')} pax)"
            if _norm(name) in existing_names:
                result["unchanged"] += 1
                result["items"].append({
                    "id": item_id, "name": bracket_label, "status": "unchanged", "changes": {},
                    "reason": "this bracket already has a supplement with this name for these exact dates",
                })
                continue

            active = _active_transport_price_entry(opt)
            new_entry_fields = {}
            summary_lines = []
            for field in ("adult", "children", "infant"):
                existing_supp = _safe_float(active.get(f"{field}PriceSupplement"))
                if is_percent:
                    new_supp = round((base[field] + existing_supp) * (amount / 100.0), 2)
                else:
                    new_supp = round(_safe_float(amount), 2)
                new_entry_fields[f"{field}PriceSupplement"] = new_supp
                if base[field] or existing_supp or new_supp:
                    summary_lines.append(
                        f"{field}: base {base[field]:.2f} + existing supplement {existing_supp:.2f} "
                        f"-> new supplement {new_supp:.2f} (total {base[field] + existing_supp + new_supp:.2f})")

            updated_opt = copy.deepcopy(opt)
            updated_opt["prices"] = (list(opt.get("prices") or [])) + [{
                "name": name, "startDate": start_date, "endDate": end_date,
                "adultPriceSupplement": new_entry_fields["adultPriceSupplement"],
                "childrenPriceSupplement": new_entry_fields["childrenPriceSupplement"],
                "infantPriceSupplement": new_entry_fields["infantPriceSupplement"],
                "adultRTPriceSupplement": 0.0, "childrenRTPriceSupplement": 0.0,
                "infantRTPriceSupplement": 0.0,
            }]
            result["will_change"] += 1
            result["items"].append({
                "id": item_id, "name": bracket_label, "status": "will_change",
                "changes": {"EN": ("", "\n".join(summary_lines) or f"+{amount} for {start_date}..{end_date}")},
                "record": updated_opt, "transport_id": t_id,
            })
    return result


def _apply_transport_supplement(client, supplier_id: str, planned: Dict[str, Any],
                                progress: Optional[Callable[[int, int, str], None]] = None
                                ) -> Dict[str, Any]:
    """Transport's own apply(): each pending item PUTs back one whole OPTION via
    update_transport_option(supplier_id, transport_id, payload) - the generic apply() below
    can't be reused as-is because it only knows update_fn(supplier_id, payload), with no way to
    pass the parent transport_id an option update also needs."""
    out = {"updated": [], "failed": [], "skipped": 0}
    pending = [i for i in planned.get("items", []) if i.get("status") == "will_change"]
    out["skipped"] = len(planned.get("items", [])) - len(pending)
    for n, item in enumerate(pending):
        if progress:
            progress(n + 1, len(pending), item.get("name", ""))
        try:
            res = client.update_transport_option(supplier_id, item["transport_id"], item["record"])
            if isinstance(res, dict) and "error" in res:
                out["failed"].append({"name": item.get("name"), "id": item.get("id"),
                                      "detail": str(res.get("message") or res.get("error"))})
            else:
                out["updated"].append({"name": item.get("name"), "id": item.get("id"),
                                       "languages": sorted(item.get("changes", {}).keys())})
        except Exception as e:
            out["failed"].append({"name": item.get("name"), "id": item.get("id"),
                                  "detail": f"{type(e).__name__}: {e}"})
    return out


def plan_structured(client, supplier_id: str, product_type: str, kind: str,
                    item_data: Dict[str, Any], codes: Optional[List[str]] = None,
                    progress: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]:
    """Same job as plan(), for a structured Supplement/Additional Service instead of a text
    field: work out exactly what would change, WITHOUT writing anything. A service that
    already has an entry with this same name is left alone (status "unchanged"), so pressing
    Send twice can't add the same supplement twice."""
    if kind == "transport_supplement":
        # Transport's structure (per-option pricing, no supplements field on the Transport
        # itself) doesn't fit the generic record/field-append path below - see
        # _plan_transport_supplement's own docstring.
        return _plan_transport_supplement(
            client, supplier_id, (item_data or {}).get("name", ""),
            (item_data or {}).get("start_date", ""), (item_data or {}).get("end_date", ""),
            bool((item_data or {}).get("is_percent")), _safe_float((item_data or {}).get("amount")),
            progress=progress)

    result: Dict[str, Any] = {"items": [], "error": None, "will_change": 0, "unchanged": 0,
                              "failed": 0, "product_type": product_type, "target": kind,
                              "structured": True}
    name = str((item_data or {}).get("name") or "").strip()
    if not name:
        result["error"] = "Give the supplement/service a name."
        return result
    field = STRUCTURED_FIELD.get(kind)
    if not field or kind not in STRUCTURED_TARGETS.get(product_type, {}).values():
        result["error"] = f"{kind!r} isn't available on a {product_type}."
        return result

    records, err = list_services(client, supplier_id, product_type, codes=codes)
    if err and not records:
        result["error"] = err
        return result
    result["error"] = err  # partial failures still worth surfacing

    cfg = PRODUCTS[product_type]
    total = len(records)
    for i, summary in enumerate(records):
        ident = summary.get(cfg["id_field"])
        disp_name = label_for(summary, product_type)
        if progress:
            progress(i + 1, total, disp_name)
        record = summary
        if not cfg["full_in_list"]:
            try:
                record = getattr(client, cfg["fetch_fn"])(supplier_id, ident)
            except Exception as e:
                record = {"error": type(e).__name__, "message": str(e)}
            if not isinstance(record, dict) or "error" in record:
                detail = (str(record.get("message") or record.get("error"))
                          if isinstance(record, dict) else
                          f"unexpected response type {type(record).__name__}")
                result["items"].append({"id": ident, "name": disp_name, "status": "failed",
                                        "detail": detail, "changes": {}})
                result["failed"] += 1
                continue

        existing_list = record.get(field) or []
        existing_names = {_norm(_structured_entry_name(e, kind))
                          for e in existing_list if isinstance(e, dict)}
        if _norm(name) in existing_names:
            result["unchanged"] += 1
            result["items"].append({
                "id": ident, "name": label_for(record, product_type), "status": "unchanged",
                "changes": {}, "reason": "this service already has an entry with this name",
            })
            continue

        new_entry = _build_structured_entry(kind, item_data, record)
        if new_entry is None:
            result["failed"] += 1
            result["items"].append({
                "id": ident, "name": label_for(record, product_type), "status": "failed",
                "detail": "couldn't build this entry (check the name/amount)", "changes": {},
            })
            continue

        updated = copy.deepcopy(record)
        current = updated.get(field)
        updated[field] = (list(current) if isinstance(current, list) else []) + [new_entry]
        result["will_change"] += 1
        result["items"].append({
            "id": ident, "name": label_for(record, product_type), "status": "will_change",
            "changes": {"EN": ("", name)}, "record": updated,
        })
    return result


def available_targets(product_type: str) -> List[str]:
    return list(TARGETS.get(product_type, {}).keys())


def unavailable_targets(product_type: str) -> Dict[str, str]:
    return dict(UNAVAILABLE_REASON.get(product_type, {}))


def needs_manual_codes(product_type: str) -> bool:
    """True when the platform cannot enumerate this product type and a human must supply
    the codes. ClosedTour only - Travel Compositor exposes no list endpoint for it."""
    return PRODUCTS.get(product_type, {}).get("list_fn") is None


# ----------------------------------------------------------------------
# Reading and writing one field on one record
# ----------------------------------------------------------------------
def _norm(text: Any) -> str:
    return " ".join(str(text or "").split()).strip().lower()


# Fields that are a LIST of bullet strings rather than one block of text. A Ticket's
# includes/excludes are List[str] (schemas.TicketDatasheetEN) - treating them as text would
# stringify the Python list into the field, so "Lunch", "Guide" would be PUT back as the
# literal characters ['Lunch', 'Guide'] and the structure would be gone.
LIST_FIELDS = {"includes", "excludes", "facilities", "languageOptions"}


def _is_list_field(product_type: str, target: str) -> bool:
    return TARGETS.get(product_type, {}).get(target) in LIST_FIELDS


def read_field(record: Dict[str, Any], product_type: str, target: str) -> Dict[str, Any]:
    """The field's current value, per language. Strings for text fields, lists for list
    fields - deliberately NOT coerced, so a caller can't accidentally flatten a list."""
    field = TARGETS.get(product_type, {}).get(target)
    if not field or not isinstance(record, dict):
        return {}
    shape = PRODUCTS[product_type]["shape"]
    if shape == SHAPE_DATASHEETS:
        sheets = record.get("datasheets") or {}
        if not isinstance(sheets, dict):
            return {}
        out = {}
        for lang, sheet in sheets.items():
            if not isinstance(sheet, dict):
                continue
            value = sheet.get(field)
            out[lang] = list(value) if isinstance(value, list) else str(value or "")
        return out
    entries = record.get(field) or []
    if not isinstance(entries, list):
        return {}
    return {str(e.get("language") or "EN"): str(e.get("description") or "")
            for e in entries if isinstance(e, dict)}


def _contains(existing: Any, text: str) -> bool:
    """Is this note already present?

    Compares whole blocks, not raw substrings. CONFIRMED REAL BUG (audit): a plain
    substring test made any note that happens to be a fragment of existing wording look
    already-published - "All pickups depart from the new terminal." was silently dropped
    because the field said "Until 30 June, all pickups depart from the new terminal.".
    The screen then reported "0 would change, already there" and offered no Send, so an
    operator would reasonably conclude the note was live when it was not."""
    needle = _norm(text)
    if not needle:
        return False
    if isinstance(existing, list):
        return any(_norm(item) == needle for item in existing)
    blocks = [_norm(b) for b in str(existing or "").split("\n\n")]
    return needle in blocks


def combine(existing: Any, text: str, mode: str):
    """The new value for one language, preserving the field's own type.

    Append puts the new text at the bottom - a new block for a text field, a new entry for
    a list field - which is what "Description (bottom)" means and keeps the supplier's own
    wording first. Replace returns only the new text. Either way a note already present is
    left alone, so pressing Send twice cannot print it twice.

    MODE_PRICE_CODE is a third, surgical mode: `text` is an ISO date, and the result strips
    whatever "(YYYYMMDD)" code is already in `existing` (if any) and appends the new one -
    every other word in the field is carried through unchanged. Never applies to a list field
    (the price-validity code only ever lives in a plain text field - see price_validity.py)."""
    if mode == MODE_PRICE_CODE:
        return price_validity.with_price_validity_code(
            str(existing or ""), {"price_valid_until_date": text})
    text = (text or "").strip()
    if isinstance(existing, list):
        if not text:
            return list(existing)
        if mode == MODE_REPLACE:
            return [text]
        return list(existing) if _contains(existing, text) else list(existing) + [text]
    existing = existing or ""
    if not text:
        return existing
    if mode == MODE_REPLACE:
        return text
    if _contains(existing, text):
        return existing
    if not str(existing).strip():
        return text
    return f"{str(existing).rstrip()}\n\n{text}"


def write_field(record: Dict[str, Any], product_type: str, target: str,
                text: str, mode: str) -> Tuple[Dict[str, Any], Dict[str, Tuple[str, str]]]:
    """Returns (new record, {language: (before, after)}) without touching the original.

    Writes into EVERY language the record already has, not just EN. A note that only
    reaches the English voucher is invisible to exactly the customers a German or French
    voucher is for. The text stays in whatever language it was typed until the Translate
    tool runs - and because the English text changed, that tool will see the change and
    re-translate the rest."""
    field = TARGETS.get(product_type, {}).get(target)
    updated = copy.deepcopy(record)
    changes: Dict[str, Tuple[str, str]] = {}
    if not field:
        return updated, changes

    shape = PRODUCTS[product_type]["shape"]
    if shape == SHAPE_DATASHEETS:
        sheets = updated.get("datasheets")
        if not isinstance(sheets, dict) or not sheets:
            # Nothing to write into - a record with no datasheets at all is reported as
            # unchanged rather than being given one, since inventing a datasheet would
            # publish a service shape nobody reviewed.
            return updated, changes
        for lang, sheet in sheets.items():
            if not isinstance(sheet, dict):
                continue
            raw = sheet.get(field)
            before = list(raw) if isinstance(raw, list) else str(raw or "")
            if _is_list_field(product_type, target) and not isinstance(before, list):
                # The schema says this field is a list; an existing scalar (or absent
                # value) is normalised rather than concatenated, so the PUT still sends
                # the shape Travel Compositor expects.
                before = [before] if str(before).strip() else []
            after = combine(before, text, mode)
            if after != before:
                sheet[field] = after
                changes[lang] = (before, after)
        return updated, changes

    entries = updated.get(field)
    if not isinstance(entries, list) or not entries:
        # A hotel with no description list yet: start one in English rather than skipping,
        # since there is no existing text that could be destroyed.
        after = combine("", text, mode)
        if after:
            updated[field] = [{"language": "EN", "description": after}]
            changes["EN"] = ("", after)
        return updated, changes
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        lang = str(entry.get("language") or "EN")
        before = str(entry.get("description") or "")
        after = combine(before, text, mode)
        if after != before:
            entry["description"] = after
            changes[lang] = (before, after)
    return updated, changes


def label_for(record: Dict[str, Any], product_type: str) -> str:
    """A human-recognisable name for one service, for the preview list."""
    if not isinstance(record, dict):
        return "(unnamed)"
    for direct in ("hotelname", "name", "commercialName"):
        if record.get(direct):
            return str(record[direct])
    sheets = record.get("datasheets") or {}
    if isinstance(sheets, dict):
        for lang in ("EN",) + tuple(sheets.keys()):
            sheet = sheets.get(lang)
            if isinstance(sheet, dict) and sheet.get("name"):
                return str(sheet["name"])
    ident = record.get(PRODUCTS.get(product_type, {}).get("id_field") or "id")
    return str(ident or "(unnamed)")


# ----------------------------------------------------------------------
# Enumerate
# ----------------------------------------------------------------------
def list_services(client, supplier_id: str, product_type: str,
                  codes: Optional[List[str]] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Every existing service of this supplier and type. Returns (records, error)."""
    cfg = PRODUCTS.get(product_type)
    if not cfg:
        return [], f"Unknown product type {product_type!r}"

    if cfg["list_fn"] is None:
        # ClosedTour: no list endpoint, so a human supplies the codes.
        records, failures = [], []
        for code in (codes or []):
            code = (code or "").strip()
            if not code:
                continue
            try:
                rec = getattr(client, cfg["fetch_fn"])(supplier_id, code)
            except Exception as e:
                failures.append(f"{code} ({type(e).__name__}: {e})")
                continue
            if not isinstance(rec, dict) or "error" in rec:
                detail = (rec.get("message", rec.get("error")) if isinstance(rec, dict)
                          else f"unexpected response type {type(rec).__name__}")
                failures.append(f"{code} ({detail})")
                continue
            records.append(rec)
        return records, ("Couldn't fetch: " + ", ".join(failures)) if failures else None

    try:
        if cfg.get("paginated"):
            records, first = [], 0
            while True:
                page = getattr(client, cfg["list_fn"])(supplier_id, first=first, limit=100)
                if isinstance(page, dict) and "error" in page:
                    return records, str(page.get("message") or page.get("error"))
                batch = _items_from(page, cfg["list_keys"])
                if not batch:
                    break
                records.extend(batch)
                first += len(batch)
                total = None
                if isinstance(page, dict):
                    pag = page.get("pagination") or {}
                    if isinstance(pag, dict):
                        total = pag.get("totalResults") or pag.get("total")
                if total is not None and first >= int(total):
                    break
                if not batch or first > 5000:
                    break
            return records, None
        data = getattr(client, cfg["list_fn"])(supplier_id)
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    if isinstance(data, dict) and "error" in data:
        return [], str(data.get("message") or data.get("error"))
    return _items_from(data, cfg["list_keys"]), None


def _items_from(data: Any, keys) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for key in tuple(keys) + ("items", "data", "results", "content"):
            if isinstance(data.get(key), list):
                return [d for d in data[key] if isinstance(d, dict)]
    return []


# ----------------------------------------------------------------------
# Plan (reads only) and apply (writes)
# ----------------------------------------------------------------------
def plan(client, supplier_id: str, product_type: str, target: str, text: str,
         mode: str = MODE_APPEND, codes: Optional[List[str]] = None,
         progress: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]:
    """Work out exactly what would change, WITHOUT writing anything.

    This function must never call an update endpoint. It exists so a person can see the
    full list, the count, and the before/after of the text before a single live service is
    touched."""
    result: Dict[str, Any] = {"items": [], "error": None, "will_change": 0,
                              "unchanged": 0, "failed": 0,
                              "product_type": product_type, "target": target,
                              "mode": mode, "text": text}
    if not (text or "").strip():
        result["error"] = "No text to add."
        return result
    if target not in TARGETS.get(product_type, {}):
        result["error"] = (f"{target!r} can't be written on a {product_type} — "
                           f"{unavailable_targets(product_type).get(target, 'no such field')}")
        return result

    records, err = list_services(client, supplier_id, product_type, codes=codes)
    if err and not records:
        result["error"] = err
        return result
    result["error"] = err  # partial failures still worth surfacing

    cfg = PRODUCTS[product_type]
    total = len(records)
    for i, summary in enumerate(records):
        ident = summary.get(cfg["id_field"])
        name = label_for(summary, product_type)
        if progress:
            progress(i + 1, total, name)
        record = summary
        if not cfg["full_in_list"]:
            # Wrapped: one unreachable service out of forty must not abort the whole
            # preview with a traceback. Without this the page crashed AND left the previous
            # plan armed, so the next Send would have pushed a stale snapshot.
            try:
                record = getattr(client, cfg["fetch_fn"])(supplier_id, ident)
            except Exception as e:
                record = {"error": type(e).__name__, "message": str(e)}
            if not isinstance(record, dict) or "error" in record:
                detail = (str(record.get("message") or record.get("error"))
                          if isinstance(record, dict) else
                          f"unexpected response type {type(record).__name__}")
                result["items"].append({"id": ident, "name": name, "status": "failed",
                                        "detail": detail, "changes": {}})
                result["failed"] += 1
                continue
        updated, changes = write_field(record, product_type, target, text, mode)
        status = "will_change" if changes else "unchanged"
        result[status] += 1
        result["items"].append({
            "id": ident, "name": label_for(record, product_type), "status": status,
            "changes": changes, "record": updated,
            "reason": "" if changes else _unchanged_reason(record, product_type, target, text, mode),
        })
    return result


def _unchanged_reason(record, product_type, target, text, mode) -> str:
    current = read_field(record, product_type, target)
    if not current:
        return "this service has no datasheet to write into"
    if mode == MODE_PRICE_CODE:
        return "this service's price-validity code already encodes this exact date"
    if any(_contains(v, text) for v in current.values()):
        return "the same text is already there"
    return "nothing to change"


def apply(client, supplier_id: str, planned: Dict[str, Any],
          progress: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]:
    """Push a plan a human has already seen. Only items marked will_change are sent.

    Each service is PUT back whole, exactly as fetched with one field changed. Failures are
    collected rather than raised: with forty services in flight, one rejection must not
    hide the thirty-nine that succeeded or leave a person unsure which is which."""
    if planned.get("kind") == "transport_supplement":
        # Each item is one OPTION, not one Transport - needs transport_id alongside the
        # payload, which the generic update_fn(supplier_id, payload) call below has no way
        # to pass. See _apply_transport_supplement's own docstring.
        return _apply_transport_supplement(client, supplier_id, planned, progress=progress)
    product_type = planned.get("product_type")
    cfg = PRODUCTS.get(product_type) or {}
    update_fn = getattr(client, cfg.get("update_fn", ""), None)
    out = {"updated": [], "failed": [], "skipped": 0}
    if not update_fn:
        out["failed"].append({"name": "-", "detail": f"No update endpoint for {product_type}"})
        return out

    pending = [i for i in planned.get("items", []) if i.get("status") == "will_change"]
    out["skipped"] = len(planned.get("items", [])) - len(pending)
    for n, item in enumerate(pending):
        if progress:
            progress(n + 1, len(pending), item.get("name", ""))
        try:
            res = update_fn(supplier_id, item["record"])
            if isinstance(res, dict) and "error" in res:
                out["failed"].append({"name": item.get("name"), "id": item.get("id"),
                                      "detail": str(res.get("message") or res.get("error"))})
            else:
                out["updated"].append({"name": item.get("name"), "id": item.get("id"),
                                       "languages": sorted(item.get("changes", {}).keys())})
        except Exception as e:
            out["failed"].append({"name": item.get("name"), "id": item.get("id"),
                                  "detail": f"{type(e).__name__}: {e}"})
    return out
