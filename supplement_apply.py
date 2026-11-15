"""
supplement_apply.py — the write half of the four bulk supplement tools.

WHY THIS EXISTS: the create/delete loops used to live inside each flow's Streamlit
render function, which made the destructive one impossible to test — you cannot exercise
"delete every supplement from every product" without a running Streamlit session and a
live Travel Compositor account. Pulled out here as plain functions, the same loops are
coverable with a fake client, which is the minimum a bulk delete deserves.

It also removes the fourth copy of a loop that was already written three times (weekly
duplicate audit, 2026-11-15): the only things that differ per product type are how a
record is addressed and which endpoint writes it, so those live in PRODUCT_SPECS and the
loops are shared.

PER-TYPE DIFFERENCES, all confined to PRODUCT_SPECS:
  Transfer    supplements[] on the record;  PUT /transfer/{supplierId}
  Ticket      supplements[] on the MODALITY; PUT /tickets/{supplierId}/{ticketCode}
  ClosedTour  supplements[] on the record;  PUT /closedtours/{supplierId}
  Hotel       CREATE goes through POST /hotel/supplement/{supplierId}/{providerCode},
              because hotel supplements have their own creation endpoint. DELETE still
              goes through PUT /hotel/{supplierId} with the array emptied, because no
              delete endpoint exists for them.
"""
import copy
from typing import Any, Callable, Dict, List, Optional

MODULE_BUILD = "2026-10-24-rollover-ext"


def _record_of(item: Dict[str, Any]) -> Dict[str, Any]:
    """Ticket items are a wrapper carrying the modality under "raw"; every other type
    hands the live record straight through."""
    return item.get("raw") if isinstance(item.get("raw"), dict) else item


def _put_back(update_call: Callable) -> Callable:
    """Builds a writer that deep-copies the record, swaps its supplements, and PUTs it.

    Deep-copied so a failed write cannot leave the in-memory list holding a supplement
    Travel Compositor never accepted — the screen would then show a state the account
    does not actually have.
    """
    def _write(client, supplier_id, item, supplements):
        payload = copy.deepcopy(_record_of(item))
        payload["supplements"] = supplements
        return update_call(client, supplier_id, item, payload)
    return _write


PRODUCT_SPECS: Dict[str, Dict[str, Any]] = {
    "Transfer": {
        "label_of": lambda i: _record_of(i).get("id") or "",
        "name_of": lambda i: _record_of(i).get("name") or _record_of(i).get("id") or "",
        "write": _put_back(lambda c, sid, i, p: c.update_transfer(sid, p)),
    },
    "Ticket": {
        "label_of": lambda i: f"{i.get('ticket_code', '')}/{i.get('option_code', '')}",
        "name_of": lambda i: f"{i.get('ticket_name', '')} / {i.get('option_name', '')}",
        "write": _put_back(
            lambda c, sid, i, p: c.update_ticket_option(sid, i.get("ticket_code", ""), p)),
    },
    "ClosedTour": {
        "label_of": lambda i: (_record_of(i).get("code")
                               or _record_of(i).get("providerCode") or ""),
        "name_of": lambda i: (_record_of(i).get("name")
                              or _record_of(i).get("code") or ""),
        "write": _put_back(lambda c, sid, i, p: c.update_closed_tour(sid, p)),
    },
    "Hotel": {
        "label_of": lambda i: (_record_of(i).get("providerCode")
                               or _record_of(i).get("code") or ""),
        "name_of": lambda i: (_record_of(i).get("name")
                              or _record_of(i).get("providerCode") or ""),
        "write": _put_back(lambda c, sid, i, p: c.update_hotel(sid, p)),
        # Hotel supplements are created through their own endpoint rather than by
        # writing the whole record back.
        "create_via_post": lambda c, sid, i, supp: c.add_hotel_supplement(
            sid, _record_of(i).get("providerCode") or _record_of(i).get("code") or "", supp),
    },
}

PRODUCT_TYPES = list(PRODUCT_SPECS.keys())


def supplements_of(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [s for s in (_record_of(item).get("supplements") or []) if isinstance(s, dict)]


def items_with_supplements(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [i for i in items if supplements_of(i)]


def count_supplements(items: List[Dict[str, Any]]) -> int:
    return sum(len(supplements_of(i)) for i in items)


def _run(items, step, progress) -> Dict[str, Any]:
    """Shared loop: run `step` per item, collecting successes and failures.

    One record failing never aborts the rest — a bulk run over a supplier's whole
    catalogue should report which ones failed, not stop at the first and leave the human
    guessing how far it got.
    """
    ok, errors = 0, []
    total = len(items)
    for n, item in enumerate(items, start=1):
        try:
            resp = step(item)
        except Exception as e:
            errors.append(f"{item['_label']}: {type(e).__name__}: {e}")
        else:
            if isinstance(resp, dict) and "error" in resp:
                errors.append(f"{item['_label']}: "
                              f"{resp.get('message') or resp.get('error')}")
            else:
                ok += 1
        if progress:
            progress(n, total)
    return {"ok": ok, "failed": len(errors), "errors": errors, "total": total}


def _labelled(spec, items):
    out = []
    for i in items:
        i = dict(i)
        i["_label"] = spec["label_of"](i) or "(unknown)"
        out.append(i)
    return out


def apply_create(client, supplier_id: str, product_type: str,
                 items: List[Dict[str, Any]], new_supplement: Dict[str, Any],
                 selected_labels: Optional[set] = None,
                 progress: Optional[Callable[[int, int], None]] = None) -> Dict[str, Any]:
    """Appends `new_supplement` to each selected record, keeping what is already there."""
    spec = PRODUCT_SPECS[product_type]
    targets = [i for i in _labelled(spec, items)
               if selected_labels is None or i["_label"] in selected_labels]

    post = spec.get("create_via_post")

    def _step(item):
        if post:
            return post(client, supplier_id, item, copy.deepcopy(new_supplement))
        return spec["write"](client, supplier_id, item,
                             supplements_of(item) + [copy.deepcopy(new_supplement)])

    return _run(targets, _step, progress)


def apply_delete_all(client, supplier_id: str, product_type: str,
                     items: List[Dict[str, Any]],
                     progress: Optional[Callable[[int, int], None]] = None) -> Dict[str, Any]:
    """Empties the supplements array on every record that has one, and writes it back.

    Records with no supplements are skipped rather than written: a PUT that changes
    nothing is still a write against a live record, and a bulk delete should touch as
    little as it can.
    """
    spec = PRODUCT_SPECS[product_type]
    targets = _labelled(spec, items_with_supplements(items))
    return _run(targets, lambda item: spec["write"](client, supplier_id, item, []),
                progress)
