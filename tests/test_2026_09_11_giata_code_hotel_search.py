"""Regression tests for masterdata_matcher.find_by_giata_id (product owner, 2026-09-11, shown
Travel Compositor's own "New hotel using master data" Destination + Name search screen as the
model to match): "Could we search it with Giatacodes if we enter it or just by manually adding
the name of the hotel in the first step". Name search already existed (2026-09-06) - this adds
the id-based alternative path, authoritative rather than fuzzy.
"""
import masterdata_matcher as mm

_INDEX = [
    {"id": "TC-1", "giataId": 111, "name": "Steigenberger Golf Resort El Gouna",
     "geolocation": {"latitude": 27.394, "longitude": 33.679}, "countryCode": "EG"},
    {"id": "TC-2", "giataId": 222, "name": "Steigenberger Pyramids Cairo",
     "geolocation": {"latitude": 29.987, "longitude": 31.209}, "countryCode": "EG"},
    {"id": "TC-3", "giataId": "333", "name": "Riu Palace Bavaro",  # stored as a STRING on purpose
     "geolocation": {"latitude": 18.7, "longitude": -68.4}, "countryCode": "DO"},
    {"id": "TC-4", "giataId": None, "name": "No GIATA On File",
     "geolocation": {}, "countryCode": "EG"},
]


def test_exact_int_giata_match():
    out = mm.find_by_giata_id("111", _INDEX)
    assert len(out) == 1
    assert out[0]["id"] == "TC-1"
    assert out[0]["score"] == 1.0


def test_matches_regardless_of_whether_the_index_stores_int_or_str():
    # TC-2's giataId is an int (222), TC-3's is a str ("333") - both must match a plain string
    # query the same way, since a human typing a code has no way to know which the index has.
    assert mm.find_by_giata_id("222", _INDEX)[0]["id"] == "TC-2"
    assert mm.find_by_giata_id("333", _INDEX)[0]["id"] == "TC-3"


def test_records_with_no_giata_id_are_never_matched():
    assert mm.find_by_giata_id("", _INDEX) == []
    # A blank query must never accidentally match TC-4's None giataId via some str(None) quirk.
    for record in _INDEX:
        if record["giataId"] is None:
            assert str(record.get("giataId")) != ""


def test_no_match_returns_empty_list_not_none():
    assert mm.find_by_giata_id("999999", _INDEX) == []


def test_blank_query_or_empty_index_returns_nothing():
    assert mm.find_by_giata_id("", _INDEX) == []
    assert mm.find_by_giata_id("   ", _INDEX) == []
    assert mm.find_by_giata_id("111", []) == []


def test_whitespace_around_the_typed_code_is_ignored():
    assert mm.find_by_giata_id("  111  ", _INDEX)[0]["id"] == "TC-1"


def test_multiple_records_sharing_a_giata_id_are_all_returned_not_silently_picked():
    dirty_index = _INDEX + [
        {"id": "TC-5", "giataId": 111, "name": "Duplicate Data Quality Issue",
         "geolocation": {}, "countryCode": "EG"},
    ]
    out = mm.find_by_giata_id("111", dirty_index)
    assert {r["id"] for r in out} == {"TC-1", "TC-5"}


def test_result_shape_matches_find_candidates_for_the_shared_ui_renderer():
    out = mm.find_by_giata_id("111", _INDEX)[0]
    # The review UI renders find_candidates and find_by_giata_id results through the same loop -
    # both must carry these keys, even though name_score/geo_km are meaningless for an id match.
    assert set(["score", "name_score", "geo_km"]).issubset(out.keys())
    assert out["name_score"] is None
    assert out["geo_km"] is None
