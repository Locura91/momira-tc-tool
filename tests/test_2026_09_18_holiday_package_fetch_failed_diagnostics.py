"""Regression tests for a real product-owner report (2026-09-18): translating a single Holiday
Package by ID (package 59011047, 2 target languages, live mode) came back with only

    {"status": "fetch_failed", "package_id": "59011047"}

and nothing else - no way to tell whether the package ID was simply wrong/belongs to a different
microsite, or whether the underlying GET /package/{microsite_id} call itself failed (auth,
network, wrong microsite ID). fetch_holiday_package_by_id used to collapse both very different
situations into a bare `None`.

Fix: fetch_holiday_package_by_id now returns (entry, error_detail) - error_detail is None on
success, or a dict with "reason": "api_error" (the raw Travel Compositor error attached under
"detail") or "reason": "not_found" (every page of the microsite's package list was read fine, but
none had this id - by far the more common real case, e.g. a mistyped ID or the wrong microsite).
sync_holiday_package merges error_detail straight into its returned "fetch_failed" result, so the
human sees the real reason without any extra plumbing (the review screen already renders the raw
result JSON verbatim, as the product owner's own screenshot showed).
"""
from sync_holiday_package import fetch_holiday_package_by_id, sync_holiday_package


class _FakeAPI:
    """Returns canned get_holiday_packages() responses in sequence, one per call - lets tests
    drive multi-page pagination and error responses without a real HTTP client."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get_holiday_packages(self, microsite_id, lang="EN", **filters):
        self.calls.append((microsite_id, lang, filters))
        return self.responses.pop(0)


def _page(packages, total=None, first_result=0, page_size=100):
    return {"pagination": {"totalResults": total if total is not None else len(packages)}, "package": packages}


# ---------------------------------------------------------------------------------------------
# fetch_holiday_package_by_id - the two distinct failure shapes
# ---------------------------------------------------------------------------------------------

def test_found_on_first_page_returns_entry_and_no_error_detail():
    api = _FakeAPI([_page([{"id": 111, "title": "A"}, {"id": 59011047, "title": "B"}])])
    entry, error_detail = fetch_holiday_package_by_id(api, "momiratravel", "59011047")
    assert entry == {"id": 59011047, "title": "B"}
    assert error_detail is None


def test_found_by_string_vs_int_id_comparison():
    # Travel Compositor ids can come back as ints; the package_id passed in is always a string
    # from the UI - the match must be string-normalized on both sides.
    api = _FakeAPI([_page([{"id": 59011047, "title": "B"}])])
    entry, error_detail = fetch_holiday_package_by_id(api, "momiratravel", "59011047")
    assert entry is not None
    assert error_detail is None


def test_api_error_is_reported_as_api_error_not_swallowed():
    api = _FakeAPI([{"error": 401, "message": "invalid auth token"}])
    entry, error_detail = fetch_holiday_package_by_id(api, "momiratravel", "59011047")
    assert entry is None
    assert error_detail["reason"] == "api_error"
    assert error_detail["detail"] == {"error": 401, "message": "invalid auth token"}


def test_genuinely_missing_id_after_reading_every_page_is_reported_as_not_found():
    api = _FakeAPI([_page([{"id": 111}, {"id": 222}])])
    entry, error_detail = fetch_holiday_package_by_id(api, "momiratravel", "59011047")
    assert entry is None
    assert error_detail["reason"] == "not_found"
    assert error_detail["packages_seen"] == 2
    assert "59011047" in error_detail["note"]
    assert "momiratravel" in error_detail["note"]


def test_not_found_paginates_through_multiple_pages_before_giving_up():
    api = _FakeAPI([
        _page([{"id": 1}, {"id": 2}], total=3),
        _page([{"id": 3}], total=3),
    ])
    entry, error_detail = fetch_holiday_package_by_id(api, "momiratravel", "999", page_size=2)
    assert entry is None
    assert error_detail["reason"] == "not_found"
    assert error_detail["packages_seen"] == 3


def test_found_on_a_later_page_stops_paginating_immediately():
    api = _FakeAPI([
        _page([{"id": 1}, {"id": 2}], total=4),
        _page([{"id": 3}, {"id": 59011047, "title": "found"}], total=4),
    ])
    entry, error_detail = fetch_holiday_package_by_id(api, "momiratravel", "59011047", page_size=2)
    assert entry == {"id": 59011047, "title": "found"}
    assert error_detail is None
    assert len(api.calls) == 2  # never fetched a third page


def test_empty_first_page_is_reported_as_not_found_not_api_error():
    api = _FakeAPI([_page([], total=0)])
    entry, error_detail = fetch_holiday_package_by_id(api, "momiratravel", "59011047")
    assert entry is None
    assert error_detail["reason"] == "not_found"
    assert error_detail["packages_seen"] == 0


# ---------------------------------------------------------------------------------------------
# sync_holiday_package - the error_detail is merged straight into the human-visible result
# ---------------------------------------------------------------------------------------------

def test_sync_holiday_package_merges_not_found_detail_into_fetch_failed_result():
    api = _FakeAPI([_page([{"id": 1}])])
    result = sync_holiday_package(api, None, None, "momiratravel", "59011047", ["DE", "PL"])
    assert result["status"] == "fetch_failed"
    assert result["package_id"] == "59011047"
    assert result["reason"] == "not_found"
    assert "59011047" in result["note"]


def test_sync_holiday_package_merges_api_error_detail_into_fetch_failed_result():
    api = _FakeAPI([{"error": 500, "message": "internal server error"}])
    result = sync_holiday_package(api, None, None, "momiratravel", "59011047", ["DE", "PL"])
    assert result["status"] == "fetch_failed"
    assert result["reason"] == "api_error"
    assert result["detail"] == {"error": 500, "message": "internal server error"}


def test_sync_holiday_package_success_path_is_unaffected_by_the_tuple_change():
    api = _FakeAPI([_page([{"id": "59011047", "title": "", "active": True, "visible": True}])])
    result = sync_holiday_package(api, None, None, "momiratravel", "59011047", ["DE"])
    # Found, so it proceeds into sync_one_package_entry rather than fetch_failed - ends up
    # "skipped" here only because there's no translatable text, not because of the fetch itself.
    assert result["status"] == "skipped"
    assert result["reason"] == "no translatable text fields found"
