"""
transport_image_bulk.py — mass-replace the image on many ALREADY-LIVE Transports at once,
scoped by supplier, by Transport Type (Car/Plane/Van/Combined), or any mixture of the two.

CONFIRMED PRODUCT-OWNER REQUEST (2026-10-18, verbatim): "the same style must be adopted for
transport. So we can then change the by supplier, then we can choose the transport type which
will be either plane, car, van, combined. The logic itself is the same as for transfers."

Direct counterpart to transfer_image_bulk.py (2026-09-25) - same plan()/apply() split, same
safety reasoning, same whole-record PUT convention. Key differences from the Transfer version:

  - Filter field is `transportType` (CAR/PLANE/VAN/COMBINED) not `serviceType` (PRIVATE/
    SHUTTLE/SHARED) - Transport has no serviceType field.
  - Transport's list endpoint returns SUMMARIES only (bulk_notes PRODUCTS["Transport"]
    full_in_list = False), so plan() must do one GET per transport id to fetch the full record
    before it can compare/update images - slower than Transfer's single-GET-per-supplier scan.
  - update_transport is used instead of update_transfer.

TRANSPORT_TYPES below: CAR/PLANE/VAN/COMBINED are the four values the product owner named
(2026-10-18: "plane, car, van, combined"). The full TC enum is unconfirmed (ContractTransportVO
docstring in schemas.py notes "8 values unconfirmed") but these four cover every real case seen.
"""

import copy
from typing import Any, Callable, Dict, List, Optional

import bulk_notes

MODULE_BUILD = "2026-10-19-rollover-fix-button"

TRANSPORT_TYPES = ["CAR", "PLANE", "VAN", "COMBINED"]
TRANSPORT_TYPE_LABELS = {
    "CAR": "Car",
    "PLANE": "Plane",
    "VAN": "Van",
    "COMBINED": "Combined",
}


def _label(record: Dict[str, Any], supplier_name: str) -> str:
    """Human-readable name for one Transport record — same route-first approach as Transfer."""
    segments = record.get("segments") or []
    if segments:
        dep = (segments[0].get("departureName") or
               segments[0].get("departureLocationCode") or "").strip()
        arr = (segments[-1].get("arrivalName") or
               segments[-1].get("arrivalLocationCode") or "").strip()
        route = f"{dep} → {arr}" if (dep or arr) else (record.get("name") or "")
    else:
        route = record.get("name") or bulk_notes.label_for(record, "Transport")
    return f"{route} · {supplier_name}" if supplier_name else route


def plan(client, suppliers: List[Dict[str, str]], transport_type: Optional[str],
         new_image_url: str,
         progress: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]:
    """Work out what would change WITHOUT writing anything.

    `suppliers` is a list of {"id", "name"} dicts (one entry for a single-supplier scope,
    every Momira_ supplier for a cross-supplier scope). `transport_type` is one of
    TRANSPORT_TYPES or None (no filter). `new_image_url` is the already-resolved, already-
    verified public URL the caller obtained (see flows/transport_image_bulk.py).

    Transport's list endpoint returns summaries only, so each matching transport id is
    individually fetched via get_transport — slower than Transfer's single-pass scan.
    """
    result: Dict[str, Any] = {
        "items": [], "error": None, "will_change": 0, "unchanged": 0,
        "transport_type": transport_type, "new_image_url": new_image_url,
    }
    if not (new_image_url or "").strip():
        result["error"] = "No image to apply."
        return result
    if not suppliers:
        result["error"] = "No supplier(s) to scan."
        return result

    errors = []
    total_suppliers = len(suppliers)
    result["_fetched"] = 0
    for si, sup in enumerate(suppliers):
        supplier_id = str(sup.get("id") or "")
        supplier_name = sup.get("name") or ""
        if not supplier_id:
            continue
        if progress:
            progress(si, total_suppliers, supplier_name or supplier_id)
        summaries, err = bulk_notes.list_services(client, supplier_id, "Transport")
        if err:
            errors.append(f"{supplier_name or supplier_id}: {err}")
        result["_fetched"] += len(summaries)
        for summary in summaries:
            if not isinstance(summary, dict):
                continue
            t_id = summary.get("id")
            if not t_id:
                continue
            # Transport list returns summaries — fetch the full record for images + transportType.
            record = client.get_transport(supplier_id, t_id)
            if isinstance(record, dict) and "error" in record:
                errors.append(f"{supplier_name or supplier_id} / {t_id}: "
                              f"{record.get('message', record)}")
                continue
            record_type = (record.get("transportType") or "").upper()
            if transport_type and record_type != transport_type.upper():
                result.setdefault("_type_skipped", 0)
                result["_type_skipped"] += 1
                continue
            current_images = list(record.get("images") or [])
            new_images = [new_image_url]
            changed = current_images != new_images
            updated = copy.deepcopy(record)
            updated["images"] = new_images
            # TC rejects PUT with airlineCode=null even for non-flight transports (confirmed real
            # validation rule: "must not be null"). Real GET responses often omit the field
            # entirely, so deepcopy leaves it absent/None. Default to "" exactly as
            # ContractTransportVO does - harmless for Car/Van/Combined, required for acceptance.
            if not updated.get("airlineCode"):
                updated["airlineCode"] = ""
            # TC also rejects PUT with companyName absent/null (confirmed real production error
            # "null PK" on the create path — same underlying entity reference). Default to ""
            # exactly as ContractTransportVO does and build_transport_swap_payload already does.
            if "companyName" not in updated or updated.get("companyName") is None:
                updated["companyName"] = ""
            result["items"].append({
                "id": t_id,
                "supplier_id": supplier_id,
                "supplier_name": supplier_name,
                "name": _label(record, supplier_name),
                "transport_type": record_type,
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
    """Push a plan a human has already reviewed.  Only items marked will_change AND in
    selected_ids (or every will_change item when selected_ids is None) are sent."""
    out: Dict[str, Any] = {"updated": [], "failed": [], "skipped": 0}
    all_will_change = [i for i in planned.get("items", []) if i.get("status") == "will_change"]
    pending = [i for i in all_will_change if selected_ids is None or i.get("id") in selected_ids]
    out["skipped"] = len(all_will_change) - len(pending)
    for n, item in enumerate(pending):
        if progress:
            progress(n + 1, len(pending), item.get("name", ""))
        debug = {"request": item.get("record"), "response": None, "verify": None}
        try:
            res = client.update_transport(item["supplier_id"], item["record"])
            debug["response"] = res
            if isinstance(res, dict) and "error" in res:
                out["failed"].append({
                    "name": item.get("name"), "id": item.get("id"),
                    "detail": str(res.get("message") or res.get("error")), "debug": debug,
                })
            else:
                # Verify the write actually landed: re-fetch and check `images`.
                verify = client.get_transport(item["supplier_id"], item["id"])
                debug["verify"] = verify
                expected = item["new_images"]
                actual = list(verify.get("images") or []) if isinstance(verify, dict) else None
                write_verified = (actual == expected)
                out["updated"].append({
                    "name": item.get("name"), "id": item.get("id"),
                    "write_verified": write_verified,
                    "actual_images": actual,
                    "debug": debug,
                })
        except Exception as e:
            out["failed"].append({
                "name": item.get("name"), "id": item.get("id"),
                "detail": f"{type(e).__name__}: {e}", "debug": debug,
            })
    return out
