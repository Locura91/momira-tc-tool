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
        # airlineCode is DELIBERATELY ABSENT here, matching real production GET responses (see
        # schemas.py's ContractTransportVO docstring, and the real 2026-09-16 CREATE rejection
        # this reproduces - see test_missing_airline_code_is_defaulted_to_empty_string below).
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


def test_option_name_that_doesnt_mention_the_route_is_left_unchanged():
    # CONFIRMED REAL BUG (product owner, 2026-09-16, live "AC First Class Seat" bracket): this
    # used to always append "(return)" (the parent name's own fallback behavior), producing the
    # nonsensical "AC First Class Seat (return)" - a service-class name, not a route description.
    # Real option names generated by this app never mention the route at all (see
    # build_transport_payloads' own option_name assembly), so this must be the common case.
    option = _real_option_get_response()
    option["translations"]["EN"]["name"] = "AC First Class Seat"
    duplicated = build_transport_option_swap_payload(option, "Alexandria Train Station", "Aswan Train Station")
    assert duplicated["translations"]["EN"]["name"] == "AC First Class Seat"


def test_option_name_that_genuinely_names_the_route_still_swaps():
    option = _real_option_get_response()
    option["translations"]["EN"]["name"] = "Marsa Matruh to Alexandria Airport (ALY) - 1 Pax"
    duplicated = build_transport_option_swap_payload(option, "Alexandria Airport (ALY)", "Marsa Matruh")
    assert duplicated["translations"]["EN"]["name"] == "Alexandria Airport (ALY) to Marsa Matruh - 1 Pax"


# ---------------------------------------------------------------------------------------------
# Two REAL production bugs (product owner, 2026-09-16, live "Aswan - Alexandria Train Ticket"
# duplicate-and-swap publish attempt) - both reported live, after the feature above first shipped:
#
# 1. "createTransport.transport.airlineCode: must not be null" - the real GET response for this
#    transport had no airlineCode at all (schemas.py's ContractTransportVO docstring already
#    flagged this as confirmed-absent-from-every-real-GET-example), and deepcopy-ing it straight
#    through carried the missing/null value into the CREATE payload, which Travel Compositor
#    rejects outright even though it tolerates the field being absent on GET.
#
# 2. "here is also an error, as no Name and no Description change was made" (screenshot: Name
#    became "Aswan - Alexandria Train Ticket (return)", Description stayed completely unswapped
#    reading "Train ticket from Aswan to Alexandria..."). Root cause: api_client.
#    resolve_transport_base returns the FORMAL Transport Base name ("Aswan Train Station",
#    "Alexandria Train Station"), but the real name/description only ever say the bare place
#    ("Aswan", "Alexandria") - so the old literal-substring check never matched even though the
#    text plainly does name the route. Fixed via _transport_location_name_aliases, which also
#    tries the place name with a trailing "Train Station"/"Airport"/etc suffix stripped.
# ---------------------------------------------------------------------------------------------

def _real_train_ticket_get_response():
    """Reproduces the exact real record from the live bug report: a Travel Compositor GET
    response with NO airlineCode field at all, and a name/description that only ever use the
    bare place names ("Aswan", "Alexandria"), never the formal Transport Base names."""
    return {
        "active": True,
        "id": "TRANSPORT-425287",
        "name": "Aswan - Alexandria Train Ticket",
        # no "airlineCode" key at all - matches the real live GET response exactly.
        "segments": [{
            "departureLocationCode": "meet_aswan", "arrivalLocationCode": "meet_alexandria",
            "departureTime": "08:00:00", "arrivalTime": "14:00:00", "plusDays": 0,
        }],
        "transportType": "TRAIN",
        "datasheets": {"EN": {
            "name": "Aswan - Alexandria Train Ticket",
            "description": "<p>Train ticket from Aswan to Alexandria. The estimated departure time "
                            "may change slightly due to ticket availability.</p><p>Three different "
                            "categories are available:</p><ul><li>AC First Class Seat</li>"
                            "<li>Sleeper Double Cabin</li><li>Sleeper Single Cabin</li></ul>",
        }},
        "currency": "USD",
        "baseAdultPrice": 105.0, "baseChildrenPrice": 105.0, "baseInfantPrice": 0.0,
        "startDate": "2026-08-25", "endDate": "2049-12-31",
        "optionCodes": ["AC First Class Seat"],
        "cancellationRanges": [{"days": 30, "percentage": 0, "isBeforeStart": True}],
    }


