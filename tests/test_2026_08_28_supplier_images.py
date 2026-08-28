"""Tests for supplier_images.py - the per-supplier "mass upload" Airport/Harbor<->Hotel
image feature for Transfer/Transport (2026-08-28, product-owner request: "I also want to
mass upload images per supplier... the tool must understand if it goes to Hotel or if it
goes to Airport/Harbor, which will always be distinguished by the name 'Airport' or 'Harbor'
in the Route").

Confirmed decisions this feature pins:
  - Direction is decided by the ARRIVAL name (not departure, not "either").
  - A route where both or neither endpoint mentions Airport/Harbor can't be classified -
    must return None (no guessing), never default to one direction.
  - Exactly ONE image per supplier+product_type+direction - saving a new one replaces the
    old one outright, no gallery/accumulation.

Uses the same offline platform_store isolation every other durable-storage test relies on
(see conftest.py: PLATFORM_STORE_PATH is a fresh temp SQLite file, no DATABASE_URL).
"""
import supplier_images as si


def _reset(product_type, supplier_id, directions=si.DIRECTIONS):
    for d in directions:
        si.delete_supplier_image(supplier_id, product_type, d)


# ---------------------------------------------------------------------------
# classify_direction
# ---------------------------------------------------------------------------

def test_arrival_at_the_airport_means_hotel_to_airport_direction():
    assert si.classify_direction("Steigenberger Hotel", "Hurghada Airport") == si.DIRECTION_HOTEL_TO_AIRPORT


def test_arrival_at_the_hotel_means_airport_to_hotel_direction():
    assert si.classify_direction("Hurghada Airport", "Steigenberger Hotel") == si.DIRECTION_AIRPORT_TO_HOTEL


def test_harbor_in_the_arrival_name_also_counts():
    assert si.classify_direction("Riu Palace Hotel", "Marsa Alam Harbor") == si.DIRECTION_HOTEL_TO_AIRPORT


def test_british_harbour_spelling_also_counts():
    assert si.classify_direction("Riu Palace Hotel", "Southampton Harbour") == si.DIRECTION_HOTEL_TO_AIRPORT


def test_classification_is_case_insensitive():
    assert si.classify_direction("hurghada AIRPORT", "steigenberger hotel") == si.DIRECTION_AIRPORT_TO_HOTEL


def test_neither_endpoint_mentioning_airport_or_harbor_is_ambiguous():
    assert si.classify_direction("Riu Palace Hotel", "Steigenberger Hotel") is None


def test_both_endpoints_mentioning_airport_or_harbor_is_ambiguous():
    assert si.classify_direction("Hurghada Airport", "Marsa Alam Harbor") is None


def test_a_hotel_named_harborview_does_not_false_positive_as_harbor():
    """Word-boundary matching - 'Harborview' contains 'harbor' as a substring but is not the
    word 'harbor' on its own, same technique already proven for is_generic_name/is_ota_or_
    marketplace in outreach_discovery.py."""
    assert si.classify_direction("Harborview Hotel", "Hurghada Airport") == si.DIRECTION_HOTEL_TO_AIRPORT


def test_an_airporter_lodge_does_not_false_positive_as_airport():
    assert si.classify_direction("Airporter Lodge", "Marsa Alam Harbor") == si.DIRECTION_HOTEL_TO_AIRPORT


def test_blank_names_are_ambiguous_not_a_crash():
    assert si.classify_direction("", "") is None
    assert si.classify_direction(None, None) is None


# ---------------------------------------------------------------------------
# get/set/delete round trip
# ---------------------------------------------------------------------------

def test_set_and_get_round_trips_bytes_and_extension():
    _reset("Transfer", "SUP-A")
    ok = si.set_supplier_image("SUP-A", "Transfer", si.DIRECTION_AIRPORT_TO_HOTEL, b"fake-image-bytes", "png")
    assert ok is True
    stored = si.get_supplier_image("SUP-A", "Transfer", si.DIRECTION_AIRPORT_TO_HOTEL)
    assert stored["ext"] == "png"
    import base64
    assert base64.b64decode(stored["bytes_b64"]) == b"fake-image-bytes"


def test_get_returns_none_when_nothing_saved():
    _reset("Transfer", "SUP-B")
    assert si.get_supplier_image("SUP-B", "Transfer", si.DIRECTION_HOTEL_TO_AIRPORT) is None


def test_saving_a_new_image_for_the_same_direction_replaces_the_old_one_outright():
    """CONFIRMED RULE (product owner, 2026-08-28): exactly one image per direction, no
    gallery/accumulation - re-uploading replaces, it doesn't add a second one."""
    _reset("Transfer", "SUP-C")
    si.set_supplier_image("SUP-C", "Transfer", si.DIRECTION_AIRPORT_TO_HOTEL, b"old-bytes", "jpg")
    si.set_supplier_image("SUP-C", "Transfer", si.DIRECTION_AIRPORT_TO_HOTEL, b"new-bytes", "png")
    stored = si.get_supplier_image("SUP-C", "Transfer", si.DIRECTION_AIRPORT_TO_HOTEL)
    import base64
    assert base64.b64decode(stored["bytes_b64"]) == b"new-bytes"
    assert stored["ext"] == "png"


def test_delete_removes_a_saved_image():
    _reset("Transfer", "SUP-D")
    si.set_supplier_image("SUP-D", "Transfer", si.DIRECTION_HOTEL_TO_AIRPORT, b"bytes", "jpg")
    assert si.get_supplier_image("SUP-D", "Transfer", si.DIRECTION_HOTEL_TO_AIRPORT) is not None
    assert si.delete_supplier_image("SUP-D", "Transfer", si.DIRECTION_HOTEL_TO_AIRPORT) is True
    assert si.get_supplier_image("SUP-D", "Transfer", si.DIRECTION_HOTEL_TO_AIRPORT) is None


