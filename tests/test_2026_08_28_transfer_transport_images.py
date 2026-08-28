"""Tests for builder._effective_images_for_update - the shared images-selection rule used by
both build_transfer_payload and build_transport_payloads (2026-08-28, supplier_images.py
feature).

Tests the pure helper directly (no network/API calls needed), same approach as
test_builder_minimum_charge_synthesis.py - the full build_transfer_payload/build_transport_
payloads pipelines need geolocation resolution this suite doesn't exercise here.

CONFIRMED RULE (product owner, 2026-08-28): "this image must replace, if there is, an
existing image" - a resolved image_urls value (set by app.py from supplier_images.py's
direction-classified mass-upload image, or a human's manual override) REPLACES whatever's
already live, unlike every other field on Transfer/Transport's update payload (dates,
properties), which are preserved from the live record instead.
"""
from builder import _effective_images_for_update


def test_no_image_urls_and_no_existing_snapshot_is_a_fresh_create_with_no_images():
    assert _effective_images_for_update({}, None) == []


def test_no_image_urls_on_an_update_preserves_the_existing_live_images():
    existing = {"images": ["https://cdn.example.com/old-photo.jpg"]}
    assert _effective_images_for_update({}, existing) == ["https://cdn.example.com/old-photo.jpg"]


def test_no_image_urls_on_an_update_with_no_existing_images_stays_empty():
    existing = {"images": []}
    assert _effective_images_for_update({}, existing) == []


def test_a_resolved_image_url_is_used_on_a_fresh_create():
    data = {"image_urls": ["https://images.example.com/airport-pickup.jpg"]}
    assert _effective_images_for_update(data, None) == ["https://images.example.com/airport-pickup.jpg"]


def test_a_resolved_image_url_replaces_the_existing_live_image_on_update():
    data = {"image_urls": ["https://images.example.com/new-hotel-pickup.jpg"]}
    existing = {"images": ["https://cdn.example.com/old-photo.jpg"]}
    result = _effective_images_for_update(data, existing)
    assert result == ["https://images.example.com/new-hotel-pickup.jpg"]
    assert "https://cdn.example.com/old-photo.jpg" not in result


def test_an_empty_image_urls_list_does_not_count_as_resolved_falls_back_to_preserve():
    data = {"image_urls": []}
    existing = {"images": ["https://cdn.example.com/old-photo.jpg"]}
    assert _effective_images_for_update(data, existing) == ["https://cdn.example.com/old-photo.jpg"]


def test_the_returned_list_is_a_copy_not_a_reference_into_extracted_data():
    data = {"image_urls": ["https://images.example.com/photo.jpg"]}
    result = _effective_images_for_update(data, None)
    result.append("https://images.example.com/tampered.jpg")
    assert data["image_urls"] == ["https://images.example.com/photo.jpg"]
