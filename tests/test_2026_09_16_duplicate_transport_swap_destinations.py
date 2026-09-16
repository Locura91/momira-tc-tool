"""Regression tests for a real product-owner request (2026-09-16), a direct same-day follow-up
to the Transfer version of this feature (test_2026_09_16_duplicate_transfer_swap_destinations.py):
"can we do the same for Transport. Changing the Destination of the original Transport ID,
adopting the Name and adopting the Description."

Fix: builder.build_transport_swap_payload(existing_transport_payload, api_client) takes a real
GET /transport/{supplierId}/{transportId} response and returns (payload, swap_report, route_info)
- a CREATE-ready parent payload for the reverse direction of the same route (id dropped, segments
reversed/swapped, name/datasheet-name/description rewritten to read in the new direction), plus a
report of which prose fields were confidently auto-swapped, plus resolved display names for the
review screen (Transport's route lives only as raw location CODES on its segments - unlike
Transfer's departure/arrival, which carry a human-readable name right on the payload - so this
needs an extra api_client.resolve_transport_base() lookup that build_transfer_swap_payload never
needed; see that function's own docstring for the full reasoning).

builder.build_transport_option_swap_payload(existing_option_payload, new_departure_name,
new_arrival_name) duplicates ONE existing occupancy-bracket Option (Transport's per-occupancy
pricing lives on separate Option sub-resources, unlike Transfer's flat pricesByOccupancy array -
see schemas.py's ContractTransportOptionVO docstring) with a freshly-generated code and, best
effort, a swapped translations.EN.name.

flows/duplicate_transport.py wires both into a new Step 1 "Create a new product" destination,
wired at DUPLICATE_TRANSPORT_CHOICE in app.py - mirrors flows/duplicate_transfer.py exactly,
adapted for the parent-then-options two-phase publish Transport needs (builder.
build_transport_payloads' own create sequencing).
"""
import os

from builder import (
    build_transport_swap_payload, build_transport_option_swap_payload, _generate_transport_option_code,
)


_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_PY = os.path.join(os.path.dirname(_HERE), "app.py")


class _FakeApiClient:
    """Stub for api_client.resolve_transport_base() - build_transport_swap_payload only ever
    calls this to turn a location CODE back into a display name (see its own docstring for why),
    so this stub just needs to answer that one method deterministically for the two codes the
    fixture below uses."""
    _CODE_TO_NAME = {
        "meet_marsa_matruh": "Marsa Matruh",
        "meet_alexandria_airport": "Alexandria Airport (ALY)",
    }

    def resolve_transport_base(self, code):
        name = self._CODE_TO_NAME.get(code)
        if name:
            return {"code": code, "name": name, "valid": True, "match_type": "code"}
        return {"code": None, "name": code, "valid": False, "match_type": "not_found"}


class _FailingApiClient:
    """Stub simulating resolve_transport_base failing entirely (network error/unknown code) -
    build_transport_swap_payload must fall back to the bare code as its own "name" rather than
    raising, per its own docstring."""
    def resolve_transport_base(self, code):
        raise RuntimeError("simulated network failure")