def test_transfer_and_transport_images_are_independent_for_the_same_supplier_and_direction():
    _reset("Transfer", "SUP-E")
    _reset("Transport", "SUP-E")
    si.set_supplier_image("SUP-E", "Transfer", si.DIRECTION_AIRPORT_TO_HOTEL, b"transfer-bytes", "jpg")
    assert si.get_supplier_image("SUP-E", "Transport", si.DIRECTION_AIRPORT_TO_HOTEL) is None
    stored = si.get_supplier_image("SUP-E", "Transfer", si.DIRECTION_AIRPORT_TO_HOTEL)
    import base64
    assert base64.b64decode(stored["bytes_b64"]) == b"transfer-bytes"


def test_an_unrecognized_extension_falls_back_to_jpg():
    _reset("Transfer", "SUP-F")
    si.set_supplier_image("SUP-F", "Transfer", si.DIRECTION_AIRPORT_TO_HOTEL, b"bytes", "tiff")
    stored = si.get_supplier_image("SUP-F", "Transfer", si.DIRECTION_AIRPORT_TO_HOTEL)
    assert stored["ext"] == "jpg"


def test_saving_with_no_bytes_is_rejected_not_stored_as_an_empty_row():
    _reset("Transfer", "SUP-G")
    ok = si.set_supplier_image("SUP-G", "Transfer", si.DIRECTION_AIRPORT_TO_HOTEL, b"", "jpg")
    assert ok is False
    assert si.get_supplier_image("SUP-G", "Transfer", si.DIRECTION_AIRPORT_TO_HOTEL) is None


# ---------------------------------------------------------------------------
# resolve_and_host_image
# ---------------------------------------------------------------------------

def test_resolve_returns_none_none_for_an_unclassifiable_route():
    tiers, direction = si.resolve_and_host_image("SUP-H", "Transfer", "Riu Palace Hotel", "Steigenberger Hotel")
    assert tiers is None
    assert direction is None


def test_resolve_returns_none_url_with_the_direction_when_classified_but_nothing_saved():
    _reset("Transfer", "SUP-I")
    url, direction = si.resolve_and_host_image("SUP-I", "Transfer", "Hurghada Airport", "Steigenberger Hotel")
    assert url is None
    assert direction == si.DIRECTION_AIRPORT_TO_HOTEL


def test_resolve_uploads_a_fresh_copy_and_never_reuses_a_cached_url(monkeypatch):
    """CONFIRMED design constraint: a mass-uploaded image must survive R2's own recommended
    ~2-day auto-expiry lifecycle rule, since it's reused across many separate publishes over
    weeks/months - achieved by minting a FRESH R2 URL from the durably-stored bytes on every
    call, never caching/reusing a previously-minted one."""
    _reset("Transfer", "SUP-J")
    si.set_supplier_image("SUP-J", "Transfer", si.DIRECTION_HOTEL_TO_AIRPORT, b"the-real-bytes", "jpg")

    calls = []

    def fake_upload_image(image_bytes, filename="image.jpg"):
        calls.append(image_bytes)
        return f"https://images.example.com/{len(calls)}.jpg"

    import r2_client
    monkeypatch.setattr(r2_client, "upload_image", fake_upload_image)

    url1, direction1 = si.resolve_and_host_image("SUP-J", "Transfer", "Steigenberger Hotel", "Hurghada Airport")
    url2, direction2 = si.resolve_and_host_image("SUP-J", "Transfer", "Riu Palace Hotel", "Marsa Alam Harbor")

    assert direction1 == direction2 == si.DIRECTION_HOTEL_TO_AIRPORT
    assert url1 == "https://images.example.com/1.jpg"
    assert url2 == "https://images.example.com/2.jpg"
    assert url1 != url2  # a fresh upload every call, never a cached URL
    assert calls == [b"the-real-bytes", b"the-real-bytes"]


def test_resolve_returns_none_url_when_the_r2_upload_fails(monkeypatch):
    _reset("Transfer", "SUP-K")
    si.set_supplier_image("SUP-K", "Transfer", si.DIRECTION_AIRPORT_TO_HOTEL, b"bytes", "jpg")

    import r2_client

    def failing_upload(image_bytes, filename="image.jpg"):
        raise RuntimeError("R2 upload failed")

    monkeypatch.setattr(r2_client, "upload_image", failing_upload)

    url, direction = si.resolve_and_host_image("SUP-K", "Transfer", "Hurghada Airport", "Steigenberger Hotel")
    assert url is None
    assert direction == si.DIRECTION_AIRPORT_TO_HOTEL


# ---------------------------------------------------------------------------
# list_supplier_images
# ---------------------------------------------------------------------------

def test_list_reports_saved_images_without_leaking_raw_bytes():
    _reset("Transfer", "SUP-L")
    si.set_supplier_image("SUP-L", "Transfer", si.DIRECTION_AIRPORT_TO_HOTEL, b"bytes", "jpg")
    rows = [r for r in si.list_supplier_images() if r["supplier_id"] == "SUP-L"]
    assert len(rows) == 1
    assert rows[0]["product_type"] == "Transfer"
    assert rows[0]["direction"] == si.DIRECTION_AIRPORT_TO_HOTEL
    assert "bytes_b64" not in rows[0]
