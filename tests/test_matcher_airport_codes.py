"""Tests for the airport-code-to-city normalization used by the fallback fuzzy matchers.

CONFIRMED REAL RULE (product owner): "when Transfer or Transport says a three letter code
from Airport, this must be seen as a City name too" - e.g. a live route recorded as "HRG to
El Gouna" should still be recognized as the same route as a document/hint that says "Hurghada
to El Gouna". The AI-facing prompts already knew this; these deterministic difflib-based
matchers did not until this rule was added.
"""
from transfer_matcher import _name_similarity as transfer_name_similarity
from transfer_matcher import suggest_existing_transfer_matches
from transport_matcher import _name_similarity as transport_name_similarity
from transport_matcher import suggest_existing_transport_matches


def test_transfer_airport_code_scores_as_high_as_full_city_name():
    plain = transfer_name_similarity("Hurghada", "Hurghada")
    with_code = transfer_name_similarity("HRG", "Hurghada")
    assert with_code == plain == 1.0


def test_transfer_suggest_matches_finds_live_route_recorded_under_full_city_name():
    existing = [{"id": "TRANSFER-1", "name": "x",
                "departure": {"name": "Hurghada"}, "arrival": {"name": "El Gouna"}}]
    candidates = suggest_existing_transfer_matches("HRG", "El Gouna", existing)
    assert candidates[0]["transfer_id"] == "TRANSFER-1"
    assert candidates[0]["score"] == 1.0


def test_transport_airport_code_scores_as_high_as_full_city_name():
    plain = transport_name_similarity("Hurghada - El Gouna", "Hurghada - El Gouna")
    with_code = transport_name_similarity("HRG - El Gouna", "Hurghada - El Gouna")
    assert with_code == plain == 1.0


def test_transport_suggest_matches_finds_live_route_recorded_under_full_city_name():
    existing = [{"id": "TRANSPORT-1", "name": "Hurghada - El Gouna"}]
    candidates = suggest_existing_transport_matches("HRG", "El Gouna", existing)
    assert candidates[0]["transport_id"] == "TRANSPORT-1"
    # Substring-containment shortcut in _half_score credits an exact (post-expansion) match.
    assert candidates[0]["score"] >= 0.9


def test_unrelated_codes_are_left_alone():
    # A code not in the confirmed table must not be mangled - only the confirmed 4 are touched.
    assert transfer_name_similarity("XYZ Airport", "XYZ Airport") == 1.0
