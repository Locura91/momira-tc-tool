"""
product_image_bulk.py — plan/apply engine for bulk image changes on Ticket, ClosedTour
and Hotel.

CONFIRMED PRODUCT-OWNER REQUEST (2026-11-15): "every option that we have for the bulk
actions, we shall check by the API if we can widen it or extend it to other products if
not happened so far." The bulk image tool covered Transfer and Transport only. The API
reference shows an image field on all five product types, so the three missing ones are
added here:

    ContractTicketVO.imageUrls      array[string]
    ContractClosedTourVO.images     array[string]
    ContractHotelDetailedVO.images  array[string]

WHY A SEPARATE MODULE FROM transfer_image_bulk.py: Transfer and Transport are scoped by
filters that exist only on those types (ServiceType, Vehicle Type, Transport Type), and
their flows carry a lot of behaviour earned from live bug reports. Rather than
generalising two working modules and risking that behaviour, the three types that filter
by supplier alone share this one table-driven engine. The field name, list call, update
call and id field are the only things that differ between them.

SAME SAFETY RULE as the Transfer screen: the caller verifies the image URL is actually
publicly fetchable BEFORE anything is planned or written. A successful upload is not
proof of that (see r2_client.verify_public_url).

MERGE RULE: the image field is REPLACED, matching the Transfer/Transport behaviour the
product owner specified originally ("the existing image will be removed and the new image
will be added"). Every other field on the record is passed back untouched.
"""
from typing import Any, Callable, Dict, List, Optional

from list_response_utils import normalize_list_response

MODULE_BUILD = "2026-10-24-rollover-ext"

_WRAPPER_FALLBACKS = ("items", "data", "results", "content")

# Per-type wiring. Everything that differs between the three types lives here and nowhere
# else, so adding a fourth type later is one entry rather than a new branch per function.
PRODUCT_SPECS: Dict[str, Dict[str, Any]] = {
    "Ticket": {
        "label": "Ticket",
        "image_field": "imageUrls",
        "id_field": "code",
        "wrapper_keys": ("ticket", "tickets"),
        "list_call": lambda client, sid: client.get_tickets(sid, first=0, limit=200),
        "get_call": lambda client, sid, pid: client.get_ticket(sid, pid),
        "update_call": lambda client, sid, payload: client.update_ticket(sid, payload),
    },
    "ClosedTour": {
        "label": "Closed Tour",
        "image_field": "images",
        "id_field": "code",
        "wrapper_keys": ("closedTour", "closedTours"),
        "list_call": lambda client, sid: client.get_closed_tours(sid, first=0, limit=200),
        "get_call": lambda client, sid, pid: client.get_closed_tour(sid, pid),
        "update_call": lambda client, sid, payload: client.update_closed_tour(sid, payload),
    },
    "Hotel": {
        "label": "Hotel",
        "image_field": "images",
        "id_field": "providerCode",
        "wrapper_keys": ("hotel", "hotels"),
        "list_call": lambda client, sid: client.get_hotels(sid),
        "get_call": lambda client, sid, pid: client.get_hotel(sid, pid),
        "update_call": lambda client, sid, payload: client.update_hotel(sid, payload),
    },
}

PRODUCT_TYPES = list(PRODUCT_SPECS.keys())


def _product_name(spec: Dict[str, Any], rec: Dict[str, Any], pid: str) -> str:
    datasheets = rec.get("datasheets") or {}
    en = datasheets.get("EN") or datasheets.get("en") or {}
    return (rec.get("name")
            or (en.get("name") if isinstance(en, dict) else None)
            or pid)


def plan(client, product_type: str, suppliers: List[Dict[str, Any]], new_url: str,
         progress: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]:
    """Scans the given suppliers and returns what WOULD change.

    Returns {"items": [...], "error": str|None, "product_type": str}. Each item is
    {id, name, supplier_id, supplier_name, current_images, new_images, status} where
    status is "will_change" or "unchanged". Nothing is written.
    """
    spec = PRODUCT_SPECS[product_type]
    field = spec["image_field"]

    items: List[Dict[str, Any]] = []
    errors: List[str] = []
    total = len(suppliers)

    for n, supplier in enumerate(suppliers, start=1):
        sid = supplier["id"]
        sname = supplier.get("name") or sid
        if progress:
            progress(n, total, sname)
        try:
            raw = spec["list_call"](client, sid)
        except Exception as e:
            errors.append(f"{sname}: {type(e).__name__}: {e}")
            continue
        if isinstance(raw, dict) and "error" in raw:
            errors.append(f"{sname}: {raw.get('message') or raw.get('error')}")
            continue

        records = normalize_list_response(
            raw, tuple(spec["wrapper_keys"]) + _WRAPPER_FALLBACKS)
        for rec in records:
            if not isinstance(rec, dict):
                continue
            if rec.get("active") is False:
                continue
            pid = rec.get(spec["id_field"]) or ""
            if not pid:
                continue
            current = [u for u in (rec.get(field) or []) if isinstance(u, str)]
            items.append({
                "id": pid,
                "name": _product_name(spec, rec, pid),
                "supplier_id": sid,
                "supplier_name": sname,
                "current_images": current,
                "new_images": [new_url],
                "status": "unchanged" if current == [new_url] else "will_change",
            })

    return {
        "items": items,
        "error": "; ".join(errors) if errors else None,
        "product_type": product_type,
    }


def apply(client, planned: Dict[str, Any], selected_ids=None,
          progress: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]:
    """Writes the planned image onto each selected record.

    Re-fetches each record immediately before writing rather than reusing the scanned
    copy, so a change made in Travel Compositor between the scan and the apply is not
    silently overwritten with stale fields.
    """
    product_type = planned["product_type"]
    spec = PRODUCT_SPECS[product_type]
    field = spec["image_field"]

    targets = [i for i in planned.get("items", [])
               if i["status"] == "will_change"
               and (selected_ids is None or i["id"] in selected_ids)]

    updated, failed, skipped = [], [], 0
    total = len(targets)

    for n, item in enumerate(targets, start=1):
        if progress:
            progress(n, total, item["name"])
        sid, pid = item["supplier_id"], item["id"]
        try:
            rec = spec["get_call"](client, sid, pid)
        except Exception as e:
            failed.append({"id": pid, "name": item["name"],
                           "detail": f"GET failed: {type(e).__name__}: {e}"})
            continue
        if isinstance(rec, dict) and "error" in rec:
            failed.append({"id": pid, "name": item["name"],
                           "detail": str(rec.get("message") or rec.get("error"))})
            continue

        if [u for u in (rec.get(field) or []) if isinstance(u, str)] == item["new_images"]:
            skipped += 1
            continue

        payload = dict(rec)
        payload[field] = list(item["new_images"])
        try:
            resp = spec["update_call"](client, sid, payload)
        except Exception as e:
            failed.append({"id": pid, "name": item["name"],
                           "detail": f"PUT failed: {type(e).__name__}: {e}"})
            continue
        if isinstance(resp, dict) and "error" in resp:
            failed.append({"id": pid, "name": item["name"],
                           "detail": str(resp.get("message") or resp.get("error"))})
        else:
            updated.append({"id": pid, "name": item["name"]})

    return {"updated": updated, "failed": failed, "skipped": skipped}