def _real_transport_get_response():
    """A realistic GET /transport/{supplierId}/{transportId} response - shape confirmed against
    schemas.py's ContractTransportVO/TransportSegmentVO/TransportDataSheetVO."""
    return {
        "active": True,
        "id": "TRANSPORT-412579",
        "name": "One-way transfer Marsa Matruh to Alexandria Airport (ALY)",
        "airlineCode": "",
        "segments": [{
            "departureLocationCode": "meet_marsa_matruh",
            "arrivalLocationCode": "meet_alexandria_airport",
            "departureTime": "08:00:00", "arrivalTime": "12:30:00", "plusDays": 0,
            "durationTime": "04:30:00", "model": None, "numService": None,
        }],
        "transportType": "CAR",
        "datasheets": {"EN": {
            "name": "One-way transfer Marsa Matruh to Alexandria Airport (ALY)",
            "description": "Private transfer from Marsa Matruh to Alexandria Airport (ALY) with an "
                            "English-speaking driver.",
        }},
        "images": ["https://example.com/car.jpg"],
        "productTypes": ["ONLY_FLIGHT"],
        "pricePerPax": True,
        "currency": "EUR",
        "vehiclePrice": 0.0,
        "baseAdultPrice": 60.0, "baseChildrenPrice": 30.0, "baseInfantPrice": 0.0,
        "baseAdultRTPrice": 0.0, "baseChildrenRTPrice": 0.0, "baseInfantRTPrice": 0.0,
        "startDate": "2026-01-01", "endDate": "2049-12-31",
        "releaseContract": 5,
        "operationalDays": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
        "optionCodes": ["MARALY1", "MARALY29"],
        "minChildAge": 2, "maxChildAge": 11, "minInfantAge": 0, "maxInfantAge": 2,
        "allowOWPrice": True, "allowRTPrice": False,
        "cancellationRanges": [{"days": 30, "percentage": 100.0, "isBeforeStart": True}],
        "combinableRtContracts": [],
    }


def _real_option_get_response():
    return {
        "code": "MARALY1", "active": True, "cabinClassType": "ECONOMY",
        "baggageAllowance": "1", "baggageAllowanceType": "PC",
        "minPassengers": 1, "maxPassengers": 1, "onRequest": False,
        "prices": [{"name": None, "startDate": "2026-01-01", "endDate": "2049-12-31",
                    "adultPriceSupplement": 10.0, "childrenPriceSupplement": 5.0,
                    "infantPriceSupplement": 0.0, "adultRTPriceSupplement": 0.0,
                    "childrenRTPriceSupplement": 0.0, "infantRTPriceSupplement": 0.0}],
        "inventories": [{"inventoryDate": {"start": "2026-01-01", "end": "2049-12-31"}, "quantity": 0}],
        "translations": {"EN": {"name": "Private - 1 Pax - Door to Door (no Guide)"}},
    }


def test_route_is_swapped_wholesale():
    source = _real_transport_get_response()
    payload, _report, route_info = build_transport_swap_payload(source, _FakeApiClient())
    assert payload["segments"][0]["departureLocationCode"] == "meet_alexandria_airport"
    assert payload["segments"][0]["arrivalLocationCode"] == "meet_marsa_matruh"
    assert route_info["old_departure_name"] == "Marsa Matruh"
    assert route_info["old_arrival_name"] == "Alexandria Airport (ALY)"
    assert route_info["new_departure_name"] == "Alexandria Airport (ALY)"
    assert route_info["new_arrival_name"] == "Marsa Matruh"


def test_multi_segment_route_order_is_reversed_too():
    source = _real_transport_get_response()
    source["segments"] = [
        {"departureLocationCode": "meet_marsa_matruh", "arrivalLocationCode": "meet_midpoint",
         "departureTime": "08:00:00", "arrivalTime": "10:00:00", "plusDays": 0},
        {"departureLocationCode": "meet_midpoint", "arrivalLocationCode": "meet_alexandria_airport",
         "departureTime": "10:15:00", "arrivalTime": "12:30:00", "plusDays": 0},
    ]
    payload, _report, _route_info = build_transport_swap_payload(source, _FakeApiClient())
    segs = payload["segments"]
    assert len(segs) == 2
    # overall departure is now the old arrival, overall arrival is now the old departure
    assert segs[0]["departureLocationCode"] == "meet_alexandria_airport"
    assert segs[-1]["arrivalLocationCode"] == "meet_marsa_matruh"
    # the leg order itself is reversed too (last leg first)
    assert segs[0]["arrivalLocationCode"] == "meet_midpoint"
    assert segs[-1]["departureLocationCode"] == "meet_midpoint"


def test_id_is_dropped_so_this_is_always_a_create():
    source = _real_transport_get_response()
    payload, _report, _route_info = build_transport_swap_payload(source, _FakeApiClient())
    assert payload.get("id") is None


def test_original_record_never_mutated():
    source = _real_transport_get_response()
    original_code = source["segments"][0]["departureLocationCode"]
    build_transport_swap_payload(source, _FakeApiClient())
    assert source["segments"][0]["departureLocationCode"] == original_code
    assert source["id"] == "TRANSPORT-412579"


