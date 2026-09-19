"""
transfer_gap_finder.py

CONFIRMED PRODUCT-OWNER REQUEST (2026-09-16), the natural next step after the Transfer
duplicate-and-swap create flow (flows/duplicate_transfer.py) was proven out and simplified:

    "Goal with the duplicate must be, that humans create one way transfers, then the app must
    be controlled by human and human adds the supplier as usually, the app checks is there are
    missing transfers and then provides a list with all possible missing transfers. The style
    can be similar to the bulk price transfer update."

Follow-up clarifications (same day, AskUserQuestion round-trip):
  - Pairing rule: "transfer flipped, it can not be looser - as it works right now is perfect.
    The route can not be touched, just swapped." I.e. a route only counts as "covered" when the
    EXACT reverse route (arrival's name == this one's departure name, departure's name == this
    one's arrival name) already exists among the supplier's own live transfers - no fuzzy/
    approximate matching, same standard as builder.build_transfer_swap_payload's own swap.
  - "Yes, please flag possible missing transfers." - every one-way transfer whose exact reverse
    is not found among the supplier's live list gets flagged as a gap.
  - Token/cost safety ("My biggest fear is, that we start creating in bulk new transfers and
    then the time/power/token from AI is gone and we miss all the process until then"): finding
    gaps is PURE PYTHON - a route-name comparison across the already-fetched transfer list, no
    AI call at all, so scanning even a supplier with hundreds of transfers costs nothing and
    can't run away. AI only gets used later, per-transfer, exactly like the existing duplicate
    flow (build_and_rewrite_transfer_swap_payload below) - only for the rare description a
    literal swap can't confidently handle, one transfer at a time, with a per-item progress bar
    the human watches, never a background bulk sweep.

MATCH KEY: departure name + arrival name (both normalized via text_normalize.normalize_name -
same normalization already used by transfer_matcher.py, so "Cairo Airport" and "cairo airport "
are recognized as the same place) PLUS vehicleType, since a supplier commonly sells more than
one vehicle class on the same route (e.g. Sedan AND Hiace, Cairo Airport -> Cairo City) and
those are genuinely separate products, not duplicates of each other.
"""
MODULE_BUILD = "2026-09-19-hotel-clarify-box-room-delete-and-occupancy-cap-confirmed"

from typing import Any, Dict, List, Optional

from text_normalize import normalize_name
from builder import build_transfer_swap_payload, build_transport_swap_payload
from ai_extractor import rewrite_route_description_for_new_direction
from ui_components import _html_to_plain_for_editing, _plain_to_html_for_saving


def _route_signature(transfer: Dict[str, Any]):
    departure = transfer.get("departure") or {}
    arrival = transfer.get("arrival") or {}
    dep_name = normalize_name(departure.get("name") or "")
    arr_name = normalize_name(arrival.get("name") or "")
    vehicle = (transfer.get("vehicleType") or "").strip().lower()
    return dep_name, arr_name, vehicle


