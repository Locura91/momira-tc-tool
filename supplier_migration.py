"""
supplier_migration.py — moves one supplier's services to a different supplier, for all 5 product
types.

CONFIRMED REAL NEED (product owner, 2026-08-24): "If I want mass change the supplier A, like
all Transfers from supplier must now be changed to supplier B." Extended (product owner,
2026-09-10): "this is not only for the transfer section, it must work for all services." -
app.py's render_supplier_migration_flow used to call straight into inline Transfer-only logic;
that logic now lives here as migrate_transfer, alongside a migrate_* for every other type.

TRANSFER / TRANSPORT: TRUE IN-PLACE MOVE, NOT A RECREATE (changed 2026-09-16). CONFIRMED
PRODUCT-OWNER CORRECTION (2026-09-16): "just exchanging the supplier and NOT creating new
services. We are within updating existing services and we are never creating new services, as
we strictly keep them separately." Chris pasted Travel Compositor's own Swagger for
`PUT /transport/{supplierId}` - the endpoint is literally described as "Updates an existing
transport", supplierId is a URL path parameter (this IS the destination), and the record's own
`id` stays in the request body pointing at the SAME record. So migrate_transfer/migrate_transport
now do exactly one call: PUT the record, unchanged id, straight to the DESTINATION supplier's
URL (client.update_transfer/update_transport with dest_id). No create call, no new id, no
separate deactivate-the-original step - there is nothing left under the source supplier to
retire, because the record itself now belongs to the destination supplier. The only change
still applied to the payload before sending is `_strip_nested_null_ids` (still needed - see that
function's docstring; unrelated to which supplier owns the record).

NOTE: this was NOT independently verified against a live call before shipping (no API access
from this environment) - it rests on Chris's read of the Swagger and his explicit instruction.
Recommend testing on ONE real route first (e.g. re-run the "Cairo - Alexandria" Transport that
originally failed) before trusting this for a full-supplier batch move.

Ticket / ClosedTour / Hotel below are UNCHANGED (still recreate-then-retire) - they were not
part of this correction and have their own reasons (documented per-type below) for needing a
multi-call create sequence rather than a single in-place update.

RECREATE MIRRORS THE REAL CREATE FLOW FOR EACH TYPE - not a shortcut invented for this tool.
Where a type's real create flow (already live elsewhere in this app) needs more than one call,
migrating it needs exactly the same sequence, in the same order, for the same reason:
  * Ticket / ClosedTour: CREATE BARE (active=True, modalityCodes/supplements cleared) -> CREATE
    EVERY OPTION/MODALITY (the source's own code is reused - option/modality codes are scoped to
    the parent, not global, so the same string is safe to reuse under a brand-new parent) ->
    FINALIZE with a follow-up PUT that declares the real modalityCodes (+ supplements, for
    ClosedTour) and the ORIGINAL's own final active state. Declaring modalityCodes any earlier is
    rejected by Travel Compositor - it validates that a referenced code already corresponds to a
    real, existing option (confirmed production failure, see app.py's ClosedTour create flow).
  * Hotel: by far the most different shape. rooms[]/mealPlans[] are required inline on the
    hotel's own create call, but per a confirmed real production failure (HRG-H1, 2026-09-06),
    submitting a BRAND-NEW room inline (providerCode unset) is rejected - every room, including
    the first, must be added afterward one at a time via POST /hotel/room, which is the only call
    that accepts an unset providerCode and returns the system-generated one. Offers, supplements
    and rooms are ALL reassigned brand-new provider codes on create (system-generated AUTO_...
    strings, confirmed never reusable) - so every rate's providerRoomCodes/offers/supplements
    list is remapped from the OLD codes to the NEW ones as each piece is created below, or a
    migrated rate would silently reference a code that no longer exists.

RETIRING THE ORIGINAL is NOT uniform across product types either:
  * Transfer / Transport / Ticket / ClosedTour all have a real `active` flag - PUT it to False.
  * Hotel has NO active flag anywhere on its schema, and no delete endpoint at all - there is
    genuinely no way to switch one off. CONFIRMED PRODUCT-OWNER DECISION (2026-09-10, asked
    directly rather than guessed, given this project's own recent history of guessing wrong
    about a schema and corrupting 168 live Transports): block every future date on every
    room of every rate on the ORIGINAL hotel, via the same stop-sale mechanism
    stop_sales_tool.py already uses for supplier-announced closures (reused here through
    stop_sales_tool.apply_to_hotel_rate, not duplicated) - so it can no longer actually be
    booked, even though the record itself stays visible in Travel Compositor.

EVERY WRITE HERE IS A REAL, LIVE CHANGE - there is no dry-run mode in this module. The calling
screen in app.py is responsible for showing a human what will move, and getting their explicit
confirmation, before any of this runs.

Every migrate_* function takes the already-fetched SOURCE record (never re-fetches it itself,
so what the human reviewed on screen is exactly what gets copied) and returns one result dict:
{"name", "ok": True | False | "partial", "stage", "detail", "new_id"} - "partial" always means
the new copy exists (fully or partially) under the destination AND the original was deliberately
NOT retired, so nothing is ever double-booked or silently lost even on a failure partway through.
"""

