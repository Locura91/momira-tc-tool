"""Regression tests for a real product-owner request (2026-09-16): "when human create a new
transfer or transport, could the app simple copy the product and just swap the destinations?"

Context (confirmed via a clarifying round-trip): 2026-08-12's redesign removed AI-document
creation for Transfer/Transport entirely ("Transfer and Transport are not possible to
automatically Import/upload"), which left NO way at all in this app to create a brand-new
Transfer - only price_refresh.py's update-existing-only flow remained. Scoped to Transfer only
for now (Transport is the natural next step), found by searching (departure/arrival text against
the supplier's live list, or a pasted Travel Compositor id) - not a picked-list of session
history.

Fix: builder.build_transfer_swap_payload(existing_transfer_payload) takes a real GET
/transfer/{supplierId}/{id} response and returns a CREATE-ready payload for the reverse
direction of the same route - same vehicle/price/cancellation/images, departure and arrival
objects swapped wholesale (not re-geocoded), id dropped, name/datasheet name rewritten to read
in the new direction. flows/duplicate_transfer.py wires this into a new Step 1 "Create a new
product" destination, wired at DUPLICATE_TRANSFER_CHOICE in app.py.
"""
import os

from builder import build_transfer_swap_payload, _swap_route_text


_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_PY = os.path.join(os.path.dirname(_HERE), "app.py")


def _real_transfer_get_response():
    """A realistic GET /transfer/{supplierId}/{id} response - shape confirmed against
    builder.build_transfer_payload's own transfer_kwargs assembly."""
    return {
        "active": True,
        "id": "TRANSFER-412545",
        "name": "Private Transfer: Cairo Airport - Hotel Le Meridien",
        "productType": "ECONOMY",
        "serviceType": "PRIVATE",
        "vehicleType": "CAR",
        "departure": {"name": "Cairo Airport", "geolocation": {"latitude": 30.11, "longitude": 31.4},
                      "zoneRadius": None},
        "arrival": {"name": "Hotel Le Meridien", "geolocation": {"latitude": 30.05, "longitude": 31.23},
                    "zoneRadius": None},
        "departureLocationId": None,
        "arrivalLocationId": None,
        "pickupInformation": "Meet at arrivals hall",
        "datasheets": {"EN": {"name": "Private Transfer: Cairo Airport - Hotel Le Meridien",
                              "description": "Comfortable private transfer.",
                              "pickupDescription": "Meet at arrivals hall",
                              "voucherRemarks": "Free cancellation up to 30 days before."}},
        "images": ["https://example.com/car.jpg"],
        "properties": [{"name": "door-to-door"}],
        "startDate": "2026-01-01",
        "endDate": "2049-12-31",
        "releaseContract": 5,
        "currency": "EUR",
        "basePrice": 25.0,
        "maxOccupancy": 4,
        "minOccupancy": 1,
        "maxVehicles": 4,
        "allowMultipleVehicles": True,
        "pricesByOccupancy": [
            {"occupancy": 1, "basePrice": {"amount": 25.0, "currency": "EUR"},
             "childPrice": {"amount": 0.0, "currency": "EUR"},
             "infantPrice": {"amount": 0.0, "currency": "EUR"}, "priceByPax": True},
        ],
        "priceByPax": True,
        "supplements": [],
        "stopSales": [],
        "additionalServices": [],
    }


def test_departure_and_arrival_are_swapped_wholesale():
    source = _real_transfer_get_response()
    swapped = build_transfer_swap_payload(source)
    assert swapped["departure"]["name"] == "Hotel Le Meridien"
    assert swapped["arrival"]["name"] == "Cairo Airport"
    # the swap reuses the ORIGINAL already-resolved coordinates, not re-geocoded ones
    assert swapped["departure"]["geolocation"] == {"latitude": 30.05, "longitude": 31.23}
    assert swapped["arrival"]["geolocation"] == {"latitude": 30.11, "longitude": 31.4}


def test_location_ids_are_swapped_too():
    source = _real_transfer_get_response()
    source["departureLocationId"] = 111
    source["arrivalLocationId"] = 222
    swapped = build_transfer_swap_payload(source)
    assert swapped["departureLocationId"] == 222
    assert swapped["arrivalLocationId"] == 111


def test_id_is_dropped_so_this_is_always_a_create():
    source = _real_transfer_get_response()
    swapped = build_transfer_swap_payload(source)
    assert swapped.get("id") is None


def test_original_record_never_mutated():
    source = _real_transfer_get_response()
    original_departure_name = source["departure"]["name"]
    build_transfer_swap_payload(source)
    assert source["departure"]["name"] == original_departure_name
    assert source["id"] == "TRANSFER-412545"


def test_name_and_datasheet_name_read_in_the_new_direction():
    source = _real_transfer_get_response()
    swapped = build_transfer_swap_payload(source)
    assert swapped["name"] == "Private Transfer: Hotel Le Meridien - Cairo Airport"
    assert swapped["datasheets"]["EN"]["name"] == "Private Transfer: Hotel Le Meridien - Cairo Airport"


def test_everything_else_is_copied_byte_identical():
    source = _real_transfer_get_response()
    swapped = build_transfer_swap_payload(source)
    for field in ("vehicleType", "serviceType", "productType", "currency", "basePrice",
                  "maxOccupancy", "minOccupancy", "startDate", "endDate", "images",
                  "properties", "pricesByOccupancy"):
        assert swapped[field] == source[field], f"{field} should be copied unchanged"
    assert swapped["datasheets"]["EN"]["voucherRemarks"] == source["datasheets"]["EN"]["voucherRemarks"]
    assert swapped["datasheets"]["EN"]["pickupDescription"] == source["datasheets"]["EN"]["pickupDescription"]


def test_swap_route_text_falls_back_to_return_suffix_when_names_dont_both_appear():
    # A name that doesn't literally contain both the old departure and arrival text (e.g. a
    # human had renamed it to something generic) must not be guessed at - append "(return)"
    # rather than producing a name that reads wrong.
    assert _swap_route_text("Airport Pickup Service", "Cairo Airport", "Hotel Le Meridien") \
        == "Airport Pickup Service (return)"


def test_swap_route_text_handles_empty_text():
    assert _swap_route_text("", "Cairo Airport", "Hotel Le Meridien") == "(return)"


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_step_1_create_menu_offers_the_duplicate_transfer_choice():
    src = _read_app_py()
    assert "DUPLICATE_TRANSFER_CHOICE" in src
    assert 'pt_choice_duplicate_transfer' in src
    assert "render_duplicate_transfer_flow(client)" in src


def test_duplicate_transfer_flow_is_imported_from_its_own_module():
    src = _read_app_py()
    assert "from flows.duplicate_transfer import render_duplicate_transfer_flow" in src
