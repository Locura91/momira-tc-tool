"""Regression tests for the 2026-09-11 fix to price_refresh.py's "Refresh prices from a rate
sheet" flow overflowing on FTS's own two-file Sedan/Hiace rate-matrix CSV export.

Real trigger (product owner, same day, verbatim): "again error for bulk price update: ...
Upload the rate sheet / FTS_Mom... Hiace.csv 2.5KB / FTS_Mom... Sedan.csv 2.5KB ... Couldn't
read the document: The AI's answer was too long and got cut off before it finished ... --> This
is crucial and we must make it possible, that the document is fully read."

This is the SAME document shape (271 routes, 21x21 city grid per vehicle) that had already
overflowed the separate bulk-IMPORT flow and was fixed there by reading the grid directly
instead of through the AI (fts_transfer_matrix.py). lookup_prices() in price_refresh.py had the
identical failure mode (one AI call, max_tokens=8192, describing every route) in this separate
refresh-existing-prices flow. The fix mirrors the import-side one: detect the FTS matrix CSV
pair by its title row (fts_transfer_matrix.classify_fts_matrix_file) and, when both a "sedan"
and a "hiace" file are present, read prices directly with
price_refresh.lookup_prices_from_fts_matrix - no AI call, so no token limit, so it cannot
overflow no matter how many routes the sheet prices.

Uses small synthetic CSVs shaped exactly like the real export (same helper pattern as
tests/test_2026_09_11_fts_transfer_matrix_parser.py) rather than the user's actual files, which
won't exist in a future test run.
"""
import csv

import pytest

import fts_transfer_matrix
import price_refresh

CITIES = ["Cairo", "Luxor", "Aswan"]


def _write_matrix_csv(path, title, grid):
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
    # Cairo -> Luxor priced in both files (the route the tests care about). Everything else is
    # filler so combine_fts_transfer_matrix has a full, valid grid to parse.
    sedan_grid = [
        ["$30", "$100", "$215"],
        ["$100", "$25", "—"],
        ["Train", "—", "$20"],
    ]
    hiace_grid = [
        ["$45", "$140", "—"],
        ["$140", "$35", "—"],
        ["Train", "—", "$28"],
    ]
    sedan_path = str(tmp_path / "FTS_Momira_Sedan.csv")
    hiace_path = str(tmp_path / "FTS_Momira_Hiace.csv")
    _write_matrix_csv(sedan_path, "TRANSFER MATRIX — SEDAN", sedan_grid)
    _write_matrix_csv(hiace_path, "TRANSFER MATRIX — TOYOTA HIACE", hiace_grid)
    return sedan_path, hiace_path


# ----------------------------------------------------------------------
# fts_transfer_matrix.classify_fts_matrix_file
# ----------------------------------------------------------------------

def test_classifies_sedan_and_hiace_files_by_their_title_row(matrices):
    sedan_path, hiace_path = matrices
    assert fts_transfer_matrix.classify_fts_matrix_file(sedan_path) == "sedan"
    assert fts_transfer_matrix.classify_fts_matrix_file(hiace_path) == "hiace"


def test_classify_returns_none_for_an_unrelated_csv(tmp_path):
    path = str(tmp_path / "some_other_rate_sheet.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Route", "Price"])
        w.writerow(["Cairo - Luxor", "100"])
    assert fts_transfer_matrix.classify_fts_matrix_file(path) is None


def test_classify_returns_none_for_a_missing_or_unreadable_file():
    assert fts_transfer_matrix.classify_fts_matrix_file("/no/such/file.csv") is None


# ----------------------------------------------------------------------
# price_refresh.lookup_prices_from_fts_matrix
# ----------------------------------------------------------------------