# Stamped on every delivery - see platform_store.py's own header for why.
MODULE_BUILD = "2026-09-22-bullet-list-formatting-preserved-on-duplicate"

import json
from datetime import date
from typing import Any, Dict, List, Optional

import stop_sales_tool
from ai_extractor import friendly_error_message
from bulk_notes import normalize_for_put

HOTEL_CLOSE_OUT_END_DATE = "2049-12-31"


def _err_detail(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("message") or result.get("error") or result)
    return str(result)


def _remap_codes(codes: Optional[List[str]], code_map: Dict[str, str]) -> List[str]:
    return [code_map.get(c, c) for c in (codes or []) if c]


def _strip_nested_null_ids(obj: Any, _top: bool = True) -> None:
    """CONFIRMED REAL PRODUCTION BUG (product owner, 2026-09-16): migrating Transfers failed at
    the create step for every route tried, with `java.lang.IllegalArgumentException: An instance
    of a null PK has been incorrectly provided for this find operation.` - Travel Compositor's
    backend treats any nested object that carries an "id" KEY (regardless of its value) as a
    reference to an existing child row it must look up; a raw GET response routinely includes
    "id": null on nested rows (price brackets, supplements, properties, etc. - Hibernate-assigned
    child-row PKs the write-side schema in schemas.py never models, since they only matter for
    reads) that are harmless to echo back on an UPDATE of the SAME parent, but crash outright on
    CREATE, where there is no existing parent row for that null-PK child lookup to attach to.

    Recursively deletes every "id" key whose value is None, at every nesting depth EXCEPT the
    top level (the top-level "id": None is deliberate - see migrate_transfer/migrate_transport,
    which need it present and None so Travel Compositor treats this as a create, not an update -
    the exact same convention builder.py's schema-based create flow already relies on). Mutates
    obj in place; walks dicts and lists only, matching plain JSON structure."""
    if isinstance(obj, dict):
        if not _top and "id" in obj and obj["id"] is None:
            del obj["id"]
        for value in obj.values():
            _strip_nested_null_ids(value, _top=False)
    elif isinstance(obj, list):
        for item in obj:
            _strip_nested_null_ids(item, _top=False)


# ----------------------------------------------------------------------
# Transfer - TRUE in-place move (2026-09-16, see module docstring) - one PUT straight to the
# destination supplier's URL, same record id, nothing created or deactivated.
# ----------------------------------------------------------------------
def migrate_transfer(client, source_id: str, dest_id: str, record: Dict[str, Any],
                     transfer_matcher=None) -> Dict[str, Any]:
    dep = (record.get("departure") or {}).get("name", "") if isinstance(record.get("departure"), dict) else ""
    arr = (record.get("arrival") or {}).get("name", "") if isinstance(record.get("arrival"), dict) else ""
    name = record.get("name") or f"{dep} - {arr}".strip(" -") or record.get("id") or "(unnamed transfer)"
    moved_id = record.get("id")

    payload = dict(record)
    _strip_nested_null_ids(payload)  # see that function's docstring - fixes the confirmed real
    # "null PK...find operation" failure (2026-09-16) - unrelated to which supplier owns it
    try:
        res = client.update_transfer(dest_id, payload)
    except Exception as e:
        return {"name": name, "ok": False, "stage": "move", "detail": friendly_error_message(e)}
    if isinstance(res, dict) and "error" in res:
        return {"name": name, "ok": False, "stage": "move", "detail": _err_detail(res)}

    if transfer_matcher is not None:
        try:
            if dep and arr:
                transfer_matcher.forget_transfer_id(source_id, dep, arr)
                transfer_matcher.remember_transfer_id(dest_id, dep, arr, moved_id)
        except Exception:
            pass

    return {"name": name, "ok": True, "stage": "done", "new_id": moved_id, "moved_in_place": True}


