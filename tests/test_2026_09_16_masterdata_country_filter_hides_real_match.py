"""Regression test for a real production bug (product owner, 2026-09-16, with screenshot):
searching the local master-data index for "Siwa Shali Resort" (Destination "Siwa Oasis, Egypt",
Country EG) returned 8 candidates - a page of unrelated Sharm-area resorts 300-950km away - and
did NOT include the actual hotel, even though Travel Compositor's own "Automap with master"
search in its back office found it immediately by the exact same name. "This is a clear problem."

Root cause: masterdata_matcher.find_candidates' country_code filter was a HARD exclude on the
candidate record's own countryCode field. Travel Compositor's ~361k-record master database can
have a missing/blank/wrong countryCode on an individual record even though the hotel itself is
genuinely in that country - a data-quality gap on Travel Compositor's side that this app's filter
was blindly trusting, silently discarding an otherwise-exact name match before it ever reached
the scoring/ranking step, while similarly-EG-tagged but geographically unrelated hotels (which
only share generic word fragments like "Resort") were free to fill the results list instead.

Fix (masterdata_matcher.find_candidates): the country filter is now a first, fast PASS rather
than an absolute one. When that pass's best result isn't a strong match (score below
_STRONG_MATCH_THRESHOLD) - including when it finds nothing at all - a second pass searches the
WHOLE index by name (+ geolocation boost/penalty), and anything it finds is merged in, flagged
country_mismatch=True so a human sees why a hotel from an unexpected country appears rather than
never being shown it. See test_2026_09_06_hotel_masterdata_search.py for the matcher-level unit
tests this shares its fixture style with; this file focuses on reproducing the exact reported
scenario end-to-end and on the UI wiring that surfaces the new flag.
"""
import os

import masterdata_matcher as mm

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_HELPERS_PATH = os.path.join(os.path.dirname(_HERE), "app_helpers.py")


def _read_app_helpers():
    with open(_APP_HELPERS_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _index_missing_country_code_on_the_real_match():
    """Mirrors the real scenario: the actual hotel's record has a blank countryCode (a Travel
    Compositor data-quality gap), while several genuinely-unrelated hotels correctly carry "EG" -
    those unrelated ones are what used to fill up the results instead."""
    return [
        {"id": "TC-SIWA", "giataId": 90001, "name": "Siwa Shali Resort",
         "geolocation": {"latitude": 29.203, "longitude": 25.519}, "countryCode": ""},
        {"id": "TC-SHARM-1", "giataId": 90002, "name": "Akassia Swiss Resort",
         "geolocation": {"latitude": 27.914, "longitude": 34.328}, "countryCode": "EG"},
        {"id": "TC-SHARM-2", "giataId": 90003, "name": "Siva Sharm Resort & Spa",
         "geolocation": {"latitude": 27.837, "longitude": 34.301}, "countryCode": "EG"},
        {"id": "TC-SHARM-3", "giataId": 90004, "name": "Sharm Resort",
         "geolocation": {"latitude": 27.912, "longitude": 34.330}, "countryCode": "EG"},
    ]


def test_the_real_hotel_is_now_found_despite_its_own_blank_country_code():
    index = _index_missing_country_code_on_the_real_match()
    # Same inputs as the real report: name search + EG country filter + geocoded Siwa Oasis coords.
    results = mm.find_candidates("Siwa Shali Resort", index, country_code="EG", lat=29.203, lon=25.519)
    ids = [r["id"] for r in results]
    assert "TC-SIWA" in ids, "the real hotel must be found even though its own record has no EG countryCode"
    real_match = next(r for r in results if r["id"] == "TC-SIWA")
    assert real_match["country_mismatch"] is True
    assert real_match["score"] > 0.9


def test_the_real_hotel_ranks_above_the_distant_unrelated_resorts():
    index = _index_missing_country_code_on_the_real_match()
    results = mm.find_candidates("Siwa Shali Resort", index, country_code="EG", lat=29.203, lon=25.519)
    ids_in_order = [r["id"] for r in results]
    assert ids_in_order[0] == "TC-SIWA", (
        "the exact-name, zero-distance match must rank first - not a same-country-tagged resort "
        "hundreds of km away that only shares generic words like 'Resort'"
    )


def test_country_mismatch_warning_is_wired_into_both_masterdata_search_screens():
    src = _read_app_helpers()
    # _render_hotel_masterdata_step (new-hotel Step 3) and _render_hotel_automap_manual_search
    # (the standalone automap-review search) both render candidates from find_candidates - both
    # must show the human why a flagged candidate is there.
    assert src.count('cand.get("country_mismatch")') == 2
    assert "Outside the country you searched for" in src
