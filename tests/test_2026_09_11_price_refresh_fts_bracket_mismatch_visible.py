"""Regression tests for the 2026-09-11 fix to price_refresh.lookup_prices_from_fts_matrix's
single-vehicle-round path (see test_2026_09_11_price_refresh_fts_matrix_bypass.py for the
happy-path coverage of that same function).

REAL REPORT (product owner, 2026-09-11, uploading only the Sedan CSV, verbatim): "the bulk update
for transport is absolutely not working. I checked now multiple times, and the App gives me
Transports, which are not on the list and it misses out on the price errors. Example Transport
from Marsa Matruh to Siwa and the price was not detected by the App. I rebooted the APP but no
mistake found, which is a real issue here" - with a screenshot confirming the Sedan matrix's own
Marsa Matruh -> Siwa cell is priced at $95.

ROOT CAUSE traced in lookup_prices_from_fts_matrix's single-file branch: when only one vehicle's
CSV is given, a route is only ever priced if an existing LIVE option's (min_pax, max_pax) is
EXACTLY equal to FTS's own 1-3 (Sedan) / 1-8 (Hiace) convention (see that branch's own long
comment for why exact match, not overlap, is required - Sedan and Hiace both start at 1 pax, so
overlap-matching a lone bracket risks silently picking the WRONG live option). This is the
correct SAFETY behavior, but when it fires, the route used to just `continue` straight past -
landing in build_proposals' generic "not found in document" bucket with `matched_row=""` and no
trace that the document actually had a price for it. That bucket is indistinguishable from a
route the sheet genuinely never mentions, which is exactly the "misses out on the price errors"
symptom - a real price discrepancy sitting in the document, silently absorbed with no signal.

FIX: this now-diagnosed case (matched to a matrix row + a price is present, but the live
brackets don't exactly line up) sets a `found: False` finding that still carries `matched_row`
and an explanatory `note` naming the live brackets that didn't match - app.py's "not found"
section shows a warning for any such entry (identified by a non-empty matched_row, which a truly
undocumented route's default finding never has), so the operator sees WHY a route with a real
price sitting in the document didn't get repriced, rather than it vanishing silently. This does
NOT change the underlying safety rule (still refuses to guess which live option a lone bracket
belongs to) - it only makes an already-correct refusal visible.
"""
import csv

import price_refresh
import fts_transfer_matrix

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


def _sedan_csv(tmp_path):
    # Cairo -> Luxor priced at $100 - the route every test below targets.
    grid = [
        ["$30", "$100", "$215"],
        ["$100", "$25", "—"],
        ["Train", "—", "$20"],
    ]
    path = str(tmp_path / "FTS_Momira_Sedan.csv")
    _write_matrix_csv(path, "TRANSFER MATRIX — SEDAN", grid)
    return path


def _live_transport_route(min_max=((1, 3), (1, 8))):
    """Mirrors test_2026_09_11_price_refresh_fts_matrix_bypass.py's own helper."""
    return {
        "id": "TRANSPORT-1", "name": "Cairo - Luxor", "departure_code": None, "arrival_code": None,
        "currency": "USD", "price_per_pax": False,
        "base_adult": 30.0, "base_child": 0.0, "base_infant": 0.0,
        "options": [
            {"code": "Sedan", "min_pax": min_max[0][0], "max_pax": min_max[0][1], "unit_price": 30.0,
             "name": "Sedan", "raw": {}},
            {"code": "Hiace", "min_pax": min_max[1][0], "max_pax": min_max[1][1], "unit_price": 45.0,
             "name": "Hiace", "raw": {}},
        ],
        "raw": {"id": "TRANSPORT-1"},
    }


