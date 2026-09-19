"""
hotel_automap.py — remembers which published hotels still need "Automap with master" set by hand
in Travel Compositor's back office, and the two ids a human needs to do it.

WHY THIS EXISTS (product owner, 2026-09-13): "when we create a new hotel ... we are searching for
existing hotels from travel c side and we must make sure, that Automap with master is also set,
so the hotel is not a duplicate in the travel compositor surface."

THE CONSTRAINT THAT SHAPES ALL OF THIS - confirmed against the real Swagger, not assumed: the
automap CANNOT be set through the API. Two sections were checked and both rule it out:

  * `POST`/`PUT /hotel/{supplierId}` takes `ContractHotelDetailedVO`, whose full field list is
    providerCode, hotelname, latitude, longitude, address, category, chain, currency, releaseDays,
    minimumStay, maximumStay, infantsAllowed, minimumChildrenAge, maximumChildrenAge, rooms,
    mealPlans, descriptions, voucherRemarks, images. No accommodationId, no giataId, no automap
    flag, nothing that references a master accommodation. (The response adds active, contractId,
    facilities, offers, supplements, rates - also none.)
  * The whole "Web content - Accommodations" section is read-only: six GET endpoints
    (/accommodations, /accommodations/datasheet, /accommodations/{id}/datasheet,
    /accommodations/preferred/{micrositeId}, /facilites/accommodation, /facilites/room) plus
    /mealplan/{micrositeId}. No POST, no PUT, nothing with "map" in it.

So no change to the publish payload can prevent the duplicate - the mapping is unavoidably a
human step in the back office. Which makes the failure mode a purely human one: publish a hotel,
mean to go map it, get interrupted, and find the duplicate weeks later. This module exists to make
that specific forgetting impossible rather than to automate something that cannot be automated.

Deliberately records TWO different situations, because both can end in a duplicate and they need
different things from a human:

  * `linked` - a master record WAS picked in Step 3. The accommodation id and GIATA id are known,
    so the back-office step is short and unambiguous: map this contract to that accommodation.
  * `unlinked` - the master-data search ran, found candidates, and a human deliberately skipped
    them with a stated reason (Step 3 will not let anyone past silently - see
    `_render_hotel_masterdata_step`). There is no id to map to; what a human needs here is to
    check whether the property really was absent from master data, since a wrong call at that
    moment is exactly what creates a duplicate.

A pending entry can be resolved two ways, and they are NOT interchangeable: `mark_mapped()` for
a hotel a human actually set "Automap with master" for, and `dismiss()` (2026-09-16 addition,
see its own docstring) for one that stopped needing that entirely - most commonly because the
human deleted the hotel from Travel Compositor directly instead of mapping it. Recording a
dismissal as "mapped" would leave the audit trail claiming a mapping happened that never did.

Storage: platform_store (Postgres when DATABASE_URL is set), NOT a local file - the same reasoning
as transfer_matcher.py's own store. Streamlit Cloud wipes the local filesystem on every redeploy,
and a reminder that silently disappears on the next deploy is worse than no reminder at all,
because by then the hotel is live and nobody is looking for it any more.
"""
# Stamped on every delivery - see platform_store.py's own header for why.
MODULE_BUILD = "2026-09-19-hotel-room-selection-gate-before-review"

import time
from typing import Any, Dict, List, Optional

import platform_store

_NAMESPACE = "hotel_automap"

STATUS_LINKED = "linked"
STATUS_UNLINKED = "unlinked"


def _key(supplier_id: Any, provider_code: str) -> str:
    """One entry per (supplier, hotel providerCode) pair.

    providerCode alone is NOT unique enough: it is human-assigned (confirmed - "HRG-H1", "CAI-H1",
    see ContractHotelVO's own docstring in schemas.py), so two different suppliers can easily both
    have an "HRG-H1" without either being wrong. Keying on the pair keeps one supplier's reminders
    from overwriting another's."""
    return f"{str(supplier_id).strip()}::{str(provider_code or '').strip()}"


def record_pending(
    supplier_id: Any,
    provider_code: str,
    hotel_name: str = "",
    accommodation_id: Optional[str] = None,
    giata_id: Optional[str] = None,
    master_name: Optional[str] = None,
    skip_reason: Optional[str] = None,
) -> bool:
    """Records that a just-published hotel still needs its automap set by hand.

    Call this right after a successful publish of a NEW hotel. Safe to call again for the same
    hotel - a re-publish overwrites the existing entry rather than creating a second one, and an
    entry already marked mapped is deliberately NOT resurrected (see below).

    Returns False if the write failed, and never raises: losing this reminder must not abort or
    roll back a publish that already succeeded against Travel Compositor. The caller surfaces the
    ids on screen either way, so a failed write costs the durable follow-up list, not the
    information itself."""
    if not provider_code:
        return False

    key = _key(supplier_id, provider_code)
    existing = platform_store.get(_NAMESPACE, key) or {}

    # A hotel a human has already gone and mapped stays mapped. Re-publishing it (a price update,
    # a new season) does not un-map it in Travel Compositor, so re-adding it to the pending list
    # would train people to ignore the list - the one failure mode this module exists to prevent.
    if existing.get("mapped_at"):
        return True

    record = {
        "supplier_id": str(supplier_id).strip(),
        "provider_code": str(provider_code).strip(),
        "hotel_name": hotel_name or "",
        "status": STATUS_LINKED if accommodation_id else STATUS_UNLINKED,
        "accommodation_id": str(accommodation_id).strip() if accommodation_id else None,
        "giata_id": str(giata_id).strip() if giata_id else None,
        "master_name": master_name or None,
        "skip_reason": (skip_reason or "").strip() or None,
        "recorded_at": existing.get("recorded_at") or time.time(),
        "mapped_at": None,
    }
    return platform_store.set(_NAMESPACE, key, record)


