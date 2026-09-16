"""Source-text wiring tests: flows/hotel.py's publish loop must treat a builder.py
action=="skipped_past" result (see test_2026_09_16_hotel_ignore_past_prices_offers_supplements.py
for the builder-level logic) as a silent skip - never a failure - for offers, supplements and
seasons, and surface a summary caption on success (product owner, 2026-09-16: "please ignore
prices, offers and supplements, which are already in the past. We only need to sell in the
future")."""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_HOTEL_FLOW_PY = os.path.join(os.path.dirname(_HERE), "flows", "hotel.py")


def _read_hotel_flow():
    with open(_HOTEL_FLOW_PY, "r", encoding="utf-8") as f:
        return f.read()


def test_offer_skipped_past_is_tracked_and_not_a_failure():
    src = _read_hotel_flow()
    idx = src.index("offers_skipped_past = []")
    window = src[idx:idx + 900]
    assert 'if res["action"] == "skipped_past":' in window
    assert "offers_skipped_past.append(name)" in window
    assert "continue" in window


def test_supplement_skipped_past_is_tracked_and_not_a_failure():
    src = _read_hotel_flow()
    idx = src.index("supplements_skipped_past = []")
    window = src[idx:idx + 600]
    assert 'if res["action"] == "skipped_past":' in window
    assert "supplements_skipped_past.append(name)" in window
    assert "continue" in window


def test_season_skipped_past_is_collected_from_season_actions():
    src = _read_hotel_flow()
    idx = src.index("seasons_skipped_past = []")
    window = src[idx:idx + 1200]
    assert 'sa.get("action") == "skipped_past"' in window


def test_skipped_past_items_are_reported_in_a_success_caption():
    src = _read_hotel_flow()
    idx = src.index("_hp_past_skipped = offers_skipped_past + supplements_skipped_past + seasons_skipped_past")
    window = src[idx:idx + 700]
    assert "already entirely in the past" in window