def test_name_and_datasheet_name_read_in_the_new_direction_matching_product_owners_own_example():
    # CONFIRMED (product owner, same wording used for the Transfer version of this feature):
    # "One-way transfer Marsa Matruh to Alexandria Airport (ALY)" must become "One way transfer
    # Alexandria Airport (ALY) to Marsa Matruh". The fixture already uses this exact route.
    source = _real_transport_get_response()
    payload, report, _route_info = build_transport_swap_payload(source, _FakeApiClient())
    assert payload["name"] == "One-way transfer Alexandria Airport (ALY) to Marsa Matruh"
    assert payload["datasheets"]["EN"]["name"] == "One-way transfer Alexandria Airport (ALY) to Marsa Matruh"
    assert report["name"] is True
    assert report["datasheet_name"] is True


def test_description_is_swapped_when_it_names_the_route():
    source = _real_transport_get_response()
    payload, report, _route_info = build_transport_swap_payload(source, _FakeApiClient())
    assert payload["datasheets"]["EN"]["description"] == (
        "Private transfer from Alexandria Airport (ALY) to Marsa Matruh with an English-speaking driver.")
    assert report["description"] is True


def test_description_left_unchanged_and_flagged_when_it_does_not_name_the_route():
    source = _real_transport_get_response()
    source["datasheets"]["EN"]["description"] = "Comfortable private transfer with air conditioning."
    payload, report, _route_info = build_transport_swap_payload(source, _FakeApiClient())
    assert payload["datasheets"]["EN"]["description"] == "Comfortable private transfer with air conditioning."
    assert report["description"] is False


def test_everything_else_is_copied_byte_identical():
    source = _real_transport_get_response()
    payload, _report, _route_info = build_transport_swap_payload(source, _FakeApiClient())
    for field in ("transportType", "currency", "baseAdultPrice", "baseChildrenPrice",
                  "baseInfantPrice", "startDate", "endDate", "images", "cancellationRanges",
                  "optionCodes"):
        assert payload[field] == source[field], f"{field} should be copied unchanged"


def test_resolve_transport_base_failure_falls_back_to_the_bare_code_rather_than_raising():
    source = _real_transport_get_response()
    payload, _report, route_info = build_transport_swap_payload(source, _FailingApiClient())
    # falls back to the raw codes as their own "names" - never raises, never leaves route_info
    # holding None
    assert route_info["old_departure_name"] == "meet_marsa_matruh"
    assert route_info["old_arrival_name"] == "meet_alexandria_airport"
    assert payload["segments"][0]["departureLocationCode"] == "meet_alexandria_airport"


def test_option_is_duplicated_with_a_regenerated_code_and_unchanged_pricing():
    option = _real_option_get_response()
    duplicated = build_transport_option_swap_payload(option, "Alexandria Airport (ALY)", "Marsa Matruh")
    assert duplicated["code"] == _generate_transport_option_code("Alexandria Airport (ALY)", "Marsa Matruh", 1, 1)
    assert duplicated["code"] != option["code"]
    assert duplicated["minPassengers"] == option["minPassengers"]
    assert duplicated["maxPassengers"] == option["maxPassengers"]
    assert duplicated["prices"] == option["prices"]
    assert duplicated["inventories"] == option["inventories"]


def test_option_original_never_mutated():
    option = _real_option_get_response()
    original_code = option["code"]
    build_transport_option_swap_payload(option, "Alexandria Airport (ALY)", "Marsa Matruh")
    assert option["code"] == original_code


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_step_1_create_menu_offers_the_duplicate_transport_choice():
    src = _read_app_py()
    assert "DUPLICATE_TRANSPORT_CHOICE" in src
    assert "pt_choice_duplicate_transport" in src
    assert "render_duplicate_transport_flow(client)" in src


def test_duplicate_transport_flow_is_imported_from_its_own_module():
    src = _read_app_py()
    assert "from flows.duplicate_transport import render_duplicate_transport_flow" in src
