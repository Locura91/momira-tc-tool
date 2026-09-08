"""Tests for the "Use Travel Compositor master data?" Hotel feature (product owner, 2026-09-06):
mirrors Travel Compositor's own manual "add hotel" screen, which offers to seed a new hotel from
Travel Compositor's own master hotel content database before asking a human to hunt for photos.

Feasibility investigation (2026-09-06, real Swagger + real API responses) confirmed Travel
Compositor's master hotel database (361,942 records via GET /accommodations) has NO live
name-search endpoint at all, and the curated GET /accommodations/preferred/{micrositeId} list is
too narrow (confirmed missing a real hotel, HRG-H1 Steigenberger Golf Resort El Gouna, entirely).
So this feature works against a LOCAL COPY synced from Travel Compositor (masterdata_store.py,
stored via platform_store.py so it survives Streamlit Cloud redeploys - see that module's own
docstring), matched by name/country/geolocation (masterdata_matcher.py) since the product owner
confirmed (2026-09-06) that GIATA ids are never present in the contracts we receive from local
partners. Every match is shown to a human to confirm, never auto-applied (product owner decision,
2026-09-06).

Uses the same offline platform_store isolation every other durable-storage test relies on
(see conftest.py: PLATFORM_STORE_PATH is a fresh temp SQLite file, no DATABASE_URL).
"""
import pytest

import masterdata_matcher as mm
import masterdata_store as ms
import platform_store


@pytest.fixture(autouse=True)
def _clean_masterdata_namespace():
    """The local master-data index lives in platform_store, which - per conftest.py - is one
    shared SQLite file for the whole test session, not reset per-test. Without this, whichever
    sync test happens to run first leaves records another test (e.g. the "no sync has ever run"
    case) would wrongly see as already present. Same isolation pattern conftest.py's own
    circuit-breaker fixture uses for other process-lifetime state."""
    for key in list(platform_store.get_namespace(ms._NAMESPACE).keys()):
        platform_store.delete(ms._NAMESPACE, key)
    yield
    for key in list(platform_store.get_namespace(ms._NAMESPACE).keys()):
        platform_store.delete(ms._NAMESPACE, key)


# ---------------------------------------------------------------------------
# masterdata_matcher.find_candidates
# ---------------------------------------------------------------------------

_INDEX = [
    {"id": "TC-1", "giataId": 111, "name": "Steigenberger Golf Resort El Gouna",
     "geolocation": {"latitude": 27.394, "longitude": 33.679}, "countryCode": "EG"},
    {"id": "TC-2", "giataId": 222, "name": "Steigenberger Pyramids Cairo",
     "geolocation": {"latitude": 29.987, "longitude": 31.209}, "countryCode": "EG"},
    {"id": "TC-3", "giataId": 333, "name": "Riu Palace Bavaro",
     "geolocation": {"latitude": 18.7, "longitude": -68.4}, "countryCode": "DO"},
]


def test_close_name_match_ranks_first_without_geo():
    results = mm.find_candidates("Steigenberger Golf Resort El Gouna", _INDEX)
    assert results
    assert results[0]["id"] == "TC-1"
    assert results[0]["score"] > 0.9


def test_country_filter_excludes_records_from_other_countries():
    results = mm.find_candidates("Steigenberger", _INDEX, country_code="DO")
    assert all(r["countryCode"] == "DO" for r in results)
    assert not any(r["id"] in ("TC-1", "TC-2") for r in results)


def test_country_filter_is_case_insensitive():
    results_lower = mm.find_candidates("Steigenberger Golf Resort El Gouna", _INDEX, country_code="eg")
    results_upper = mm.find_candidates("Steigenberger Golf Resort El Gouna", _INDEX, country_code="EG")
    assert [r["id"] for r in results_lower] == [r["id"] for r in results_upper]


def test_geolocation_boosts_score_for_the_nearby_record_over_a_similarly_named_one():
    # Both Steigenberger hotels share a lot of name overlap; a real geolocation near El Gouna
    # should push TC-1 further ahead of TC-2 than name similarity alone would.
    no_geo = mm.find_candidates("Steigenberger", _INDEX)
    no_geo_gap = next(r for r in no_geo if r["id"] == "TC-1")["score"] - next(r for r in no_geo if r["id"] == "TC-2")["score"]

    with_geo = mm.find_candidates("Steigenberger", _INDEX, lat=27.4, lon=33.68)
    with_geo_gap = next(r for r in with_geo if r["id"] == "TC-1")["score"] - next(r for r in with_geo if r["id"] == "TC-2")["score"]

    assert with_geo_gap > no_geo_gap


def test_far_away_geolocation_gives_no_boost():
    results = mm.find_candidates("Steigenberger Golf Resort El Gouna", _INDEX, lat=18.7, lon=-68.4)
    top = results[0]
    assert top["geo_km"] is not None and top["geo_km"] > mm._GEO_BOOST_RADIUS_KM
    assert top["score"] == top["name_score"]


def test_blank_query_or_empty_index_returns_nothing_and_never_raises():
    assert mm.find_candidates("", _INDEX) == []
    assert mm.find_candidates("Steigenberger", []) == []
    assert mm.find_candidates(None, _INDEX) == []


def test_unrelated_name_below_threshold_is_dropped():
    results = mm.find_candidates("Completely Unrelated Guesthouse Name Xyz", _INDEX)
    assert results == []


def test_results_are_capped_at_limit():
    big_index = [{"id": f"TC-{i}", "name": "Steigenberger Golf Resort El Gouna",
                  "geolocation": {}, "countryCode": "EG"} for i in range(20)]
    results = mm.find_candidates("Steigenberger Golf Resort El Gouna", big_index, limit=3)
    assert len(results) == 3


