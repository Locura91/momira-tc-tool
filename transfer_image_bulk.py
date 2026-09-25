"""
transfer_image_bulk.py — mass-replace the image on many ALREADY-LIVE Transfers at once, scoped
by supplier, by ServiceType (Private/Shuttle/Shared), or both together.

CONFIRMED PRODUCT-OWNER REQUEST (2026-09-25, verbatim): "i need to create a mass image upload
for Transfers. The goal is that human can select the supplier if he wants or the human selects
ServiceType by Transfer(Private; Shuttle or Shared) and the existing image will be removed and
the new image will be added... so human can either select a supplier or transfertype to change
the image." Follow-up (verbatim): "either per supplier or per transfertype or a mixture" — so
both filters are optional and combinable, not an either/or choice. The image itself "shall come
from the local PC or from another URL that the human can select" — see flows/transfer_image_
bulk.py for the upload-vs-URL widget; this module only ever deals with a final resolved URL.

HOW THIS DIFFERS FROM supplier_images.py: that module saves ONE photo per (supplier, product
type, direction) that gets auto-applied the NEXT time that route is built/republished — a
standing default for FUTURE builds, direction-aware, no immediate write. This module is the
opposite: an immediate, one-time bulk WRITE against services that are ALREADY LIVE today,
picked by supplier and/or ServiceType, never direction-aware, and with no memory of what was
applied afterward. The two are entirely independent and share no storage — a human might use
this tool once to fix every existing Shuttle transfer's photo, and separately keep supplier_
images.py's per-direction default for whatever gets created next through the duplicate/create
flow.

CROSS-SUPPLIER SCAN: when no supplier is given (ServiceType-only scope), every Momira_ supplier
is scanned — the ONLY tool in this codebase that does this (every other bulk tool here is
confirmed per-supplier-only, see cancellation_bulk.py's own docstring). This is an explicit
product-owner choice, not a default, and is slower (one GET per supplier) — the caller (flows/
transfer_image_bulk.py) is responsible for building the supplier list to scan (via Travel
Compositor's supplier list + the "Momira_"-prefix / is_active_supplier rule every other flow in
this app already uses) and passing it in; this module has no opinion on where that list came
from.

SAFETY: callers are expected to require at least one of supplier_id / service_type before
calling plan() — this module itself does not enforce "must have at least one filter" (it would
happily plan against a single supplier's every Transfer, or literally every Transfer of every
Momira supplier if handed the full list with no service_type — that second case is deliberately
gated in the UI layer, not here, so a future legitimate caller isn't blocked by it).

WHOLE-RECORD PUT: exactly like every other bulk tool in this codebase (bulk_notes.py's own
docstring explains why) — Travel Compositor's PUT overwrites the whole resource, so each
Transfer is sent back exactly as fetched with only `images` changed, never a partial payload.

Reuses bulk_notes.list_services/label_for for fetching and naming records rather than
duplicating that plumbing — Transfer's list endpoint already returns full records
(bulk_notes.PRODUCTS["Transfer"]["full_in_list"] is True), so this is one GET per supplier, no
per-item re-fetch needed.
"""

import copy
from typing import Any, Callable, Dict, List, Optional

import bulk_notes

# Stamped on every delivery - see platform_store.py's own header for why.
MODULE_BUILD = "2026-09-25-transfer-image-bulk-verify-public-url-fix"

SERVICE_TYPES = ["PRIVATE", "SHUTTLE", "SHARED"]
SERVICE_TYPE_LABELS = {"PRIVATE": "Private", "SHUTTLE": "Shuttle", "SHARED": "Shared"}


def _label(record: Dict[str, Any], supplier_name: str) -> str:
    """A human-recognisable name for one Transfer row - route names first (what a human
    actually recognizes a Transfer by, since Transfer has no human-assigned code, see
    schemas.ContractTransferVO's own docstring), falling back to bulk_notes.label_for's generic
    logic for anything unusual."""
    dep = ((record.get("departure") or {}).get("name") or "").strip()
    arr = ((record.get("arrival") or {}).get("name") or "").strip()
    route = f"{dep} → {arr}" if (dep or arr) else bulk_notes.label_for(record, "Transfer")
    return f"{route} · {supplier_name}" if supplier_name else route


