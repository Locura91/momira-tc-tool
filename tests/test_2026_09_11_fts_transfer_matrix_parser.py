"""Tests for fts_transfer_matrix.py - the deterministic (non-AI) CSV parser for FTS's
"TRANSFER MATRIX" format (added 2026-09-11).

Real trigger: uploading FTS's real Sedan + Hiace matrix CSVs (271 valid routes each, 21x21 city
grid) through the normal AI extraction pipeline overflowed ("AI's answer was too long and got
cut off"). The user explicitly flagged this as a blocker given ~200 more supplier sheets still
to load ("thats a big problem for Bulk update! ... Is there something we can fix"). These tests
use small synthetic CSVs shaped exactly like the real export (same row/column layout, same "$",
"—", "Train" cell notations) rather than depending on the user's actual uploaded files, which
won't exist in a future test run.
"""
import csv
import os

import pytest

from fts_transfer_matrix import (
    parse_fts_matrix_csv, combine_fts_transfer_matrix, fts_route_to_extracted_transport_data,
    build_fts_matrix_candidates, FTS_SEDAN_BRACKET, FTS_HIACE_BRACKET,
)

CITIES = ["Cairo", "Luxor", "Aswan"]


def _write_matrix_csv(path, title, grid):
    """grid: list of rows (one per origin city, same order as CITIES), each a list of 3 cell
    strings (one per destination, same order as CITIES) - mirrors the real file's layout: title
    row, note row, season row, blank row, header row, then one row per origin city."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([title] + [""] * len(CITIES))
        w.writerow(["Select a season in B3. Prices are one-way net B2B USD per vehicle."])
        w.writerow(["Season", "Standard"])
        w.writerow([""])
        w.writerow(["From / To"] + CITIES)
        for origin, row in zip(CITIES, grid):
            w.writerow([origin] + row)


@pytest.fixture
def matrices(tmp_path):
    # Cairo-Luxor and Luxor-Cairo priced both ways, in both files.
    # Cairo-Aswan: Sedan priced but Hiace missing (one_sided mismatch, on purpose).
    # Aswan-Cairo: "Train" in both (train, skipped).
    # Luxor-Aswan / Aswan-Luxor: "—" in both (unavailable, skipped).
    # Diagonal (same-city): priced in both, kept as an ordinary route.
    sedan_grid = [
        ["$30", "$100", "$215"],   # Cairo -> Cairo, Luxor, Aswan
        ["$100", "$25", "—"],      # Luxor -> Cairo, Luxor, Aswan
        ["Train", "—", "$20"],     # Aswan -> Cairo, Luxor, Aswan
    ]
    hiace_grid = [
        ["$45", "$140", "—"],      # Cairo -> Aswan missing here (one_sided)
        ["$140", "$35", "—"],
        ["Train", "—", "$28"],
    ]
    sedan_path = str(tmp_path / "sedan.csv")
    hiace_path = str(tmp_path / "hiace.csv")
    _write_matrix_csv(sedan_path, "TRANSFER MATRIX — SEDAN", sedan_grid)
    _write_matrix_csv(hiace_path, "TRANSFER MATRIX — TOYOTA HIACE", hiace_grid)
    return sedan_path, hiace_path


def test_parse_single_matrix_reads_cities_and_cells(matrices):
    sedan_path, _ = matrices
    parsed = parse_fts_matrix_csv(sedan_path)
    assert parsed["format_error"] is None
    assert parsed["cities"] == CITIES
    assert parsed["season"] == "Standard"
    assert parsed["cells"][("Cairo", "Cairo")] == {"raw": "$30", "price": 30.0, "kind": "price"}
    assert parsed["cells"][("Luxor", "Aswan")]["kind"] == "unavailable"
    assert parsed["cells"][("Aswan", "Cairo")]["kind"] == "train"


def test_combine_pairs_up_sedan_and_hiace_correctly(matrices):
    sedan_path, hiace_path = matrices
    combined = combine_fts_transfer_matrix(sedan_path, hiace_path)
    assert combined["format_error"] is None
    routes_by_pair = {(r["departure_name"], r["arrival_name"]): r for r in combined["routes"]}
    # Cairo -> Luxor: both priced -> a real route.
    assert routes_by_pair[("Cairo", "Luxor")] == {
        "departure_name": "Cairo", "arrival_name": "Luxor",
        "sedan_price": 100.0, "hiace_price": 140.0,
    }
    # Diagonal (Cairo -> Cairo) kept as an ordinary route, not special-cased away.
    assert ("Cairo", "Cairo") in routes_by_pair
    assert routes_by_pair[("Cairo", "Cairo")]["sedan_price"] == 30.0

    skipped_by_pair = {(s["departure_name"], s["arrival_name"]): s for s in combined["skipped"]}
    # Cairo -> Aswan: Sedan priced, Hiace missing -> one_sided, NOT silently dropped as unavailable.
    assert skipped_by_pair[("Cairo", "Aswan")]["reason"] == "one_sided"
    # Aswan -> Cairo: "Train" in both -> train, not treated as an error.
    assert skipped_by_pair[("Aswan", "Cairo")]["reason"] == "train"
    # Luxor -> Aswan: "—" in both -> genuinely unavailable.
    assert skipped_by_pair[("Luxor", "Aswan")]["reason"] == "unavailable"

    # Every one of the 3x3=9 cells ends up in exactly one of routes/skipped - nothing vanishes.
    assert len(combined["routes"]) + len(combined["skipped"]) == len(CITIES) * len(CITIES)


def test_mismatched_city_lists_between_files_is_a_format_error(tmp_path):
    sedan_path = str(tmp_path / "sedan.csv")
    hiace_path = str(tmp_path / "hiace.csv")
    _write_matrix_csv(sedan_path, "SEDAN", [["$1", "$2", "$3"]] * 3)
    with open(hiace_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["TITLE"])
        w.writerow(["note"])
        w.writerow(["Season", "Standard"])
        w.writerow([""])
        w.writerow(["From / To", "Cairo", "Luxor"])  # only 2 cities, not 3
        w.writerow(["Cairo", "$1", "$2"])
        w.writerow(["Luxor", "$1", "$2"])
    combined = combine_fts_transfer_matrix(sedan_path, hiace_path)
    assert combined["format_error"] is not None
    assert combined["routes"] == []
    assert combined["skipped"] == []


def test_file_with_no_header_row_is_a_format_error(tmp_path):
    bad_path = str(tmp_path / "bad.csv")
    with open(bad_path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["not", "a", "matrix"])
    parsed = parse_fts_matrix_csv(bad_path)
    assert parsed["format_error"] is not None
    assert parsed["cities"] == []


def test_fts_route_to_extracted_transport_data_shape():
    route = {"departure_name": "Cairo", "arrival_name": "Marsa Alam",
             "sedan_price": 215.0, "hiace_price": 270.0}
    extracted = fts_route_to_extracted_transport_data(route)
    assert extracted["departure_name"] == "Cairo"
    assert extracted["arrival_name"] == "Marsa Alam"
    assert extracted["charge_unit"] == "per_service"  # per-vehicle, not per-pax
    assert extracted["currency"] == "USD"
    brackets = {(b["min_occupancy"], b["max_occupancy"]): b["price"]
                for b in extracted["occupancy_brackets"]}
    assert brackets[FTS_SEDAN_BRACKET] == 215.0
    assert brackets[FTS_HIACE_BRACKET] == 270.0


def test_build_fts_matrix_candidates_end_to_end(matrices):
    sedan_path, hiace_path = matrices
    result = build_fts_matrix_candidates(sedan_path, hiace_path)
    assert result["format_error"] is None
    # Both-priced pairs: Cairo-Cairo, Cairo-Luxor, Luxor-Cairo, Luxor-Luxor, Aswan-Aswan.
    assert len(result["candidates"]) == 5
    pairs = {(c["departure_name"], c["arrival_name"]) for c in result["candidates"]}
    assert ("Cairo", "Luxor") in pairs
    assert ("Cairo", "Aswan") not in pairs  # one_sided, excluded from candidates
    assert ("Aswan", "Cairo") not in pairs  # train, excluded
    assert ("Luxor", "Aswan") not in pairs  # unavailable, excluded
    for c in result["candidates"]:
        assert "extracted_transport_data" in c
        assert c["extracted_transport_data"]["departure_name"] == c["departure_name"]


def test_candidates_feed_directly_into_build_transport_payloads_with_correct_base(matrices, fake_api_client):
    from schemas import TransportHumanPreConfig
    from builder import build_transport_payloads

    sedan_path, hiace_path = matrices
    result = build_fts_matrix_candidates(sedan_path, hiace_path)
    candidate = next(c for c in result["candidates"]
                     if c["departure_name"] == "Cairo" and c["arrival_name"] == "Luxor")
    pre_config = TransportHumanPreConfig(supplier_id="12345", currency="USD")
    built = build_transport_payloads(
        pre_config, candidate["extracted_transport_data"], fake_api_client,
        force_base_occupancy=FTS_SEDAN_BRACKET)
    assert built["transport_error"] is None
    assert built["force_base_occupancy_matched"] is True
    # Sedan (100) is base -> vehiclePrice=100, Hiace (140) carries a +40 supplement.
    assert built["transport_payload"]["vehiclePrice"] == 100.0
    by_range = {(a["min_occupancy"], a["max_occupancy"]): a for a in built["option_actions"]}
    assert by_range[FTS_SEDAN_BRACKET]["option_payload"]["prices"] == []
    hiace_prices = by_range[FTS_HIACE_BRACKET]["option_payload"]["prices"]
    assert len(hiace_prices) == 1
    assert hiace_prices[0]["adultPriceSupplement"] == 40.0
