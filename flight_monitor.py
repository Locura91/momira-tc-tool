"""
flight_monitor.py — comparison logic for the Flight Change Monitor (v1 test phase, 2026-11-01).

CONFIRMED PRODUCT-OWNER RULES (2026-11-01, verbatim):
  - "if there is a flight, the same flight number and just the arrival or departure time changed
    by 15 minutes, we ignore it" -> a time drift of 15 minutes or less is GREEN.
  - "we need to track a flight change by 16 or more minutes" -> 16+ minutes is RED.
  - "especially the moment there is a flight number change, we need an immediate attention needed
    sign" -> ANY flight-number change (any letter/digit different) is RED, regardless of time.

v1 test phase has no cron and no bulk 48h pull - a human pastes in one BNR at a time (see
flows/flight_monitor.py) and this module compares that one flight's TC data against AviationStack.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import aviationstack_client
from flight_extractor import FlightRecord

RED_THRESHOLD_MINUTES = 16


@dataclass
class ComparisonResult:
    flight_number: str
    tc_scheduled_departure: Optional[str]
    live_scheduled_departure: Optional[str]
    live_flight_number: Optional[str]
    delta_minutes: Optional[int]
    flight_number_changed: bool
    flag: str  # "RED" | "GREEN" | "UNKNOWN"
    message: str


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    # AviationStack and TC both commonly use ISO 8601, sometimes with a trailing timezone
    # offset (e.g. "2027-02-28T19:40:00+01:00" or "...+0100"). Try a few known shapes rather
    # than failing outright on a format we haven't seen yet.
    candidates = [value, value.replace("Z", "+00:00")]
    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def compare_flight(flight: FlightRecord) -> ComparisonResult:
    """Looks up ONE FlightRecord against AviationStack and returns the comparison. Never raises -
    any lookup failure (no key configured, flight not found, network error) becomes flag=UNKNOWN
    so the UI can show it distinctly from a genuine GREEN (CONFIRMED PRODUCT-OWNER RULE from the
    original research spec: defaulting unknowns to GREEN would hide exactly the cases needing
    human attention)."""
    tc_dt = _parse_dt(flight.scheduled_departure)
    flight_date = tc_dt.strftime("%Y-%m-%d") if tc_dt else None

    if not flight_date:
        return ComparisonResult(
            flight_number=flight.flight_number,
            tc_scheduled_departure=flight.scheduled_departure,
            live_scheduled_departure=None,
            live_flight_number=None,
            delta_minutes=None,
            flight_number_changed=False,
            flag="UNKNOWN",
            message="Could not parse TC's scheduled departure date/time - can't look this flight up.",
        )

    lookup = aviationstack_client.lookup_flight(flight.flight_number, flight_date)

    if lookup["status"] == "no_key":
        return ComparisonResult(
            flight_number=flight.flight_number,
            tc_scheduled_departure=flight.scheduled_departure,
            live_scheduled_departure=None,
            live_flight_number=None,
            delta_minutes=None,
            flight_number_changed=False,
            flag="UNKNOWN",
            message="AviationStack API key not configured yet - showing TC data only.",
        )

    if lookup["status"] != "ok":
        return ComparisonResult(
            flight_number=flight.flight_number,
            tc_scheduled_departure=flight.scheduled_departure,
            live_scheduled_departure=None,
            live_flight_number=None,
            delta_minutes=None,
            flight_number_changed=False,
            flag="UNKNOWN",
            message=lookup.get("message", "AviationStack lookup failed."),
        )

    fields = aviationstack_client.extract_comparison_fields(lookup["data"])
    live_dt = _parse_dt(fields["live_scheduled_departure"])
    live_flight_number = fields["live_flight_number"]

    delta_minutes = None
    if tc_dt and live_dt:
        delta_minutes = round(abs((live_dt - tc_dt).total_seconds()) / 60)

    flight_number_changed = bool(
        live_flight_number and live_flight_number.strip().upper() != flight.flight_number.strip().upper()
    )

    flag = "GREEN"
    message = "No change detected."
    if flight_number_changed:
        flag = "RED"
        message = f"Flight number changed: TC has {flight.flight_number}, live data shows {live_flight_number}."
    elif delta_minutes is not None and delta_minutes >= RED_THRESHOLD_MINUTES:
        flag = "RED"
        message = f"Departure time changed by {delta_minutes} minutes."
    elif delta_minutes is None:
        flag = "UNKNOWN"
        message = "Could not compare departure times (missing or unparseable data)."

    return ComparisonResult(
        flight_number=flight.flight_number,
        tc_scheduled_departure=flight.scheduled_departure,
        live_scheduled_departure=fields["live_scheduled_departure"],
        live_flight_number=live_flight_number,
        delta_minutes=delta_minutes,
        flight_number_changed=flight_number_changed,
        flag=flag,
        message=message,
    )