def test_a_matched_route_whose_live_brackets_dont_exactly_line_up_is_no_longer_silent(tmp_path):
    sedan_path = _sedan_csv(tmp_path)
    # Sedan option is live as 1-4 pax, not FTS's own 1-3 - matched by name, priced by the
    # document, but the exact-bracket safety rule (correctly) refuses to guess which live
    # option a lone Sedan bracket belongs to.
    route = _live_transport_route(min_max=((1, 4), (1, 8)))
    findings, err = price_refresh.lookup_prices_from_fts_matrix([route], sedan_csv_path=sedan_path)
    assert err is None
    assert 0 in findings  # no longer silently absent from the findings dict entirely
    finding = findings[0]
    assert finding["found"] is False
    assert finding["brackets"] == []
    # This is the signal app.py's "not found" section keys off to show a warning instead of a
    # bare "not found" row - a genuinely-undocumented route's default finding has matched_row="".
    assert finding["matched_row"] != ""
    assert "Cairo" in finding["matched_row"] and "Luxor" in finding["matched_row"]
    assert "100" in finding["note"] or "100.0" in finding["note"]
    assert "1-4" in finding["note"]  # names the live bracket that didn't line up
    assert "1-3" in finding["note"]  # names what FTS's own convention expects


def test_build_proposals_still_reports_it_as_not_in_document_not_a_crash(tmp_path):
    # The diagnostic finding must still flow cleanly through build_proposals - it's a status
    # change (visible vs silent), not a new code path that could itself error out.
    sedan_path = _sedan_csv(tmp_path)
    route = _live_transport_route(min_max=((1, 4), (1, 8)))
    findings, err = price_refresh.lookup_prices_from_fts_matrix([route], sedan_csv_path=sedan_path)
    assert err is None
    proposals = price_refresh.build_proposals([route], findings)
    assert proposals[0]["status"] == "not_in_document"
    assert proposals[0]["changes"] == []
    assert proposals[0]["accepted"] is False  # never auto-applied off a diagnostic non-finding


def test_an_exact_bracket_match_still_prices_normally_unaffected_by_this_fix(tmp_path):
    # The common, correct case (already covered in test_2026_09_11_price_refresh_fts_matrix_
    # bypass.py) must be completely unaffected - this fix only touches the previously-silent
    # mismatch branch.
    sedan_path = _sedan_csv(tmp_path)
    route = _live_transport_route()  # exact FTS brackets, 1-3 / 1-8
    findings, err = price_refresh.lookup_prices_from_fts_matrix([route], sedan_csv_path=sedan_path)
    assert err is None
    assert findings[0]["found"] is True
    brackets = {(b["min_pax"], b["max_pax"]) for b in findings[0]["brackets"]}
    assert brackets == {fts_transfer_matrix.FTS_SEDAN_BRACKET}


def test_a_route_the_document_genuinely_never_prices_is_still_a_plain_not_found(tmp_path):
    # Must not regress into flagging EVERY not-found route as a "bracket mismatch" - only ones
    # that actually matched a matrix row get the diagnostic. An unrelated route (no name overlap
    # with any of the 3 matrix cities) never even reaches the bracket check.
    sedan_path = _sedan_csv(tmp_path)
    route = _live_transport_route()
    route["name"] = "Alexandria - Port Said"
    findings, err = price_refresh.lookup_prices_from_fts_matrix([route], sedan_csv_path=sedan_path)
    assert err is None
    assert 0 not in findings  # unmatched entirely, not even a diagnostic entry


def test_hiace_only_round_names_the_hiace_convention_in_its_diagnostic(tmp_path):
    # Same mismatch, but for a Hiace-only round - confirms the diagnostic text names the
    # RIGHT vehicle/bracket (1-8, not 1-3) rather than being hardcoded to the Sedan case.
    grid = [
        ["$45", "$140", "—"],
        ["$140", "$35", "—"],
        ["Train", "—", "$28"],
    ]
    hiace_path = str(tmp_path / "FTS_Momira_Hiace.csv")
    _write_matrix_csv(hiace_path, "TRANSFER MATRIX — TOYOTA HIACE", grid)
    route = _live_transport_route(min_max=((1, 3), (1, 9)))  # Hiace live as 1-9, not FTS's 1-8
    findings, err = price_refresh.lookup_prices_from_fts_matrix([route], hiace_csv_path=hiace_path)
    assert err is None
    assert findings[0]["found"] is False
    assert "1-8" in findings[0]["note"]
    assert "1-9" in findings[0]["note"]