def _live_transport_route(name="Cairo - Luxor", min_max=((1, 3), (1, 8))):
    # CONFIRMED FINAL TRANSPORT PRICE-STRUCTURE MODEL (product owner, 2026-09-11): a two-modality
    # route now requires an explicit base_bracket_override - Sedan (the narrower bracket) is the
    # designated base in every fixture here, matching FTS's own real structure (see
    # fts_transfer_matrix.py's own module docstring: "Sedan is base price").
    return {
        "id": "TRANSPORT-1", "name": name, "departure_code": None, "arrival_code": None,
        "currency": "USD", "price_per_pax": False, "base_bracket_override": min_max[0],
        "base_adult": 30.0, "base_child": 0.0, "base_infant": 0.0,
        "options": [
            {"code": "Sedan", "min_pax": min_max[0][0], "max_pax": min_max[0][1], "unit_price": 30.0,
             "name": "Sedan", "raw": {}},
            {"code": "Hiace", "min_pax": min_max[1][0], "max_pax": min_max[1][1], "unit_price": 45.0,
             "name": "Hiace", "raw": {}},
        ],
        "raw": {"id": "TRANSPORT-1"},
    }


def test_finds_prices_for_a_live_route_with_no_ai_call(matrices, monkeypatch):
    sedan_path, hiace_path = matrices

    def _boom(*a, **kw):
        raise AssertionError("the AI must never be called on this path")
    monkeypatch.setattr(price_refresh.ai_extractor, "_call_claude", _boom)

    routes = [_live_transport_route()]
    findings, err = price_refresh.lookup_prices_from_fts_matrix(routes, sedan_path, hiace_path)
    assert err is None
    assert findings[0]["found"] is True
    prices = {(b["min_pax"], b["max_pax"]): b["price"] for b in findings[0]["brackets"]}
    assert prices[fts_transfer_matrix.FTS_SEDAN_BRACKET] == 100.0   # Cairo -> Luxor, sedan
    assert prices[fts_transfer_matrix.FTS_HIACE_BRACKET] == 140.0   # Cairo -> Luxor, hiace


def test_a_live_route_not_in_the_matrix_is_reported_not_found(matrices):
    sedan_path, hiace_path = matrices
    # Neither place name shares anything with any of the matrix's 3 cities (Cairo/Luxor/Aswan) -
    # unlike e.g. "Cairo - Marsa Alam", which would score high on the "Cairo" half alone.
    routes = [_live_transport_route(name="Alexandria - Port Said")]
    findings, err = price_refresh.lookup_prices_from_fts_matrix(routes, sedan_path, hiace_path)
    assert err is None
    assert 0 not in findings  # no match cleared the score floor - left unmatched, not guessed at


def test_end_to_end_builds_a_changed_proposal(matrices):
    sedan_path, hiace_path = matrices
    routes = [_live_transport_route()]
    findings, err = price_refresh.lookup_prices_from_fts_matrix(routes, sedan_path, hiace_path)
    assert err is None
    proposals = price_refresh.build_proposals(routes, findings)
    assert proposals[0]["status"] == "changed"
    changes = {c["code"]: c["new"] for c in proposals[0]["changes"]}
    assert changes["Sedan"] == 100.0
    assert changes["Hiace"] == 140.0


def test_bracket_boundaries_dont_have_to_match_exactly_thanks_to_overlap_matching(matrices):
    # Live Hiace bracket is 4-8, not FTS's own 1-8 - bracket_price_for's existing overlap-
    # matching (used for every other supplier's document already) still finds it, since 4-8
    # overlaps 1-8 but not the Sedan bracket 1-3 at all (no min_pax=1 ambiguity between the two
    # live brackets here).
    sedan_path, hiace_path = matrices
    routes = [_live_transport_route(min_max=((1, 3), (4, 8)))]
    findings, err = price_refresh.lookup_prices_from_fts_matrix(routes, sedan_path, hiace_path)
    assert err is None
    proposals = price_refresh.build_proposals(routes, findings)
    changes = {c["code"]: c["new"] for c in proposals[0]["changes"]}
    assert changes["Sedan"] == 100.0
    assert changes["Hiace"] == 140.0


