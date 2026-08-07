"""
transport_matcher.py

Solves the same "recognize the correct existing record for an update" problem
already solved for Transfer (see transfer_matcher.py) - Travel Compositor's
Transport schema also has NO human-assigned code anywhere, only a TC-generated
id like "TRANSPORT-412579". Mirrors transfer_matcher.py's confirmed approach:

  1. PRIMARY - app-tracked id: once this app creates (or a human confirms a
     match for) a transport, it remembers the route -> TC id mapping locally,
     keyed by supplier. Any future update for the "same" route reuses that id
     automatically, no human step needed.
  2. FALLBACK - departure/arrival similarity: for a transport this app has
     never seen before, score every transport in the supplier's full live
     list (GET /transport/{supplierId}) by how closely its departure/arrival
     segment locations match the new route, and present the best candidates
     to a human. Never decides on its own - a human always explicitly
     confirms (or rejects) a match before anything gets treated as an
     update-to-existing rather than a brand-new create. Once confirmed,
     remember_transport_id() should be called so the same route never needs
     this fallback step again.

ADDITIONAL PROBLEM UNIQUE TO TRANSPORT (Transfer doesn't have this): per-
occupancy pricing is modelled as SEPARATE OPTION SUB-RESOURCES, one per
passenger bracket (see ContractTransportOptionVO's docstring in schemas.py) -
so recognizing "the existing transport" isn't enough on its own; updating a
transport's prices also means recognizing WHICH existing option corresponds
to each bracket in the newly-extracted rate sheet, so that option's price can
be updated in place rather than a duplicate being created. Real option codes
are not predictable/derivable (confirmed: "ASWHRG", "PraslinLaDigue12", and
codes literally equal to the transport's own name all seen in real data), so
this matches by minPassengers/maxPassengers overlap instead of by code - see
match_bracket_to_existing_option() below.

Storage: same plain local JSON file approach as transfer_matcher.py (separate
file so the two don't collide) - safe to lose, worst case falls back to the
departure/arrival matching step again. Source of truth is always Travel
Compositor itself (confirmed via get_transport/get_transports).
"""
import os
import json
import re
import difflib
from typing import Dict, Any, List, Optional

_STORE_PATH = os.getenv(
    "TRANSPORT_MATCH_STORE_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "transport_match_store.json"),
)


def _load_store() -> Dict[str, Any]:
    if not os.path.exists(_STORE_PATH):
        return {}
    try:
        with open(_STORE_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ Could not read transport match store ({e}) - starting fresh (falls back to departure/arrival matching).")
        return {}


def _save_store(store: Dict[str, Any]) -> None:
    try:
        with open(_STORE_PATH, "w") as f:
            json.dump(store, f, indent=2)
    except Exception as e:
        print(f"⚠️ Could not save transport match store ({e}) - the id -> route mapping just learned won't persist "
              f"to the next session, but nothing about this upload itself failed.")


def _route_key(departure_name: str, arrival_name: str) -> str:
    """Normalizes a departure/arrival pair into a stable dict key - same convention as
    transfer_matcher._route_key(), so trivial formatting differences don't create duplicate
    tracked entries for what is really the same route."""
    def norm(s):
        return re.sub(r"\s+", " ", (s or "").strip().lower())
    return f"{norm(departure_name)}::{norm(arrival_name)}"


def remember_transport_id(supplier_id: str, departure_name: str, arrival_name: str, transport_id: str) -> None:
    """Call right after a successful create, OR right after a human explicitly confirms a
    departure/arrival-matched candidate as the correct existing transport."""
    if not (supplier_id and departure_name and arrival_name and transport_id):
        return
    store = _load_store()
    supplier_key = str(supplier_id)
    store.setdefault(supplier_key, {})
    store[supplier_key][_route_key(departure_name, arrival_name)] = transport_id
    _save_store(store)
    print(f"📌 Remembered TC id '{transport_id}' for route '{departure_name}' -> '{arrival_name}' "
          f"(supplier {supplier_id}) - future updates to this route will auto-match.")


def forget_transport_id(supplier_id: str, departure_name: str, arrival_name: str) -> None:
    """Removes a tracked mapping - e.g. if a human later says a previous auto-match was wrong."""
    store = _load_store()
    supplier_key = str(supplier_id)
    if supplier_key in store:
        store[supplier_key].pop(_route_key(departure_name, arrival_name), None)
        _save_store(store)


def lookup_tracked_transport_id(supplier_id: str, departure_name: str, arrival_name: str) -> Optional[str]:
    """PRIMARY matching step - returns a TC id if this app has already created/confirmed a
    match for this exact route before, or None if it isn't tracked yet (caller should then fall
    back to suggest_existing_transport_matches)."""
    store = _load_store()
    supplier_key = str(supplier_id)
    return store.get(supplier_key, {}).get(_route_key(departure_name, arrival_name))


def _name_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, (a or "").strip().lower(), (b or "").strip().lower()).ratio()


