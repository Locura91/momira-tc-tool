"""
transfer_matcher.py

Solves the "recognize the correct Transfer for an update" problem the
product owner flagged as the central design challenge for this feature:
unlike ClosedTour (providerCode) or Ticket (code), Travel Compositor's
Transfer schema has NO human-assigned code anywhere - only a TC-generated
id like "TRANSFER-412545" that's only known once a transfer has already
been created/fetched via the API.

CONFIRMED APPROACH (agreed with the product owner):
  1. PRIMARY - app-tracked id: once this app creates (or a human confirms a
     match for) a transfer, it remembers the route -> TC id mapping locally,
     keyed by supplier. Any future update for the "same" route reuses that
     id automatically, no human step needed.
  2. FALLBACK - departure/arrival similarity: for a transfer this app has
     never seen before (e.g. one that already existed in Travel Compositor
     before this tool was used), score every transfer in the supplier's
     full live list (GET /transfer/{supplierId}) by how closely its
     departure/arrival names match the new route, and present the best
     candidates to a human. This function NEVER decides on its own - the
     human always explicitly confirms (or rejects) a match before anything
     gets treated as an update-to-existing rather than a brand-new create.
     Once confirmed, remember_transfer_id() should be called so the same
     route never needs this fallback step again.

Storage: a plain local JSON file (no server-side persistence exists for
this mapping anywhere in Travel Compositor). Safe to lose - worst case the
app just falls back to the departure/arrival matching step again next time,
it is a convenience cache, never a source of truth. Source of truth is
always Travel Compositor itself (confirmed via get_transfer/get_transfers).
"""
import os
import json
import re
import difflib
from typing import Dict, Any, List, Optional

_STORE_PATH = os.getenv(
    "TRANSFER_MATCH_STORE_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "transfer_match_store.json"),
)


def _load_store() -> Dict[str, Any]:
    if not os.path.exists(_STORE_PATH):
        return {}
    try:
        with open(_STORE_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ Could not read transfer match store ({e}) - starting fresh (falls back to departure/arrival matching).")
        return {}


def _save_store(store: Dict[str, Any]) -> None:
    try:
        with open(_STORE_PATH, "w") as f:
            json.dump(store, f, indent=2)
    except Exception as e:
        print(f"⚠️ Could not save transfer match store ({e}) - the id -> route mapping just learned won't persist "
              f"to the next session, but nothing about this upload itself failed.")


def _route_key(departure_name: str, arrival_name: str) -> str:
    """Normalizes a departure/arrival pair into a stable dict key (lowercased, whitespace-
    collapsed) so trivial formatting differences don't create duplicate tracked entries
    for what is really the same route."""
    def norm(s):
        return re.sub(r"\s+", " ", (s or "").strip().lower())
    return f"{norm(departure_name)}::{norm(arrival_name)}"


def remember_transfer_id(supplier_id: str, departure_name: str, arrival_name: str, transfer_id: str) -> None:
    """
    Call this right after a successful create, OR right after a human
    explicitly confirms a departure/arrival-matched candidate as the
    correct existing transfer - so future updates to this exact route
    auto-match via lookup_tracked_transfer_id() without needing the
    fallback matching step (or a human decision) again.
    """
    if not (supplier_id and departure_name and arrival_name and transfer_id):
        return
    store = _load_store()
    supplier_key = str(supplier_id)
    store.setdefault(supplier_key, {})
    store[supplier_key][_route_key(departure_name, arrival_name)] = transfer_id
    _save_store(store)
    print(f"📌 Remembered TC id '{transfer_id}' for route '{departure_name}' -> '{arrival_name}' "
          f"(supplier {supplier_id}) - future updates to this route will auto-match.")


def forget_transfer_id(supplier_id: str, departure_name: str, arrival_name: str) -> None:
    """Removes a tracked mapping - e.g. if a human later says a previous auto-match was wrong."""
    store = _load_store()
    supplier_key = str(supplier_id)
    if supplier_key in store:
        store[supplier_key].pop(_route_key(departure_name, arrival_name), None)
        _save_store(store)


def lookup_tracked_transfer_id(supplier_id: str, departure_name: str, arrival_name: str) -> Optional[str]:
    """PRIMARY matching step - returns a TC id if this app has already created/confirmed a
    match for this exact route before, or None if it isn't tracked yet (caller should then
    fall back to suggest_existing_transfer_matches)."""
    store = _load_store()
    supplier_key = str(supplier_id)
    return store.get(supplier_key, {}).get(_route_key(departure_name, arrival_name))


def _name_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, (a or "").strip().lower(), (b or "").strip().lower()).ratio()


def suggest_existing_transfer_matches(departure_name: str, arrival_name: str, existing_transfers: List[dict],
                                       top_n: int = 5) -> List[Dict[str, Any]]:
    """
    FALLBACK matching step - NEVER auto-applied. Scores every transfer in
    `existing_transfers` (the full list from a real GET /transfer/{supplierId}
    response) against the new departure/arrival names using plain text
    similarity on each side's 'name' field, and returns the top N candidates
    ranked best-first so a human can look at them and explicitly confirm or
    reject - this app never overwrites an existing transfer based on this
    fallback alone. Once a human confirms one, call remember_transfer_id()
    so this step isn't needed again for the same route.

    Each candidate: {"transfer_id", "name", "departure_name", "arrival_name",
    "score" (0.0-1.0, higher = more likely the same route)}.
    """
    scored = []
    for t in existing_transfers or []:
        if not isinstance(t, dict):
            continue
        t_departure = (t.get("departure") or {}).get("name", "")
        t_arrival = (t.get("arrival") or {}).get("name", "")
        dep_score = _name_similarity(departure_name, t_departure)
        arr_score = _name_similarity(arrival_name, t_arrival)
        score = (dep_score + arr_score) / 2
        scored.append({
            "transfer_id": t.get("id"),
            "name": t.get("name", ""),
            "departure_name": t_departure,
            "arrival_name": t_arrival,
            "score": round(score, 3),
        })
    scored.sort(key=lambda c: c["score"], reverse=True)
    return scored[:top_n]


def resolve_transfer_match(client, supplier_id: str, departure_name: str, arrival_name: str) -> Dict[str, Any]:
    """
    Convenience wrapper combining both matching steps for a UI to call in
    one shot: tries the app-tracked id first (silent, safe to auto-apply),
    and only if that comes up empty, fetches the supplier's full live
    transfer list and returns ranked fallback candidates for a human to
    confirm.

    Returns:
      {"tracked_id": str or None,       # safe to use directly if set
       "fallback_candidates": [...],    # only populated if tracked_id is None - MUST be human-confirmed
       "fetch_error": dict or None}     # set if get_transfers() itself failed - candidates will be empty, not a hard failure
    """
    tracked_id = lookup_tracked_transfer_id(supplier_id, departure_name, arrival_name)
    if tracked_id:
        return {"tracked_id": tracked_id, "fallback_candidates": [], "fetch_error": None}

    result = client.get_transfers(supplier_id)
    if isinstance(result, dict) and "error" in result:
        return {"tracked_id": None, "fallback_candidates": [], "fetch_error": result}

    existing = result.get("transfer", []) if isinstance(result, dict) else (result or [])
    candidates = suggest_existing_transfer_matches(departure_name, arrival_name, existing)
    return {"tracked_id": None, "fallback_candidates": candidates, "fetch_error": None}
