"""Regression test for a product-owner request (2026-09-16):

    "all hotel creation must be start with on request at the beginning. no hotel room shall be
    on free sale"

Context: ContractHotelSeasonPricesVO.unitsQuota is the room's GUARANTEED, immediately-bookable
("free sale") allotment; unitsOnRequest is the extra count that needs advance request/approval
before it's confirmed. The document extraction and the schema default both defaulted to
unitsQuota=20/unitsOnRequest=0 - i.e. every room started fully free-sale unless the document
said otherwise.

Fix: build_hotel_rate_payloads now flips this ONLY when existing_hotel_snapshot is falsy - i.e.
this call IS a brand-new hotel's first creation, not a later update to an already-existing one.
In that case the room's whole count (document's stated units_quota + units_on_request, or the
20/0 fallback) moves entirely into unitsOnRequest, and unitsQuota is forced to 0 - no room starts
free-sale. An update to an existing hotel (existing_hotel_snapshot present) is untouched - this
rule is about hotel CREATION specifically, per the product owner's own wording, not every later
price refresh.
"""
from builder import build_hotel_rate_payloads


def _rate_data(units_quota=None, units_on_request=None):
    room_price = {
        "room_name": "Deluxe Room",
        "distribution_prices": [{"amount": 200.0, "adults": 2, "children": 0}],
        "base_price": 200.0, "adult_prices": [], "child_prices": [],
    }
    if units_quota is not None:
        room_price["units_quota"] = units_quota
    if units_on_request is not None:
        room_price["units_on_request"] = units_on_request
    return [{
        "name": "Standard Rates",
        "seasons": [{
            "name": "Season 1",
            "date_ranges": [{"start": "2027-01-01", "end": "2027-12-31"}],
            "room_prices": [room_price],
            "meal_plans": [],
        }],
        "offer_names": [], "supplement_names": [], "stop_sales": [],
    }]


_ROOM_MAP = {"Deluxe Room": "AUTO123"}
_DISTRIBUTIONS = {"Deluxe Room": [{"adults": 2, "children": 0}]}


def _season_prices(results):
    return results[0]["rate_payload"]["seasons"][0]["seasonRoomPrices"][0]


def test_new_hotel_creation_defaults_flip_to_fully_on_request():
    # existing_hotel_snapshot=None (the default) - a brand-new hotel.
    results = build_hotel_rate_payloads(
        _rate_data(), _ROOM_MAP, {}, {}, room_name_to_distributions=_DISTRIBUTIONS)
    sp = _season_prices(results)
    assert sp["unitsQuota"] == 0
    assert sp["unitsOnRequest"] == 20  # the 20/0 fallback's whole count moved to on-request


def test_new_hotel_creation_flips_a_document_stated_quota_too():
    results = build_hotel_rate_payloads(
        _rate_data(units_quota=12, units_on_request=3), _ROOM_MAP, {}, {},
        room_name_to_distributions=_DISTRIBUTIONS)
    sp = _season_prices(results)
    assert sp["unitsQuota"] == 0
    assert sp["unitsOnRequest"] == 15  # 12 + 3, the document's whole stated count


def test_existing_hotel_update_keeps_the_documents_own_quota_split():
    existing_snapshot = {"rooms": [], "rates": []}
    results = build_hotel_rate_payloads(
        _rate_data(units_quota=12, units_on_request=3), _ROOM_MAP, {}, {},
        existing_hotel_snapshot=existing_snapshot, room_name_to_distributions=_DISTRIBUTIONS)
    sp = _season_prices(results)
    assert sp["unitsQuota"] == 12
    assert sp["unitsOnRequest"] == 3


def test_existing_hotel_update_keeps_the_2000_default_untouched():
    existing_snapshot = {"rooms": [], "rates": []}
    results = build_hotel_rate_payloads(
        _rate_data(), _ROOM_MAP, {}, {},
        existing_hotel_snapshot=existing_snapshot, room_name_to_distributions=_DISTRIBUTIONS)
    sp = _season_prices(results)
    assert sp["unitsQuota"] == 20
    assert sp["unitsOnRequest"] == 0
