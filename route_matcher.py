"""
route_matcher.py

Shared route-matching logic for Transfer and Transport. Both product types face
the same "recognize the correct existing record for an update" problem: TC has
no human-assigned code, only a generated id (TRANSFER-412545 / TRANSPORT-412579).

This module provides a RouteMatcher class parameterized by product type, with:
  1. PRIMARY - app-tracked id: remembered route -> TC id mappings (platform_store)
  2. FALLBACK - departure/arrival text similarity for human-confirmed matching

transfer_matcher.py and transport_matcher.py are thin wrappers that instantiate
this with the right product_type string and re-export the same public API.
"""
MODULE_BUILD = "2026-10-24-rollover-ext"

import os
import json
import re
import difflib
from typing import Dict, Any, List, Optional

import platform_store
from text_normalize import normalize_name

# CONFIRMED REAL RULE (product owner): "when Transfer or Transport says a three letter code
# from Airport, this must be seen as a City name too" - e.g. "HRG to El Gouna" is the same
# route as "Hurghada to El Gouna". Only the codes already confirmed elsewhere in this codebase
# are covered here; extend as more are confirmed rather than guessing at codes nobody has verified.
AIRPORT_CODE_TO_CITY = {
    "RMF": "Marsa Alam", "HRG": "Hurghada", "SSH": "Sharm El Sheikh", "CAI": "Cairo",
}


def expand_airport_codes(text: str) -> str:
    result = text or ""
    for code, city in AIRPORT_CODE_TO_CITY.items():
        result = re.sub(rf"\b{code}\b", city, result, flags=re.IGNORECASE)
    return result


def name_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(
        None, expand_airport_codes(a).strip().lower(), expand_airport_codes(b).strip().lower()
    ).ratio()


def route_key(departure_name: str, arrival_name: str) -> str:
    """Normalizes a departure/arrival pair into a stable dict key so trivial formatting
    differences don't create duplicate tracked entries for what is really the same route."""
    return f"{normalize_name(departure_name)}::{normalize_name(arrival_name)}"


class RouteMatcher:
    """Parameterized route matcher for a single product type (transfer or transport)."""

    def __init__(self, product_type: str, legacy_env_var: str, legacy_filename: str):
        self._product_type = product_type
        self._namespace = f"{product_type}_match"
        self._legacy_store_path = os.getenv(
            legacy_env_var,
            os.path.join(os.path.dirname(os.path.abspath(__file__)), legacy_filename),
        )

    def _load_store(self) -> Dict[str, Any]:
        """Reads the whole supplier -> {route: tc_id} map from the shared durable store.
        A legacy JSON file is still read ONCE if present and merged in."""
        store = platform_store.get_namespace(self._namespace)
        if not store and os.path.exists(self._legacy_store_path):
            try:
                with open(self._legacy_store_path, "r") as f:
                    legacy = json.load(f)
                if legacy:
                    for supplier_key, routes in legacy.items():
                        platform_store.set(self._namespace, str(supplier_key), routes)
                    print(f"📦 Migrated {len(legacy)} supplier(s) of {self._product_type} match history "
                          f"from {self._legacy_store_path} into durable storage.")
                    return legacy
            except Exception as e:
                print(f"⚠️ Could not read the legacy {self._product_type} match store ({e}) - starting fresh "
                      f"(falls back to name matching, nothing breaks).")
        return store

    def _save_supplier(self, supplier_key: str, routes: Dict[str, Any]) -> None:
        if not platform_store.set(self._namespace, str(supplier_key), routes):
            print(f"⚠️ Could not save the {self._product_type} match store - the id -> route mapping just "
                  "learned won't persist to the next session, but nothing about this upload failed.")

    def remember_id(self, supplier_id: str, departure_name: str, arrival_name: str, record_id: str) -> None:
        """Call right after a successful create, OR right after a human explicitly confirms a match."""
        if not (supplier_id and departure_name and arrival_name and record_id):
            return
        supplier_key = str(supplier_id)
        routes = platform_store.get(self._namespace, supplier_key) or {}
        routes[route_key(departure_name, arrival_name)] = record_id
        self._save_supplier(supplier_key, routes)
        print(f"📌 Remembered TC id '{record_id}' for route '{departure_name}' -> '{arrival_name}' "
              f"(supplier {supplier_id}) - future updates to this route will auto-match.")

    def forget_id(self, supplier_id: str, departure_name: str, arrival_name: str) -> None:
        """Removes a tracked mapping - e.g. if a human later says a previous auto-match was wrong."""
        supplier_key = str(supplier_id)
        routes = platform_store.get(self._namespace, supplier_key)
        if routes:
            routes.pop(route_key(departure_name, arrival_name), None)
            self._save_supplier(supplier_key, routes)

    def lookup_tracked_id(self, supplier_id: str, departure_name: str, arrival_name: str) -> Optional[str]:
        """PRIMARY matching step - returns a TC id if this app has already created/confirmed a
        match for this exact route before, or None."""
        self._load_store()
        routes = platform_store.get(self._namespace, str(supplier_id)) or {}
        return routes.get(route_key(departure_name, arrival_name))


