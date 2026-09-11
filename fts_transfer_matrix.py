"""Deterministic (non-AI) parser for FTS's "TRANSFER MATRIX" CSV format.

Real trigger (2026-09-11): the user uploaded two of these CSVs (one per vehicle class - Sedan
and Toyota Hiace) exported from FTS's Excel rate sheet ("FTS_Momira_Whole_Egypt_B2B_Catalogue.
xlsx"). Each is a 21x21 city-to-city price grid - 271 valid routes per file. Routing that
through the normal AI detection+extraction pipeline (one call per route) is what caused the
"AI's answer was too long and got cut off" failure the user hit, and would in any case be slow
and expensive at this scale, which matters because the user has 200+ Transports worth of similar
supplier sheets still to load. This module reads the grid directly with plain CSV parsing - no
AI call, no token limit, no per-route latency - and turns it into ready-to-build Transport
candidates.

SCOPE (explicitly confirmed by the product owner, 2026-09-11, via AskUserQuestion): this format
is specific to FTS. "Only this one supplier (FTS) uses it" - every other supplier's documents
still go through the normal AI-based ai_extractor.py pipeline. Do not generalize this parser to
other CSV shapes; a different supplier's matrix (different header rows, different city list
position, different price notation) needs its own parser, not a stretched version of this one.

PRICING RULE (explicitly confirmed by the product owner, 2026-09-11): "Price logic in Transport:
Sedan is base price as one modality. Hiace is second modality and is used as price supplement
with Sedan. Therefore: Sedan Base price. Hiace as second modality = Hiace price - Sedan price -->
Difference is added as price supplement." Bracket ranges (also confirmed): Sedan 1-3 pax, Hiace
1-8 pax. This is why every candidate this module builds passes force_base_occupancy=(1, 3) to
builder.build_transport_payloads - see that function's own docstring for why the default
"widest bracket wins" heuristic would otherwise wrongly pick Hiace (the wider bracket) as base
and give Sedan a negative supplement, the opposite of the confirmed rule.

GRID SHAPE (confirmed against the real uploaded files): row 0 = title ("TRANSFER MATRIX -
SEDAN"/"... HIACE"), row 1 = a note ("Select a season in B3. Prices are one-way net B2B USD per
vehicle."), row 2 = "Season" / the season name (only "Standard" has been seen so far - no other
season columns exist in the real data), row 3 = blank, row 4 = header ("From / To" then 21 city
names), rows 5-25 = one origin city per row, with the same 21 cities as columns. Each cell is
either "$<number>" (a valid price), an em dash "—" (that pair genuinely has no road transfer -
e.g. two remote destinations with nothing between them), or "Train" (that pair is only sold as a
train journey, not a road Transport - explicitly out of scope for this parser; Travel
Compositor's Transport product type is for vehicles, and a "Train" cell is not a supplier error,
just a different product type this parser correctly leaves alone). The diagonal (origin ==
destination) is populated too (e.g. Cairo->Cairo) and is kept as an ordinary route - it reads as
a genuine local/within-city transfer price in the real data, no different in shape from any
other cell.
"""
import csv
import re
from typing import Any, Dict, List, Optional, Tuple

from builder import build_transport_payloads
from transport_matcher import suggest_existing_transport_matches, remember_transport_id

_PRICE_RE = re.compile(r"^\$\s*([0-9]+(?:\.[0-9]+)?)$")

# Row/column layout, 0-indexed, confirmed against the real FTS export (see module docstring).
_HEADER_ROW = 4
_FIRST_DATA_ROW = 5


class FtsMatrixFormatError(ValueError):
    """Raised when a CSV doesn't look like an FTS transfer matrix at all (wrong file uploaded,
    or FTS changed their export layout) - deliberately NOT raised for an individual bad/missing
    cell, which is a per-route "skipped" entry instead so one bad cell can't block the other 270
    good routes."""


