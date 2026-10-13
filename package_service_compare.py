"""
package_service_compare.py — Service-level comparison between a Holiday Package's current
departure and a proposed replacement calendar entry.

CONFIRMED BUSINESS RULE (product owner, 2026-10-13): auto-update is only safe when ALL
of the following match exactly between the current package and the proposed departure:
  - Flights (same flight, with luggage / baggage)
  - Hotels (same hotel, same meal type)
  - Transfers (all, same)
  - Tickets (all, same)
  - Transports (all, same)
  - Closed tours (all, same)

If any component differs the update must NOT be applied automatically — the human decides.

This module is deliberately a pure comparison helper (no Streamlit, no API calls) so it
can be unit-tested without the app running.  The render helpers that call st.* live in
package_rollover_tool.py.

FIELD-NAME STATUS (2026-10-13): Travel Compositor's exact field names for components inside
a calendar entry / day-to-day response are NOT YET CONFIRMED against a real API response.
The extractors below use the same best-effort heuristic as package_rollover_rules._find_field
— broad case-insensitive substring matching on candidate key names, with the real matched key
always surfaced alongside the value so the human (and a future code fix) can see exactly
what was matched.  On the first real test run, every matched key is shown in the UI so the
field names can be confirmed or corrected.
"""
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Component type labels (consistent across the UI)
# ---------------------------------------------------------------------------
COMPONENT_TYPES = ["flight", "hotel", "transfer", "ticket", "transport", "closedtour"]

_COMPONENT_KEY_HINTS: Dict[str, List[str]] = {
    "flight":      ["flight", "air", "fly"],
    "hotel":       ["hotel", "accommodation", "property", "lodg"],
    "transfer":    ["transfer"],
    "ticket":      ["ticket", "entry", "attraction"],
    "transport":   ["transport", "bus", "coach", "rail", "train"],
    "closedtour":  ["closedtour", "closed_tour", "cruise", "tour"],
}

# Fields that identify a flight's luggage/baggage allowance
_BAGGAGE_HINTS = ["baggage", "luggage", "bag", "checkedin", "checked_in", "allowance"]

# Fields that identify a hotel's meal plan
_MEAL_HINTS = ["meal", "board", "basis", "catering", "mealplan", "meal_plan",
               "mealtype", "meal_type"]

