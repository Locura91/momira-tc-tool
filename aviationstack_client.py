"""
aviationstack_client.py — thin wrapper around the AviationStack API, for the Flight Change
Monitor (v1 test phase, 2026-11-01).

CONFIRMED (per aviationstack.com/pricing, 2026-11-01): the Free plan is 100 requests/month, not
500 as originally assumed - re-confirm before relying on this in production. No API key is
configured yet (test phase) - every method here degrades gracefully to "unknown" rather than
raising, so the UI can show "live check unavailable" instead of crashing.

The single-flight-lookup query param (flight_iata vs flight_number) was NOT live-tested before
this was written - _lookup_params() tries flight_iata first per AviationStack's own docs; if that
turns out wrong once a real key is available, this is the one place to fix it.
"""
import os
from typing import Any, Dict, Optional

import requests

API_BASE = "https://api.aviationstack.com/v1"


def has_api_key() -> bool:
    return bool(os.getenv("AVIATIONSTACK_API_KEY", "").strip())


def lookup_flight(flight_number: str, flight_date: str) -> Dict[str, Any]:
    """Looks up ONE flight by IATA flight number (e.g. "LH441") + date (YYYY-MM-DD).
    Returns {"status": "ok", "data": {...}} on success, or {"status": "no_key"|"not_found"|
    "error", "message": ...} otherwise - callers must check "status" before touching "data"."""
    api_key = os.getenv("AVIATIONSTACK_API_KEY", "").strip()
    if not api_key:
        return {"status": "no_key", "message": "AVIATIONSTACK_API_KEY not configured yet."}

    params = {
        "access_key": api_key,
        "flight_iata": flight_number,
        "flight_date": flight_date,
    }
    try:
        res = requests.get(f"{API_BASE}/flights", params=params, timeout=15)
    except requests.RequestException as e:
        return {"status": "error", "message": f"Network error calling AviationStack: {e}"}

    if res.status_code != 200:
        return {"status": "error", "message": f"AviationStack returned HTTP {res.status_code}: {res.text[:300]}"}

    try:
        body = res.json()
    except ValueError:
        return {"status": "error", "message": "AviationStack returned a non-JSON response."}

    data = body.get("data") or []
    if not data:
        return {"status": "not_found", "message": f"No AviationStack data for {flight_number} on {flight_date}."}

    return {"status": "ok", "data": data[0]}


def extract_comparison_fields(as_flight: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Pulls the fields the Flight Change Monitor compares against TC's stored data: the
    SCHEDULED departure time (not estimated/actual - see flight_monitor.py's compare logic for
    why) and the live flight number."""
    departure = as_flight.get("departure") or {}
    flight = as_flight.get("flight") or {}
    return {
        "live_scheduled_departure": departure.get("scheduled"),
        "live_flight_number": flight.get("iata") or flight.get("number"),
        "flight_status": as_flight.get("flight_status"),
    }
