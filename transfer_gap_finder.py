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
MODULE_BUILD = "2026-09-24-closedtour-hotels-html-leak-and-markdown-display-fix"

from typing import Any, Dict, List, Optional

from text_normalize import normalize_name
from builder import (
    build_transfer_swap_payload, build_transport_swap_payload, build_transport_option_swap_payload,
)
from ai_extractor import rewrite_route_description_for_new_direction
from ui_components import _html_to_plain_for_editing, _plain_to_html_for_saving
import transport_matcher


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


# ============================================================================
# TRANSPORT: missing-reverse-direction scan + batch create
# ============================================================================
# CONFIRMED PRODUCT-OWNER REQUEST (2026-09-22): "when duplicating transfer, I can select the
# supplier and then direct I can scan this supplier for missing transfers - that should be
# exactly the same for transport. currently I can only dubilicate one transpport at the time,
# but that is not practical." Direct Transport counterpart to find_missing_reverse_transfers/
# the batch-create loop in flows/missing_transfers.py, wired the same way as the rest of this
# module already pairs a Transfer function with its Transport counterpart just above.
def _transport_route_codes(transport: Dict[str, Any]) -> Optional[tuple]:
    """Reads the raw (dep_code, arr_code, transport_type) off a Transport's segments - a pure
    structural read, no resolution and no matching decision of its own (see
    find_missing_reverse_transports for why the actual gap-matching does NOT compare these raw
    codes directly). The overwhelming norm is a single segment even for a combined multi-leg
    journey, so segments[0]/segments[-1] (matching build_transport_swap_payload's own
    old_departure_code/old_arrival_code reads) is the same "good enough for the common case"
    boundary already accepted there. transportType (CAR/PLANE/COMBINED) is Transport's equivalent
    of Transfer's vehicleType - a supplier can sell the same route by more than one transport
    type, and those are genuinely separate products, not duplicates. Returns None (never paired,
    never flagged as a false gap) for a transport with no segments or a missing departure/arrival
    code on the first/last segment."""
    segments = transport.get("segments") or []
    if not segments:
        return None
    dep_code = (segments[0].get("departureLocationCode") or "").strip()
    arr_code = (segments[-1].get("arrivalLocationCode") or "").strip()
    if not dep_code or not arr_code:
        return None
    transport_type = (transport.get("transportType") or "").strip().lower()
    return dep_code, arr_code, transport_type


def find_missing_reverse_transports(transports: List[Dict[str, Any]], api_client) -> List[Dict[str, Any]]:
    """Given a supplier's full live transport list (exactly what GET /transport/{supplierId}
    returns), returns one entry per transport whose exact reverse route is NOT present elsewhere
    in the same list - same exact-match-only pairing rule confirmed for Transfer
    (find_missing_reverse_transfers's own docstring: "The route can not be touched, just
    swapped"):

        {"source": transport, "missing_from_name": str, "missing_to_name": str,
         "transport_type": str}

    Deliberately excludes any transport whose 'active' field is explicitly False, same as the
    Transfer version.

    CONFIRMED REAL BUG (product owner, 2026-09-22, screenshot from the first live test): a Luxor
    -> Hurghada Transport and its already-published Hurghada -> Luxor reverse - sitting right next
    to each other in the scanned list - were BOTH wrongly flagged as missing each other. Root
    cause: the first version of this matched on the raw location CODE (on the theory that "a code
    IS the canonical identifier", unlike Transfer's free-text location name). Real Travel
    Compositor master data proved that assumption wrong - the same real-world place ("Luxor City
    Center") can carry more than one Transport Base code, so a departure using one code and an
    arrival elsewhere using a different code for what is visibly the same place never matched by
    code, even though they were plainly each other's reverse to a human reading the resolved
    names. FIXED to match the exact same way Transfer already does (find_missing_reverse_transfers's
    own MATCH KEY): on the RESOLVED, NORMALIZED display name (api_client.resolve_transport_base +
    text_normalize.normalize_name - same normalization already used everywhere else in this app
    for exactly this "same place, different spelling/whitespace/code" problem), not the raw code.
    A transport whose code fails to resolve at all falls back to the raw code as its "name" (see
    _resolve_name below) - it can still match another transport that resolves to the identical
    raw code, just not one that resolves to a different code for the same real place; that residual
    gap is a real Travel Compositor master-data quirk this scan can't see past, and is why this
    function's flags should be reviewed by a human before batch-creating, exactly as the UI already
    has them do (select all/select none, never an unattended auto-create).

    api_client is used to resolve each location CODE to a human-readable name - both for display
    AND now for the match itself (api_client.resolve_transport_base - the same resolver
    build_transport_swap_payload's own code already uses). Each distinct code is resolved at most
    once per call (cached locally), so a supplier with hundreds of transports sharing a handful of
    real bases costs a handful of lookups, not hundreds - the scan step overall is still a small,
    bounded number of HTTP calls, never one per transport, so it can't run away regardless of
    supplier size."""
    live = [t for t in (transports or []) if t.get("active") is not False]

    name_cache: Dict[str, str] = {}

    def _resolve_name(code: str) -> str:
        if not code:
            return code
        if code in name_cache:
            return name_cache[code]
        try:
            result = api_client.resolve_transport_base(code)
        except Exception:
            result = None
        name = result["name"] if isinstance(result, dict) and result.get("valid") and result.get("name") else code
        name_cache[code] = name
        return name

    def _name_signature(t):
        codes = _transport_route_codes(t)
        if not codes:
            return None
        dep_code, arr_code, transport_type = codes
        dep_name = normalize_name(_resolve_name(dep_code))
        arr_name = normalize_name(_resolve_name(arr_code))
        if not dep_name or not arr_name:
            return None
        return dep_name, arr_name, transport_type

    signatures = {sig for sig in (_name_signature(t) for t in live) if sig}

    gaps = []
    for t in live:
        sig = _name_signature(t)
        if not sig:
            continue
        dep_name_norm, arr_name_norm, transport_type = sig
        reverse_signature = (arr_name_norm, dep_name_norm, transport_type)
        if reverse_signature not in signatures:
            codes = _transport_route_codes(t)
            dep_code, arr_code, _ = codes
            gaps.append({
                "source": t,
                "missing_from_name": _resolve_name(arr_code),
                "missing_to_name": _resolve_name(dep_code),
                "transport_type": t.get("transportType") or "",
            })
    return gaps