# ---------------------------------------------------------------------------
# masterdata_matcher.datasheet_to_masterdata_seed
# ---------------------------------------------------------------------------

def test_datasheet_seed_collects_hosted_image_urls_without_dedup_loss():
    datasheet = {
        "name": "Steigenberger Golf Resort El Gouna",
        "images": [{"url": "https://tc.example/a.jpg"}, {"url": "https://tc.example/b.jpg"},
                    {"url": "https://tc.example/a.jpg"}],  # real duplicate confirmed possible
        "description": "A resort in El Gouna.",
        "address": "El Gouna, Egypt",
        "geolocation": {"latitude": 27.4, "longitude": 33.68},
    }
    seed = mm.datasheet_to_masterdata_seed(datasheet)
    assert seed["image_urls"] == ["https://tc.example/a.jpg", "https://tc.example/b.jpg"]
    assert seed["name"] == "Steigenberger Golf Resort El Gouna"
    assert seed["geolocation"] == {"latitude": 27.4, "longitude": 33.68}
    assert "El Gouna" in seed["text_block"]
    assert "TRAVEL COMPOSITOR MASTER DATA" in seed["text_block"]


def test_datasheet_seed_handles_missing_fields_without_raising():
    assert mm.datasheet_to_masterdata_seed({}) == {
        "image_urls": [], "text_block": "", "geolocation": None, "name": None}
    assert mm.datasheet_to_masterdata_seed(None)["image_urls"] == []


# ---------------------------------------------------------------------------
# masterdata_store: sync + local index, against platform_store (offline SQLite per conftest.py)
# ---------------------------------------------------------------------------

class _FakeClient:
    """Mimics TravelCompositorAPI.get_accommodations_page's paginated response shape, confirmed
    real via Swagger 2026-09-06 (accommodations / pagination.totalResults)."""

    def __init__(self, records, fail_at_offset=None):
        self._records = records
        self._fail_at_offset = fail_at_offset

    def get_accommodations_page(self, first, limit):
        if self._fail_at_offset is not None and first >= self._fail_at_offset:
            return {"error": 500, "message": "simulated failure"}
        batch = self._records[first:first + limit]
        return {
            "accommodations": batch,
            "pagination": {"firstResult": first, "pageResults": len(batch), "totalResults": len(self._records)},
        }


def _make_records(n):
    return [{"id": f"id-{i}", "giataId": i, "name": f"Hotel {i}",
             "geolocation": {"latitude": 1.0, "longitude": 2.0}, "countryCode": "EG",
             "lastUpdate": "2026-09-06T00:00:00Z"} for i in range(n)]


def test_sync_paginates_until_total_results_reached_and_index_becomes_usable():
    client = _FakeClient(_make_records(12))
    result = ms.sync_accommodation_index(client)
    assert result["ok"] is True
    assert result["total_records"] == 12
    assert ms.index_is_usable() is True

    loaded = ms.load_index()
    assert len(loaded) == 12
    assert {r["id"] for r in loaded} == {f"id-{i}" for i in range(12)}


def test_sync_chunks_records_across_multiple_stored_pages():
    # Force multiple storage chunks even for a small record count, so the chunking logic itself
    # (not just the fetch-pagination logic) is exercised without needing a real 361k-record run.
    original_page_size = ms._PAGE_SIZE_STORE
    ms._PAGE_SIZE_STORE = 5
    try:
        client = _FakeClient(_make_records(12))
        result = ms.sync_accommodation_index(client)
        assert result["ok"] is True
        meta = ms.index_meta()
        assert meta["page_count"] == 3  # 5 + 5 + 2
        assert len(ms.load_index()) == 12
    finally:
        ms._PAGE_SIZE_STORE = original_page_size


def test_failed_sync_leaves_no_usable_index_and_reports_the_error():
    # fail_at_offset must land on a SECOND fetch page to actually be exercised - the real
    # _PAGE_SIZE_FETCH (1000) would fetch these 20 records in one call otherwise.
    original_page_size = ms._PAGE_SIZE_FETCH
    ms._PAGE_SIZE_FETCH = 5
    try:
        client = _FakeClient(_make_records(20), fail_at_offset=5)
        result = ms.sync_accommodation_index(client)
        assert result["ok"] is False
        assert result["error"]
        assert ms.index_is_usable() is False
    finally:
        ms._PAGE_SIZE_FETCH = original_page_size


def test_failed_resync_does_not_clobber_a_previously_complete_index():
    # A real production risk: a sync that fails partway through must never leave the app worse
    # off than before it started - the previous, complete index has to stay usable.
    good_client = _FakeClient(_make_records(10))
    assert ms.sync_accommodation_index(good_client)["ok"] is True
    assert len(ms.load_index()) == 10

    original_page_size = ms._PAGE_SIZE_FETCH
    ms._PAGE_SIZE_FETCH = 3
    try:
        bad_client = _FakeClient(_make_records(50), fail_at_offset=3)
        bad_result = ms.sync_accommodation_index(bad_client)
        assert bad_result["ok"] is False
    finally:
        ms._PAGE_SIZE_FETCH = original_page_size

    # The old, good index must still be there and still usable.
    assert ms.index_is_usable() is True
    assert len(ms.load_index()) == 10


def test_no_sync_ever_run_reports_empty_and_unusable():
    assert ms.index_meta() is None
    assert ms.index_is_usable() is False
    assert ms.load_index() == []
