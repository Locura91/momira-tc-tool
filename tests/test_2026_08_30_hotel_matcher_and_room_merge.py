"""Tests for hotel_matcher.py's name-matching (_norm-based) and its consumer in
builder.build_hotel_contract_payload's room merge-on-update logic.

Written 2026-08-30 to fix two known Hotel bugs flagged in full-app-audit-2026-08-28.md:

  1. match_room_by_name/match_rate_by_name/match_season_to_existing/
     match_offer_or_supplement_by_name all used exact case/outer-whitespace-only string
     matching (_norm was just `.strip().lower()`). A room name that changed by even a
     whitespace or Unicode-equivalent character (a double space, a tab from PDF extraction, a
     smart-quote variant) silently failed to match the existing room, so the fresh document's
     room was treated as brand-new instead of updating the existing one - creating a duplicate
     room in Travel Compositor rather than reusing its real providerCode.

  2. Fixing that normalization on its own would have introduced a NEW bug: build_hotel_
     contract_payload's carry-forward loop (which re-adds any EXISTING room the fresh document
     doesn't mention, so PUT's full-array-replace semantics never silently drop it) tracked
     "already matched" rooms with its own, separately-normalized `.strip().lower()` set. Once
     match_room_by_name got more lenient than that set's normalization, a room correctly
     matched and merged above could ALSO get carried forward again as an unmatched duplicate.
     Fixed by tracking matched existing rooms by object identity instead of by re-deriving and
     comparing a name string - see the regression test below that guards this specifically.

Neither hotel_matcher.py nor build_hotel_contract_payload had any test coverage before this
file.
"""
import hotel_matcher
from schemas import HumanPreConfig
from builder import build_hotel_contract_payload


# ----------------------------------------------------------------------
# hotel_matcher._norm
# ----------------------------------------------------------------------

def test_norm_is_case_and_outer_whitespace_insensitive():
    assert hotel_matcher._norm("  Deluxe Room  ") == "deluxe room"
    assert hotel_matcher._norm("DELUXE ROOM") == hotel_matcher._norm("deluxe room")


def test_norm_collapses_internal_whitespace_runs():
    # CONFIRMED FIX: a double space, or a tab/newline left over from PDF text extraction, must
    # not break a match against the same room name typed with single spaces.
    assert hotel_matcher._norm("Deluxe  Room") == hotel_matcher._norm("Deluxe Room")
    assert hotel_matcher._norm("Deluxe\tRoom") == hotel_matcher._norm("Deluxe Room")
    assert hotel_matcher._norm("Deluxe\n Room") == hotel_matcher._norm("Deluxe Room")


def test_norm_normalizes_unicode_equivalents():
    # A combining-accent variant of the same visible text should compare equal (NFKC).
    assert hotel_matcher._norm("Café Room") == hotel_matcher._norm("Café Room")


def test_norm_does_not_treat_different_words_as_equal():
    # Deliberately NOT fuzzy-matched - see hotel_matcher._norm's own docstring for why merging
    # two differently-named rooms would be a worse failure than missing a match.
    assert hotel_matcher._norm("Deluxe Room") != hotel_matcher._norm("Deluxe Suite")


def test_norm_handles_none_and_empty():
    assert hotel_matcher._norm(None) == ""
    assert hotel_matcher._norm("") == ""
    assert hotel_matcher._norm("   ") == ""


# ----------------------------------------------------------------------
# match_room_by_name
# ----------------------------------------------------------------------

def test_match_room_by_name_exact():
    rooms = [{"name": "Deluxe Room", "providerCode": "AUTO_1"}]
    assert hotel_matcher.match_room_by_name("Deluxe Room", rooms)["providerCode"] == "AUTO_1"


def test_match_room_by_name_tolerates_double_space():
    rooms = [{"name": "Deluxe  Room", "providerCode": "AUTO_1"}]  # as stored, double space
    assert hotel_matcher.match_room_by_name("Deluxe Room", rooms)["providerCode"] == "AUTO_1"


def test_match_room_by_name_returns_none_for_genuinely_new_room():
    rooms = [{"name": "Deluxe Room", "providerCode": "AUTO_1"}]
    assert hotel_matcher.match_room_by_name("Deluxe Suite", rooms) is None