def mark_mapped(supplier_id: Any, provider_code: str) -> bool:
    """Marks a hotel as mapped, once a human confirms they've set the automap in Travel Compositor.

    Keeps the entry rather than deleting it, so `list_mapped()` can still answer "was this one
    ever dealt with, and when" - a deleted row and a never-recorded row are indistinguishable,
    and that ambiguity is the thing someone will want resolved six months from now when a
    duplicate does turn up."""
    key = _key(supplier_id, provider_code)
    record = platform_store.get(_NAMESPACE, key)
    if not record:
        return False
    record["mapped_at"] = time.time()
    return platform_store.set(_NAMESPACE, key, record)


def dismiss(supplier_id: Any, provider_code: str, reason: str = "") -> bool:
    """Marks a pending entry as no longer relevant - NOT because it was mapped, but because
    there's nothing left to map any more.

    CONFIRMED REAL CASE (product owner, 2026-09-16): published a test hotel (Steigenberger Golf
    Resort El Gouna), then deleted it directly in Travel Compositor because "the prices were
    wrong and the matches not included - therefore it was useless data" - but the automap
    checklist kept nagging about it regardless, since nothing here can see a deletion that
    happened entirely on Travel Compositor's side (same read-only-API constraint as the rest of
    this module - there's no way to detect it automatically, only a human saying so). "Mark as
    done" (mark_mapped) would be a LIE in the audit trail here - the hotel was never mapped, it
    stopped existing - so this is a separate action with its own timestamp/reason, not a reuse of
    mapped_at.

    Kept in the store (unlike forget(), which hard-deletes) specifically so the audit trail still
    answers "was this one ever dealt with, and how" for this case too - a duplicate turning up
    later under the same provider code shouldn't require re-investigating whether anyone ever
    looked at it."""
    key = _key(supplier_id, provider_code)
    record = platform_store.get(_NAMESPACE, key)
    if not record:
        return False
    record["dismissed_at"] = time.time()
    record["dismiss_reason"] = (reason or "").strip() or None
    return platform_store.set(_NAMESPACE, key, record)


def forget(supplier_id: Any, provider_code: str) -> bool:
    """Removes an entry entirely - for one recorded by mistake (e.g. a test publish). Prefer
    mark_mapped() for a hotel that was genuinely dealt with, or dismiss() for one that no longer
    exists in Travel Compositor but is still worth a trace of having been checked."""
    return platform_store.delete(_NAMESPACE, _key(supplier_id, provider_code))


def _all_records() -> List[Dict[str, Any]]:
    records = [r for r in (platform_store.get_namespace(_NAMESPACE) or {}).values()
               if isinstance(r, dict)]
    # Oldest first: something published two weeks ago and still unmapped is more urgent than
    # something published ten minutes ago, and is also the one most likely to have already
    # produced a duplicate.
    records.sort(key=lambda r: r.get("recorded_at") or 0)
    return records


def list_pending() -> List[Dict[str, Any]]:
    """Every hotel still awaiting a back-office automap, oldest first. Excludes anything already
    mapped OR dismissed (no longer exists in Travel Compositor) - both are resolved, just for
    different reasons."""
    return [r for r in _all_records() if not r.get("mapped_at") and not r.get("dismissed_at")]


def list_mapped() -> List[Dict[str, Any]]:
    """Every hotel a human has confirmed as mapped, oldest first - the audit trail."""
    return [r for r in _all_records() if r.get("mapped_at")]


def list_dismissed() -> List[Dict[str, Any]]:
    """Every hotel dismissed as no-longer-relevant (deleted in Travel Compositor, or otherwise
    moot) rather than mapped, oldest first - a separate audit trail from list_mapped() so the two
    outcomes are never conflated."""
    return [r for r in _all_records() if r.get("dismissed_at")]


def pending_count() -> int:
    """Cheap enough to call on every page render to badge a menu entry."""
    return len(list_pending())


def get(supplier_id: Any, provider_code: str) -> Optional[Dict[str, Any]]:
    return platform_store.get(_NAMESPACE, _key(supplier_id, provider_code))
