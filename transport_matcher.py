"""
transport_matcher.py — thin wrapper around route_matcher for Transport products.

All shared logic lives in route_matcher.py. This module re-exports the same
public API names so no caller needs to change its imports.
"""
MODULE_BUILD = "2026-10-24-rollover-ext"

from route_matcher import (
    RouteMatcher,
    name_similarity as _name_similarity,
    expand_airport_codes as _expand_airport_codes,
    route_key as _route_key,
    suggest_transport_matches as suggest_existing_transport_matches,
    resolve_transport_match as _resolve_transport_match,
    match_bracket_to_existing_option,
    AIRPORT_CODE_TO_CITY as _AIRPORT_CODE_TO_CITY,
)

_matcher = RouteMatcher(
    product_type="transport",
    legacy_env_var="TRANSPORT_MATCH_STORE_PATH",
    legacy_filename="transport_match_store.json",
)


def remember_transport_id(supplier_id, departure_name, arrival_name, transport_id):
    _matcher.remember_id(supplier_id, departure_name, arrival_name, transport_id)


def forget_transport_id(supplier_id, departure_name, arrival_name):
    _matcher.forget_id(supplier_id, departure_name, arrival_name)


def lookup_tracked_transport_id(supplier_id, departure_name, arrival_name):
    return _matcher.lookup_tracked_id(supplier_id, departure_name, arrival_name)


def resolve_transport_match(client, supplier_id, departure_name, arrival_name):
    return _resolve_transport_match(_matcher, client, supplier_id, departure_name, arrival_name)
