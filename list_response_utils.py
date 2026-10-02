"""
list_response_utils.py — shared "don't assume the response shape" normalizer for Travel
Compositor list-type GET endpoints.

CONSOLIDATED 2026-10-02 (weekly duplicate-code audit; product-owner request, verbatim: "please
start the weekly check ... with the focus on duplicates ... we need to check completely"). The
exact same 8-line block - "a bare list, or a dict wrapping the list under one of a few likely
keys depending on account/version" - was hand-copied 4 times: app_helpers.get_existing_tour_names,
app_helpers.get_existing_ticket_codes, app_helpers.get_existing_hotel_names, and
translation_tool._normalize_closed_tour_list (whose own docstring already said, before this
refactor, that it was a deliberate copy of app_helpers.get_existing_tour_names's logic - i.e. the
duplication was acknowledged, not accidental drift, which is exactly the case a shared helper is
for). Only the tuple of fallback dict keys differs between the four call sites.

Deliberately its own standalone module rather than living in app_helpers.py: app_helpers.py
already imports from translation_tool.py (DEFAULT_TARGET_LANGUAGES) at module level, so putting
this here - rather than in either of those two files - lets both import it with zero risk of
introducing a circular import between them.
"""
from typing import Any, Dict, List, Sequence


def normalize_list_response(result: Any, wrapper_keys: Sequence[str]) -> List[Dict[str, Any]]:
    """
    Normalizes a Travel Compositor list-endpoint response into a flat list of item dicts.
    Travel Compositor may return either a bare JSON list, or a dict wrapping the real list under
    one of a few likely keys depending on account/version - this tries `wrapper_keys` in order and
    returns the first one found. Returns [] if `result` is neither a list nor a dict with any of
    `wrapper_keys` holding a list (the caller's existing "no items / unrecognized shape" handling
    is unchanged by this helper - it only centralizes the shape-sniffing, not what callers do with
    an empty result).
    """
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in wrapper_keys:
            if isinstance(result.get(key), list):
                return result[key]
    return []