def test_match_room_by_name_empty_and_garbage_input():
    assert hotel_matcher.match_room_by_name("", [{"name": "Deluxe Room"}]) is None
    assert hotel_matcher.match_room_by_name("Deluxe Room", []) is None
    assert hotel_matcher.match_room_by_name("Deluxe Room", None) is None
    assert hotel_matcher.match_room_by_name("Deluxe Room", ["not a dict"]) is None


# ----------------------------------------------------------------------
# match_rate_by_name / match_season_to_existing / match_offer_or_supplement_by_name
# ----------------------------------------------------------------------

def test_match_rate_by_name():
    rates = [{"name": "Standard Rate", "id": 5}]
    assert hotel_matcher.match_rate_by_name("standard  rate", rates)["id"] == 5
    assert hotel_matcher.match_rate_by_name("Different Rate", rates) is None


def test_match_season_to_existing_prefers_name_match():
    seasons = [{"id": 1, "name": "Summer", "dateRanges": [{"start": "2027-06-01", "end": "2027-08-31"}]}]
    # Date ranges given don't overlap at all - name match must still win.
    match = hotel_matcher.match_season_to_existing(
        "summer", [{"start": "2027-01-01", "end": "2027-01-05"}], seasons)
    assert match["id"] == 1


def test_match_season_to_existing_falls_back_to_date_overlap():
    seasons = [{"id": 1, "name": "Season AUTEO EXAMPLE", "dateRanges": [{"start": "2027-06-01", "end": "2027-08-31"}]}]
    match = hotel_matcher.match_season_to_existing(
        "High Season", [{"start": "2027-07-01", "end": "2027-07-15"}], seasons)
    assert match["id"] == 1  # name differs, but the date range overlaps


def test_match_season_to_existing_none_when_neither_matches():
    seasons = [{"id": 1, "name": "Summer", "dateRanges": [{"start": "2027-06-01", "end": "2027-08-31"}]}]
    match = hotel_matcher.match_season_to_existing(
        "Winter", [{"start": "2027-12-01", "end": "2027-12-15"}], seasons)
    assert match is None


def test_match_offer_or_supplement_by_name():
    items = [{"providerCode": "OFF-1", "names": [{"description": "Early Bird"}]}]
    assert hotel_matcher.match_offer_or_supplement_by_name("early  bird", items)["providerCode"] == "OFF-1"
    assert hotel_matcher.match_offer_or_supplement_by_name("Late Bird", items) is None


# ----------------------------------------------------------------------
# build_hotel_contract_payload - room merge-on-update
# ----------------------------------------------------------------------

def make_pre_config(**overrides):
    defaults = dict(
        supplier_id="48940", provider_code="CAI-H1", min_pax=1, max_pax=4,
        currency="EUR", modality_code="STANDARD",
    )
    defaults.update(overrides)
    return HumanPreConfig(**defaults)


def _room(name, adults=2, children=0):
    return {"name": name, "distributions": [{"adults": adults, "children": children}]}


def test_new_room_gets_no_provider_code():
    extracted = {"hotelname": "Test Hotel", "rooms": [_room("Deluxe Room")]}
    result = build_hotel_contract_payload(make_pre_config(), extracted, existing_hotel_snapshot=None)
    assert result["hotel_error"] is None
    assert result["is_update"] is False
    rooms = result["hotel_payload"]["rooms"]
    assert len(rooms) == 1
    assert rooms[0]["providerCode"] is None


def test_document_room_matching_existing_exactly_reuses_provider_code():
    existing_snapshot = {"rooms": [{"name": "Deluxe Room", "providerCode": "AUTO_1",
                                    "distributions": [{"adults": 2, "children": 0}]}]}
    extracted = {"hotelname": "Test Hotel", "rooms": [_room("Deluxe Room")]}
    result = build_hotel_contract_payload(make_pre_config(), extracted, existing_hotel_snapshot=existing_snapshot)
    rooms = result["hotel_payload"]["rooms"]
    assert len(rooms) == 1  # updated in place, not duplicated
    assert rooms[0]["providerCode"] == "AUTO_1"
    assert result["is_update"] is True