def _parse_cell(raw: str) -> Tuple[Optional[float], str]:
    """Returns (price_or_None, kind) where kind is one of "price", "unavailable", "train",
    "blank", or "unrecognized" (a cell that's none of the known notations - surfaced to the
    caller rather than silently treated as unavailable, since it likely means the sheet's format
    changed and a human should look before 200+ suppliers' worth of sheets get built on a wrong
    assumption)."""
    text = (raw or "").strip()
    if not text:
        return None, "blank"
    if text in ("—", "-", "–"):
        return None, "unavailable"
    if text.lower() == "train":
        return None, "train"
    m = _PRICE_RE.match(text)
    if m:
        return float(m.group(1)), "price"
    return None, "unrecognized"


def parse_fts_matrix_csv(path: str) -> Dict[str, Any]:
    """Reads one FTS transfer-matrix CSV (one vehicle class) and returns:
        {"cities": [...21 city names, in column order...],
         "season": "Standard",
         "cells": {(origin_city, dest_city): {"raw": str, "price": float|None, "kind": str}},
         "format_error": str|None}
    format_error is set (and cities/cells left empty) when the file doesn't match the expected
    layout at all - see FtsMatrixFormatError's docstring for why that's structural, not per-cell.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    if len(rows) <= _HEADER_ROW:
        return {"cities": [], "season": None, "cells": {},
                "format_error": f"File has only {len(rows)} row(s) - expected a header row at "
                                f"row {_HEADER_ROW + 1} followed by one row per origin city."}

    header = rows[_HEADER_ROW]
    cities = [c.strip() for c in header[1:] if c.strip()]
    if not cities:
        return {"cities": [], "season": None, "cells": {},
                "format_error": f"Row {_HEADER_ROW + 1} has no city names after the first "
                                f"column - expected 'From / To' followed by one column per "
                                f"destination city."}

    season = None
    if len(rows) > 2 and len(rows[2]) > 1:
        season = (rows[2][1] or "").strip() or None

    cells: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows[_FIRST_DATA_ROW:]:
        if not row or not (row[0] or "").strip():
            continue
        origin = row[0].strip()
        for col_idx, dest in enumerate(cities, start=1):
            raw = row[col_idx] if col_idx < len(row) else ""
            price, kind = _parse_cell(raw)
            cells[(origin, dest)] = {"raw": (raw or "").strip(), "price": price, "kind": kind}

    return {"cities": cities, "season": season, "cells": cells, "format_error": None}


def combine_fts_transfer_matrix(sedan_csv_path: str, hiace_csv_path: str) -> Dict[str, Any]:
    """Reads both matrices and pairs every (origin, destination) cell across them. Returns:
        {"routes": [...one dict per city pair with a usable price in BOTH files...],
         "skipped": [...one dict per city pair that couldn't be used, with why...],
         "format_error": str|None}  (set if either file's layout didn't parse at all)

    Each "routes" entry: {"departure_name", "arrival_name", "sedan_price", "hiace_price"}.
    Each "skipped" entry: {"departure_name", "arrival_name", "reason", "sedan_kind", "hiace_kind"}
    - reasons are "train" (that pair is train-only, not a road Transport - not an error), "one_
    sided" (one matrix has a price and the other doesn't - a real supplier-data inconsistency
    worth a human's eyes, most likely to happen if the two files were exported at different
    times), and "unavailable" (both matrices agree there's no transfer for that pair).
    Nothing is silently dropped - every one of the 21*21 cells in the Sedan file ends up in
    exactly one of "routes" or "skipped".
    """
    sedan = parse_fts_matrix_csv(sedan_csv_path)
    hiace = parse_fts_matrix_csv(hiace_csv_path)
    if sedan["format_error"] or hiace["format_error"]:
        return {"routes": [], "skipped": [],
                "format_error": sedan["format_error"] or hiace["format_error"]}

    if sedan["cities"] != hiace["cities"]:
        return {"routes": [], "skipped": [],
                "format_error": "The Sedan and Hiace files list different cities (or a "
                                "different order) - they must be exported from the same "
                                "workbook/season so every cell lines up 1:1. Sedan: "
                                f"{sedan['cities']}. Hiace: {hiace['cities']}."}

    routes = []
    skipped = []
    for origin in sedan["cities"]:
        for dest in sedan["cities"]:
            s = sedan["cells"].get((origin, dest), {"price": None, "kind": "blank"})
            h = hiace["cells"].get((origin, dest), {"price": None, "kind": "blank"})
            if s["kind"] == "price" and h["kind"] == "price":
                routes.append({
                    "departure_name": origin, "arrival_name": dest,
                    "sedan_price": s["price"], "hiace_price": h["price"],
                })
            elif s["kind"] == "train" or h["kind"] == "train":
                skipped.append({"departure_name": origin, "arrival_name": dest, "reason": "train",
                                "sedan_kind": s["kind"], "hiace_kind": h["kind"]})
            elif s["kind"] == "price" or h["kind"] == "price":
                skipped.append({"departure_name": origin, "arrival_name": dest,
                                "reason": "one_sided", "sedan_kind": s["kind"],
                                "hiace_kind": h["kind"]})
            else:
                skipped.append({"departure_name": origin, "arrival_name": dest,
                                "reason": "unavailable", "sedan_kind": s["kind"],
                                "hiace_kind": h["kind"]})

    return {"routes": routes, "skipped": skipped, "format_error": None}


# Confirmed bracket ranges (product owner, 2026-09-11) - see module docstring.
FTS_SEDAN_BRACKET = (1, 3)
FTS_HIACE_BRACKET = (1, 8)


def fts_route_to_extracted_transport_data(route: Dict[str, Any], currency: str = "USD",
                                           service_name: str = "Private Transfer"
                                           ) -> Dict[str, Any]:
    """Turns one combine_fts_transfer_matrix() route dict into the extracted_transport_data
    shape builder.build_transport_payloads() expects, ready to pass straight through (along with
    force_base_occupancy=FTS_SEDAN_BRACKET - see that function's docstring for why the override
    is required here). charge_unit="per_service" is what makes build_transport_payloads treat
    this as a per-vehicle Transport (pricePerPax=False, using vehiclePrice) rather than a
    per-passenger one - matches the matrix's own "Prices are one-way net B2B USD per vehicle"
    note.
    """
    return {
        "departure_name": route["departure_name"],
        "arrival_name": route["arrival_name"],
        "service_name": service_name,
        "charge_unit": "per_service",
        "currency": currency,
        "occupancy_brackets": [
            {"min_occupancy": FTS_SEDAN_BRACKET[0], "max_occupancy": FTS_SEDAN_BRACKET[1],
             "price": route["sedan_price"]},
            {"min_occupancy": FTS_HIACE_BRACKET[0], "max_occupancy": FTS_HIACE_BRACKET[1],
             "price": route["hiace_price"]},
        ],
    }


def build_fts_matrix_candidates(sedan_csv_path: str, hiace_csv_path: str, currency: str = "USD",
                                 service_name: str = "Private Transfer") -> Dict[str, Any]:
    """Top-level entry point: parses both CSVs and returns ready-to-review Transport candidates,
    each carrying the extracted_transport_data dict a caller passes straight into
    builder.build_transport_payloads(..., force_base_occupancy=FTS_SEDAN_BRACKET).

    Returns {"candidates": [{"departure_name", "arrival_name", "sedan_price", "hiace_price",
    "extracted_transport_data"}], "skipped": [...], "format_error": str|None} - same "skipped"/
    "format_error" contract as combine_fts_transfer_matrix.
    """
    combined = combine_fts_transfer_matrix(sedan_csv_path, hiace_csv_path)
    if combined["format_error"]:
        return {"candidates": [], "skipped": [], "format_error": combined["format_error"]}

    candidates = []
    for route in combined["routes"]:
        candidates.append({
            **route,
            "extracted_transport_data": fts_route_to_extracted_transport_data(
                route, currency=currency, service_name=service_name),
        })
    return {"candidates": candidates, "skipped": combined["skipped"], "format_error": None}


# Auto-match confidence floor. Deliberately loose (transport_matcher's own scoring gives a
# strong 0.9 to any substring-contained place-name match) since a "create" default for a route
# that actually already exists is cheap to notice and fix later (a harmless duplicate a human
# reviews and merges), while a MISSED match that should have been flagged "update" and instead
# silently creates a duplicate live route is the worse failure mode to bias away from. Every
# "update" this produces is still just a SUGGESTION though - see match_fts_candidates_to_existing's
# docstring: nothing updates until a human ticks it verified in the review UI.
_AUTO_MATCH_MIN_SCORE = 0.5


def match_fts_candidates_to_existing(candidates: List[Dict[str, Any]],
                                      existing_transports: List[Dict[str, Any]],
                                      min_score: float = _AUTO_MATCH_MIN_SCORE
                                      ) -> List[Dict[str, Any]]:
    """Pure, deterministic (no API calls) matching step for the bulk FTS import - added
    2026-09-11 per explicit product-owner instruction: "it must auto match, but the human must
    verify it before update". Requiring a human to manually pick a match for each of 271 routes
    one at a time (the single-route flow's existing bar) doesn't scale, but skipping human
    confirmation entirely on an UPDATE (which overwrites a live record's price) is exactly the
    kind of silent bulk mistake this project has already been burned by once (168 corrupted live
    Transports from an earlier guessed-wrong assumption). This resolves that by auto-computing
    the best-scoring existing-transport match per route (via transport_matcher.
    suggest_existing_transport_matches, the SAME scoring the single-route flow's manual picker
    already shows a human as its top-ranked candidate) and returning it as a SUGGESTED action -
    the caller (app.py's review UI) still requires an explicit per-row human tick before any
    "update" row is actually published; "create" rows need no such gate since they cannot
    overwrite anything.

    Takes existing_transports as an already-fetched list (one client.get_transports(supplier_id)
    call for the WHOLE supplier, reused across all candidates) rather than fetching per-route -
    271 individual GETs for the same data would be needlessly slow.

    Returns a NEW list (inputs unmodified), each candidate augmented with:
      "action": "create" | "update"
      "matched_transport_id": str | None
      "matched_transport_name": str | None
      "match_score": float | None
    """
    out = []
    for c in candidates:
        matches = suggest_existing_transport_matches(
            c["departure_name"], c["arrival_name"], existing_transports, top_n=1)
        best = matches[0] if matches else None
        if best and best.get("transport_id") and best.get("score", 0) >= min_score:
            out.append({
                **c, "action": "update",
                "matched_transport_id": best["transport_id"],
                "matched_transport_name": best.get("name"),
                "match_score": best.get("score"),
            })
        else:
            out.append({
                **c, "action": "create",
                "matched_transport_id": None, "matched_transport_name": None, "match_score": None,
            })
    return out


def publish_fts_candidate(client, supplier_id: str, pre_config, candidate: Dict[str, Any]
                           ) -> Dict[str, Any]:
    """Builds and publishes ONE candidate (as returned by match_fts_candidates_to_existing, with
    a human-confirmed action - see that function's docstring) end to end, mirroring the exact
    two-stage sequence (parent transport, then one Option per occupancy bracket, then deactivate
    any stale option) app.py's single-route Transport publish button already uses - pulled out
    here so the bulk review screen doesn't have to duplicate that sequence 271 times inline.

    candidate["action"] must already reflect the human's decision for this row by the time this
    is called (a "create" candidate is always published as a fresh transport; an "update"
    candidate is published against candidate["matched_transport_id"] - the caller is responsible
    for not calling this on an "update" row the human hasn't verified).

    Never raises - every TravelCompositorAPI call is caught and folded into the returned
    "errors" list so one bad route in a 271-route batch can't take down the whole run.

    Returns {"ok": bool, "transport_id": str|None, "errors": [str, ...]}.
    """
    existing_id = candidate.get("matched_transport_id") if candidate.get("action") == "update" else None
    existing_transport_snapshot = None
    existing_options_snapshot = None
    if existing_id:
        try:
            snapshot_result = client.get_transport(supplier_id, existing_id)
        except Exception as e:
            snapshot_result = {"error": str(e)}
        if isinstance(snapshot_result, dict) and "error" not in snapshot_result:
            existing_transport_snapshot = snapshot_result
            opts = []
            for opt_code in (snapshot_result.get("optionCodes") or []):
                try:
                    opt = client.get_transport_option(supplier_id, existing_id, opt_code)
                except Exception:
                    continue
                if isinstance(opt, dict) and "error" not in opt:
                    opts.append(opt)
            existing_options_snapshot = opts
        # else: tolerate a failed fetch and still attempt the update, same as the single-route
        # flow's own "couldn't fetch - will use the document's own dates/images instead" warning
        # path - not fatal, just loses the merge-preserved fields for this one route.

    built = build_transport_payloads(
        pre_config, candidate["extracted_transport_data"], client,
        existing_transport_id=existing_id,
        existing_transport_snapshot=existing_transport_snapshot,
        existing_options_snapshot=existing_options_snapshot,
        force_base_occupancy=FTS_SEDAN_BRACKET,
    )
    if built.get("transport_error"):
        return {"ok": False, "transport_id": None,
                "errors": [f"Transport payload error: {built['transport_error']}"]}
    option_actions = built.get("option_actions") or []
    option_errors = [a for a in option_actions if a.get("option_error")]
    if option_errors:
        return {"ok": False, "transport_id": None,
                "errors": [f"Bracket {a['min_occupancy']}-{a['max_occupancy']}: {a['option_error']}"
                          for a in option_errors]}
    if not built.get("departure_base_resolved") or not built.get("arrival_base_resolved"):
        return {"ok": False, "transport_id": None,
                "errors": [f"Location not resolved (departure: "
                          f"{built.get('departure_base_match_type')}, arrival: "
                          f"{built.get('arrival_base_match_type')})"]}

    try:
        if existing_id:
            result = client.update_transport(supplier_id, built["transport_payload"])
        else:
            result = client.create_transport(supplier_id, built["transport_payload"])
    except Exception as e:
        return {"ok": False, "transport_id": None, "errors": [f"Publish failed: {e}"]}
    if isinstance(result, dict) and "error" in result:
        return {"ok": False, "transport_id": None, "errors": [str(result)]}

    new_id = result.get("id") if isinstance(result, dict) else None
    final_id = existing_id or new_id
    if not final_id:
        return {"ok": False, "transport_id": None,
                "errors": ["No transport id returned - options not attached."]}

    errors = []
    for a in option_actions:
        if not a.get("option_payload"):
            continue
        try:
            if a["action"] == "update":
                opt_result = client.update_transport_option(supplier_id, final_id, a["option_payload"])
            else:
                opt_result = client.create_transport_option(supplier_id, final_id, a["option_payload"])
        except Exception as e:
            errors.append(f"Option {a['code']}: {e}")
            continue
        if isinstance(opt_result, dict) and "error" in opt_result:
            errors.append(f"Option {a['code']}: {opt_result}")

    for stale in (built.get("options_to_deactivate") or []):
        try:
            stale_result = client.update_transport_option(supplier_id, final_id, stale)
        except Exception as e:
            errors.append(f"Deactivate {stale.get('code')}: {e}")
            continue
        if isinstance(stale_result, dict) and "error" in stale_result:
            errors.append(f"Deactivate {stale.get('code')}: {stale_result}")

    remember_transport_id(supplier_id, candidate["departure_name"], candidate["arrival_name"], final_id)

    return {"ok": not errors, "transport_id": final_id, "errors": errors}