def suggest_existing_transport_matches(departure_name: str, arrival_name: str, existing_transports: List[dict],
                                        top_n: int = 5) -> List[Dict[str, Any]]:
    """
    FALLBACK matching step - NEVER auto-applied. Scores every transport in `existing_transports`
    (the full list from a real GET /transport/{supplierId} response) against the new departure/
    arrival names using text similarity, and returns the top N candidates ranked best-first so a
    human can explicitly confirm or reject.

    NOTE: unlike Transfer (which has a nested departure/arrival.name field), Transport's location
    is only available as a raw LOCATION CODE on each segment (e.g. "meet_aswan") - there's no
    human-readable name on the transport record itself, only on the resolved Transport Base. This
    matches against the transport's own top-level 'name' field instead (confirmed real examples:
    "Aswan - Hurghada", "One-way transfer Praslin - La Digue" - route names in practice), scored
    against BOTH the departure and arrival name individually so a match doesn't require exact
    word order.

    Each candidate: {"transport_id", "name", "score" (0.0-1.0, higher = more likely the same route)}.
    """
    def _half_score(place_name: str, route_name: str) -> float:
        # A short place name (e.g. "Praslin") against a longer descriptive route name (e.g.
        # "One-way transfer Praslin - La Digue") scores poorly under plain SequenceMatcher.ratio()
        # even on a perfect substring match, since ratio is diluted by the length difference of
        # the two strings - a real risk here since Transport's own 'name' field is typically a
        # full descriptive route name, not just the bare place name the way Transfer's departure/
        # arrival.name fields are. Substring containment is checked first and given strong credit;
        # ratio() is only the fallback for genuinely fuzzy (non-substring) matches.
        place_clean = (place_name or "").strip().lower()
        route_clean = (route_name or "").strip().lower()
        if place_clean and place_clean in route_clean:
            return 0.9
        return _name_similarity(place_name, route_name)

    scored = []
    for t in existing_transports or []:
        if not isinstance(t, dict):
            continue
        t_name = t.get("name", "")
        dep_score = _half_score(departure_name, t_name)
        arr_score = _half_score(arrival_name, t_name)
        score = (dep_score + arr_score) / 2
        scored.append({"transport_id": t.get("id"), "name": t_name, "score": round(score, 3)})
    scored.sort(key=lambda c: c["score"], reverse=True)
    return scored[:top_n]


def resolve_transport_match(client, supplier_id: str, departure_name: str, arrival_name: str) -> Dict[str, Any]:
    """
    Convenience wrapper combining both matching steps, mirroring transfer_matcher.resolve_transfer_match():
      {"tracked_id": str or None,       # safe to use directly if set
       "fallback_candidates": [...],    # only populated if tracked_id is None - MUST be human-confirmed
       "fetch_error": dict or None}     # set if get_transports() itself failed
    """
    tracked_id = lookup_tracked_transport_id(supplier_id, departure_name, arrival_name)
    if tracked_id:
        return {"tracked_id": tracked_id, "fallback_candidates": [], "fetch_error": None}

    result = client.get_transports(supplier_id)
    if isinstance(result, dict) and "error" in result:
        return {"tracked_id": None, "fallback_candidates": [], "fetch_error": result}

    existing = result.get("transport", []) if isinstance(result, dict) else (result or [])
    candidates = suggest_existing_transport_matches(departure_name, arrival_name, existing)
    return {"tracked_id": None, "fallback_candidates": candidates, "fetch_error": None}


def match_bracket_to_existing_option(min_passengers: int, max_passengers: int,
                                      existing_options: List[dict]) -> Optional[dict]:
    """
    UNIQUE TO TRANSPORT (Transfer has no equivalent - its occupancy pricing is one flat array on
    the single record, fully overwritten on every update). Finds which, if any, existing option
    (a real ContractTransportOptionVO dict, as returned by get_transport_option) corresponds to a
    newly-extracted bracket, so its price can be updated in place instead of creating a duplicate.

    Matches by minPassengers/maxPassengers OVERLAP, not by code - real option codes are not
    predictable or derivable from the bracket (confirmed: "ASWHRG", "PraslinLaDigue12", and codes
    literally equal to the transport's own name all seen in real data), so code can never be used
    to recognize "the same bracket" across a rate refresh. Prefers an EXACT min/max match; if none
    exists, falls back to the existing option with the most overlapping passenger range (handles
    a rate sheet that reshuffled bracket boundaries, e.g. last year's "5-8 pax" becoming this
    year's "5-6" + "7-8").

    Returns the matched existing option dict, or None if no existing option overlaps at all (this
    bracket is genuinely new - create it rather than update).
    """
    exact = [
        o for o in (existing_options or [])
        if isinstance(o, dict) and o.get("minPassengers") == min_passengers and o.get("maxPassengers") == max_passengers
    ]
    if exact:
        return exact[0]

    def _overlap(o: dict) -> int:
        o_min, o_max = o.get("minPassengers"), o.get("maxPassengers")
        if o_min is None or o_max is None:
            return 0
        return max(0, min(max_passengers, o_max) - max(min_passengers, o_min) + 1)

    candidates = [(o, _overlap(o)) for o in (existing_options or []) if isinstance(o, dict)]
    candidates = [(o, overlap) for o, overlap in candidates if overlap > 0]
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[1], reverse=True)
    return candidates[0][0]