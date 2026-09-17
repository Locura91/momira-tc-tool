"""Regression tests for a real production bug (product owner, 2026-09-17), reported via two
side-by-side screenshots (real Travel Compositor admin UI vs. this app's own Transport duplicate
review screen):

    "same issues as with transfer: Name is wrong and description must be rewritten by AI.
    Biggest iussue is the reading of the prices. In travel c is per vehicle 220 USD and a
    supplement. But on the app nothing is seen and all prices are 0 USD, which is the biggest
    issue."

ROOT CAUSE (confirmed, not a data-loss bug): builder.build_transport_swap_payload/
build_transport_option_swap_payload both deepcopy the source untouched, so vehiclePrice and every
occupancy bracket's price supplement WERE already correct in the payload the whole time -
flows/duplicate_transport.py's review screen simply never displayed vehiclePrice anywhere (it
only ever showed baseAdultPrice/baseChildrenPrice/baseInfantPrice, which are legitimately 0.0 for
a per-vehicle transport - schemas.py's ContractTransportVO docstring, and the same pricePerPax
convention already established elsewhere in this app: price_refresh.py, bulk_notes.py both branch
on `bool(payload.get("pricePerPax", True))` the same way).

FIX 1 (this file, source-text checks - flows/*.py isn't importable in a test process due to
app.py's circular imports, same established pattern as every other flows/*.py wiring test):
the Price section now branches on pricePerPax - shows the three base fields for a per-pax
transport (unchanged), shows a single editable "Vehicle price" field (backed by payload
["vehiclePrice"]) for a per-vehicle one. The Occupancy brackets table mirrors this: three
per-passenger supplement columns for per-pax, one "price_supplement" column for per-vehicle
(builder.build_transport_payloads' own docstring: ContractTransportOptionPriceVO has no generic
"vehicle" supplement field, so a per-vehicle bracket's delta is written into adultPriceSupplement
the same way a per-pax bracket's is - it's the only numeric delta field the schema offers).

FIX 2 (this file + transfer_gap_finder.py): "description must be rewritten by AI" - a confirmed
exact analog of the gap Transfer's duplicate flow already closed on 2026-09-16. builder.
build_transport_swap_payload's own description swap only ever had the literal/alias text match,
with no AI fallback - transfer_gap_finder.build_and_rewrite_transport_swap_payload wraps it with
the same already-proven ai_extractor.rewrite_route_description_for_new_direction helper Transfer
uses (see transfer_gap_finder.build_and_rewrite_transfer_swap_payload, same file), and
flows/duplicate_transport.py now calls that wrapper instead of the bare builder function.
"""
import os
from unittest.mock import patch

from transfer_gap_finder import build_and_rewrite_transport_swap_payload

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_DUPLICATE_TRANSPORT_PY = os.path.join(_REPO_ROOT, "flows", "duplicate_transport.py")


def _read_flow():
    with open(_DUPLICATE_TRANSPORT_PY, "r", encoding="utf-8") as f:
        return f.read()


class _FakeApiClient:
    _CODE_TO_NAME = {
        "meet_alexandria": "Alexandria Train Station",
        "meet_siwa": "Siwa",
    }

    def resolve_transport_base(self, code):
        name = self._CODE_TO_NAME.get(code)
        return {"code": code, "name": name, "valid": bool(name), "match_type": "code"}


def _real_per_vehicle_transport_get_response():
    """Reproduces the real production example from the screenshots: TRANSPORT-425549, a
    per-vehicle (pricePerPax=False) transport priced at 220.00 USD, with a 55.00 USD occupancy
    bracket supplement tied to a date range."""
    return {
        "active": True,
        "id": "TRANSPORT-425549",
        "name": "Alexandria to Siwa Private Transfer",
        "segments": [{
            "departureLocationCode": "meet_alexandria", "arrivalLocationCode": "meet_siwa",
            "departureTime": "08:00:00", "arrivalTime": "14:00:00", "plusDays": 0,
        }],
        "transportType": "CAR",
        "datasheets": {"EN": {
            "name": "Alexandria to Siwa Private Transfer",
            "description": "Comfortable private vehicle transfer with air conditioning.",
        }},
        "currency": "USD",
        "pricePerPax": False,
        "vehiclePrice": 220.0,
        "baseAdultPrice": 0.0, "baseChildrenPrice": 0.0, "baseInfantPrice": 0.0,
        "startDate": "2026-08-25", "endDate": "2049-12-31",
        "optionCodes": ["ALEXSIWA1"],
        "cancellationRanges": [{"days": 30, "percentage": 0, "isBeforeStart": True}],
    }


def _real_per_vehicle_option_get_response():
    return {
        "code": "ALEXSIWA1", "active": True, "cabinClassType": "ECONOMY",
        "minPassengers": 1, "maxPassengers": 4, "onRequest": False,
        "prices": [{"name": "Sedan", "startDate": "2026-08-25", "endDate": "2049-12-31",
                    "adultPriceSupplement": 55.0, "childrenPriceSupplement": 0.0,
                    "infantPriceSupplement": 0.0, "adultRTPriceSupplement": 0.0,
                    "childrenRTPriceSupplement": 0.0, "infantRTPriceSupplement": 0.0}],
        "inventories": [],
        "translations": {"EN": {"name": "Sedan (1-4 Pax)"}},
    }


# ---------------------------------------------------------------------------------------------
# 1. vehiclePrice/occupancy-bracket supplements survive the swap untouched (already true before
#    this fix - confirms the bug really was display-only, never a data-loss bug).
# ---------------------------------------------------------------------------------------------