# ---------------------------------------------------------------------------
# Transfer-specific: suggest matches using nested departure/arrival.name fields
# ---------------------------------------------------------------------------

def suggest_transfer_matches(departure_name: str, arrival_name: str, existing_transfers: List[dict],
                             top_n: int = 5) -> List[Dict[str, Any]]:
    """FALLBACK matching step for transfers. Scores by departure/arrival.name similarity."""
    scored = []
    for t in existing_transfers or []:
        if not isinstance(t, dict):
            continue
        t_departure = (t.get("departure") or {}).get("name", "")
        t_arrival = (t.get("arrival") or {}).get("name", "")
        dep_score = name_similarity(departure_name, t_departure)
        arr_score = name_similarity(arrival_name, t_arrival)
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


# ---------------------------------------------------------------------------
# Transport-specific: suggest matches using the top-level 'name' field (no nested departure/arrival)
# ---------------------------------------------------------------------------

def _half_score(place_name: str, route_name: str) -> float:
    """Transport's 'name' field is a full descriptive route name, not a bare place name.
    Substring containment is checked first for strong credit; ratio() is the fallback."""
    place_clean = expand_airport_codes(place_name or "").strip().lower()
    route_clean = expand_airport_codes(route_name or "").strip().lower()
    if place_clean and place_clean in route_clean:
        return 0.9
    return name_similarity(place_name, route_name)


def suggest_transport_matches(departure_name: str, arrival_name: str, existing_transports: List[dict],
                              top_n: int = 5) -> List[Dict[str, Any]]:
    """FALLBACK matching step for transports. Scores by top-level name field similarity."""
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


def match_bracket_to_existing_option(min_passengers: int, max_passengers: int,
                                     existing_options: List[dict]) -> Optional[dict]:
    """UNIQUE TO TRANSPORT. Finds which existing option corresponds to a newly-extracted bracket
    by minPassengers/maxPassengers overlap. Prefers an EXACT min/max match; falls back to the
    existing option with the most overlapping passenger range."""
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


# ---------------------------------------------------------------------------
# Resolve wrappers (combine tracked + fallback for a UI to call in one shot)
# ---------------------------------------------------------------------------

def resolve_transfer_match(matcher: RouteMatcher, client, supplier_id: str,
                           departure_name: str, arrival_name: str) -> Dict[str, Any]:
    tracked_id = matcher.lookup_tracked_id(supplier_id, departure_name, arrival_name)
    if tracked_id:
        return {"tracked_id": tracked_id, "fallback_candidates": [], "fetch_error": None}
    result = client.get_transfers(supplier_id)
    if isinstance(result, dict) and "error" in result:
        return {"tracked_id": None, "fallback_candidates": [], "fetch_error": result}
    existing = result.get("transfer", []) if isinstance(result, dict) else (result or [])
    candidates = suggest_transfer_matches(departure_name, arrival_name, existing)
    return {"tracked_id": None, "fallback_candidates": candidates, "fetch_error": None}


def resolve_transport_match(matcher: RouteMatcher, client, supplier_id: str,
                            departure_name: str, arrival_name: str) -> Dict[str, Any]:
    tracked_id = matcher.lookup_tracked_id(supplier_id, departure_name, arrival_name)
    if tracked_id:
        return {"tracked_id": tracked_id, "fallback_candidates": [], "fetch_error": None}
    result = client.get_transports(supplier_id)
    if isinstance(result, dict) and "error" in result:
        return {"tracked_id": None, "fallback_candidates": [], "fetch_error": result}
    existing = result.get("transport", []) if isinstance(result, dict) else (result or [])
    candidates = suggest_transport_matches(departure_name, arrival_name, existing)
    return {"tracked_id": None, "fallback_candidates": candidates, "fetch_error": None}