class _TrainStationApiClient:
    """resolve_transport_base returning the FORMAL Transport Base name, exactly as the real bug
    report's success banner showed ("Aswan Train Station", "Alexandria Train Station") - the
    formal name is deliberately NOT what the fixture's name/description text says."""
    _CODE_TO_NAME = {"meet_aswan": "Aswan Train Station", "meet_alexandria": "Alexandria Train Station"}

    def resolve_transport_base(self, code):
        name = self._CODE_TO_NAME.get(code)
        return {"code": code, "name": name, "valid": bool(name), "match_type": "code"}


def test_missing_airline_code_is_defaulted_to_empty_string():
    source = _real_train_ticket_get_response()
    assert "airlineCode" not in source  # sanity-check the fixture matches the real bug report
    payload, _report, _route_info = build_transport_swap_payload(source, _TrainStationApiClient())
    assert payload["airlineCode"] == ""


def test_missing_company_name_is_defaulted_to_empty_string():
    # CONFIRMED REAL PRODUCTION BUG (product owner, 2026-09-16): the SAME "java.lang.
    # IllegalArgumentException: An instance of a null PK has been incorrectly provided for this
    # find operation" error was still happening on a genuinely redeployed build, even after the
    # optionCodes fix (diagnosed live via screen-share - the actual submitted payload's
    # optionCodes matched correctly, so that wasn't it this time). companyName was the one field
    # missing ENTIRELY (not present at all, not even as null) from the real payload - and the
    # admin UI has a "Transport Company" DROPDOWN for this exact field, suggesting it's a real
    # entity reference rather than a plain string. Same fixture as the airlineCode test above
    # (the real GET response never included companyName either) - the fix must add it as an
    # explicit "" key, matching what the working, non-duplicate create path already always sends.
    source = _real_train_ticket_get_response()
    assert "companyName" not in source  # sanity-check the fixture matches the real bug report
    payload, _report, _route_info = build_transport_swap_payload(source, _TrainStationApiClient())
    assert payload["companyName"] == ""
    assert "companyName" in payload  # must be an explicit key, not merely absent-and-falsy


def test_name_swaps_using_the_bare_place_name_when_the_formal_name_never_appears():
    source = _real_train_ticket_get_response()
    payload, report, _route_info = build_transport_swap_payload(source, _TrainStationApiClient())
    assert payload["name"] == "Alexandria - Aswan Train Ticket"
    assert payload["datasheets"]["EN"]["name"] == "Alexandria - Aswan Train Ticket"
    assert report["name"] is True
    assert report["datasheet_name"] is True


def test_description_swaps_using_the_bare_place_name_when_the_formal_name_never_appears():
    source = _real_train_ticket_get_response()
    payload, report, _route_info = build_transport_swap_payload(source, _TrainStationApiClient())
    description = payload["datasheets"]["EN"]["description"]
    assert "Train ticket from Alexandria to Aswan." in description
    assert "AC First Class Seat" in description  # untouched prose stays exactly as-is
    assert report["description"] is True


def test_full_formal_name_is_still_preferred_over_the_short_alias_when_present():
    # When the text DOES spell out the full formal name, that exact match must win over the
    # shortened alias - never swap a short alias if the fuller, more specific one is right there.
    source = _real_train_ticket_get_response()
    source["name"] = "Aswan Train Station to Alexandria Train Station - Ticket"
    source["datasheets"]["EN"]["name"] = source["name"]
    payload, _report, _route_info = build_transport_swap_payload(source, _TrainStationApiClient())
    assert payload["name"] == "Alexandria Train Station to Aswan Train Station - Ticket"


def _read_app_py():
    with open(_APP_PY, "r", encoding="utf-8") as f:
        return f.read()


_FLOWS_DIR = os.path.join(os.path.dirname(_HERE), "flows")
_DUPLICATE_TRANSPORT_FLOW_PY = os.path.join(_FLOWS_DIR, "duplicate_transport.py")