def test_vehicle_price_is_carried_through_the_swap_unchanged():
    from builder import build_transport_swap_payload
    source = _real_per_vehicle_transport_get_response()
    payload, _report, _route_info = build_transport_swap_payload(source, _FakeApiClient())
    assert payload["vehiclePrice"] == 220.0
    assert payload["pricePerPax"] is False


def test_option_price_supplement_is_carried_through_the_swap_unchanged():
    from builder import build_transport_option_swap_payload
    option = _real_per_vehicle_option_get_response()
    duplicated = build_transport_option_swap_payload(option, "Siwa", "Alexandria Train Station")
    assert duplicated["prices"][0]["adultPriceSupplement"] == 55.0


# ---------------------------------------------------------------------------------------------
# 2. the review screen now branches on pricePerPax and surfaces vehiclePrice as an editable field
# ---------------------------------------------------------------------------------------------

def test_flow_branches_on_price_per_pax_for_the_price_section():
    src = _read_flow()
    assert 'per_pax = bool(payload.get("pricePerPax", True))' in src


def test_flow_shows_vehicle_price_as_an_editable_field_for_a_per_vehicle_transport():
    src = _read_flow()
    assert 'payload["vehiclePrice"] = st.number_input(' in src
    assert 'key="dtp_vehicle_price"' in src


def test_flow_still_shows_the_three_base_fields_for_a_per_pax_transport():
    src = _read_flow()
    assert 'payload["baseAdultPrice"] = st.number_input(' in src
    assert 'payload["baseChildrenPrice"] = st.number_input(' in src
    assert 'payload["baseInfantPrice"] = st.number_input(' in src


def test_vehicle_price_is_no_longer_silently_excluded_from_the_json_preview():
    # it's now a first-class visible/editable field above, so it must be excluded from the
    # catch-all "everything else" JSON dump the same way the base price fields already are -
    # otherwise it would show up twice.
    src = _read_flow()
    json_exclude_idx = src.index('if k not in ("segments", "name", "datasheets", "baseAdultPrice",')
    window = src[json_exclude_idx:json_exclude_idx + 200]
    assert '"vehiclePrice"' in window


def test_occupancy_bracket_table_shows_a_single_price_supplement_column_for_per_vehicle():
    src = _read_flow()
    assert '"price_supplement": first_price.get("adultPriceSupplement", 0.0)' in src


# ---------------------------------------------------------------------------------------------
# 3. the flow now calls the shared AI-rewrite wrapper instead of the bare builder function
# ---------------------------------------------------------------------------------------------

def test_flow_imports_the_ai_rewrite_wrapper():
    src = _read_flow()
    assert "from transfer_gap_finder import build_and_rewrite_transport_swap_payload" in src
    assert "build_and_rewrite_transport_swap_payload(source, client)" in src
    # the bare builder function must no longer be imported/called directly here - the wrapper
    # subsumes it (it calls build_transport_swap_payload itself).
    assert "from builder import build_transport_swap_payload" not in src


def test_flow_shows_an_ai_rewritten_info_message():
    src = _read_flow()
    assert 'swap_report.get("description") == "ai"' in src
    assert "Rewritten automatically by AI for the new direction" in src


# ---------------------------------------------------------------------------------------------
# 4. transfer_gap_finder.build_and_rewrite_transport_swap_payload itself
# ---------------------------------------------------------------------------------------------

def test_rewrite_wrapper_returns_the_same_shape_as_the_bare_builder():
    source = _real_per_vehicle_transport_get_response()
    payload, swap_report, route_info = build_and_rewrite_transport_swap_payload(source, _FakeApiClient())
    assert payload["segments"][0]["departureLocationCode"] == "meet_siwa"
    assert route_info["new_departure_name"] == "Siwa"
    assert "description" in swap_report


def test_rewrite_wrapper_calls_ai_only_when_the_literal_description_swap_failed():
    # the fixture's description ("Comfortable private vehicle transfer with air conditioning.")
    # never names either location, so the literal swap reports False and the AI rewrite should
    # be attempted.
    source = _real_per_vehicle_transport_get_response()
    with patch("transfer_gap_finder.rewrite_route_description_for_new_direction") as mock_rewrite:
        mock_rewrite.return_value = "Comfortable private vehicle transfer from Siwa to Alexandria Train Station."
        payload, swap_report, _route_info = build_and_rewrite_transport_swap_payload(source, _FakeApiClient())
    mock_rewrite.assert_called_once()
    assert swap_report["description"] == "ai"
    assert "Comfortable private vehicle transfer from Siwa to Alexandria Train Station." in (
        payload["datasheets"]["EN"]["description"])


def test_rewrite_wrapper_skips_ai_when_the_literal_swap_already_succeeded():
    source = _real_per_vehicle_transport_get_response()
    source["datasheets"]["EN"]["description"] = "Private transfer from Alexandria to Siwa."
    with patch("transfer_gap_finder.rewrite_route_description_for_new_direction") as mock_rewrite:
        payload, swap_report, _route_info = build_and_rewrite_transport_swap_payload(source, _FakeApiClient())
    mock_rewrite.assert_not_called()
    assert swap_report["description"] is True
    assert payload["datasheets"]["EN"]["description"] == "Private transfer from Siwa to Alexandria."


def test_rewrite_wrapper_falls_back_to_false_when_ai_makes_no_change():
    source = _real_per_vehicle_transport_get_response()
    with patch("transfer_gap_finder.rewrite_route_description_for_new_direction") as mock_rewrite:
        mock_rewrite.return_value = "Comfortable private vehicle transfer with air conditioning."
        _payload, swap_report, _route_info = build_and_rewrite_transport_swap_payload(source, _FakeApiClient())
    assert swap_report["description"] is False
