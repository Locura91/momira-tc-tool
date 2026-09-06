"""Regression tests for a real customer-facing bug (product owner, 2026-09-06):

    Hotel HRG-T1's publish was rejected outright with:
        "Minimum size of 500x400 required, 433x650 found. Image:
         'https://images.pexels.com/photos/20401343/pexels-photo-20401343.jpeg?...'"
    User's explicit instruction: "If an image is not big enough, take the default image, same
    as for tickets and closedtours ans skip the error."

Root cause: Travel Compositor enforces a hard 500x400 minimum on every image and rejects the
ENTIRE publish if even one image is smaller. Pexels' "large" preset requests a fixed w x h crop,
but for a portrait-oriented source photo it fits to whichever dimension the photo's own aspect
ratio constrains - so the returned image can come back narrower than requested. Nothing in this
codebase ever checked an image's real pixel size before publish, on ANY of the five product
flows (not just Hotel, despite the user's belief that Ticket/ClosedTour already handled this).

Fix (2026-09-06): image_dimensions.py adds a cached, network-based dimension check
(image_dimensions.py's own module docstring explains the caching - build_*_payload functions
re-run on every Streamlit interaction, so this must never re-fetch the same URL twice). Hotel,
Ticket, and ClosedTour builders now filter their final image list down to ones that measure at
least 500x400, falling back to the shared FALLBACK_IMAGE placeholder only when there WAS at
least one picked image and every one of them failed the check (a genuinely empty pick still
hits Hotel's existing "at least one image required" block, unaffected by this fix).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import image_dimensions as imgdim
from schemas import HumanPreConfig
from builder import build_hotel_contract_payload, build_ticket_payloads, build_closed_tour_payloads

MODULE_BUILD = "2026-09-06-image-size-filter"


def setup_function(_):
    # The dimension cache is module-level/process-lifetime by design (see image_dimensions.py's
    # docstring) - clear it between tests so one test's monkeypatched fetch never leaks into
    # another's assertions.
    imgdim._dimension_cache.clear()


class _FakeAPI:
    def __getattr__(self, name):
        return lambda *a, **k: {}


def _fake_dimensions(mapping):
    """monkeypatch target: returns mapping[url] (or None if absent) instead of hitting the network."""
    def fake_get(url):
        return mapping.get(url)
    return fake_get


# ======================================================================
# image_dimensions.py - the shared, cached size check itself
# ======================================================================
def test_image_meets_minimum_size_true_for_large_enough_image(monkeypatch):
    monkeypatch.setattr(imgdim, "get_image_dimensions", _fake_dimensions({"https://x/img.jpg": (940, 650)}))
    assert imgdim.image_meets_minimum_size("https://x/img.jpg") is True


def test_image_meets_minimum_size_false_for_the_real_reported_dimensions(monkeypatch):
    # The exact real-world failure: 433x650 - tall enough, but narrower than the 500px minimum.
    monkeypatch.setattr(imgdim, "get_image_dimensions", _fake_dimensions({"https://x/img.jpg": (433, 650)}))
    assert imgdim.image_meets_minimum_size("https://x/img.jpg") is False


def test_image_meets_minimum_size_fails_open_when_dimensions_unknown(monkeypatch):
    monkeypatch.setattr(imgdim, "get_image_dimensions", _fake_dimensions({}))
    assert imgdim.image_meets_minimum_size("https://x/unreachable.jpg") is True


def test_filter_images_by_minimum_size_splits_kept_and_dropped(monkeypatch):
    monkeypatch.setattr(imgdim, "get_image_dimensions", _fake_dimensions({
        "https://x/good.jpg": (940, 650), "https://x/bad.jpg": (433, 650),
    }))
    kept, dropped = imgdim.filter_images_by_minimum_size(["https://x/good.jpg", "https://x/bad.jpg"])
    assert kept == ["https://x/good.jpg"]
    assert dropped == ["https://x/bad.jpg"]


def test_ensure_images_meet_minimum_size_falls_back_to_placeholder_when_everything_is_too_small(monkeypatch):
    monkeypatch.setattr(imgdim, "get_image_dimensions", _fake_dimensions({"https://x/bad.jpg": (433, 650)}))
    final_images, dropped = imgdim.ensure_images_meet_minimum_size(["https://x/bad.jpg"])
    assert final_images == [imgdim.FALLBACK_IMAGE]
    assert dropped == ["https://x/bad.jpg"]


def test_ensure_images_meet_minimum_size_keeps_the_good_ones_and_drops_only_the_bad(monkeypatch):
    monkeypatch.setattr(imgdim, "get_image_dimensions", _fake_dimensions({
        "https://x/good.jpg": (940, 650), "https://x/bad.jpg": (433, 650),
    }))
    final_images, dropped = imgdim.ensure_images_meet_minimum_size(["https://x/good.jpg", "https://x/bad.jpg"])
    assert final_images == ["https://x/good.jpg"]
    assert dropped == ["https://x/bad.jpg"]


def test_dimension_lookup_is_cached_and_never_refetches_the_same_url(monkeypatch):
    calls = []

    class FakeResp:
        content = b"fake-bytes"
        def raise_for_status(self): pass

    def fake_requests_get(url, timeout=10):
        calls.append(url)
        return FakeResp()

    class FakeImage:
        size = (940, 650)

    monkeypatch.setattr(imgdim.requests, "get", fake_requests_get)
    monkeypatch.setattr(imgdim.Image, "open", lambda _bytes: FakeImage())

    imgdim.get_image_dimensions("https://x/repeat.jpg")
    imgdim.get_image_dimensions("https://x/repeat.jpg")
    imgdim.get_image_dimensions("https://x/repeat.jpg")
    assert calls == ["https://x/repeat.jpg"]  # only the first call actually hit the network


# ======================================================================
# Hotel builder integration - the exact reported case
# ======================================================================
def _hotel_pre_config(**overrides):
    defaults = dict(supplier_id="51216", provider_code="HRG-T1", min_pax=1, max_pax=4,
                     currency="EUR", modality_code="STANDARD")
    defaults.update(overrides)
    return HumanPreConfig(**defaults)


def _hotel_room():
    return {"name": "Deluxe Room", "distributions": [{"adults": 2, "children": 0}]}


def test_hotel_drops_undersized_image_and_keeps_publishing(monkeypatch):
    good = "https://images.pexels.com/good.jpeg"
    bad = "https://images.pexels.com/photos/20401343/pexels-photo-20401343.jpeg?auto=compress&cs=tinysrgb&h=650&w=940"
    monkeypatch.setattr(imgdim, "get_image_dimensions", _fake_dimensions({good: (940, 650), bad: (433, 650)}))
    extracted = {"hotelname": "Test Hotel", "rooms": [_hotel_room()], "latitude": 27.39, "longitude": 33.68,
                 "images": [good, bad]}
    result = build_hotel_contract_payload(_hotel_pre_config(), extracted, existing_hotel_snapshot=None)
    assert result["hotel_error"] is None
    assert result["hotel_payload"]["images"] == [good]
    assert result["images_dropped_too_small"] == [bad]


def test_hotel_falls_back_to_placeholder_when_the_only_image_is_too_small(monkeypatch):
    bad = "https://images.pexels.com/photos/20401343/pexels-photo-20401343.jpeg"
    monkeypatch.setattr(imgdim, "get_image_dimensions", _fake_dimensions({bad: (433, 650)}))
    extracted = {"hotelname": "Test Hotel", "rooms": [_hotel_room()], "latitude": 27.39, "longitude": 33.68,
                 "images": [bad]}
    result = build_hotel_contract_payload(_hotel_pre_config(), extracted, existing_hotel_snapshot=None)
    # CONFIRMED REAL BUG this reproduces: publishing used to be rejected outright by Travel
    # Compositor over this one undersized image, even though the hotel was otherwise ready.
    assert result["hotel_error"] is None
    assert result["hotel_payload"]["images"] == [imgdim.FALLBACK_IMAGE]
    assert result["images_dropped_too_small"] == [bad]


def test_hotel_with_no_images_at_all_still_hits_the_existing_required_image_gate():
    # This fix must NOT bypass the separate, already-confirmed "at least one image required"
    # block (reported 2026-09-02) - a hotel with zero images picked stays zero images, not a
    # silent placeholder, so the human is still told to add a real photo.
    extracted = {"hotelname": "Test Hotel", "rooms": [_hotel_room()], "latitude": 27.39, "longitude": 33.68,
                 "images": []}
    result = build_hotel_contract_payload(_hotel_pre_config(), extracted, existing_hotel_snapshot=None)
    assert result["hotel_payload"]["images"] == []
    assert result["images_dropped_too_small"] == []


# ======================================================================
# Ticket / ClosedTour builder integration - "same as tickets and closedtours"
# ======================================================================
def test_ticket_drops_undersized_image_and_falls_back_when_nothing_else_is_left(monkeypatch):
    from test_builder_ticket import make_pre_config, minimal_ticket_data

    bad = "https://images.pexels.com/photos/20401343/pexels-photo-20401343.jpeg"
    monkeypatch.setattr(imgdim, "get_image_dimensions", _fake_dimensions({bad: (433, 650)}))
    data = minimal_ticket_data(image_urls=[bad])
    result = build_ticket_payloads(make_pre_config(), data, _FakeAPI())
    assert result["main_ticket_payload"]["imageUrls"] == [imgdim.FALLBACK_IMAGE]


def test_ticket_with_no_images_stays_empty_not_placeholder(monkeypatch):
    from test_builder_ticket import make_pre_config, minimal_ticket_data

    data = minimal_ticket_data(image_urls=[])
    result = build_ticket_payloads(make_pre_config(), data, _FakeAPI())
    assert result["main_ticket_payload"]["imageUrls"] == []


def test_closed_tour_drops_undersized_image_and_falls_back_when_nothing_else_is_left(monkeypatch, fake_api_client):
    from test_builder_closed_tour import make_pre_config, minimal_extracted_data

    bad = "https://images.pexels.com/photos/20401343/pexels-photo-20401343.jpeg"
    monkeypatch.setattr(imgdim, "get_image_dimensions", _fake_dimensions({bad: (433, 650)}))
    data = minimal_extracted_data(image_urls=[bad])
    result = build_closed_tour_payloads(make_pre_config(), data, fake_api_client)
    assert result["main_tour_payload"]["images"] == [imgdim.FALLBACK_IMAGE]


def test_closed_tour_with_no_images_stays_empty_not_placeholder(fake_api_client):
    from test_builder_closed_tour import make_pre_config, minimal_extracted_data

    data = minimal_extracted_data(image_urls=[])
    result = build_closed_tour_payloads(make_pre_config(), data, fake_api_client)
    assert result["main_tour_payload"]["images"] == []