# Fields we try to use as a stable component identity (ID, code, name — in priority order)
_ID_HINTS    = ["id", "code", "ref", "reference", "identifier"]
_NAME_HINTS  = ["name", "title", "description", "label"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _lowered(d: Dict) -> Dict[str, Tuple[str, Any]]:
    """Returns {lower_key: (real_key, value)} for every top-level entry in d."""
    return {k.lower(): (k, v) for k, v in d.items()} if isinstance(d, dict) else {}


def _first_match(lowered_dict: Dict[str, Tuple[str, Any]],
                 hints: List[str]) -> Tuple[Optional[str], Any]:
    """Returns (real_key, value) for the first hint that appears as a substring of any key,
    or (None, None) if nothing matches."""
    for key_l, (real_key, value) in lowered_dict.items():
        for hint in hints:
            if hint in key_l:
                return real_key, value
    return None, None


def _extract_id(entry: Dict) -> Tuple[Optional[str], Any]:
    lw = _lowered(entry)
    k, v = _first_match(lw, _ID_HINTS)
    if v is not None:
        return k, str(v)
    k, v = _first_match(lw, _NAME_HINTS)
    return k, str(v) if v is not None else None


def _extract_meal(entry: Dict) -> Tuple[Optional[str], Any]:
    lw = _lowered(entry)
    return _first_match(lw, _MEAL_HINTS)


def _extract_baggage(entry: Dict) -> Tuple[Optional[str], Any]:
    lw = _lowered(entry)
    return _first_match(lw, _BAGGAGE_HINTS)


def _find_list_for_type(data: Any, ctype: str) -> List[Dict]:
    """Tries to pull the list of component entries for a given component type out of a
    package response object.  TC may nest them under a key like 'flights', 'hotels', etc.,
    or the top-level object itself might be a list.  Returns [] when nothing is found."""
    if not isinstance(data, dict):
        return []
    hints = _COMPONENT_KEY_HINTS[ctype]
    lw = _lowered(data)
    for key_l, (real_key, value) in lw.items():
        if any(h in key_l for h in hints):
            if isinstance(value, list):
                return [v for v in value if isinstance(v, dict)]
            if isinstance(value, dict):
                return [value]
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_components(data: Any) -> Dict[str, List[Dict]]:
    """Returns {component_type: [list of raw dicts]} for all six component types,
    extracted from a package day-to-day or calendar-entry response."""
    return {ctype: _find_list_for_type(data, ctype) for ctype in COMPONENT_TYPES}


def summarise_component(ctype: str, entry: Dict) -> Dict[str, Any]:
    """Produces a human-readable summary dict for one component entry, always surfacing
    the real matched field names so the first real test can confirm them."""
    id_key, id_val = _extract_id(entry)
    out: Dict[str, Any] = {
        "type": ctype,
        "id_field": id_key,
        "id_value": id_val,
        "raw_keys": list(entry.keys()),
    }
    if ctype == "hotel":
        meal_key, meal_val = _extract_meal(entry)
        out["meal_field"] = meal_key
        out["meal_value"] = meal_val
    if ctype == "flight":
        bag_key, bag_val = _extract_baggage(entry)
        out["baggage_field"] = bag_key
        out["baggage_value"] = bag_val
    return out


def _component_signature(ctype: str, entry: Dict) -> str:
    """A normalised, comparable string identity for one component — used to decide whether
    current and proposed entries are the same.  Deliberately coarse (lowercase, stripped)
    to survive minor formatting differences; deliberately includes meal/baggage for hotels
    and flights so those business-rule attributes are part of the match."""
    _, id_val = _extract_id(entry)
    base = str(id_val or "").strip().lower()
    if ctype == "hotel":
        _, meal = _extract_meal(entry)
        base += "|meal=" + str(meal or "").strip().lower()
    if ctype == "flight":
        _, bag = _extract_baggage(entry)
        base += "|bag=" + str(bag or "").strip().lower()
    return base


def compare_components(
    current_data: Any,
    proposed_data: Any,
) -> Dict[str, Any]:
    """
    Top-level comparison.  Returns:
    {
      "all_match": bool,
      "components": {
        "flight": {"current": [...summaries], "proposed": [...summaries],
                   "match": bool, "reason": str},
        "hotel":  {...},
        ...
      },
      "unknown_fields": bool,   # True if any component had no id/meal/baggage field matched
    }
    """
    current_by_type  = extract_components(current_data)
    proposed_by_type = extract_components(proposed_data)

    result: Dict[str, Any] = {"components": {}, "unknown_fields": False}
    all_match = True

    for ctype in COMPONENT_TYPES:
        cur_list  = current_by_type[ctype]
        prop_list = proposed_by_type[ctype]

        cur_summaries  = [summarise_component(ctype, e) for e in cur_list]
        prop_summaries = [summarise_component(ctype, e) for e in prop_list]

        # Check for unresolved field names
        for s in cur_summaries + prop_summaries:
            if s["id_value"] is None:
                result["unknown_fields"] = True
            if ctype == "hotel" and s.get("meal_field") is None and s["id_value"] is not None:
                result["unknown_fields"] = True
            if ctype == "flight" and s.get("baggage_field") is None and s["id_value"] is not None:
                result["unknown_fields"] = True

        cur_sigs  = sorted(_component_signature(ctype, e) for e in cur_list)
        prop_sigs = sorted(_component_signature(ctype, e) for e in prop_list)

        if cur_sigs == prop_sigs:
            match = True
            reason = "identical"
        elif not cur_list and not prop_list:
            match = True
            reason = "none in either"
        elif not cur_list:
            match = False
            reason = f"current has none, proposed has {len(prop_list)}"
        elif not prop_list:
            match = False
            reason = f"current has {len(cur_list)}, proposed has none"
        else:
            match = False
            reason = "components differ"

        if not match:
            all_match = False

        result["components"][ctype] = {
            "current":  cur_summaries,
            "proposed": prop_summaries,
            "match":    match,
            "reason":   reason,
        }

    result["all_match"] = all_match
    return result
