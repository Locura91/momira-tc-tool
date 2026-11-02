"""
flight_extractor.py — turns a raw Travel Compositor booking JSON object into normalized
FlightRecord objects, for the Flight Change Monitor (v1 test phase, 2026-11-01).

Per the confirmed Swagger research, a booking can carry flight data in up to three shapes:
  (a) API-booked transport  — transportservice[].segment[]  (full data, handled here)
  (b) Manually-entered transport — manualServices.transport[] (simpler shape, handled here)
  (c) Flights bundled inside a Closed Tour — closedtourservice[].transportServices[].flightDetails[]
CONFIRMED PRODUCT-OWNER DECISION (2026-11-01): shape (c) is explicitly OUT of scope - closed-tour
flights are the supplier's own responsibility to monitor, not Momira's. Only (a) and (b) are
extracted here.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FlightRecord:
    booking_reference: str
    transport_booking_reference: Optional[str]
    flight_number: str
    departure_iata: Optional[str]
    arrival_iata: Optional[str]
    departure_name: Optional[str]        # e.g. "Frankfurt am Main" - only on shape (a), see below
    arrival_name: Optional[str]
    scheduled_departure: Optional[str]   # ISO datetime string, as TC gave it
    scheduled_arrival: Optional[str]
    pax_count: Optional[int]
    source_shape: str                    # "api_booked" | "manual"


def _pax_count(booking: Dict[str, Any]) -> Optional[int]:
    adults = booking.get("adultCount")
    children = booking.get("childCount")
    infants = booking.get("infantCount")
    if adults is None and children is None and infants is None:
        return None
    return (adults or 0) + (children or 0) + (infants or 0)


def _extract_api_booked(booking: Dict[str, Any], pax_count: Optional[int]) -> List[FlightRecord]:
    """Shape (a): transportservice[].segment[] — the primary, full-data shape."""
    records = []
    booking_ref = booking.get("bookingReference") or booking.get("reference") or ""
    for service in booking.get("transportservice") or []:
        # CONFIRMED against live Swagger (BookedTransportServiceVO, 2026-11-02): the field is
        # bookingReference (providerBookingReference as fallback) - there is no
        # transportBookingReference field.
        transport_ref = service.get("bookingReference") or service.get("providerBookingReference")
        for segment in service.get("segment") or []:
            flight_number = segment.get("flightNumber")
            if not flight_number:
                continue
            records.append(FlightRecord(
                booking_reference=booking_ref,
                transport_booking_reference=transport_ref,
                flight_number=str(flight_number).strip(),
                departure_iata=segment.get("departureAirport"),
                arrival_iata=segment.get("arrivalAirport"),
                departure_name=segment.get("departureAirportName"),
                arrival_name=segment.get("arrivalAirportName"),
                scheduled_departure=segment.get("departureDate"),
                scheduled_arrival=segment.get("arrivalDate"),
                pax_count=pax_count,
                source_shape="api_booked",
            ))
    return records


def _extract_manual(booking: Dict[str, Any], pax_count: Optional[int]) -> List[FlightRecord]:
    """Shape (b): manualServices.transport[] — simpler, flatter shape (no segment nesting)."""
    records = []
    booking_ref = booking.get("bookingReference") or booking.get("reference") or ""
    manual = booking.get("manualServices") or {}
    for t in manual.get("transport") or []:
        flight_number = t.get("flightNumber")
        if not flight_number:
            continue
        records.append(FlightRecord(
            booking_reference=booking_ref,
            transport_booking_reference=t.get("pnr"),
            flight_number=str(flight_number).strip(),
            departure_iata=t.get("origin"),
            arrival_iata=t.get("destination"),
            departure_name=None,  # BookedTransportManualServiceVO has no airport-name field
            arrival_name=None,
            scheduled_departure=t.get("departureHour"),
            scheduled_arrival=t.get("arrivalHour"),
            pax_count=pax_count,
            source_shape="manual",
        ))
    return records


def extract_flights(booking: Dict[str, Any]) -> List[FlightRecord]:
    """Extracts every FlightRecord (shapes a + b only - see module docstring) from one raw
    TC booking object, as returned by TravelCompositorAPI.get_booking()."""
    if not isinstance(booking, dict):
        return []
    pax_count = _pax_count(booking)
    records = []
    records.extend(_extract_api_booked(booking, pax_count))
    records.extend(_extract_manual(booking, pax_count))
    return records
