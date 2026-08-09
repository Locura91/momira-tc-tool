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
        "full_in_list": False, "shape": SHAPE_DATASHEETS,
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
        # Transport genuinely has nothing else - no voucher remarks field exists on it,
        # which is why its cancellation text already goes into the description everywhere
        # else in this platform.
        "Description (bottom)": "description",
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
        "Voucher remarks": "Transport has no voucher remarks field in Travel Compositor — "
                           "only a name and a description.",
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


def read_field(record: Dict[str, Any], product_type: str, target: str) -> Dict[str, str]:
    """The field's current text, per language. Returns {language: text}."""
    field = TARGETS.get(product_type, {}).get(target)
    if not field or not isinstance(record, dict):
        return {}
    shape = PRODUCTS[product_type]["shape"]
    if shape == SHAPE_DATASHEETS:
        sheets = record.get("datasheets") or {}
        if not isinstance(sheets, dict):
            return {}
        return {lang: str((sheet or {}).get(field) or "")
                for lang, sheet in sheets.items() if isinstance(sheet, dict)}
    entries = record.get(field) or []
    if not isinstance(entries, list):
        return {}
    return {str(e.get("language") or "EN"): str(e.get("description") or "")
            for e in entries if isinstance(e, dict)}


def combine(existing: str, text: str, mode: str) -> str:
    """The new value for one language.

    Append puts the new text at the bottom, separated by a blank line, which is what
    "Description (bottom)" means and keeps the supplier's own wording first. Replace
    returns just the new text. Either way, text already present is left alone: the same
    note sent twice must not appear twice."""
    existing = existing or ""
    text = (text or "").strip()
    if not text:
        return existing
    if mode == MODE_REPLACE:
        return text
    if _norm(text) and _norm(text) in _norm(existing):
        return existing
    if not existing.strip():
        return text
    return f"{existing.rstrip()}\n\n{text}"


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
            before = str(sheet.get(field) or "")
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
            rec = getattr(client, cfg["fetch_fn"])(supplier_id, code)
            if isinstance(rec, dict) and "error" in rec:
                failures.append(f"{code} ({rec.get('message', rec.get('error'))})")
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
                if len(batch) < 100 or first > 5000:
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
            record = getattr(client, cfg["fetch_fn"])(supplier_id, ident)
            if isinstance(record, dict) and "error" in record:
                result["items"].append({"id": ident, "name": name, "status": "failed",
                                        "detail": str(record.get("message") or record.get("error")),
                                        "changes": {}})
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
    if any(_norm(text) in _norm(v) for v in current.values()):
        return "the same text is already there"
    return "nothing to change"


def apply(client, supplier_id: str, planned: Dict[str, Any],
          progress: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]:
    """Push a plan a human has already seen. Only items marked will_change are sent.

    Each service is PUT back whole, exactly as fetched with one field changed. Failures are
    collected rather than raised: with forty services in flight, one rejection must not
    hide the thirty-nine that succeeded or leave a person unsure which is which."""
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
