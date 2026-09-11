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
        # CORRECTED (2026-09-10, real production evidence): "Voucher remarks" used to be
        # aliased to "description" here, on the belief that Transport had no separate
        # voucherRemarks field in Travel Compositor. A real screenshot of Travel Compositor's
        # own Transport edit screen proved that wrong - it has a genuine, separate Voucher
        # remarks input, same as every other product type (see schemas.py's
        # TransportDataSheetVO.voucherRemarks). This bug sent a real bulk price-validity-code
        # write into the WRONG field for all 168 Transports of one supplier before it was
        # caught - see bulk_notes._plan_transport_voucher_code_repair for the one-off fix that
        # moves an already-mis-written code back to the right field.
        # "Cancellation update" is left pointing at "description" deliberately - that is a
        # separate, unrelated rule (see builder.py's own comment: Transport's cancellation/
        # conditions text is locked whole to description on every update, code or no code).
        "Description (bottom)": "description",
        "Voucher remarks": "voucherRemarks",
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
# top of the parent's baseAdultPrice - confirmed real semantics). "transport_supplement" adds
# a NEW entry per bracket for the peak period - but, CORRECTED (2026-09-10, product owner):
# unlike every other structured target here, this is NOT a simple append. Each bracket
# (modality) can have several occupancy options (e.g. Sedan 1-3pax, Hiace 1-8pax), each with
# its own independent price and its own independent standing (always-on) supplement entry, and
# a bracket's price entries must never overlap in date the way Travel Compositor's own UI never
# lets them. So adding a peak-season markup on top of an existing standing rate means: truncate
# the standing entry to end the day before the peak starts, insert the new peak entry, and
# insert a fresh "resume normal" entry the day after the peak ends (carrying the standing
# entry's own original supplement, unchanged) - see
# _carve_transport_option_price_window's own docstring for the full carve logic. It is planned
# and applied by _plan_transport_supplement/_apply_transport_supplement below instead of the
# generic single-field-append path, because it operates at Option (not Transport) level and
# Travel Compositor has no native PERCENT type for Transport pricing (unlike Transfer) - the
# percent-of-(price+existing supplement) math has to be done by the app itself, per bracket,
# before writing.
STRUCTURED_TARGETS: Dict[str, Dict[str, str]] = {
    "ClosedTour": {"Supplement (applies to all Modalities)": "closedtour_supplement"},
    "Transfer": {"Supplement": "transfer_supplement", "Additional Service": "transfer_additional_service"},
    "Transport": {
        "Price supplement (dated, e.g. Christmas/NYE/Easter)": "transport_supplement",
        "Permanent price increase (% or flat amount)": "transport_price_increase",
        # One-off repair (2026-09-10) for the 168 Transports the earlier wrong TARGETS mapping
        # bulk-wrote a price-validity code into description instead of voucherRemarks - see
        # _plan_transport_voucher_code_repair's own docstring.
        "Repair: move a price-validity code from Description to Voucher remarks": "transport_voucher_code_repair",
        # One-off repair (2026-09-11, product owner: "I just want to move this phrase
        # 'Cancellation Policy: ...' from Description to Voucher remark") - see
        # _plan_transport_cancellation_text_repair's own docstring.
        "Repair: move cancellation policy text from Description to Voucher remarks": "transport_cancellation_text_repair",
    },
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


_TRANSPORT_BASE_PRICE_FIELDS = (
    "baseAdultPrice", "baseChildrenPrice", "baseInfantPrice",
    "baseAdultRTPrice", "baseChildrenRTPrice", "baseInfantRTPrice",
)
_TRANSPORT_SUPPLEMENT_PRICE_FIELDS = (
    "adultPriceSupplement", "childrenPriceSupplement", "infantPriceSupplement",
    "adultRTPriceSupplement", "childrenRTPriceSupplement", "infantRTPriceSupplement",
)


def _plan_transport_price_increase(client, supplier_id: str, amount: float, increase_base: bool,
                                   increase_supplement: bool, is_percent: bool = True,
                                   progress: Optional[Callable[[int, int, str], None]] = None
                                   ) -> Dict[str, Any]:
    """CONFIRMED PRODUCT-OWNER REQUEST (2026-09-10): "Update existing price by percentage...
    current price is 60 USD and we want to just add 10%... all the prices for the transport
    must be automatically calculated and the new price must be added to travel compositor." A
    PERMANENT price rise, unlike _plan_transport_supplement's dated, additive surcharge - and,
    per the product owner's own follow-up, the human decides EACH RUN whether the % applies to
    the base price, to whatever supplement is currently active, or to both together (never
    assumed): "Just one of each or both. Human must decide."

    EXTENDED, same day (product owner): "I want that the app can do that. Human shall select
    bulk price transport update, select supplier, adds manually amount of percentage or
    absolute number and this will be added to the already existing base price." `is_percent`
    picks between the two: True multiplies by (1 + amount/100) (the original behavior, still the
    default), False simply adds `amount` to whatever the field already holds - same ABSOLUTE-vs-
    PERCENT choice already offered on the dated transport_supplement target, kept symmetrical.

    Two independent kinds of write can result from one run, so each item carries its own
    `write_kind` for apply() to dispatch on, rather than the whole plan being one kind the way
    _plan_transport_supplement's is:
      - "transport": the base*Price fields on the Transport's OWN record (parent level) - or,
        for a per-vehicle transport (pricePerPax=False), its `vehiclePrice` field instead (see
        the per_pax branch below - same fix as _plan_transport_supplement's own per-vehicle
        handling, confirmed needed the same day: "priceperpax must also work, therefore both
        options must work") - one item per Transport, only when increase_base is set.
      - "transport_option": the CURRENTLY ACTIVE price entry's supplement fields, mutated IN
        PLACE (never appended - this raises what's already there, it does not add a new dated
        entry the way _plan_transport_supplement does) - one item per bracket, only when
        increase_supplement is set and that bracket actually has an active entry with a
        nonzero supplement to raise.
    A field already at 0 is left at 0 in BOTH modes - for percent this is naturally a no-op
    (0 * anything is still 0); for absolute it's a deliberate choice (a base field genuinely at
    0 means "not priced/not offered", e.g. a route with no infant price - adding a flat amount
    to it would silently start charging for something that was intentionally free/absent,
    which is not what "raise what's already there" means)."""
    result: Dict[str, Any] = {"items": [], "error": None, "will_change": 0, "unchanged": 0,
                              "failed": 0, "product_type": "Transport",
                              "target": "transport_price_increase", "structured": True,
                              "kind": "transport_price_increase"}
    if not increase_base and not increase_supplement:
        result["error"] = "Choose at least one: increase the base price, the active supplement, or both."
        return result
    if not amount:
        result["error"] = ("Give a percentage greater than 0." if is_percent
                           else "Give an amount greater than 0.")
        return result
    try:
        listing = getattr(client, "get_transports")(supplier_id)
    except Exception as e:
        result["error"] = f"Could not list transports: {type(e).__name__}: {e}"
        return result
    transports = listing.get("transport") if isinstance(listing, dict) else listing
    transports = transports or []
    total = len(transports)

    def _raise(old: float) -> float:
        return round(old * (1.0 + amount / 100.0), 2) if is_percent else round(old + amount, 2)
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
            result["items"].append({"id": f"{t_id}:base", "name": t_name, "status": "failed",
                                    "detail": f"couldn't fetch transport: {e}", "changes": {}})
            continue

        if increase_base:
            updated_t = copy.deepcopy(t)
            _normalize_for_put(updated_t, "Transport")
            lines = []
            # CONFIRMED REAL BUG (product owner, 2026-09-10, same root cause as
            # _plan_transport_supplement's per-vehicle fix earlier today - "priceperpax must
            # also work, therefore both options must work"): a per-vehicle transport
            # (pricePerPax=False) has all of baseAdultPrice/baseChildrenPrice/baseInfantPrice
            # at 0 - its real base price lives in vehiclePrice instead (confirmed
            # ContractTransportVO field). This path only ever looked at the three base*Price
            # fields, so a percent base-price increase silently did nothing for a per-vehicle
            # transport ("no base price set on this Transport") even though vehiclePrice was
            # clearly nonzero. Mirrors the same per_pax branch already used above.
            per_pax = bool(t.get("pricePerPax", True))
            base_fields = _TRANSPORT_BASE_PRICE_FIELDS if per_pax else ("vehiclePrice",)
            for field in base_fields:
                old = _safe_float(t.get(field))
                if old <= 0:
                    continue
                new = _raise(old)
                if new != old:
                    updated_t[field] = new
                    lines.append(f"{field}: {old:.2f} -> {new:.2f}")
            item_id = f"{t_id}:base"
            if lines:
                result["will_change"] += 1
                result["items"].append({
                    "id": item_id, "name": f"{t_name} — base price", "status": "will_change",
                    "changes": {"EN": ("", "\n".join(lines))}, "record": updated_t,
                    "write_kind": "transport",
                })
            else:
                result["unchanged"] += 1
                result["items"].append({
                    "id": item_id, "name": f"{t_name} — base price", "status": "unchanged",
                    "changes": {}, "reason": "no base price set on this Transport",
                })

        if increase_supplement:
            for code in (t.get("optionCodes") or []):
                item_id = f"{t_id}:{code}:supplement"
                bracket_label = f"{t_name} — {code} — active supplement"
                try:
                    opt = client.get_transport_option(supplier_id, t_id, code)
                except Exception as e:
                    result["failed"] += 1
                    result["items"].append({
                        "id": item_id, "name": bracket_label, "status": "failed",
                        "detail": f"couldn't fetch option: {e}", "changes": {},
                    })
                    continue
                bracket_label = (f"{t_name} — {code} ({opt.get('minPassengers', '?')}-"
                                f"{opt.get('maxPassengers', '?')} pax) — active supplement")
                active = _active_transport_price_entry(opt)
                if not active:
                    result["unchanged"] += 1
                    result["items"].append({
                        "id": item_id, "name": bracket_label, "status": "unchanged", "changes": {},
                        "reason": "this bracket has no active price entry to increase",
                    })
                    continue
                updated_opt = copy.deepcopy(opt)
                # `active` came from the just-fetched `opt`; find the SAME entry inside the
                # deepcopy (identity breaks across deepcopy, so match by full equality instead -
                # reliable here since a duplicate byte-identical entry would be a data anomaly
                # in its own right, not something this code should silently pick between).
                target_entry = next((e for e in (updated_opt.get("prices") or []) if e == active), None)
                if target_entry is None:
                    result["unchanged"] += 1
                    result["items"].append({
                        "id": item_id, "name": bracket_label, "status": "unchanged", "changes": {},
                        "reason": "this bracket has no active price entry to increase",
                    })
                    continue
                lines = []
                for field in _TRANSPORT_SUPPLEMENT_PRICE_FIELDS:
                    old = _safe_float(active.get(field))
                    if old <= 0:
                        continue
                    new = _raise(old)
                    if new != old:
                        target_entry[field] = new
                        lines.append(f"{field}: {old:.2f} -> {new:.2f}")
                if lines:
                    result["will_change"] += 1
                    result["items"].append({
                        "id": item_id, "name": bracket_label, "status": "will_change",
                        "changes": {"EN": ("", "\n".join(lines))}, "record": updated_opt,
                        "transport_id": t_id, "write_kind": "transport_option",
                    })
                else:
                    result["unchanged"] += 1
                    result["items"].append({
                        "id": item_id, "name": bracket_label, "status": "unchanged", "changes": {},
                        "reason": "the active entry has no supplement amount to increase",
                    })
    return result


def _apply_transport_price_increase(client, supplier_id: str, planned: Dict[str, Any],
                                    progress: Optional[Callable[[int, int, str], None]] = None
                                    ) -> Dict[str, Any]:
    """Each item is tagged with its own write_kind ("transport" or "transport_option"), since
    one run of _plan_transport_price_increase can produce BOTH kinds together (base price AND
    active-supplement items for the same Transport) - unlike _apply_transport_supplement, which
    only ever writes options."""
    out = {"updated": [], "failed": [], "skipped": 0}
    pending = [i for i in planned.get("items", []) if i.get("status") == "will_change"]
    out["skipped"] = len(planned.get("items", [])) - len(pending)
    for n, item in enumerate(pending):
        if progress:
            progress(n + 1, len(pending), item.get("name", ""))
        try:
            if item.get("write_kind") == "transport":
                res = client.update_transport(supplier_id, item["record"])
            else:
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


def _plan_transport_voucher_code_repair(
        client, supplier_id: str,
        progress: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]:
    """One-off data repair (product owner, 2026-09-10): "Code upload in Bulk for Transport
    worked, but it was uploaded in the Description field - the goal was to upload it within
    the Voucher remarks of the base information. Now I have full 168 Transports with the code
    in the wrong field." Root cause: TARGETS used to (wrongly) alias Transport's "Voucher
    remarks" bulk target to `description` - see this module's own corrected comment on
    TARGETS["Transport"]. Transport genuinely has its own voucherRemarks field (schemas.py's
    TransportDataSheetVO.voucherRemarks), confirmed via a real screenshot of Travel
    Compositor's own Transport edit screen.

    For every Transport of this supplier whose EN description carries a plausible
    "(YYYYMMDD)" price-validity code, this MOVES it in one write: strips the code out of
    description (collapsing any blank line the removal leaves - same rule
    strip_price_validity_code always applies - every other word is untouched) and writes it
    into voucherRemarks instead, composed onto whatever voucher-remarks text is already there
    (preserved verbatim). A Transport whose description has no code is left completely
    untouched - never a needless write, and never confused with one that has a code in the
    CORRECT field already (voucherRemarks is not re-checked - the whole point is "was this
    caught by the bug", not "does it currently have a code at all")."""
    result: Dict[str, Any] = {"items": [], "error": None, "will_change": 0, "unchanged": 0,
                              "failed": 0, "product_type": "Transport",
                              "target": "transport_voucher_code_repair", "structured": True,
                              "kind": "transport_voucher_code_repair"}
    records, err = list_services(client, supplier_id, "Transport")
    if err and not records:
        result["error"] = err
        return result
    result["error"] = err
    total = len(records)
    for i, record in enumerate(records):
        name = label_for(record, "Transport")
        if progress:
            progress(i + 1, total, name)
        rec_id = record.get("id")
        item_id = str(rec_id or name)
        sheets = record.get("datasheets") or {}
        en = sheets.get("EN") if isinstance(sheets, dict) else None
        description = (en or {}).get("description") or "" if isinstance(en, dict) else ""
        code_date = price_validity.extract_price_validity_date(description)
        if not code_date:
            result["unchanged"] += 1
            result["items"].append({
                "id": item_id, "name": name, "status": "unchanged", "changes": {},
                "reason": "no price-validity code found in this Transport's description",
            })
            continue
        new_description = price_validity.strip_price_validity_code(description)
        existing_voucher_remarks = (en or {}).get("voucherRemarks") or ""
        new_voucher_remarks = price_validity.with_price_validity_code(
            existing_voucher_remarks, {"price_valid_until_date": code_date.isoformat()})
        updated = copy.deepcopy(record)
        _normalize_for_put(updated, "Transport")
        updated.setdefault("datasheets", {}).setdefault("EN", {})
        updated["datasheets"]["EN"]["description"] = new_description
        updated["datasheets"]["EN"]["voucherRemarks"] = new_voucher_remarks
        result["will_change"] += 1
        result["items"].append({
            "id": item_id, "name": name, "status": "will_change",
            "changes": {"EN": (
                f"description had: (...{code_date.strftime('%Y%m%d')})",
                f"moved to voucherRemarks; description code removed")},
            "record": updated, "write_kind": "transport",
        })
    return result


def _apply_transport_voucher_code_repair(
        client, supplier_id: str, planned: Dict[str, Any],
        progress: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]:
    """Pushes the moves a human has already previewed via _plan_transport_voucher_code_repair.
    Every item is a whole-Transport-record write (write_kind "transport"), same shape as the
    generic apply() path - kept as its own function only so it can be dispatched to before the
    generic path runs (see apply()'s own dispatch), mirroring _apply_transport_price_increase."""
    out = {"updated": [], "failed": [], "skipped": 0}
    pending = [i for i in planned.get("items", []) if i.get("status") == "will_change"]
    out["skipped"] = len(planned.get("items", [])) - len(pending)
    for n, item in enumerate(pending):
        if progress:
            progress(n + 1, len(pending), item.get("name", ""))
        try:
            res = client.update_transport(supplier_id, item["record"])
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


def _plan_transport_cancellation_text_repair(
        client, supplier_id: str,
        progress: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]:
    """One-off data repair (product owner, 2026-09-11): "I just want to move this phrase
    'Cancellation Policy: - Free cancellation if cancelled at least 30 days before arrival.'
    from Description to Voucher remark from Supplier MOMIRA_EG_FT and MOMIRA_TEST." The
    companion repair to _plan_transport_voucher_code_repair (same MOVE-not-copy shape, same
    "unchanged if nothing to move" safety) - that one moves a price-validity CODE; this one
    moves the whole cancellation-POLICY SENTENCE, which for Transport has always lived inline
    in `description` as one of several "<p>...</p>" paragraphs (see cancellation_bulk_
    transport.py's own module docstring: "do not change the name and the description of
    transfer and transport" locked it there even after voucherRemarks was confirmed to
    genuinely exist for Transport). Reuses cancellation_bulk_transport's own paragraph-matching
    helpers (_current_cancellation_snippet to find it, _remove_cancellation_paragraph to delete
    it cleanly, leaving every other paragraph - service description, "What to bring:", manual
    notes - untouched) rather than re-deriving that "first paragraph mentioning 'cancella'"
    matching logic a second time.

    Imported lazily (inside this function, not at module level) to avoid a circular import -
    cancellation_bulk_transport.py already imports bulk_notes.normalize_for_put.

    For every Transport of this supplier whose EN description carries a cancellation
    paragraph, this MOVES it: removes it from description (every other paragraph untouched)
    and appends it to voucherRemarks via builder._append_if_new - the SAME idempotent-append
    rule every other voucher-text composition in this codebase uses, so running this twice (or
    against a Transport whose voucherRemarks already happens to contain that exact sentence)
    never duplicates it. A Transport whose description has no cancellation paragraph at all is
    left completely untouched."""
    import cancellation_bulk_transport

    result: Dict[str, Any] = {"items": [], "error": None, "will_change": 0, "unchanged": 0,
                              "failed": 0, "product_type": "Transport",
                              "target": "transport_cancellation_text_repair", "structured": True,
                              "kind": "transport_cancellation_text_repair"}
    records, err = list_services(client, supplier_id, "Transport")
    if err and not records:
        result["error"] = err
        return result
    result["error"] = err
    total = len(records)
    for i, record in enumerate(records):
        name = label_for(record, "Transport")
        if progress:
            progress(i + 1, total, name)
        rec_id = record.get("id")
        item_id = str(rec_id or name)
        sheets = record.get("datasheets") or {}
        en = sheets.get("EN") if isinstance(sheets, dict) else None
        description = (en or {}).get("description") or "" if isinstance(en, dict) else ""
        snippet = cancellation_bulk_transport._current_cancellation_snippet_in_description(description)
        if not snippet:
            result["unchanged"] += 1
            result["items"].append({
                "id": item_id, "name": name, "status": "unchanged", "changes": {},
                "reason": "no cancellation paragraph found in this Transport's description",
            })
            continue
        new_description, found = cancellation_bulk_transport._remove_cancellation_paragraph(description)
        if not found:
            # Belt-and-braces only - _current_cancellation_snippet already found a match above,
            # so this should never actually happen, but a non-match here must never silently
            # drop the paragraph without also moving it to voucherRemarks.
            result["unchanged"] += 1
            result["items"].append({
                "id": item_id, "name": name, "status": "unchanged", "changes": {},
                "reason": "no cancellation paragraph found in this Transport's description",
            })
            continue
        existing_voucher_remarks = (en or {}).get("voucherRemarks") or ""
        new_voucher_remarks = builder._append_if_new(existing_voucher_remarks, snippet)
        updated = copy.deepcopy(record)
        _normalize_for_put(updated, "Transport")
        updated.setdefault("datasheets", {}).setdefault("EN", {})
        updated["datasheets"]["EN"]["description"] = new_description
        updated["datasheets"]["EN"]["voucherRemarks"] = new_voucher_remarks
        result["will_change"] += 1
        result["items"].append({
            "id": item_id, "name": name, "status": "will_change",
            "changes": {"EN": (
                f"description had: \"{snippet}\"",
                f"moved to voucherRemarks; description paragraph removed")},
            "record": updated, "write_kind": "transport",
        })
    return result


def _apply_transport_cancellation_text_repair(
        client, supplier_id: str, planned: Dict[str, Any],
        progress: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]:
    """Pushes the moves a human has already previewed via
    _plan_transport_cancellation_text_repair. Every item is a whole-Transport-record write
    (write_kind "transport"), identical shape to _apply_transport_voucher_code_repair - kept as
    its own function only so it can be dispatched to before the generic path runs (see apply()'s
    own dispatch)."""
    out = {"updated": [], "failed": [], "skipped": 0}
    pending = [i for i in planned.get("items", []) if i.get("status") == "will_change"]
    out["skipped"] = len(planned.get("items", [])) - len(pending)
    for n, item in enumerate(pending):
        if progress:
            progress(n + 1, len(pending), item.get("name", ""))
        try:
            res = client.update_transport(supplier_id, item["record"])
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


def _day_offset(date_str: str, days: int) -> str:
    """`date_str` (ISO 'YYYY-MM-DD') shifted by `days`. Used only for the adjacent-day boundary
    math a peak-season carve needs (the day before a period starts, the day after it ends) -
    never for anything a human typed directly, which always goes through date_format.py."""
    from datetime import date as _date, timedelta as _timedelta
    y, m, d = (int(p) for p in date_str.split("-"))
    return (_date(y, m, d) + _timedelta(days=days)).isoformat()


_TRANSPORT_OPTION_PRICE_SUPP_FIELDS = (
    "adultPriceSupplement", "childrenPriceSupplement", "infantPriceSupplement",
    "adultRTPriceSupplement", "childrenRTPriceSupplement", "infantRTPriceSupplement",
)


def _carve_transport_option_price_window(entries: List[Dict[str, Any]], start_date: str,
                                          end_date: str, new_fields: Dict[str, float],
                                          name: str) -> List[Dict[str, Any]]:
    """Returns a NEW, non-overlapping `prices` list for one Option/bracket, with a peak-season
    entry carved into it.

    CONFIRMED REAL BUG this replaces (product owner, 2026-09-10): each Option's price entries
    must never overlap in date - Travel Compositor's own Transport UI never produces two
    entries covering the same day for the same bracket. The original version of this function
    (2026-09-09) blindly APPENDED the new peak entry on top of whatever was already there, so a
    bracket with a standing always-on entry (its normal, everyday rate) ended up with TWO
    entries covering the peak dates at once - the standing one AND the new peak one - which
    Travel Compositor has no defined way to resolve (and, per the product owner, isn't how its
    own UI ever represents this).

    CONFIRMED PROCESS instead (product owner, same conversation), for adding a peak-season
    markup on top of a modality's (bracket's) existing standing rate: "change the end date of
    the existing modality to the last day before the peak season start, add a new modality
    [entry] with the additional percentage or absolute number, add another modality for [the
    same bracket] for the day after the peak the defined day and add the regular supplement
    until end of 2049 (on default)." So every existing entry that overlaps [start_date,
    end_date] is split into up to two remainder pieces - the part before the peak (end date
    moved back to the day before it starts) and the part after (start date moved forward to the
    day after it ends) - each keeping its ORIGINAL supplement amounts completely unchanged (this
    is what re-establishes "the regular supplement" afterward, all the way out to that entry's
    own end date - already confirmed to be 2049-12-31 for every real Transport). An entry that
    sits ENTIRELY inside [start_date, end_date] is dropped rather than split - it is being
    superseded outright by the new peak entry for that exact window (makes re-running this tool
    for the same period idempotent instead of piling up duplicates). An entry with no overlap at
    all is carried through completely untouched.

    Per-bracket, because each modality (Sedan, Hiace, ...) prices and is supplemented
    completely independently - see STRUCTURED_TARGETS' own "why Transport is different"
    comment; the caller (`_plan_transport_supplement`) already loops per bracket and calls this
    once per bracket, using THAT bracket's own existing entries and its own newly-computed
    peak-markup fields."""
    kept: List[Dict[str, Any]] = []
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        e_start = e.get("startDate") or ""
        e_end = e.get("endDate") or "2049-12-31"
        if e_end < start_date or e_start > end_date:
            # No overlap with the peak window at all - untouched.
            kept.append(e)
            continue
        if e_start < start_date:
            before = dict(e)
            before["endDate"] = _day_offset(start_date, -1)
            kept.append(before)
        if e_end > end_date:
            after = dict(e)
            after["startDate"] = _day_offset(end_date, 1)
            kept.append(after)
        # An entry entirely inside [start_date, end_date] contributes neither piece - dropped,
        # superseded by the new peak entry below.
    kept.append({
        "name": name, "startDate": start_date, "endDate": end_date,
        **{field: round(_safe_float(new_fields.get(field, 0.0)), 2)
           for field in _TRANSPORT_OPTION_PRICE_SUPP_FIELDS},
    })
    kept.sort(key=lambda e: e.get("startDate") or "")
    return kept


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
        # CONFIRMED REAL BUG (product owner, 2026-09-10, real TRANSPORT-415965 test: every row
        # in Travel Compositor's own Supplement column came back 0,00 EUR): a per-vehicle
        # transport (pricePerPax=False, "Price Per Pax" unchecked in the app's own Prices tab)
        # has ALL of baseAdultPrice/baseChildrenPrice/baseInfantPrice at 0 - its real base price
        # lives in vehiclePrice instead (confirmed ContractTransportVO field, same one
        # build_transport_payload writes to when pricePerPax is False). Computing against
        # baseAdultPrice=0 made a percent supplement compound onto nothing and come out 0 every
        # time, regardless of the percentage entered. ContractTransportOptionPriceVO has no
        # vehicle-specific supplement field of its own (confirmed: only adult/children/infant
        # *PriceSupplement) - build_transport_payload's own confirmed create flow (see its
        # per-bracket adult_delta/children_delta/infant_delta) writes a per-vehicle bracket's
        # whole delta into ONLY adultPriceSupplement and leaves children/infant at 0 - there is
        # nothing to key a per-headcount surcharge off when the whole vehicle is one lump price
        # - mirrored here so a bulk supplement lands the same way a fresh publish would.
        per_pax = bool(t.get("pricePerPax", True))
        if per_pax:
            base = {
                "adult": _safe_float(t.get("baseAdultPrice")),
                "children": _safe_float(t.get("baseChildrenPrice")),
                "infant": _safe_float(t.get("baseInfantPrice")),
            }
            supplement_fields = ("adult", "children", "infant")
        else:
            base = {"adult": _safe_float(t.get("vehiclePrice")), "children": 0.0, "infant": 0.0}
            supplement_fields = ("adult",)
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

            # Anchored to start_date, not "today": the "existing supplement" a peak markup
            # compounds onto is whichever rate is in effect AS OF the day the peak period
            # begins - not whatever happens to be active on the day this tool is run (a future
            # Christmas surcharge planned in September must still compound onto December's own
            # rate, not September's).
            active = _active_transport_price_entry(opt, today=start_date)
            new_entry_fields = {}
            summary_lines = []
            for field in supplement_fields:
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
            updated_opt["prices"] = _carve_transport_option_price_window(
                list(opt.get("prices") or []), start_date, end_date, new_entry_fields, name)
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

    if kind == "transport_price_increase":
        # A PERMANENT price change (base fields and/or the currently-active dated supplement
        # entry, mutated in place) - the opposite philosophy from transport_supplement above,
        # which only ever appends a new dated entry. See _plan_transport_price_increase's own
        # docstring.
        # "amount" is the current field name (percent-or-absolute, per is_percent below);
        # "percent" is kept as a fallback so a caller built against the pre-2026-09-10 signature
        # (percent-only) still works unchanged.
        _amount = (item_data or {}).get("amount", (item_data or {}).get("percent"))
        return _plan_transport_price_increase(
            client, supplier_id, _safe_float(_amount),
            bool((item_data or {}).get("increase_base")),
            bool((item_data or {}).get("increase_supplement")),
            is_percent=bool((item_data or {}).get("is_percent", True)),
            progress=progress)

    if kind == "transport_voucher_code_repair":
        return _plan_transport_voucher_code_repair(client, supplier_id, progress=progress)

    if kind == "transport_cancellation_text_repair":
        return _plan_transport_cancellation_text_repair(client, supplier_id, progress=progress)

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
        _normalize_for_put(updated, product_type)
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


# CONFIRMED REAL PRODUCTION BUG (product owner, 2026-09-10): a real bulk Voucher-remarks run
# against every Transport of a supplier failed ALL 168 services with "updateTransport.transport.
# airlineCode: must not be null". Root cause: this module PUTs each record back exactly as
# fetched (with one field changed) - it never goes through schemas.ContractTransportVO the way
# the single-service create/update flow does, so it never gets that model's own
# `airlineCode: str = ""` default. airlineCode is REQUIRED by Travel Compositor's PUT validation
# even though it's routinely ABSENT (or null) from a real GET response for a non-flight
# transport (see schemas.py's own confirmed note on ContractTransportVO.airlineCode) - so a
# record fetched via get_transports() and PUT back untouched, valid as a GET response, can still
# be rejected as a PUT. Every OTHER bulk-write path (structured Supplement entries, the
# transport_supplement per-option writer) is unaffected: this only bites a whole-record PUT
# (update_transport/update_transfer/etc), which is exactly what write_field's callers do.
#
# RECURRED (product owner, 2026-09-11): the SAME error, same field, on a DIFFERENT whole-record-
# PUT module (cancellation_bulk_transport.py's bulk cancellation-policy update) - "0 updated ·
# 168 failed", identical airlineCode message on every row. That module built its own PUT payload
# independently (`dict(p["raw"])`) instead of reusing this fix, because this function used to be
# private (`_normalize_for_put`) to this file alone. Made public (dropped the leading
# underscore) and now imported directly by every other module that PUTs a whole live Transport
# record back (cancellation_bulk_transport.py, supplier_migration.py's transport deactivation
# step) so this exact bug class has one fix, not one per module that happens to remember it
# exists. If a NEW whole-record Transport PUT site turns up anywhere else, it needs this call
# too - the underlying cause (Travel Compositor's PUT validation being stricter than its own GET
# response) does not go away just because a new caller didn't know about it yet.
_REQUIRED_STRING_DEFAULTS: Dict[str, Dict[str, str]] = {
    "Transport": {"airlineCode": ""},
}


def normalize_for_put(record: Dict[str, Any], product_type: str) -> None:
    """Fills in, IN PLACE, any field Travel Compositor's PUT requires non-null but its own GET
    can legitimately omit or send as null - see _REQUIRED_STRING_DEFAULTS's own comment. Only
    touches a field that is genuinely missing or None; never overwrites a real (even empty-
    string) value already on the record.

    PUBLIC (2026-09-11, was module-private `_normalize_for_put`) - every module that PUTs a
    whole live Transport record back (not just this one) needs this exact call before the PUT,
    or it hits the identical "airlineCode: must not be null" failure on every row. See this
    section's own comment above for the real recurrence that prompted making it shared."""
    if not isinstance(record, dict):
        return
    for field, default in _REQUIRED_STRING_DEFAULTS.get(product_type, {}).items():
        if record.get(field) is None:
            record[field] = default


# Old private name kept as an alias - every call site in THIS file was already written against
# it before the 2026-09-11 publicize; no need to touch each one just to drop an underscore.
_normalize_for_put = normalize_for_put


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
    _normalize_for_put(updated, product_type)
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
    if planned.get("kind") == "transport_price_increase":
        # Items are a mix of write_kind "transport" (base price, whole record) and
        # "transport_option" (active supplement entry) - see
        # _apply_transport_price_increase's own docstring.
        return _apply_transport_price_increase(client, supplier_id, planned, progress=progress)
    if planned.get("kind") == "transport_voucher_code_repair":
        return _apply_transport_voucher_code_repair(client, supplier_id, planned, progress=progress)
    if planned.get("kind") == "transport_cancellation_text_repair":
        return _apply_transport_cancellation_text_repair(client, supplier_id, planned, progress=progress)
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