# ----------------------------------------------------------------------
# Transport - TRUE in-place move (2026-09-16, see module docstring) - one PUT straight to the
# destination supplier's URL, same record id, options move with it (they're keyed to the
# transport's own id, not the supplier), nothing created or deactivated.
# ----------------------------------------------------------------------
def migrate_transport(client, source_id: str, dest_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    name = record.get("name") or record.get("id") or "(unnamed transport)"
    moved_id = record.get("id")

    payload = dict(record)
    _strip_nested_null_ids(payload)  # see that function's docstring - fixes the confirmed real
    # "null PK...find operation" failure (2026-09-16) - unrelated to which supplier owns it
    # CONFIRMED REAL PRODUCTION BUG (product owner, 2026-09-11, cancellation_bulk_transport.py):
    # a whole-record Transport write built from a raw GET response, same as this one, failed
    # every row with "updateTransport.transport.airlineCode: must not be null" - airlineCode is
    # REQUIRED by Travel Compositor's own Swagger even though a real GET response for a
    # non-flight Transport routinely omits it or returns null. See bulk_notes.normalize_for_put's
    # own docstring for the full history.
    normalize_for_put(payload, "Transport")
    try:
        res = client.update_transport(dest_id, payload)
    except Exception as e:
        return {"name": name, "ok": False, "stage": "move", "detail": friendly_error_message(e)}
    if isinstance(res, dict) and "error" in res:
        return {"name": name, "ok": False, "stage": "move", "detail": _err_detail(res)}

    return {"name": name, "ok": True, "stage": "done", "new_id": moved_id, "moved_in_place": True}


# ----------------------------------------------------------------------
# Ticket - create bare / create modalities / finalize. Mirrors app.py's real Ticket create flow.
# ----------------------------------------------------------------------
def migrate_ticket(client, source_id: str, dest_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    ticket_code = record.get("code")
    name = record.get("name") or ticket_code or "(unnamed ticket)"
    modality_codes = list(record.get("modalityCodes") or [])
    original_active = bool(record.get("active", True))

    create_payload = dict(record)
    create_payload["active"] = True
    create_payload["modalityCodes"] = []
    try:
        create_res = client.create_ticket(dest_id, create_payload)
    except Exception as e:
        return {"name": name, "ok": False, "stage": "create", "detail": friendly_error_message(e)}
    if isinstance(create_res, dict) and "error" in create_res:
        return {"name": name, "ok": False, "stage": "create", "detail": _err_detail(create_res)}
    real_code = create_res.get("code") if isinstance(create_res, dict) else None
    if not real_code:
        return {"name": name, "ok": False, "stage": "create",
                "detail": "the create call didn't return a code - the original was NOT "
                          "deactivated, nothing was lost."}

    created_modality_codes = []
    option_failures = []
    for code in modality_codes:
        try:
            option = client.get_ticket_option(source_id, ticket_code, code)
        except Exception as e:
            option_failures.append(f"{code} (couldn't read: {friendly_error_message(e)})")
            continue
        if not isinstance(option, dict) or "error" in option:
            option_failures.append(f"{code} (couldn't read: {_err_detail(option)})")
            continue
        try:
            opt_res = client.create_ticket_option(dest_id, real_code, option)
        except Exception as e:
            option_failures.append(f"{code} ({friendly_error_message(e)})")
            continue
        if isinstance(opt_res, dict) and "error" in opt_res:
            option_failures.append(f"{code} ({_err_detail(opt_res)})")
        else:
            created_modality_codes.append(code)

    finalize_payload = dict(record)
    finalize_payload["code"] = real_code
    finalize_payload["active"] = original_active
    finalize_payload["modalityCodes"] = created_modality_codes
    try:
        finalize_res = client.update_ticket(dest_id, finalize_payload)
    except Exception as e:
        return {"name": name, "ok": "partial", "stage": "finalize", "new_id": real_code,
                "detail": f"created as `{real_code}` with {len(created_modality_codes)}/"
                          f"{len(modality_codes)} modalit(y/ies), but the follow-up update "
                          f"(declaring modality codes and final active state) failed - the "
                          f"original was NOT deactivated: {friendly_error_message(e)}"}
    if isinstance(finalize_res, dict) and "error" in finalize_res:
        return {"name": name, "ok": "partial", "stage": "finalize", "new_id": real_code,
                "detail": f"created as `{real_code}` with {len(created_modality_codes)}/"
                          f"{len(modality_codes)} modalit(y/ies), but the follow-up update failed "
                          f"and the original was NOT deactivated: {_err_detail(finalize_res)}"}

    if option_failures:
        return {"name": name, "ok": "partial", "stage": "options", "new_id": real_code,
                "detail": f"created as `{real_code}`, but {len(option_failures)}/{len(modality_codes)} "
                          f"modalit(y/ies) failed and the original was NOT deactivated: "
                          f"{'; '.join(option_failures)}"}

    deactivate_payload = dict(record)
    deactivate_payload["code"] = ticket_code
    deactivate_payload["active"] = False
    try:
        deact_res = client.update_ticket(source_id, deactivate_payload)
    except Exception as e:
        return {"name": name, "ok": "partial", "stage": "deactivate", "new_id": real_code,
                "detail": f"created as `{real_code}`, but couldn't deactivate the original: {friendly_error_message(e)}"}
    if isinstance(deact_res, dict) and "error" in deact_res:
        return {"name": name, "ok": "partial", "stage": "deactivate", "new_id": real_code,
                "detail": f"created as `{real_code}`, but couldn't deactivate the original: {_err_detail(deact_res)}"}

    return {"name": name, "ok": True, "stage": "done", "new_id": real_code}


# ----------------------------------------------------------------------
# ClosedTour - same create-bare/create-modalities/finalize dance as Ticket, plus supplements
# (which reference modality codes and must be filtered the same way the real create flow does).
# ----------------------------------------------------------------------
def migrate_closed_tour(client, source_id: str, dest_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    tour_code = record.get("code")
    name = record.get("name") or tour_code or "(unnamed tour)"
    modality_codes = list(record.get("modalityCodes") or [])
    original_active = bool(record.get("active", False))
    original_supplements = record.get("supplements") or []

    create_payload = dict(record)
    create_payload["active"] = True
    create_payload["modalityCodes"] = []
    create_payload["supplements"] = []
    try:
        create_res = client.create_closed_tour(dest_id, create_payload)
    except Exception as e:
        return {"name": name, "ok": False, "stage": "create", "detail": friendly_error_message(e)}
    if isinstance(create_res, dict) and "error" in create_res:
        return {"name": name, "ok": False, "stage": "create", "detail": _err_detail(create_res)}
    real_code = create_res.get("code") if isinstance(create_res, dict) else None
    if not real_code:
        return {"name": name, "ok": False, "stage": "create",
                "detail": "the create call didn't return a code - the original was NOT "
                          "deactivated, nothing was lost."}

    created_modality_codes = []
    option_failures = []
    for code in modality_codes:
        try:
            option = client.get_closed_tour_option(source_id, tour_code, code)
        except Exception as e:
            option_failures.append(f"{code} (couldn't read: {friendly_error_message(e)})")
            continue
        if not isinstance(option, dict) or "error" in option:
            option_failures.append(f"{code} (couldn't read: {_err_detail(option)})")
            continue
        try:
            opt_res = client.create_closed_tour_option(dest_id, real_code, option)
        except Exception as e:
            option_failures.append(f"{code} ({friendly_error_message(e)})")
            continue
        if isinstance(opt_res, dict) and "error" in opt_res:
            option_failures.append(f"{code} ({_err_detail(opt_res)})")
        else:
            created_modality_codes.append(code)

    finalize_payload = dict(record)
    finalize_payload["code"] = real_code
    finalize_payload["active"] = original_active
    finalize_payload["modalityCodes"] = created_modality_codes
    finalize_payload["supplements"] = [
        s for s in original_supplements
        if not s.get("modalityCodes") or all(c in created_modality_codes for c in s["modalityCodes"])
    ]
    try:
        finalize_res = client.update_closed_tour(dest_id, finalize_payload)
    except Exception as e:
        return {"name": name, "ok": "partial", "stage": "finalize", "new_id": real_code,
                "detail": f"created as `{real_code}` with {len(created_modality_codes)}/"
                          f"{len(modality_codes)} modalit(y/ies), but the follow-up update failed "
                          f"- the original was NOT deactivated: {friendly_error_message(e)}"}
    if isinstance(finalize_res, dict) and "error" in finalize_res:
        return {"name": name, "ok": "partial", "stage": "finalize", "new_id": real_code,
                "detail": f"created as `{real_code}` with {len(created_modality_codes)}/"
                          f"{len(modality_codes)} modalit(y/ies), but the follow-up update failed "
                          f"and the original was NOT deactivated: {_err_detail(finalize_res)}"}

    if option_failures:
        return {"name": name, "ok": "partial", "stage": "options", "new_id": real_code,
                "detail": f"created as `{real_code}`, but {len(option_failures)}/{len(modality_codes)} "
                          f"modalit(y/ies) failed and the original was NOT deactivated: "
                          f"{'; '.join(option_failures)}"}

    deactivate_payload = dict(record)
    deactivate_payload["code"] = tour_code
    deactivate_payload["active"] = False
    try:
        deact_res = client.update_closed_tour(source_id, deactivate_payload)
    except Exception as e:
        return {"name": name, "ok": "partial", "stage": "deactivate", "new_id": real_code,
                "detail": f"created as `{real_code}`, but couldn't deactivate the original: {friendly_error_message(e)}"}
    if isinstance(deact_res, dict) and "error" in deact_res:
        return {"name": name, "ok": "partial", "stage": "deactivate", "new_id": real_code,
                "detail": f"created as `{real_code}`, but couldn't deactivate the original: {_err_detail(deact_res)}"}

    return {"name": name, "ok": True, "stage": "done", "new_id": real_code}


# ----------------------------------------------------------------------
# Hotel - by far the biggest one. See this module's own docstring for the full reasoning.
# ----------------------------------------------------------------------
def migrate_hotel(client, source_id: str, dest_id: str, record: Dict[str, Any],
                  today_iso: Optional[str] = None) -> Dict[str, Any]:
    today_iso = today_iso or date.today().isoformat()
    provider_code = record.get("providerCode")
    name = record.get("hotelname") or provider_code or "(unnamed hotel)"

    phase1_payload = dict(record)
    phase1_payload["rooms"] = []
    try:
        create_res = client.create_hotel(dest_id, phase1_payload)
    except Exception as e:
        return {"name": name, "ok": False, "stage": "create", "detail": friendly_error_message(e)}
    if isinstance(create_res, dict) and "error" in create_res:
        return {"name": name, "ok": False, "stage": "create", "detail": _err_detail(create_res)}

    # ---- rooms, one at a time - see ContractRoomVO's own docstring in schemas.py for why a
    # brand-new room can't go in the main call, confirmed by a real production failure.
    room_map: Dict[str, str] = {}
    room_failures = []
    for room in record.get("rooms") or []:
        old_code = room.get("providerCode")
        room_payload = dict(room)
        room_payload["providerCode"] = None
        try:
            room_res = client.create_hotel_room(dest_id, provider_code, room_payload)
        except Exception as e:
            room_failures.append(f"{room.get('name') or old_code or '(unnamed room)'} ({friendly_error_message(e)})")
            continue
        if isinstance(room_res, dict) and "error" in room_res:
            room_failures.append(f"{room.get('name') or old_code or '(unnamed room)'} ({_err_detail(room_res)})")
            continue
        new_code = room_res.get("providerCode") if isinstance(room_res, dict) else None
        if old_code and new_code:
            room_map[old_code] = new_code

    if room_failures:
        return {"name": name, "ok": "partial", "stage": "rooms", "new_id": provider_code,
                "detail": f"hotel created as `{provider_code}`, but {len(room_failures)} room(s) "
                          f"failed and the original was NOT closed out: {'; '.join(room_failures)}"}

    # ---- offers ----
    offer_map: Dict[str, str] = {}
    offer_failures = []
    for offer in record.get("offers") or []:
        old_code = offer.get("providerCode")
        offer_label = ((offer.get("names") or [{}])[0] or {}).get("description") or old_code or "(unnamed offer)"
        offer_payload = dict(offer)
        offer_payload["providerCode"] = None
        offer_payload["providerRoomCodes"] = _remap_codes(offer.get("providerRoomCodes"), room_map)
        try:
            offer_res = client.create_hotel_offer(dest_id, provider_code, offer_payload)
        except Exception as e:
            offer_failures.append(f"{offer_label} ({friendly_error_message(e)})")
            continue
        if isinstance(offer_res, dict) and "error" in offer_res:
            offer_failures.append(f"{offer_label} ({_err_detail(offer_res)})")
            continue
        new_code = offer_res.get("providerCode") if isinstance(offer_res, dict) else None
        if old_code and new_code:
            offer_map[old_code] = new_code

    # ---- supplements ----
    supplement_map: Dict[str, str] = {}
    supp_failures = []
    for supp in record.get("supplements") or []:
        old_code = supp.get("providerCode")
        supp_label = ((supp.get("names") or [{}])[0] or {}).get("description") or old_code or "(unnamed supplement)"
        supp_payload = dict(supp)
        supp_payload["providerCode"] = None
        supp_payload["providerRoomCodes"] = _remap_codes(supp.get("providerRoomCodes"), room_map)
        try:
            supp_res = client.create_hotel_supplement(dest_id, provider_code, supp_payload)
        except Exception as e:
            supp_failures.append(f"{supp_label} ({friendly_error_message(e)})")
            continue
        if isinstance(supp_res, dict) and "error" in supp_res:
            supp_failures.append(f"{supp_label} ({_err_detail(supp_res)})")
            continue
        new_code = supp_res.get("providerCode") if isinstance(supp_res, dict) else None
        if old_code and new_code:
            supplement_map[old_code] = new_code

    # ---- rates - remap room/offer/supplement codes throughout, incl. inside nested seasons ----
    rate_failures = []
    for rate in record.get("rates") or []:
        rate_payload = json.loads(json.dumps(rate))  # deep copy - seasons/seasonRoomPrices nest
        rate_payload["id"] = None
        rate_payload["offers"] = _remap_codes(rate.get("offers"), offer_map)
        rate_payload["supplements"] = _remap_codes(rate.get("supplements"), supplement_map)
        for season in rate_payload.get("seasons") or []:
            for srp in season.get("seasonRoomPrices") or []:
                old_room_code = srp.get("providerRoomCode")
                if old_room_code in room_map:
                    srp["providerRoomCode"] = room_map[old_room_code]
        try:
            rate_res = client.create_hotel_rates(dest_id, provider_code, rate_payload)
        except Exception as e:
            rate_failures.append(f"{rate.get('name') or '(unnamed rate)'} ({friendly_error_message(e)})")
            continue
        if isinstance(rate_res, dict) and "error" in rate_res:
            rate_failures.append(f"{rate.get('name') or '(unnamed rate)'} ({_err_detail(rate_res)})")

    if offer_failures or supp_failures or rate_failures:
        all_failures = offer_failures + supp_failures + rate_failures
        return {"name": name, "ok": "partial", "stage": "offers_supplements_rates", "new_id": provider_code,
                "detail": f"hotel and rooms created as `{provider_code}`, but "
                          f"{len(all_failures)} offer(s)/supplement(s)/rate(s) failed and the "
                          f"original was NOT closed out: {'; '.join(all_failures)}"}

    # ---- close out the ORIGINAL: no active flag and no delete endpoint exists for Hotel (see
    # this module's own docstring), so block every date instead. Re-fetches the source hotel
    # fresh right before writing, rather than reusing the copy captured when the list was
    # loaded, so a concurrent edit in Travel Compositor isn't silently overwritten. ----
    try:
        live_source = client.get_hotel(source_id, provider_code)
    except Exception as e:
        return {"name": name, "ok": "partial", "stage": "close_out", "new_id": provider_code,
                "detail": f"hotel fully created as `{provider_code}`, but couldn't re-read the "
                          f"original to close it out - it is still bookable, close it manually: "
                          f"{friendly_error_message(e)}"}
    if not isinstance(live_source, dict) or "error" in live_source:
        return {"name": name, "ok": "partial", "stage": "close_out", "new_id": provider_code,
                "detail": f"hotel fully created as `{provider_code}`, but couldn't re-read the "
                          f"original to close it out - it is still bookable, close it manually: "
                          f"{_err_detail(live_source)}"}

    close_failures = []
    all_room_names = sorted({r.get("name") for r in (live_source.get("rooms") or []) if r.get("name")})
    for rate in live_source.get("rates") or []:
        if not all_room_names:
            continue
        result = stop_sales_tool.apply_to_hotel_rate(
            client, source_id, provider_code, rate,
            [{"start": today_iso, "end": HOTEL_CLOSE_OUT_END_DATE}], all_room_names)
        if result.get("status") == "failed":
            close_failures.append(f"{rate.get('name') or '(unnamed rate)'} ({result.get('detail')})")

    if close_failures:
        return {"name": name, "ok": "partial", "stage": "close_out", "new_id": provider_code,
                "detail": f"hotel fully created as `{provider_code}`, but closing out the "
                          f"original failed on {len(close_failures)} rate(s) - it is still "
                          f"bookable, close it manually: {'; '.join(close_failures)}"}

    return {"name": name, "ok": True, "stage": "done", "new_id": provider_code}
