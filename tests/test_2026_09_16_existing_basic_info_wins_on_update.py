"""Regression tests for the "create the hotel shell in Travel Compositor, then only use this app
to add rooms/rates/meal plans/offers/supplements" workflow (product owner, 2026-09-16):

    "the information already provided by Travel C is great and no rewrite needed. We shall focus
    only on prices, supplement, room types, meal types and offers."

Follows the geolocation "UPDATE PRIORITY FLIP" fixed the same day (see
test_2026_09_06_hotel_manual_geolocation.py's test_existing_snapshot_coordinates_win_over_
document_on_update) - the same reasoning now applies to the rest of a hotel's IDENTITY info:
name, address, category, chain, and images. On an UPDATE (a hotel that already exists in Travel
Compositor), a fresh rate-sheet document that happens to also restate a slightly different name,
address, category, or set of images must not silently drift the hotel away from data that's
already correct there. On a brand-new CREATE, this is a no-op - there's no existing record to
prefer, so the document is still the only source there is.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas import HumanPreConfig
from builder import build_hotel_contract_payload


def make_pre_config(**overrides):
    defaults = dict(
        supplier_id="51216", provider_code="HRG-H1", min_pax=1, max_pax=4,
        currency="EUR", modality_code="STANDARD",
    )
    defaults.update(overrides)
    return HumanPreConfig(**defaults)


def _room(name="Deluxe Room"):
    return {"name": name, "distributions": [{"adults": 2, "children": 0}]}


def _existing_snapshot(**overrides):
    base = {
        "rooms": [], "latitude": 27.394900, "longitude": 33.678400,
        "hotelname": "Steigenberger Golf Resort El Gouna",
        "category": "5 STARS",
        "chain": "ORASCOM",
        "images": ["https://tc-cdn.example.com/existing1.jpg", "https://tc-cdn.example.com/existing2.jpg"],
        "address": {
            "address": "El Gouna, Hurghada", "locationName": "El Gouna", "postalCode": "84513",
            "country": "Egypt", "phone": "20-65-3580140", "fax": None, "email": None,
        },
    }
    base.update(overrides)
    return base


def _doc_extracted(**overrides):
    base = {
        "hotelname": "Steigenberger Golf Resort (from rate sheet)", "rooms": [_room()],
        "latitude": None, "longitude": None,
        "category": "4 STARS", "chain": "Different Chain Name",
        "images": ["https://supplier-site.example.com/newpick.jpg"],
        "address": {
            "address": "A different street address", "location_name": "El Gouna Bay",
            "postal_code": "99999", "country": "EGY", "phone": "000-000-0000",
            "fax": "111-111", "email": "sales@supplier.example.com",
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------------------------
# UPDATE - existing wins over the document
# ---------------------------------------------------------------------------------------------

def test_hotelname_kept_from_existing_snapshot_on_update():
    result = build_hotel_contract_payload(make_pre_config(), _doc_extracted(),
                                           existing_hotel_snapshot=_existing_snapshot())
    assert result["hotel_payload"]["hotelname"] == "Steigenberger Golf Resort El Gouna"


def test_category_kept_from_existing_snapshot_on_update():
    result = build_hotel_contract_payload(make_pre_config(), _doc_extracted(),
                                           existing_hotel_snapshot=_existing_snapshot())
    assert result["hotel_payload"]["category"] == "5 STARS"


def test_chain_kept_from_existing_snapshot_on_update():
    result = build_hotel_contract_payload(make_pre_config(), _doc_extracted(),
                                           existing_hotel_snapshot=_existing_snapshot())
    assert result["hotel_payload"]["chain"] == "ORASCOM"


def test_address_block_kept_from_existing_snapshot_on_update():
    result = build_hotel_contract_payload(make_pre_config(), _doc_extracted(),
                                           existing_hotel_snapshot=_existing_snapshot())
    address = result["hotel_payload"]["address"]
    assert address["address"] == "El Gouna, Hurghada"
    assert address["locationName"] == "El Gouna"
    assert address["postalCode"] == "84513"
    assert address["country"] == "Egypt"
    assert address["phone"] == "20-65-3580140"


def test_images_kept_from_existing_snapshot_on_update():
    result = build_hotel_contract_payload(make_pre_config(), _doc_extracted(),
                                           existing_hotel_snapshot=_existing_snapshot())
    image_urls = [img.get("url") if isinstance(img, dict) else img for img in result["hotel_payload"]["images"]]
    assert any("existing1.jpg" in (u or "") for u in image_urls)
    assert not any("newpick.jpg" in (u or "") for u in image_urls)


# ---------------------------------------------------------------------------------------------
# UPDATE - existing record has a genuine gap, document still fills it in
# ---------------------------------------------------------------------------------------------

def test_document_fills_a_field_the_existing_snapshot_is_missing_on_update():
    sparse_existing = _existing_snapshot(chain=None)
    result = build_hotel_contract_payload(make_pre_config(), _doc_extracted(),
                                           existing_hotel_snapshot=sparse_existing)
    assert result["hotel_payload"]["chain"] == "Different Chain Name"


def test_document_fills_images_when_existing_snapshot_has_none_on_update():
    sparse_existing = _existing_snapshot(images=[])
    result = build_hotel_contract_payload(make_pre_config(), _doc_extracted(),
                                           existing_hotel_snapshot=sparse_existing)
    image_urls = [img.get("url") if isinstance(img, dict) else img for img in result["hotel_payload"]["images"]]
    assert any("newpick.jpg" in (u or "") for u in image_urls)


# ---------------------------------------------------------------------------------------------
# CREATE - brand-new hotel, no existing snapshot: document is unaffected, still the only source
# ---------------------------------------------------------------------------------------------

def test_hotelname_still_comes_from_the_document_on_a_brand_new_create():
    result = build_hotel_contract_payload(make_pre_config(), _doc_extracted(), existing_hotel_snapshot=None)
    assert result["hotel_payload"]["hotelname"] == "Steigenberger Golf Resort (from rate sheet)"


def test_category_and_chain_still_come_from_the_document_on_a_brand_new_create():
    result = build_hotel_contract_payload(make_pre_config(), _doc_extracted(), existing_hotel_snapshot=None)
    assert result["hotel_payload"]["category"] == "4 STARS"
    assert result["hotel_payload"]["chain"] == "Different Chain Name"


def test_address_still_comes_from_the_document_on_a_brand_new_create():
    result = build_hotel_contract_payload(make_pre_config(), _doc_extracted(), existing_hotel_snapshot=None)
    address = result["hotel_payload"]["address"]
    assert address["locationName"] == "El Gouna Bay"
    assert address["country"] == "EGY"