def test_sedan_only_round_prices_only_the_sedan_bracket(matrices):
    # Product owner, 2026-09-11: "could The app understand that Sedan is for base modality and
    # Hiace is for Price supplement calculated? So I would price update in two parts: One round
    # for Sedan and one round for Hiace - would that work?" - a Sedan-only file must report only
    # the Sedan bracket, leaving Hiace alone (missing, not found:false) for this round.
    sedan_path, hiace_path = matrices
    routes = [_live_transport_route()]
    findings, err = price_refresh.lookup_prices_from_fts_matrix(routes, sedan_csv_path=sedan_path)
    assert err is None
    assert findings[0]["found"] is True
    brackets = {(b["min_pax"], b["max_pax"]) for b in findings[0]["brackets"]}
    assert brackets == {fts_transfer_matrix.FTS_SEDAN_BRACKET}
    proposals = price_refresh.build_proposals(routes, findings)
    changes = {c["code"]: c["new"] for c in proposals[0]["changes"]}
    assert changes == {"Sedan": 100.0}  # Hiace untouched this round
    # Hiace isn't counted as "missing" either - only_option_code excludes it from this round's
    # evaluation entirely, rather than reporting it as a bracket the document failed to price.
    assert proposals[0]["missing"] == 0


def test_hiace_only_round_never_reprices_the_untouched_sedan_option(matrices):
    # CONFIRMED HAZARD this test locks down: a live Hiace bracket (1-8) and a live Sedan
    # bracket (1-3) both start at 1 pax, so they always OVERLAP each other in pax-range terms -
    # without only_option_code, bracket_price_for's overlap fallback would apply the lone Hiace
    # price to the Sedan option too, silently moving a price this round said nothing about. Give
    # Sedan a starting price that visibly differs from what Hiace's price would produce, so a
    # regression here would show up as a spurious Sedan change.
    sedan_path, hiace_path = matrices
    route = _live_transport_route()
    route["options"][0]["unit_price"] = 999.0  # Sedan's current price, deliberately untouched
    findings, err = price_refresh.lookup_prices_from_fts_matrix([route], hiace_csv_path=hiace_path)
    assert err is None
    proposals = price_refresh.build_proposals([route], findings)
    changed_codes = {c["code"] for c in proposals[0]["changes"]}
    assert changed_codes == {"Hiace"}
    assert "Sedan" not in changed_codes


def test_hiace_only_round_prices_only_the_hiace_bracket(matrices):
    sedan_path, hiace_path = matrices
    routes = [_live_transport_route()]
    findings, err = price_refresh.lookup_prices_from_fts_matrix(routes, hiace_csv_path=hiace_path)
    assert err is None
    brackets = {(b["min_pax"], b["max_pax"]) for b in findings[0]["brackets"]}
    assert brackets == {fts_transfer_matrix.FTS_HIACE_BRACKET}
    proposals = price_refresh.build_proposals(routes, findings)
    changes = {c["code"]: c["new"] for c in proposals[0]["changes"]}
    assert changes == {"Hiace": 140.0}


def test_no_file_given_is_a_format_error_not_a_crash():
    findings, err = price_refresh.lookup_prices_from_fts_matrix([_live_transport_route()])
    assert findings == {}
    assert err is not None


def test_format_error_is_reported_when_the_two_files_dont_pair_up(tmp_path):
    sedan_path = str(tmp_path / "sedan.csv")
    hiace_path = str(tmp_path / "hiace.csv")
    _write_matrix_csv(sedan_path, "TRANSFER MATRIX — SEDAN",
                      [["$30", "$100", "$215"], ["$100", "$25", "—"], ["Train", "—", "$20"]])
    with open(hiace_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["TRANSFER MATRIX — TOYOTA HIACE"])
        w.writerow(["Select a season in B3."])
        w.writerow(["Season", "Standard"])
        w.writerow([""])
        w.writerow(["From / To", "Cairo", "Luxor"])  # different city list than the sedan file
        w.writerow(["Cairo", "$45", "$140"])
        w.writerow(["Luxor", "$140", "$35"])

    findings, err = price_refresh.lookup_prices_from_fts_matrix(
        [_live_transport_route()], sedan_path, hiace_path)
    assert findings == {}
    assert err is not None