def plan(client, suppliers: List[Dict[str, str]], service_type: Optional[str],
         new_image_url: str,
         progress: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]:
    """Work out exactly what would change, WITHOUT writing anything - same plan()/apply()
    split every other bulk tool in this codebase uses (see bulk_notes.py's own docstring for
    why: nothing gets written before a human has seen the full list).

    `suppliers` is a list of {"id", "name"} dicts - one entry for a single-supplier scope,
    every Momira_ supplier for a cross-supplier ServiceType-only scope (built by the caller).
    `service_type` is one of SERVICE_TYPES or None (no filter - every ServiceType matches).
    """
    result: Dict[str, Any] = {
        "items": [], "error": None, "will_change": 0, "unchanged": 0,
        "service_type": service_type, "new_image_url": new_image_url,
    }
    if not (new_image_url or "").strip():
        result["error"] = "No image to apply."
        return result
    if not suppliers:
        result["error"] = "No supplier(s) to scan."
        return result

    errors = []
    total_suppliers = len(suppliers)
    for si, sup in enumerate(suppliers):
        supplier_id = str(sup.get("id") or "")
        supplier_name = sup.get("name") or ""
        if not supplier_id:
            continue
        if progress:
            progress(si, total_suppliers, supplier_name or supplier_id)
        records, err = bulk_notes.list_services(client, supplier_id, "Transfer")
        if err:
            errors.append(f"{supplier_name or supplier_id}: {err}")
        for record in records:
            if not isinstance(record, dict):
                continue
            record_service_type = (record.get("serviceType") or "").upper()
            if service_type and record_service_type != service_type.upper():
                continue
            current_images = list(record.get("images") or [])
            new_images = [new_image_url]
            changed = current_images != new_images
            updated = copy.deepcopy(record)
            updated["images"] = new_images
            result["items"].append({
                "id": record.get("id"),
                "supplier_id": supplier_id,
                "supplier_name": supplier_name,
                "name": _label(record, supplier_name),
                "service_type": record_service_type,
                "current_images": current_images,
                "new_images": new_images,
                "status": "will_change" if changed else "unchanged",
                "record": updated,
            })
            result["will_change" if changed else "unchanged"] += 1
    if progress:
        progress(total_suppliers, total_suppliers, "Done")
    if errors:
        result["error"] = "; ".join(errors)
    return result


def apply(client, planned: Dict[str, Any], selected_ids: Optional[set] = None,
          progress: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]:
    """Push a plan a human has already seen. Only items marked will_change AND present in
    selected_ids (or every will_change item if selected_ids is None) are sent.

    Each Transfer is PUT back whole, exactly as fetched with only `images` changed - see this
    module's own docstring on why a partial payload would be wrong. Failures are collected
    rather than raised, same "one rejection must not hide which of forty succeeded" reasoning
    as bulk_notes.apply."""
    out: Dict[str, Any] = {"updated": [], "failed": [], "skipped": 0}
    all_will_change = [i for i in planned.get("items", []) if i.get("status") == "will_change"]
    pending = [i for i in all_will_change if selected_ids is None or i.get("id") in selected_ids]
    out["skipped"] = len(all_will_change) - len(pending)
    for n, item in enumerate(pending):
        if progress:
            progress(n + 1, len(pending), item.get("name", ""))
        debug = {"request": item.get("record"), "response": None}
        try:
            res = client.update_transfer(item["supplier_id"], item["record"])
            debug["response"] = res
            if isinstance(res, dict) and "error" in res:
                out["failed"].append({
                    "name": item.get("name"), "id": item.get("id"),
                    "detail": str(res.get("message") or res.get("error")), "debug": debug,
                })
            else:
                out["updated"].append({
                    "name": item.get("name"), "id": item.get("id"), "debug": debug,
                })
        except Exception as e:
            out["failed"].append({
                "name": item.get("name"), "id": item.get("id"),
                "detail": f"{type(e).__name__}: {e}", "debug": debug,
            })
    return out