def _read_duplicate_transport_flow():
    with open(_DUPLICATE_TRANSPORT_FLOW_PY, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------------------------
# Three more real bugs, same live "Aswan - Alexandria Train Ticket" publish attempt, reported
# right after the airlineCode/name/description fixes above:
#
# 1. "java.lang.IllegalArgumentException: An instance of a null PK has been incorrectly provided
#    for this find operation" on the PARENT create call - build_transport_swap_payload's deepcopy
#    carried over the OLD transport's optionCodes (a code belonging to the transport being
#    duplicated FROM, not the brand-new one about to be created) unchanged. Fixed in
#    flows/duplicate_transport.py, not builder.py (it's the one place that knows both the parent
#    payload AND the freshly-generated option codes at the same time) - see the fix's own comment.
#
# 2. "No coding lines please, some humans will not understand it in the future" (screenshot: the
#    Description box showed raw "<p>...</p><ul><li>...</li></ul>" markup). Fixed by reusing this
#    app's existing HTML<->plain-text conversion helpers (ui_components._html_to_plain_for_editing
#    / _plain_to_html_for_saving - already used everywhere else in the app for an HTML-backed
#    description field), so the human only ever sees/types plain text.
#
# 3. "the Duplication is wrong in the order as nothing changed in this field, which could cause a
#    misunderstanding" (the success banner showed the UNCHANGED original route, reading as if
#    nothing had been swapped). Fixed by stating both the original AND the new route explicitly.
# ---------------------------------------------------------------------------------------------

def test_parent_option_codes_are_set_from_the_freshly_generated_option_codes():
    src = _read_duplicate_transport_flow()
    assert 'payload["optionCodes"] = [opt.get("code") for opt in duplicated_options if opt.get("code")]' in src


def test_description_field_uses_the_plain_text_conversion_helpers_not_raw_html():
    src = _read_duplicate_transport_flow()
    assert "_html_to_plain_for_editing" in src
    assert "_plain_to_html_for_saving" in src
    # the raw HTML must never be handed straight to a text_area's value=
    assert 'st.text_area(\n            "Description", value=en.get("description", "")' not in src


def test_success_banner_states_both_the_original_and_the_new_route():
    src = _read_duplicate_transport_flow()
    assert "Original route:" in src
    assert "New route being created:" in src


# ---------------------------------------------------------------------------------------------
# Fourth real bug, same live "Alexandria - Aswan Train Ticket" publish attempt: the SAME null-PK
# error persisted even after (1) the optionCodes fix above and (2) a companyName default fix in
# builder.py, confirmed via a second live screen-share that the actual submitted parent payload
# had every field present with sane values, including a correctly-matching optionCodes -  yet
# the PARENT create call itself still failed, before any Option was ever submitted. Root cause:
# Transport has never had a genuinely working, exercised CREATE path in this app (only UPDATE,
# where the Options already exist) - sending optionCodes that reference Options which don't
# exist YET apparently makes Travel Compositor's create endpoint try to resolve them and fail
# with a null PK. Fix: create the parent with optionCodes EMPTY, create every Option under the
# new id, then a follow-up PUT (update_transport) links the parent to the now-real codes.
# ---------------------------------------------------------------------------------------------

def test_parent_is_first_created_with_empty_option_codes():
    src = _read_duplicate_transport_flow()
    assert 'create_payload["optionCodes"] = []' in src
    assert "client.create_transport(supplier_id, create_payload)" in src


def test_parent_is_linked_to_the_real_option_codes_via_a_follow_up_update_after_options_exist():
    src = _read_duplicate_transport_flow()
    assert 'link_payload["optionCodes"] = created_codes' in src
    assert "client.update_transport(supplier_id, link_payload)" in src
    # the linking update must carry the real, TC-assigned id, not the pre-create payload's own
    assert 'link_payload["id"] = new_id' in src


def test_step_1_create_menu_offers_the_duplicate_transport_choice():
    # UPDATE (2026-09-22): DUPLICATE_TRANSPORT_CHOICE now routes to the combined scan+duplicate
    # screen (render_transport_duplicate_and_create_flow) instead of calling
    # render_duplicate_transport_flow directly - see
    # tests/test_2026_09_22_transport_missing_reverse_and_duplicate_combined.py for the full
    # coverage of that change. render_duplicate_transport_flow itself is unchanged and still used
    # by the combined screen's manual duplicate-by-id fallback (via its own
    # _render_duplicate_transport_body split).
    src = _read_app_py()
    assert "DUPLICATE_TRANSPORT_CHOICE" in src
    assert "pt_choice_duplicate_transport" in src
    assert "render_transport_duplicate_and_create_flow(client)" in src


def test_duplicate_transport_flow_module_still_exists_for_the_manual_fallback():
    # UPDATE (2026-09-22): app.py no longer imports render_duplicate_transport_flow directly
    # (same pattern already established for Transfer's own combined flow) - it's reached through
    # flows/transport_duplicate_and_create.py's import of the module's
    # _render_duplicate_transport_body instead.
    src = _read_app_py()
    assert "from flows.duplicate_transport import render_duplicate_transport_flow" not in src
    assert "from flows.transport_duplicate_and_create import render_transport_duplicate_and_create_flow" in src
