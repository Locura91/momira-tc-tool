"""Regression tests for a real publish failure (product owner, 2026-09-16, Four Seasons Resort
Seychelles at Desroches Island):

    * Additional Person Supplement / Child Supplement: "has no charging basis... the app will
      not guess it" (the existing, already-editable safety message - not a bug on its own).
    * Four Seasons Resort Seychelles at Desroches Island - Standard Rates:
      {"error":["java.lang.NullPointerException: Cannot invoke
      \"java.lang.Integer.intValue()\" because the return value of
      \"com.tr2.binding.api.hotel.ContractHotelConfigurationVO.getReleaseDays()\" is
      null"],"status":"BAD_REQUEST"}

Follow-up ask: "we need to avoid that this errors not happen again in the future... a better
workaround or callback solution with simple human interaction must solve that issue."

Two separate fixes:

1. releaseDays NPE: Travel Compositor auto-unboxes releaseDays as a primitive int server-side,
   so ContractHotelRateVO/ContractHotelSeasonVO/ContractHotelOffersVO/ContractHotelSupplementVO
   all used to send it as None whenever the document never states one (the overwhelmingly common
   case) - crashing the WHOLE rate publish with a raw NPE. builder.py now defaults releaseDays to
   0 ("no release delay") everywhere it's built from a document, never sending it unset.

2. Missing apply/charging basis: this used to only warn on the Supplements table (still does),
   but Publish itself wasn't blocked - the human found out only after a live API rejection,
   requiring a fix-and-re-run cycle. Same "block before the API call, not after" gate already
   used for rooms/images/geolocation is now extended to a missing apply basis too.
"""
import os

from builder import build_hotel_rate_payloads, _build_offer_or_supplement_common_kwargs

_HERE = os.path.dirname(os.path.abspath(__file__))
_HOTEL_FLOW_PY = os.path.join(os.path.dirname(_HERE), "flows", "hotel.py")


def _read_hotel_flow():
    with open(_HOTEL_FLOW_PY, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------------------------
# 1. releaseDays never sent as None
# ---------------------------------------------------------------------------------------------

_ROOM_MAP = {"Deluxe Room": "AUTO123"}
_DISTRIBUTIONS = {"Deluxe Room": [{"adults": 2, "children": 0}]}


def _rate_data_no_release_days():
    return [{
        "name": "Standard Rates",
        "seasons": [{
            "name": "Season 1",
            "date_ranges": [{"start": "2027-01-01", "end": "2027-12-31"}],
            "room_prices": [{
                "room_name": "Deluxe Room",
                "distribution_prices": [{"amount": 200.0, "adults": 2, "children": 0}],
                "base_price": 200.0, "adult_prices": [], "child_prices": [],
            }],
            "meal_plans": [],
        }],
        "offer_names": [], "supplement_names": [], "stop_sales": [],
    }]


def test_rate_release_days_defaults_to_zero_not_none():
    results = build_hotel_rate_payloads(
        _rate_data_no_release_days(), _ROOM_MAP, {}, {},
        room_name_to_distributions=_DISTRIBUTIONS)
    assert results[0]["rate_payload"]["releaseDays"] == 0
    assert results[0]["rate_payload"]["releaseDays"] is not None


def test_season_release_days_defaults_to_zero_not_none():
    results = build_hotel_rate_payloads(
        _rate_data_no_release_days(), _ROOM_MAP, {}, {},
        room_name_to_distributions=_DISTRIBUTIONS)
    assert results[0]["rate_payload"]["seasons"][0]["releaseDays"] == 0


def test_rate_release_days_still_honors_a_document_stated_value():
    rate_data = _rate_data_no_release_days()
    rate_data[0]["release_days"] = 14
    results = build_hotel_rate_payloads(
        rate_data, _ROOM_MAP, {}, {}, room_name_to_distributions=_DISTRIBUTIONS)
    assert results[0]["rate_payload"]["releaseDays"] == 14


def test_offer_supplement_common_kwargs_release_days_defaults_to_zero():
    kwargs = _build_offer_or_supplement_common_kwargs(
        {"name": "Resort Fee"}, ["ROOM1"], ["ROOM_ONLY"], apply_default="LODGING")
    assert kwargs["releaseDays"] == 0
    assert kwargs["releaseDays"] is not None


def test_offer_supplement_common_kwargs_release_days_honors_a_stated_value():
    kwargs = _build_offer_or_supplement_common_kwargs(
        {"name": "Resort Fee", "release_days": 5}, ["ROOM1"], ["ROOM_ONLY"], apply_default="LODGING")
    assert kwargs["releaseDays"] == 5


# ---------------------------------------------------------------------------------------------
# 2. Publish is blocked (not just warned) while a supplement has no apply basis
# ---------------------------------------------------------------------------------------------

def test_publish_button_is_disabled_when_supplements_are_missing_apply_basis():
    src = _read_hotel_flow()
    supplements_ok_idx = src.index("supplements_ok = not _missing_apply")
    button_idx = src.index('st.button(f"🚀 Publish', supplements_ok_idx)
    window = src[supplements_ok_idx:button_idx + 400]
    assert "not supplements_ok" in window


def test_publish_gate_shows_an_actionable_error_before_the_button():
    src = _read_hotel_flow()
    idx = src.index("supplements_ok = not _missing_apply")
    window = src[idx:idx + 700]
    assert "no apply/charging basis yet" in window
    assert "before publishing" in window