def find_missing_reverse_transfers(transfers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Given a supplier's full live transfer list (exactly what GET /transfer/{supplierId}
    returns - a plain list of ContractTransferVO-shaped dicts), returns one entry per transfer
    whose exact reverse route is NOT present elsewhere in the same list:

        {"source": transfer, "missing_from_name": str, "missing_to_name": str,
         "vehicle_type": str}

    "missing_from_name"/"missing_to_name" describe the direction that needs to be CREATED - the
    reverse of the source transfer's own departure/arrival. Only transfers with both a
    departure and an arrival name are considered (nothing else can be paired at all); a
    transfer missing one or both names is silently skipped, not flagged as a false gap.

    Deliberately excludes any transfer whose 'active' field is explicitly False - an inactive
    (retired) transfer's missing reverse isn't a real gap to fill.
    """
    live = [t for t in (transfers or []) if t.get("active") is not False]
    signatures = {_route_signature(t) for t in live}

    gaps = []
    for t in live:
        departure = t.get("departure") or {}
        arrival = t.get("arrival") or {}
        dep_name = (departure.get("name") or "").strip()
        arr_name = (arrival.get("name") or "").strip()
        if not dep_name or not arr_name:
            continue
        dep_norm, arr_norm, vehicle = _route_signature(t)
        reverse_signature = (arr_norm, dep_norm, vehicle)
        if reverse_signature not in signatures:
            gaps.append({
                "source": t,
                "missing_from_name": arr_name,
                "missing_to_name": dep_name,
                "vehicle_type": t.get("vehicleType") or "",
            })
    return gaps


def build_and_rewrite_transfer_swap_payload(source: Dict[str, Any]):
    """Shared by flows/duplicate_transfer.py and flows/missing_transfers.py: wraps
    builder.build_transfer_swap_payload with the same automatic AI-rewrite step (CONFIRMED
    PRODUCT-OWNER FEEDBACK, 2026-09-16: "the 'Rewrite with AI for the new direction' works
    perfectly, please automatically use that already - no human must click additionally on this
    button") so both flows apply it identically instead of duplicating the logic.

    Returns (payload, swap_report, route_info) - same shape as build_transfer_swap_payload
    itself. swap_report[field] becomes "ai" (distinct from True/False) only when the rewrite
    genuinely changed the text; if the AI call fails or returns the text unchanged, swap_report
    stays False and the caller's own "couldn't auto-swap" warning still applies - nothing is
    silently lost either way, and no AI call happens at all for a field that already swapped
    cleanly for free.
    """
    payload, swap_report, route_info = build_transfer_swap_payload(source)

    datasheets = dict(payload.get("datasheets") or {})
    en = dict(datasheets.get("EN") or {})
    for field in ("description", "pickupDescription"):
        if swap_report.get(field) is False:
            plain = _html_to_plain_for_editing(en.get(field, ""))
            rewritten = rewrite_route_description_for_new_direction(
                plain, route_info.get("old_departure_name", ""), route_info.get("old_arrival_name", ""),
                route_info.get("new_departure_name", ""), route_info.get("new_arrival_name", ""))
            if rewritten.strip() and rewritten.strip() != plain.strip():
                en[field] = _plain_to_html_for_saving(rewritten)
                swap_report[field] = "ai"
    datasheets["EN"] = en
    payload["datasheets"] = datasheets

    return payload, swap_report, route_info


def build_and_rewrite_transport_swap_payload(source: Dict[str, Any], api_client):
    """Transport counterpart to build_and_rewrite_transfer_swap_payload, wired in the same place
    (flows/duplicate_transport.py) for the same reason.

    CONFIRMED PRODUCT-OWNER FEEDBACK (2026-09-17, Transport duplicate screenshots): "same issues
    as with transfer: Name is wrong and description must be rewritten by AI." The description
    half is a confirmed, exact analog of the Transfer gap this module already closed on 2026-09-16
    ("the 'Rewrite with AI for the new direction' works perfectly, please automatically use that
    already") - builder.build_transport_swap_payload's own description swap only ever had the
    literal/alias text match (_swap_with_aliases_if_found), with no AI fallback at all, unlike
    Transfer's build_transfer_swap_payload which already got this exact treatment. Wiring the same
    already-proven rewrite_route_description_for_new_direction helper here closes that gap instead
    of building a second, divergent implementation.

    Transport's own name/datasheet_name swap (builder.build_transport_swap_payload's own
    docstring) already uses the SAME "always append (return) as a last resort" fallback that
    Transfer's name/datasheet_name swap uses, and that fallback was already confirmed correct by
    the product owner for Transfer ("Name/datasheet-name swapping was already correct") - so it is
    not re-touched here. Transport has no pickupDescription field (that is Transfer-only), so only
    'description' is a candidate for the AI rewrite.

    Returns (payload, swap_report, route_info) - same shape as build_transport_swap_payload
    itself, with swap_report['description'] becoming "ai" (like the Transfer version) only when
    the rewrite genuinely changed the text."""
    payload, swap_report, route_info = build_transport_swap_payload(source, api_client)

    datasheets = dict(payload.get("datasheets") or {})
    en = dict(datasheets.get("EN") or {})
    if swap_report.get("description") is False:
        plain = _html_to_plain_for_editing(en.get("description", ""))
        rewritten = rewrite_route_description_for_new_direction(
            plain, route_info.get("old_departure_name", ""), route_info.get("old_arrival_name", ""),
            route_info.get("new_departure_name", ""), route_info.get("new_arrival_name", ""))
        if rewritten.strip() and rewritten.strip() != plain.strip():
            en["description"] = _plain_to_html_for_saving(rewritten)
            swap_report["description"] = "ai"
    datasheets["EN"] = en
    payload["datasheets"] = datasheets

    return payload, swap_report, route_info