def create_duplicate_transport(client, supplier_id: str, source: Dict[str, Any]) -> Dict[str, Any]:
    """Builds and publishes ONE new Transport as the swapped-direction duplicate of `source`,
    including every one of its existing occupancy-bracket Options - the exact same fetch-source-
    options / build-swap / create-parent-inactive / create-options / link-and-activate sequence
    flows/duplicate_transport.py's own Publish button uses (see that flow's own module docstring
    for the two confirmed production bugs this sequence exists to avoid: a stale-optionCodes null
    PK on the parent create, and Travel Compositor's "must add at least one modality" rejection of
    an active parent with zero Options yet). Used by flows/missing_transports.py's batch-create
    loop, where there is no human review step in between - CONFIRMED PRODUCT-OWNER REQUEST
    (2026-09-22): "that should be exactly the same for transport" as Transfer's missing-transfers
    batch create, which likewise publishes each accepted gap directly with no per-item review.

    Returns {"status": "created"|"partial"|"created_unlinked"|"failed", "name": str,
             "new_id": str|None, "route": str|None, "detail": str|None} - "partial" means the
    parent and SOME occupancy brackets published but at least one bracket failed; "created_unlinked"
    means every bracket published but the follow-up PUT that links/activates the parent failed
    (the brackets themselves are NOT lost - the caller's message should point the human at the new
    id to fix in Travel Compositor directly, same as the single-transport flow's own equivalent
    warning)."""
    source_options = []
    for opt_code in (source.get("optionCodes") or []):
        opt = client.get_transport_option(supplier_id, source.get("id"), opt_code)
        if isinstance(opt, dict) and "error" not in opt:
            source_options.append(opt)
        # A single occupancy bracket failing to fetch is not fatal to the whole gap - matches
        # flows/duplicate_transport.py's own _fetch_source_and_options, which only warns and
        # carries on rather than aborting the whole duplicate.

    payload, _swap_report, route_info = build_and_rewrite_transport_swap_payload(source, client)
    new_dep_name = route_info.get("new_departure_name", "")
    new_arr_name = route_info.get("new_arrival_name", "")
    label = source.get("name") or f"{new_dep_name} → {new_arr_name}"
    route_label = f"{new_dep_name} → {new_arr_name}"

    duplicated_options = [
        build_transport_option_swap_payload(opt, new_dep_name, new_arr_name) for opt in source_options
    ]
    payload["optionCodes"] = []
    payload["active"] = False

    try:
        result = client.create_transport(supplier_id, payload)
    except Exception as e:
        return {"status": "failed", "name": label, "new_id": None, "route": None, "detail": str(e)}
    if isinstance(result, dict) and "error" in result:
        return {"status": "failed", "name": label, "new_id": None, "route": None,
                "detail": result.get("message", result)}
    new_id = result.get("id") if isinstance(result, dict) else None
    if not new_id:
        return {"status": "failed", "name": label, "new_id": None, "route": None,
                "detail": "created but no id came back from Travel Compositor"}

    failed_options = []
    created_codes = []
    for opt in duplicated_options:
        opt_result = client.create_transport_option(supplier_id, new_id, opt)
        if isinstance(opt_result, dict) and "error" in opt_result:
            failed_options.append((opt.get("code"), opt_result))
        else:
            created_codes.append(opt.get("code"))

    if created_codes:
        link_payload = dict(payload)
        link_payload["id"] = new_id
        link_payload["optionCodes"] = created_codes
        link_payload["active"] = True
        link_result = client.update_transport(supplier_id, link_payload)
        if isinstance(link_result, dict) and "error" in link_result:
            transport_matcher.remember_transport_id(supplier_id, new_dep_name, new_arr_name, new_id)
            return {"status": "created_unlinked", "name": label, "new_id": new_id, "route": route_label,
                    "detail": f"couldn't link occupancy bracket(s) to the parent: "
                              f"{link_result.get('message', link_result)}"}
    elif duplicated_options:
        # Every occupancy bracket failed to publish - the parent is still sitting at
        # active=False (correctly - it genuinely has no modalities yet).
        transport_matcher.remember_transport_id(supplier_id, new_dep_name, new_arr_name, new_id)
        return {"status": "partial", "name": label, "new_id": new_id, "route": route_label,
                "detail": "every occupancy bracket failed to publish - the transport was left "
                          "inactive in Travel Compositor (add at least one bracket manually, "
                          "then activate it there)"}

    transport_matcher.remember_transport_id(supplier_id, new_dep_name, new_arr_name, new_id)
    if failed_options:
        return {"status": "partial", "name": label, "new_id": new_id, "route": route_label,
                "detail": f"{len(failed_options)} of {len(duplicated_options)} occupancy "
                          f"bracket(s) failed to publish: " +
                          ", ".join(code or "?" for code, _err in failed_options)}
    return {"status": "created", "name": label, "new_id": new_id, "route": route_label, "detail": None}
