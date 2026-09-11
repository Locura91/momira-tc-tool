"""
masterdata_store.py — local cache of Travel Compositor's "Web Content Accommodations" master
hotel database, used to let a human search/match a hotel BEFORE asking anyone to hunt for photos
manually when creating a brand-new Hotel contract (product owner request, 2026-09-06 - mirrors
Travel Compositor's own manual "add hotel" screen, which offers exactly this choice).

WHY A LOCAL CACHE AT ALL (feasibility investigation, 2026-09-06, confirmed against real API
responses via Swagger):
  * GET /accommodations (the only endpoint covering Travel Compositor's full ~361,942-record
    database) has NO name/text search parameter - only 'first'/'limit' pagination. There is no
    live call that can answer "does a hotel named X exist in your master data".
  * GET /accommodations/preferred/{micrositeId} DOES support real filtering (countryCode/
    destinationId), but it's scoped to hotels Travel Compositor has marked "preferred" for this
    microsite - confirmed too narrow: a real hotel (HRG-H1, Steigenberger Golf Resort El Gouna)
    that prompted this whole investigation does NOT appear in it at all, filtered by countryCode
    EG, even though it's a well-known chain property.
  * The user confirmed (2026-09-06) that GIATA ids - the one clean cross-reference key TC's data
    carries (IdeaHotelDataVO.giataId / ApiStaticContentAccommodationVO.giataId) - are never
    present in the contracts we receive from local partners, so matching has to fall back to
    name + country + geolocation (see masterdata_matcher.py).

The only way to make "search by name" possible at all is therefore a ONE-TIME (then
periodically-refreshed) bulk sync: page through the whole lightweight list once and store it
locally, so matching happens against that local copy instead of a live call. GET /accommodations
without a datasheet call already returns everything a NAME/COUNTRY/GEO match needs per record
(id, giataId, name, geolocation, countryCode, lastUpdate) - no per-hotel detail call is needed
just to build the index; a detail call (client.get_accommodation_datasheet) is only made once, for
whichever single candidate a human actually picks.

WHERE IT'S STORED: platform_store.py, not a local file - see that module's own docstring for why
(this app runs on Streamlit Cloud, whose filesystem is wiped on every redeploy/restart). The index
is chunked into fixed-size pages under one namespace rather than written as a single giant blob,
so a sync that's interrupted partway still leaves earlier pages readable, and so no single
key/value write has to carry the entire ~30-50MB payload at once.
"""

# Stamped on every delivery - see platform_store.py's own header for why.
MODULE_BUILD = "2026-09-11-price-code-en-only"

import time
from typing import Any, Callable, Dict, List, Optional

import platform_store

_NAMESPACE = "tc_accommodation_index"
_META_KEY = "_meta"
_PAGE_SIZE_STORE = 5000   # records per stored chunk (keeps each platform_store value modest-sized)
_PAGE_SIZE_FETCH = 1000   # records per GET /accommodations call


def index_meta() -> Optional[Dict[str, Any]]:
    """Returns {'total_records', 'page_count', 'synced_at', 'complete'} for whatever sync last
    finished or was interrupted, or None if no sync has ever run. Cheap - a single small read,
    safe to call on every page render to decide whether to show a "last synced" caption."""
    return platform_store.get(_NAMESPACE, _META_KEY)


def index_is_usable() -> bool:
    """True if there's a COMPLETE local index to search against. A partial/interrupted sync
    (complete=False) is deliberately not offered for searching - a search that silently only
    covers e.g. the first 40,000 of 361,942 hotels would look like a real "not found" answer
    when it's actually just an unfinished sync."""
    meta = index_meta()
    return bool(meta and meta.get("complete"))


def load_index() -> List[Dict[str, Any]]:
    """Loads the full local index into memory (list of {id, giataId, name, geolocation,
    countryCode, lastUpdate} dicts). Empty list if no complete sync exists yet. Callers (the
    Streamlit hotel flow) should cache this in st.session_state per session rather than calling
    it on every rerun - at ~361k small dicts this is a few tens of MB, fine to hold in memory
    once, wasteful to re-read from the store on every widget interaction."""
    meta = index_meta()
    if not meta or not meta.get("complete"):
        return []
    records: List[Dict[str, Any]] = []
    for page_num in range(meta.get("page_count", 0)):
        page = platform_store.get(_NAMESPACE, f"page_{page_num}")
        if isinstance(page, list):
            records.extend(page)
    return records


def sync_accommodation_index(client, progress_callback: Optional[Callable[[int, int], None]] = None) -> Dict[str, Any]:
    """
    Pages through GET /accommodations (client: TravelCompositorAPI) from scratch and rewrites the
    local index. Meant to be run as an explicit, human-triggered action (a button in the app) -
    not on every page load - since a full pass is ~361 calls at limit=1000 and takes real time.

    progress_callback(fetched_so_far, total_results), if given, is called after every page so a
    Streamlit progress bar can be driven.

    Returns {'ok': bool, 'total_records': int, 'error': str|None}. Deliberately does NOT mark the
    index complete (and does not touch the previously-stored pages) until every page has been
    fetched successfully - a failure partway through leaves whatever index already existed intact
    and usable, rather than replacing it with a half-written one. On failure, the newly-fetched
    partial pages are written under a separate in-progress marker only, never promoted.
    """
    first = 0
    total_results: Optional[int] = None
    fetched_records: List[Dict[str, Any]] = []
    page_num = 0

    while True:
        resp = client.get_accommodations_page(first=first, limit=_PAGE_SIZE_FETCH)
        if not isinstance(resp, dict) or "error" in resp:
            return {"ok": False, "total_records": len(fetched_records),
                     "error": f"Sync failed at offset {first}: {resp.get('message') if isinstance(resp, dict) else resp}"}

        batch = resp.get("accommodations") or []
        pagination = resp.get("pagination") or {}
        if total_results is None:
            total_results = pagination.get("totalResults")

        fetched_records.extend(
            {
                "id": r.get("id"),
                "giataId": r.get("giataId"),
                "name": r.get("name"),
                "geolocation": r.get("geolocation") or {},
                "countryCode": r.get("countryCode"),
                "lastUpdate": r.get("lastUpdate"),
            }
            for r in batch if isinstance(r, dict) and r.get("id")
        )

        if progress_callback:
            try:
                progress_callback(len(fetched_records), total_results or 0)
            except Exception:
                pass

        if not batch or (total_results is not None and first + len(batch) >= total_results):
            break
        first += len(batch)

    # Only now, with a fully-fetched list in hand, replace the stored index - chunked into
    # fixed-size pages so no single write carries the whole thing.
    page_count = 0
    for chunk_start in range(0, len(fetched_records), _PAGE_SIZE_STORE):
        chunk = fetched_records[chunk_start:chunk_start + _PAGE_SIZE_STORE]
        if not platform_store.set(_NAMESPACE, f"page_{page_count}", chunk):
            return {"ok": False, "total_records": len(fetched_records),
                     "error": "Sync fetched all records but failed writing them to storage."}
        page_count += 1

    # Delete any leftover pages from a previous, larger sync.
    prior_meta = platform_store.get(_NAMESPACE, _META_KEY)
    if isinstance(prior_meta, dict):
        for stale_page in range(page_count, prior_meta.get("page_count", 0)):
            platform_store.delete(_NAMESPACE, f"page_{stale_page}")

    platform_store.set(_NAMESPACE, _META_KEY, {
        "total_records": len(fetched_records),
        "page_count": page_count,
        "synced_at": time.time(),
        "complete": True,
    })

    return {"ok": True, "total_records": len(fetched_records), "error": None}