def test_document_room_matching_existing_via_widened_normalization_is_not_duplicated():
    # CONFIRMED FIX regression test (the exact scenario described in this file's docstring):
    # the existing room's real stored name has a double space; the document's room name has a
    # single space. Before the _norm fix this wouldn't have matched at all. After fixing _norm
    # alone (without the id()-based carry-forward fix below it in the same commit), this would
    # have matched AND ALSO been carried forward a second time as an unmatched "existing" room -
    # a silent duplicate room in the payload. Must be exactly one room, with the real providerCode.
    existing_snapshot = {"rooms": [{"name": "Deluxe  Room", "providerCode": "AUTO_1",
                                    "distributions": [{"adults": 2, "children": 0}]}]}
    extracted = {"hotelname": "Test Hotel", "rooms": [_room("Deluxe Room")]}
    result = build_hotel_contract_payload(make_pre_config(), extracted, existing_hotel_snapshot=existing_snapshot)
    rooms = result["hotel_payload"]["rooms"]
    assert len(rooms) == 1
    assert rooms[0]["providerCode"] == "AUTO_1"
    assert result["room_name_matches"]["Deluxe Room"] == "AUTO_1"


def test_existing_room_not_in_document_is_carried_forward_unchanged():
    existing_snapshot = {"rooms": [
        {"name": "Deluxe Room", "providerCode": "AUTO_1", "distributions": [{"adults": 2, "children": 0}]},
        {"name": "Suite", "providerCode": "AUTO_2", "distributions": [{"adults": 4, "children": 1}]},
    ]}
    # This refresh's document only mentions "Deluxe Room" - "Suite" isn't in it at all.
    extracted = {"hotelname": "Test Hotel", "rooms": [_room("Deluxe Room")]}
    result = build_hotel_contract_payload(make_pre_config(), extracted, existing_hotel_snapshot=existing_snapshot)
    rooms = result["hotel_payload"]["rooms"]
    names = sorted(r["name"] for r in rooms)
    assert names == ["Deluxe Room", "Suite"]  # Suite carried forward, not dropped, not duplicated
    suite = next(r for r in rooms if r["name"] == "Suite")
    assert suite["providerCode"] == "AUTO_2"
    assert suite["distributions"][0]["adults"] == 4


def test_brand_new_room_added_alongside_carried_forward_existing_room():
    existing_snapshot = {"rooms": [
        {"name": "Suite", "providerCode": "AUTO_2", "distributions": [{"adults": 4, "children": 1}]},
    ]}
    extracted = {"hotelname": "Test Hotel", "rooms": [_room("Deluxe Room")]}  # genuinely new room
    result = build_hotel_contract_payload(make_pre_config(), extracted, existing_hotel_snapshot=existing_snapshot)
    rooms = result["hotel_payload"]["rooms"]
    assert len(rooms) == 2
    new_room = next(r for r in rooms if r["name"] == "Deluxe Room")
    assert new_room["providerCode"] is None  # brand new, not matched to Suite
    carried = next(r for r in rooms if r["name"] == "Suite")
    assert carried["providerCode"] == "AUTO_2"


# ----------------------------------------------------------------------
# build_hotel_contract_payload - the OTHER known Hotel bug (latitude fallback), already fixed
# before this audit but with no regression test locking it in until now.
# ----------------------------------------------------------------------

def test_latitude_longitude_fall_back_to_existing_snapshot_when_extraction_has_none():
    # CONFIRMED, already fixed (see builder.py's own comment at the latitude= line, "audit,
    # 2026-08-24"): the extractor ALWAYS sets latitude/longitude keys, to None when a document
    # states no coordinates. A naive `extracted.get("latitude", snapshot_lat)` never falls back
    # because the key is present. Verified here still using the safe `or` form.
    existing_snapshot = {
        "rooms": [], "latitude": 30.0444, "longitude": 31.2357,  # Cairo, from a prior upload
    }
    extracted = {"hotelname": "Test Hotel", "rooms": [_room("Deluxe Room")], "latitude": None, "longitude": None}
    result = build_hotel_contract_payload(make_pre_config(), extracted, existing_hotel_snapshot=existing_snapshot)
    assert result["hotel_payload"]["latitude"] == 30.0444
    assert result["hotel_payload"]["longitude"] == 31.2357


def test_latitude_longitude_from_extraction_wins_when_present():
    existing_snapshot = {"rooms": [], "latitude": 30.0444, "longitude": 31.2357}
    extracted = {"hotelname": "Test Hotel", "rooms": [_room("Deluxe Room")], "latitude": 29.9, "longitude": 31.1}
    result = build_hotel_contract_payload(make_pre_config(), extracted, existing_hotel_snapshot=existing_snapshot)
    assert result["hotel_payload"]["latitude"] == 29.9
    assert result["hotel_payload"]["longitude"] == 31.1
