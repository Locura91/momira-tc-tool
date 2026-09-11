"""
price_refresh.py — update the prices of transports that already exist.

WHY THIS EXISTS, AND WHY IT IS THE EASIER SHAPE: the upload flow reads a document and
constructs a product from it. That means the AI has to decide what products the document
describes, resolve each end of the route to a Travel Compositor location, name it, describe
it, estimate a duration, and build the modalities - and every one of those is a judgement
that can come back wrong or empty. Most of a rate sheet's life is not that. It is the same
routes as last year with new numbers.

So this flow inverts the source of truth. The list of products comes from Travel Compositor,
which is a FACT, not from the AI reading a document, which is a judgement. Everything except
the numbers is already correct in the live record and is never touched: the route, the
locations, the times, the names, the modality structure. The AI's whole job shrinks to "what
does this document say the price is for THIS route I am telling you about" - a lookup with a
known answer shape rather than an open reading.

WHAT THAT BUYS: no detection step, so no empty result. No location resolution, no duration
estimate, no house-style naming, no create-versus-update decision, and no way to produce a
duplicate. The worst case is a wrong number on an existing product, which is exactly what the
human's yes/no is there to catch.

WHAT IT CANNOT DO, stated plainly: it cannot create a transport that does not exist yet -
that stays with the upload flow. A route in the document matching nothing live is REPORTED,
never silently dropped, and a human can point it at the right transport by hand.

WHERE A TRANSPORT'S PRICE ACTUALLY LIVES (two places, and they must stay consistent):
  * the parent record's baseAdultPrice / baseChildrenPrice / baseInfantPrice - OR, for a
    per-vehicle transport (pricePerPax=False, confirmed real example: every FTS-created
    Transport), vehiclePrice instead, with baseAdultPrice/baseChildrenPrice/baseInfantPrice
    genuinely 0 (see load_supplier_transports and rebuild_prices for the confirmed 2026-09-11
    fix - this module used to read/write baseAdultPrice unconditionally, the same bug already
    found once in bulk_notes.py's dated-supplement flow, see
    claude/transport-supplement-per-vehicle-price-bug-2026-09-10.md);
  * each option's prices[].adultPriceSupplement, which is added to the base.
So a modality's real price is base + its own supplement. Changing prices means recomputing
both together, which is why this module owns that arithmetic rather than leaving it to a
caller - see rebuild_prices().
"""

# Stamped on every delivery. app.py compares this against its own build string and says
# so on screen when they differ - a partial push (one file committed, another not) used to
# surface only as a traceback whose line numbers pointed at unrelated code.
MODULE_BUILD = "2026-09-11-transport-price-consistency-report"

import json
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

import ai_extractor
import fts_transfer_matrix
import transfer_matcher
import transport_matcher
from bulk_notes import normalize_for_put

# The product types this flow can refresh. Transport/Transfer are priced per occupancy and both
# arrive on the same kind of rate sheet, but they store the numbers very differently - see
# load_supplier_products() and rebuild_prices(). Ticket (added 2026-08-25, Phase 1 of the
# product-owner's request: "the next developement must be done, when we are talking about
# updating Tickets or ClosedTours for the new Seasons with new prices... Could we plan this the
# same for Tickets and closedtours") is deliberately its OWN pipeline (load_supplier_tickets /
# lookup_ticket_prices / build_ticket_proposals / rebuild_ticket_prices / apply_ticket_proposals)
# rather than another branch bolted onto the Transport/Transfer functions above - see the
# Ticket section further down for why: matching is by exact CODE instead of fuzzy place-name
# matching, a "route" is a (ticket, modality) pair rather than a single record, and the PUT
# shape (one Modality, whole) is different again from either Transport or Transfer. It still
# reuses bracket_price_for() and the same finding/proposal SHAPE as the functions above, so a
# human reviewing either screen sees the same "old -> new, accept or reject" pattern.
KIND_TRANSPORT = "Transport"
KIND_TRANSFER = "Transfer"
KIND_TICKET = "Ticket"

PRICE_LOOKUP_SYSTEM_PROMPT = """You are reading a supplier's rate sheet to find the NEW PRICE for routes that
already exist in a booking system. You are NOT deciding which products exist - that list is given to you and
it is correct. Your only job is to find each one's price in the document.

You will be given a numbered list of ROUTES, each with the passenger brackets it is sold in, and then the
document. For each route, report the price the document states for each bracket.

You may also be given an OPERATOR INSTRUCTION - a human typed this in their own words to point you at part
of the document (e.g. "focus only on Sharm El Sheikh"). Match it BY MEANING, never as exact text: ignore
capitalization and wording differences entirely - "sharm el sheikh", "Sharm El Sheikh" and "SHARM EL SHEIKH"
all mean the identical place and must be treated identically. The instruction only narrows which routes you
report as found; it is never a reason to report a route "found": false that the document genuinely prices.

MATCHING A ROUTE TO A ROW - this is the part that goes wrong:
- The route names PLACES; the document often names AIRPORTS. "RMF Airport" is Marsa Alam, "HRG Airport" is
  Hurghada, "SSH Airport" is Sharm El Sheikh, "CAI Airport" is Cairo, and in general "<city> Airport" is
  that city. So the route "Marsa Alam to Hurghada" matches the row "RMF Airport | Hurghada".
- A section heading tells you where a block of rows departs from: rows under "Transfer Fees Marsa Allam"
  are Marsa Alam departures even where the cell shows only an airport code.
- A route may be the REVERSE of the row. Rate sheets are usually priced "per way" and list one direction
  only; the return leg has the same price unless the document says otherwise. Use the same row.
- Match the SERVICE CLASS too. A document with a "Shuttle" column and a "Private" column prices two
  different products for the same row - take the column that matches the route's own class.
- BUNDLED ROUTES: a route name joining several places with "/" or "or" (e.g. "Hurghada / El Gouna or Soma
  Bay") is ONE product combining what the document prices as separate rows. Find each place's row and use
  the HIGHEST of their prices - report "confidence": "high" for this, and briefly say in "note" which
  places you combined.

PRICES:
- Report the number exactly as the document states it. Never convert a currency, never apply a discount,
  never interpolate a bracket the document does not price.
- "per person, minimum 2 pax" means the 2+ bracket takes the stated number. Report that bracket and set
  "minimum_pax" to the stated minimum - don't also add a separate min_pax=1 entry, the application
  computes the 1-pax price itself from minimum_pax.
- SEASONS: a document may price the SAME bracket differently for different date ranges (e.g. "standard
  season", "high season", "peak season", each with its own dates). When it does, put "price" as the
  currently-applicable rate (so old behaviour is unaffected), AND additionally list EVERY stated season in
  "seasons", each with its own "start_date"/"end_date" (ISO YYYY-MM-DD if the document gives real dates,
  otherwise your best reading of what it says, e.g. "2026-10-01") and "price". Report every season the
  document states for that bracket, not only the one active today - a human who only updates one modality
  at a time still needs every future period filled in from a single document read. Leave "seasons" empty
  when the bracket has only one flat price.
- If the document does not price a route at all, say so with "found": false. That is a useful, correct
  answer - a route the supplier dropped this season should not be guessed at.
- If you are unsure which row a route matches, set "confidence": "low" and say why in "note". A human
  confirms every price before anything is written, so an honest doubt is far more useful than a guess
  presented as fact.
- Before you answer, check yourself: if the document plainly prices a section (e.g. its own heading names
  the place) but you are about to report every route under it as not found, re-read that section - you are
  very likely missing the row, not looking at a document that truly has no prices for it.

Output ONLY valid JSON, no markdown fences:
{
  "routes": [
    {"index": 0,
     "found": true,
     "matched_row": "the document's own wording for the row you used, quoted",
     "currency": "the currency code if the document states one, else empty",
     "minimum_pax": 2,
     "brackets": [{"min_pax": 2, "max_pax": 9, "price": 42.0, "child_price": null, "infant_price": null}],
     "confidence": "high",
     "note": ""}
  ]
}
Report every route you were given, in the same order, including the ones you did not find."""

# CONFIRMED REAL BUG (product owner, 2026-08-14): "before it was working fine... now the AI did
# not detect any transfer" - EVERY route came back "not found", not just the hard ones. Root
# cause was almost certainly the fully permissive tool schema (see ai_extractor._call_claude's
# default): the same pattern was already confirmed once before, on apply_clarification, where a
# permissive schema let Claude drop a required field on every single call once nothing but prose
# was enforcing the shape. Here the at-risk fields are "found" and "brackets" - lookup_prices
# treats a missing/empty "brackets" as not-found (see the `bool(brackets)` check below), so if
# Claude drops that key for one route it silently drops it for all of them, and a mid-round
# prompt-wording change is exactly the kind of thing that can trigger it. A real schema that
# REQUIRES "found" and "brackets" on every route item closes this off structurally, the same way
# CLARIFY_TOOL_SCHEMA closes it off for apply_clarification - rather than depending on prose to
# keep the model reporting every field, every time.
PRICE_LOOKUP_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "routes": {
            "type": "array",
            "description": "One entry per route you were given, in the same order, including the "
                            "ones you did not find.",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "found": {"type": "boolean"},
                    "matched_row": {"type": "string"},
                    "currency": {"type": "string"},
                    "minimum_pax": {"type": "integer"},
                    "brackets": {
                        "type": "array",
                        "description": "Empty array if found is false or the document doesn't "
                                        "price any bracket for this route.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "min_pax": {"type": "integer"},
                                "max_pax": {"type": "integer"},
                                "price": {"type": "number"},
                                "child_price": {"type": ["number", "null"]},
                                "infant_price": {"type": ["number", "null"]},
                                "seasons": {
                                    "type": "array",
                                    "description": "Only when the document states MORE THAN ONE price "
                                                    "for this bracket, each for a different date range "
                                                    "(standard/high/peak season). Empty when the bracket "
                                                    "has one flat price.",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "start_date": {"type": "string"},
                                            "end_date": {"type": "string"},
                                            "price": {"type": "number"},
                                            "child_price": {"type": ["number", "null"]},
                                            "infant_price": {"type": ["number", "null"]},
                                        },
                                        "required": ["price"],
                                    },
                                },
                            },
                            "required": ["min_pax", "max_pax", "price"],
                        },
                    },
                    "confidence": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["index", "found", "brackets"],
            },
        },
    },
    "required": ["routes"],
}


# ----------------------------------------------------------------------
# Reading what is already live
# ----------------------------------------------------------------------
def _parse_date_safe(value: Any) -> Optional[date]:
    """An ISO (or DD/MM/YYYY-ish) date, or None - never raises. Reuses date_format's own
    tolerant parsing so a human-typed date and a wire ISO date both work the same way here."""
    if not value:
        return None
    try:
        import date_format
        iso = date_format.to_iso_date(value)
        return date.fromisoformat(iso[:10]) if iso else None
    except (ValueError, TypeError, ImportError):
        return None


def _entry_covers(entry: Dict[str, Any], on_date: date) -> bool:
    """Whether this ONE price entry's own [startDate, endDate] window covers on_date. A missing
    startDate reads as "always started" and a missing endDate as "never ends" - permissive on
    purpose, since a real single-period entry very often carries no dates at all, and that must
    keep reading as "currently active" exactly as it always has."""
    start = _parse_date_safe(entry.get("startDate"))
    end = _parse_date_safe(entry.get("endDate"))
    if start and on_date < start:
        return False
    if end and on_date > end:
        return False
    return True


def _select_price_entry(prices: List[Dict[str, Any]], on_date: Optional[date] = None
                        ) -> Optional[Dict[str, Any]]:
    """Which of a modality's price entries is ACTIVE right now (or as of on_date).

    CONFIRMED REAL GAP (product owner, 2026-09-11): "it can have a price supplement for
    different modalities and it can have different period of times but the time can not
    overlap within the same modality" - a modality's `prices` list can genuinely hold more than
    one entry, each its own non-overlapping date range (a standard rate now and an already-
    scheduled future/peak-season rate, confirmed as already live on real transports). This used
    to be read with NO date awareness at all - just whichever entry happened to be LAST in the
    list. Now prefers whichever entry's own window covers on_date (today by default). Falls
    back to the LAST entry when none does, or when no entry carries dates at all (the common
    single-period case) - identical to the old behavior, so a single-period transport is
    completely unaffected by this change."""
    dated = [p for p in (prices or []) if isinstance(p, dict)]
    if not dated:
        return None
    on_date = on_date or date.today()
    covering = [p for p in dated if _entry_covers(p, on_date)]
    if len(covering) == 1:
        return covering[0]
    if len(covering) > 1:
        # Two entries covering the SAME date is the "must not overlap" rule already being
        # violated in the live data - a display/reference choice only (never a write): the most
        # recently scheduled one (latest startDate) is the more likely intended "current" rate.
        return max(covering, key=lambda p: _parse_date_safe(p.get("startDate")) or date.min)
    return dated[-1]


def _entry_matches_period(entry: Dict[str, Any], start_date: Optional[str],
                          end_date: Optional[str]) -> bool:
    """Whether an existing raw price entry IS the entry for a document-stated season period
    (both compared as parsed dates, so "2026-10-01" and "01/10/2026" match identically)."""
    return (_parse_date_safe(entry.get("startDate")) == _parse_date_safe(start_date) and
            _parse_date_safe(entry.get("endDate")) == _parse_date_safe(end_date))


def _find_entry_for_period(option: Dict[str, Any], start_date: Optional[str],
                           end_date: Optional[str]) -> Optional[Dict[str, Any]]:
    """The existing raw price entry (from option['raw']['prices']) that a document-stated season
    period corresponds to, for seasonal multi-period writes (see rebuild_prices and
    bracket_periods_for). A flat, non-seasonal period (start_date and end_date both None) uses
    the same "whichever entry is active today" choice _select_price_entry already makes, so a
    non-seasonal document still lands on the one entry a pre-seasonal refresh always touched.
    A seasonal period first looks for an entry whose OWN dates match exactly; failing that, an
    entry that covers the period's start date - a document rewording an existing season's exact
    boundary dates should still update that same season's entry rather than create a duplicate.
    None when nothing on the live option corresponds to this period at all - the period is new."""
    entries = [p for p in ((option.get("raw") or {}).get("prices") or []) if isinstance(p, dict)]
    if not entries:
        return None
    if start_date is None and end_date is None:
        return _select_price_entry(entries)
    for entry in entries:
        if _entry_matches_period(entry, start_date, end_date):
            return entry
    p_start = _parse_date_safe(start_date)
    if p_start:
        for entry in entries:
            if _entry_covers(entry, p_start):
                return entry
    return None


def option_unit_price(option: Dict[str, Any], base_adult: float) -> float:
    """What one passenger in this bracket actually costs RIGHT NOW: base plus whichever of this
    option's price entries is active today (see _select_price_entry)."""
    current = _select_price_entry(option.get("prices") or [])
    supplement = _num((current or {}).get("adultPriceSupplement"))
    return round(base_adult + supplement, 2)


def _num(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def load_supplier_transports(client, supplier_id: str,
                             progress: Optional[Callable[[int, int, str], None]] = None
                             ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Every transport this supplier has, with its modalities and their current prices.

    The options are fetched per transport because the list endpoint returns only their codes.
    That is one request per transport, so this is the slow part of the flow and the only one -
    everything after it is local."""
    try:
        data = client.get_transports(supplier_id)
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    if isinstance(data, dict) and "error" in data:
        return [], str(data.get("message") or data.get("error"))
    records = data.get("transport", []) if isinstance(data, dict) else (data or [])
    records = [r for r in records if isinstance(r, dict)]

    out = []
    for i, summary in enumerate(records):
        t_id = summary.get("id")
        name = summary.get("name") or ""
        if progress:
            progress(i + 1, len(records), name)
        # CONFIRMED REAL PRODUCTION BUG (product owner, 2026-09-11): a real bulk price-refresh
        # run against 13 selected FTS-matched transports failed ALL 13 with "updateTransport.
        # transport.airlineCode: must not be null" - the identical failure, and identical root
        # cause, already diagnosed and fixed once this same day in
        # cancellation_bulk_transport.load_supplier_transports_for_cancellation (see
        # claude/incident-2026-09-11-bulk-cancellation-transport-airlinecode.md): this function
        # used to build `raw` (what rebuild_prices/apply_proposals later PUT back whole)
        # directly from the LIST endpoint's own entry, which can genuinely lack a field (or
        # send it null) that the transport's own individual GET record actually has populated.
        # Fixed the same way: re-fetch each transport's full individual record before reading
        # anything off it. A row whose individual re-fetch fails falls back to the list entry
        # (so one bad row can't block the rest) but is flagged `full_fetch_failed` so the
        # review screen can warn a human before it gets PUT back on a possibly-incomplete
        # record - apply_proposals also re-checks with bulk_notes.normalize_for_put as a
        # last-resort belt-and-suspenders default, never as the primary fix (defaulting a
        # missing field to "" would silently erase a real value the individual record has -
        # the exact mistake the product owner caught and corrected in the sibling incident).
        record = summary
        full_fetch_failed = False
        if t_id:
            try:
                full = client.get_transport(supplier_id, t_id)
            except Exception:
                full = None
            if isinstance(full, dict) and "error" not in full:
                record = full
            else:
                full_fetch_failed = True
        else:
            full_fetch_failed = True
        # CONFIRMED REAL BUG (found 2026-09-11, tracing the FTS matrix per-vehicle transports
        # this flow's new lookup_prices_from_fts_matrix path is meant to refresh): a per-vehicle
        # transport (pricePerPax=False - every FTS-created Transport is one, see
        # fts_transfer_matrix.fts_route_to_extracted_transport_data's charge_unit="per_service")
        # has baseAdultPrice/baseChildrenPrice/baseInfantPrice all genuinely 0 - the real base
        # price lives in vehiclePrice instead (confirmed ContractTransportVO field; see
        # builder.build_transport_payloads: `vehiclePrice=0.0 if price_per_pax else base_price,
        # baseAdultPrice=base_price if price_per_pax else 0.0`). This module used to read
        # baseAdultPrice unconditionally, so every per-vehicle transport's "current price" here
        # came back 0 regardless of what it actually sells for - the SAME root cause already
        # found and fixed once in bulk_notes.py's dated-supplement flow
        # (claude/transport-supplement-per-vehicle-price-bug-2026-09-10.md); mirrored here
        # exactly (see also rebuild_prices' matching write-side fix below). Defaulting the
        # missing-key case to per-pax (True) matches bulk_notes.py's own confirmed default -
        # "a transport missing the pricePerPax field entirely still defaults to the old per-pax
        # behavior (the confirmed common case)" - the previous `bool(record.get("pricePerPax"))`
        # here silently defaulted a MISSING key to False (per-vehicle) instead, the opposite of
        # the confirmed rule.
        per_pax = bool(record.get("pricePerPax", True))
        base_for_options = _num(record.get("baseAdultPrice")) if per_pax else _num(record.get("vehiclePrice"))
        options = []
        for code in (record.get("optionCodes") or []):
            try:
                opt = client.get_transport_option(supplier_id, record.get("id"), code)
            except Exception as e:
                opt = {"error": type(e).__name__, "message": str(e)}
            if not isinstance(opt, dict) or "error" in opt:
                options.append({"code": code, "fetch_failed": True})
                continue
            options.append({
                "code": code,
                "min_pax": int(opt.get("minPassengers") or 1),
                "max_pax": int(opt.get("maxPassengers") or 1),
                "unit_price": option_unit_price(opt, base_for_options),
                "name": ((opt.get("translations") or {}).get("EN") or {}).get("name", ""),
                "raw": opt,
            })
        segment = (record.get("segments") or [{}])[0]
        out.append({
            "id": record.get("id"),
            "name": name,
            "departure_code": segment.get("departureLocationCode"),
            "arrival_code": segment.get("arrivalLocationCode"),
            "currency": record.get("currency"),
            "price_per_pax": per_pax,
            "base_adult": base_for_options,
            "base_child": _num(record.get("baseChildrenPrice")) if per_pax else 0.0,
            "base_infant": _num(record.get("baseInfantPrice")) if per_pax else 0.0,
            "options": sorted(options, key=lambda o: o.get("min_pax", 0)),
            "full_fetch_failed": full_fetch_failed,
            "raw": record,
        })
    return out, None


DEFAULT_BRACKET_CODE = "__default__"


def _transfer_brackets(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A transfer's prices, expressed as brackets so both product types look the same upstream.

    CONFIRMED SEMANTICS (schemas.TransferOccupancyPriceVO): basePrice is the DEFAULT rate for
    any occupancy, and pricesByOccupancy holds an entry ONLY for an occupancy whose rate
    genuinely differs - a solo surcharge, typically. So the default is modelled as one bracket
    spanning min to max occupancy, and each explicit entry as a bracket of exactly one."""
    base = _num(record.get("basePrice"))
    min_occ = int(_num(record.get("minOccupancy"), 1)) or 1
    max_occ = int(_num(record.get("maxOccupancy"), 1)) or 1
    brackets = [{"code": DEFAULT_BRACKET_CODE, "min_pax": min_occ, "max_pax": max_occ,
                 "unit_price": round(base, 2), "name": "default rate", "raw": None}]
    for entry in (record.get("pricesByOccupancy") or []):
        if not isinstance(entry, dict):
            continue
        occ = int(_num(entry.get("occupancy"), 0))
        if occ <= 0:
            continue
        amount = entry.get("basePrice")
        amount = _num(amount.get("amount")) if isinstance(amount, dict) else _num(amount)
        brackets.append({"code": f"occ{occ}", "min_pax": occ, "max_pax": occ,
                         "unit_price": round(amount, 2), "name": f"{occ} pax", "raw": dict(entry)})
    return sorted(brackets, key=lambda b: (b["min_pax"], b["max_pax"]))


def load_supplier_transfers(client, supplier_id: str,
                            progress: Optional[Callable[[int, int, str], None]] = None
                            ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Every transfer this supplier has. One request, unlike Transport - a transfer's prices
    all live on the record itself, with no option sub-resources to fetch."""
    try:
        data = client.get_transfers(supplier_id)
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    if isinstance(data, dict) and "error" in data:
        return [], str(data.get("message") or data.get("error"))
    records = data.get("transfer", []) if isinstance(data, dict) else (data or [])
    records = [r for r in records if isinstance(r, dict)]

    out = []
    for i, record in enumerate(records):
        dep = (record.get("departure") or {}).get("name", "") if isinstance(record.get("departure"), dict) else ""
        arr = (record.get("arrival") or {}).get("name", "") if isinstance(record.get("arrival"), dict) else ""
        name = record.get("name") or ((record.get("datasheets") or {}).get("EN") or {}).get("name", "") \
            or f"{dep} - {arr}".strip(" -")
        if progress:
            progress(i + 1, len(records), name)
        out.append({
            "kind": KIND_TRANSFER,
            "id": record.get("id"),
            "name": name,
            "departure_name": dep,
            "arrival_name": arr,
            "currency": record.get("currency"),
            "price_per_pax": bool(record.get("priceByPax", True)),
            "base_adult": _num(record.get("basePrice")),
            "base_child": 0.0,
            "base_infant": 0.0,
            "options": _transfer_brackets(record),
            "raw": record,
        })
    return out, None


def load_supplier_products(client, supplier_id: str, kind: str,
                           progress: Optional[Callable[[int, int, str], None]] = None
                           ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    if kind == KIND_TRANSFER:
        return load_supplier_transfers(client, supplier_id, progress=progress)
    return load_supplier_transports(client, supplier_id, progress=progress)


# ----------------------------------------------------------------------
# Ticket (Phase 1, 2026-08-25): reading what is already live
# ----------------------------------------------------------------------
# CONFIRMED SCOPE (product owner, AskUserQuestion 2026-08-25): Ticket only for now (ClosedTour
# is a separate later follow-up); a Peak Season supplement is ALWAYS ADDED and never replaces
# an existing one (Phase 2, not built here); percentage surcharges compute as 15% of the base
# adult/child price (Phase 2, not built here); existing language-choice supplement prices CAN
# also be refreshed (Phase 3, not built here). THIS pass covers base/occupancy price only:
# load an existing Ticket Modality's live occupancyPrices from Travel Compositor, match it to
# the document by its own CODE, diff, let a human accept/reject/edit, and apply.
TICKET_SUPPORTED_PRICE_TYPE = "OCCUPANCY"


def _ticket_price_type_supported(price_type: Optional[str]) -> bool:
    """OCCUPANCY is the only Modality pricing mode this phase understands - its per-headcount
    occupancyPrices table maps directly onto the rate sheet's per-group-size brackets. A
    DISTRIBUTION (flat per-adult/child regardless of group size) or SERVICE (one flat total)
    Modality is NOT guessed at - it is listed and named as unsupported, consistent with this
    codebase's established "block rather than guess" rule (see build_proposals' blocked_
    unreadable handling above)."""
    return (price_type or TICKET_SUPPORTED_PRICE_TYPE) == TICKET_SUPPORTED_PRICE_TYPE


def _ticket_occupancy_options(modality: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """A live Modality's occupancyPrices, split into adult and child option lists.

    CONFIRMED REAL WIRE SHAPE (captured GET response): occupancyPrices is a FLAT list mixing
    adult and child rows - an adult row is {"occupancy": n, "amount": <price>}, a child row at
    the SAME occupancy carries an extra "ageRange": {"min": ..., "max": ...} key, which is the
    only thing that distinguishes it. There is no separate child array.

    Each row is expressed as an EXACT-headcount bracket (min_pax == max_pax == occupancy) so
    the existing bracket_price_for() - built for Transport/Transfer's brackets - can be reused
    unchanged for Ticket prices too; an exact-headcount bracket is just a zero-width range."""
    adult, child = [], []
    for row in (modality.get("occupancyPrices") or []):
        if not isinstance(row, dict):
            continue
        occ = int(_num(row.get("occupancy"), 0))
        if occ <= 0:
            continue
        entry = {"code": f"occ{occ}", "min_pax": occ, "max_pax": occ,
                 "unit_price": round(_num(row.get("amount")), 2), "name": f"{occ} pax",
                 "raw": dict(row)}
        (child if isinstance(row.get("ageRange"), dict) else adult).append(entry)
    return (sorted(adult, key=lambda o: o["min_pax"]), sorted(child, key=lambda o: o["min_pax"]))


def load_supplier_tickets(client, supplier_id: str,
                          progress: Optional[Callable[[int, int, str], None]] = None
                          ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Every (Ticket, Modality) pair this supplier has, with its current occupancy prices.

    One "route" per live Modality - like Transport, the list endpoint gives only each Ticket's
    modalityCodes, so each Modality needs its own GET (see sync_ticket.fetch_all_tickets /
    fetch_all_tickets's own paging pattern, reused here for the same reason: a supplier can
    have more tickets than one page)."""
    try:
        data = client.get_tickets(supplier_id, first=0, limit=200)
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    if isinstance(data, dict) and "error" in data:
        return [], str(data.get("message") or data.get("error"))
    tickets = data.get("tickets", []) if isinstance(data, dict) else (data or [])
    tickets = [t for t in tickets if isinstance(t, dict)]

    pagination = data.get("pagination", {}) if isinstance(data, dict) else {}
    total_tickets = pagination.get("totalResults", len(tickets))
    first = 200
    while len(tickets) < total_tickets:
        try:
            more = client.get_tickets(supplier_id, first=first, limit=200)
        except Exception:
            break
        more_list = more.get("tickets", []) if isinstance(more, dict) else []
        if not more_list:
            break
        tickets.extend(t for t in more_list if isinstance(t, dict))
        first += 200

    total_modalities = sum(len(t.get("modalityCodes") or []) for t in tickets) or 1
    out = []
    done = 0
    for ticket in tickets:
        ticket_code = ticket.get("code")
        ticket_name = ticket.get("name") or ((ticket.get("datasheets") or {}).get("EN") or {}).get("name", "") \
            or ticket_code
        currency = ticket.get("currency") or "EUR"
        for modality_code in (ticket.get("modalityCodes") or []):
            done += 1
            if progress:
                progress(done, total_modalities, f"{ticket_name} — {modality_code}")
            try:
                opt = client.get_ticket_option(supplier_id, ticket_code, modality_code)
            except Exception as e:
                opt = {"error": type(e).__name__, "message": str(e)}
            if not isinstance(opt, dict) or "error" in opt:
                out.append({"kind": KIND_TICKET, "id": f"{ticket_code}/{modality_code}",
                           "ticket_code": ticket_code, "modality_code": modality_code,
                           "name": f"{ticket_name} — {modality_code}", "currency": currency,
                           "price_type": None, "fetch_failed": True,
                           "options": [], "child_options": [], "raw": None})
                continue
            price_type = opt.get("priceType") or TICKET_SUPPORTED_PRICE_TYPE
            adult_options, child_options = _ticket_occupancy_options(opt)
            out.append({
                "kind": KIND_TICKET,
                "id": f"{ticket_code}/{modality_code}",
                "ticket_code": ticket_code,
                "modality_code": modality_code,
                "name": f"{ticket_name} — {modality_code}",
                "currency": currency,
                "price_type": price_type,
                "fetch_failed": False,
                "options": adult_options,
                "child_options": child_options,
                "raw": opt,
            })
    return out, None


def route_places(route: Dict[str, Any]) -> Tuple[str, str]:
    """Departure and arrival as readable place names.

    A transport's name is the house "DEPARTURE - ARRIVAL" pattern, which is the only place a
    readable place name appears - the segment carries codes like "meet_LXR". Falls back to the
    codes when the name is not in that shape, so a badly-named record still matches on
    something rather than on nothing."""
    # A transfer carries real place names on the record; a transport only carries codes, so
    # its name is the only readable source.
    if route.get("departure_name") and route.get("arrival_name"):
        return str(route["departure_name"]).strip(), str(route["arrival_name"]).strip()
    name = str(route.get("name") or "")
    for separator in (" - ", " – ", " to ", " > ", "->"):
        if separator in name:
            left, _, right = name.partition(separator)
            if left.strip() and right.strip():
                return left.strip(), right.strip()
    dep = str(route.get("departure_code") or "").replace("meet_", "")
    arr = str(route.get("arrival_code") or "").replace("meet_", "")
    return dep, arr


# ----------------------------------------------------------------------
# Asking the document for each route's price
# ----------------------------------------------------------------------
def lookup_prices(routes: List[Dict[str, Any]], raw_text: str,
                  model: str = "claude-sonnet-5", human_hint: str = "") -> Dict[int, Dict[str, Any]]:
    """Find each known route's price in the document. Returns {route index: finding}."""
    if not routes or not (raw_text or "").strip():
        return {}
    described = []
    for i, route in enumerate(routes):
        dep, arr = route_places(route)
        brackets = ", ".join(f"{o.get('min_pax')}-{o.get('max_pax')} pax" for o in route["options"]) \
            or "no brackets recorded"
        described.append(
            f"{i}. {dep} to {arr} | class: {route.get('name') or '(unnamed)'} | "
            f"brackets: {brackets} | currency now: {route.get('currency') or '?'} | "
            f"{'per person' if route.get('price_per_pax') else 'per vehicle'}")
    instruction = f"OPERATOR INSTRUCTION: {human_hint.strip()}\n\n" if (human_hint or "").strip() else ""
    user_content = (f"{instruction}ROUTES TO PRICE:\n" + "\n".join(described) +
                    f"\n\n--- DOCUMENT ---\n{raw_text}")
    data = ai_extractor._call_claude(PRICE_LOOKUP_SYSTEM_PROMPT, user_content, model,
                                     max_tokens=8192, input_schema=PRICE_LOOKUP_TOOL_SCHEMA) or {}
    findings = {}
    for item in (data.get("routes") or []):
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if not (0 <= index < len(routes)):
            continue
        brackets = []
        for b in (item.get("brackets") or []):
            if not isinstance(b, dict):
                continue
            price = _num(b.get("price"), fallback=-1.0)
            if price < 0:
                continue
            seasons = []
            for s in (b.get("seasons") or []):
                if not isinstance(s, dict):
                    continue
                s_price = _num(s.get("price"), fallback=-1.0)
                if s_price < 0:
                    continue
                seasons.append({
                    "start_date": str(s.get("start_date") or "").strip() or None,
                    "end_date": str(s.get("end_date") or "").strip() or None,
                    "price": round(s_price, 2),
                    "child_price": None if s.get("child_price") is None else round(_num(s.get("child_price")), 2),
                    "infant_price": None if s.get("infant_price") is None else round(_num(s.get("infant_price")), 2),
                })
            brackets.append({
                "min_pax": int(_num(b.get("min_pax"), 1)),
                "max_pax": int(_num(b.get("max_pax"), 1)),
                "price": round(price, 2),
                "child_price": None if b.get("child_price") is None else round(_num(b.get("child_price")), 2),
                "infant_price": None if b.get("infant_price") is None else round(_num(b.get("infant_price")), 2),
                "seasons": seasons,
            })
        findings[index] = {
            "found": bool(item.get("found")) and bool(brackets),
            "matched_row": str(item.get("matched_row") or "").strip(),
            "currency": str(item.get("currency") or "").strip().upper(),
            "minimum_pax": int(_num(item.get("minimum_pax"), 1)) or 1,
            "brackets": sorted(brackets, key=lambda b: b["min_pax"]),
            "confidence": str(item.get("confidence") or "low").lower(),
            "note": str(item.get("note") or "").strip(),
        }
    return findings


# ----------------------------------------------------------------------
# FTS matrix CSV: a deterministic sibling of lookup_prices() for exactly one document shape
# ----------------------------------------------------------------------
# CONFIRMED REAL BUG (product owner, 2026-09-11): "again error for bulk price update... Couldn't
# read the document: The AI's answer was too long and got cut off before it finished... This is
# crucial and we must make it possible, that the document is fully read." The document is the
# same FTS Sedan/Hiace matrix CSV pair (271 routes) that already overflowed the bulk-IMPORT flow
# and was fixed there by reading the grid directly instead of through the AI (see
# fts_transfer_matrix.py's module docstring) - lookup_prices() above has the identical failure
# mode for the same reason (one AI call, max_tokens=8192, describing every route plus the whole
# document), just in this separate refresh-existing-prices flow. Rather than raising the token
# limit (which only moves the ceiling, and this flow has no cap on how many routes a supplier can
# have), this bypasses the AI entirely for this one confirmed document shape, the same fix already
# applied to the import side.
# SUPERSEDED 2026-09-11 (see fts_transfer_matrix.FTS_CITY_MATCH_MIN_SCORE's own comment for the
# measured failure this replaced): this flow no longer fuzzy-scores a whole live route name
# against every priced cell's concatenated "origin - arrival" name. Each endpoint is resolved to
# exactly one matrix city on its own, and the price comes from that one exact cell. Kept only
# because it is part of this module's public surface; nothing reads it any more.
FTS_MATCH_MIN_SCORE = 0.5


def lookup_prices_from_fts_matrix(routes: List[Dict[str, Any]], sedan_csv_path: Optional[str] = None,
                                  hiace_csv_path: Optional[str] = None
                                  ) -> Tuple[Dict[int, Dict[str, Any]], Optional[str]]:
    """Finds each known route's price directly in FTS's own rate-matrix CSV export - no AI call,
    so it cannot overflow no matter how many routes the sheet prices (271, in the real document
    that triggered this). See the module-level comment above for why this exists.

    Either file may be omitted (added 2026-09-11, product owner: "If I have two modalities...
    could The app understand that Sedan is for base modality and Hiace is for Price supplement
    calculated? So I would price update in two parts: One round for Sedan and one round for
    Hiace - would that work?"). Given only one file, this reports a finding with just THAT
    vehicle's bracket - build_proposals then proposes a change for only that option, leaving the
    other one's price untouched (reported as "missing" for that option, same as any document
    that doesn't price every bracket) - exactly a one-vehicle-at-a-time round. Given both, both
    brackets are reported together in one round, same as before this option existed. Raises
    nothing and never guesses which file is which - the caller (app.py) is responsible for
    passing each path under the right keyword, having already classified it with
    fts_transfer_matrix.classify_fts_matrix_file.

    MATCHING (rewritten 2026-09-11 after a confirmed real failure - see
    fts_transfer_matrix.FTS_CITY_MATCH_MIN_SCORE's own comment for the measured numbers): each
    LIVE route (from Travel Compositor, named via route_places()) has its departure and arrival
    resolved SEPARATELY to one of the matrix's own 21 city names
    (fts_transfer_matrix.match_place_to_city), and the price is then read from that one exact
    (origin, destination) cell. The matrix is a structured grid, so there is never a reason to
    guess at a nearby cell: a route whose endpoints don't both resolve is left unpriced, a route
    whose endpoints resolve to a cell the sheet doesn't price (an em dash, or a train-only pair)
    is reported as exactly that, and an endpoint that resolves ambiguously (the grid holds both
    "Marsa Alam" and "Marsa Matruh") is reported rather than picked. Nothing here ever reads a
    price from a different city pair than the one the route actually names.

    PRICES are reported as FTS's own confirmed bracket ranges - Sedan 1-3 pax, Hiace 1-8 pax
    (fts_transfer_matrix.FTS_SEDAN_BRACKET/FTS_HIACE_BRACKET) - regardless of what the LIVE
    option's own min/max_pax actually are. That is intentional, not an approximation:
    bracket_price_for() (used by build_proposals for every document, not just this one) already
    matches a document's bracket to a live option by OVERLAP, not exact equality, specifically so
    a rate sheet with different bracket boundaries than what's live still prices correctly - the
    same tolerance every other supplier's document already relies on applies here unchanged.

    Returns (findings, format_error). format_error is set (findings then {}) when neither path is
    given, or when a given file doesn't parse as an FTS matrix at all - mirrors
    fts_transfer_matrix.combine_fts_transfer_matrix's own contract, so a caller can fall back to
    the normal AI-based lookup_prices() rather than silently doing nothing."""
    if not sedan_csv_path and not hiace_csv_path:
        return {}, "No FTS rate-matrix file given."

    sedan = fts_transfer_matrix.parse_fts_matrix_csv(sedan_csv_path) if sedan_csv_path else None
    hiace = fts_transfer_matrix.parse_fts_matrix_csv(hiace_csv_path) if hiace_csv_path else None
    for parsed in (sedan, hiace):
        if parsed and parsed["format_error"]:
            return {}, parsed["format_error"]
    if sedan and hiace and sedan["cities"] != hiace["cities"]:
        return {}, ("The Sedan and Hiace files list different cities (or a different order) - "
                    "they must be exported from the same workbook/season so every cell lines up "
                    f"1:1. Sedan: {sedan['cities']}. Hiace: {hiace['cities']}.")
    grid = sedan or hiace
    cities = grid["cities"]
    if not any(c["kind"] == "price" for c in grid["cells"].values()):
        return {}, "The rate sheet parsed, but has no priced routes to read."

    def _cell_price(parsed, origin, dest):
        """The ONE exact cell for this city pair, or (None, why-not). Never a neighbouring cell,
        never the reverse direction - see fts_transfer_matrix's own FTS_CITY_MATCH_MIN_SCORE
        comment for the measured damage the old nearest-match behaviour did."""
        if parsed is None:
            return None, None
        cell = parsed["cells"].get((origin, dest))
        if cell is None:
            return None, "not in the grid"
        if cell["kind"] == "price":
            return cell["price"], None
        return None, {"train": "sold as a train journey, not a road transfer",
                      "unavailable": "marked as no transfer available (—)",
                      "blank": "left blank",
                      "unrecognized": f"an unrecognized entry ({cell['raw']!r})"}.get(
                          cell["kind"], cell["kind"])

    findings: Dict[int, Dict[str, Any]] = {}
    for i, route in enumerate(routes):
        dep, arr = route_places(route)
        dep_match = fts_transfer_matrix.match_place_to_city(dep, cities)
        arr_match = fts_transfer_matrix.match_place_to_city(arr, cities)
        if not dep_match["city"] or not arr_match["city"]:
            # An endpoint the sheet simply doesn't list is the ordinary case for a supplier's
            # non-FTS routes - it is left out silently, exactly as "the document doesn't price
            # this" already means everywhere else. An AMBIGUOUS one is different and is reported:
            # the app could see a plausible city but genuinely cannot tell which, and a human
            # needs to know that rather than have it look identical to "not covered".
            ambiguous = [(dep, dep_match), (arr, arr_match)]
            ambiguous = [(name, m) for name, m in ambiguous if m["ambiguous"]]
            if ambiguous:
                bits = "; ".join(f"“{name}” could be {' or '.join(m['ambiguous'])}"
                                 for name, m in ambiguous)
                findings[i] = {
                    "found": False, "brackets": [], "confidence": "low", "minimum_pax": 1,
                    "currency": "", "matched_row": "ambiguous place name",
                    "note": (f"Not repriced because a place name on this route is ambiguous in the "
                             f"FTS matrix: {bits}. Rename the transport (or tell me which city it "
                             f"is) rather than risk pricing the wrong one."),
                }
            continue
        origin_city, dest_city = dep_match["city"], arr_match["city"]
        sedan_price, sedan_why = _cell_price(sedan, origin_city, dest_city)
        hiace_price, hiace_why = _cell_price(hiace, origin_city, dest_city)
        if sedan_price is None and hiace_price is None:
            # Both endpoints resolved, so this route IS one the sheet covers - it just has no
            # usable price in this cell. That is worth saying out loud (train-only pairs and "—"
            # pairs are deliberate supplier decisions, not app failures), and is precisely the
            # case the old nearest-match behaviour used to paper over with another cell's price.
            why = "; ".join(f"{label}: {reason}" for label, reason in
                            (("Sedan", sedan_why), ("Hiace", hiace_why)) if reason)
            findings[i] = {
                "found": False, "brackets": [], "confidence": "low", "minimum_pax": 1,
                "currency": "", "matched_row": f"{origin_city} -> {dest_city} (FTS matrix)",
                "note": (f"Matched {origin_city} → {dest_city} in the FTS matrix, but that "
                         f"cell carries no price ({why}) - left exactly as it is."),
            }
            continue
        m = {"departure_name": origin_city, "arrival_name": dest_city,
             "sedan_price": sedan_price, "hiace_price": hiace_price}
        best = {"score": round(min(dep_match["score"], arr_match["score"]), 3)}
        brackets = []
        only_option_code = None
        priced = [(label, price, bracket) for label, price, bracket in (
            ("Sedan", sedan_price, fts_transfer_matrix.FTS_SEDAN_BRACKET),
            ("Hiace", hiace_price, fts_transfer_matrix.FTS_HIACE_BRACKET),
        ) if price is not None]
        if len(priced) > 1:
            # Both vehicles priced this round - bracket_price_for's EXACT match (checked before
            # its overlap fallback) resolves each live option to the right one of these two
            # brackets unambiguously, same as it already does for every other two-bracket
            # document. This is the shape that expresses the product owner's own confirmed rule
            # end to end: base (vehiclePrice) = Sedan, and Hiace's supplement = Hiace - Sedan,
            # both computed from the document in one round.
            for _label, price, bracket in priced:
                brackets.append({"min_pax": bracket[0], "max_pax": bracket[1],
                                 "price": round(price, 2),
                                 "child_price": None, "infant_price": None})
        else:
            # CONFIRMED HAZARD (found while building this): a SINGLE bracket cannot safely rely
            # on bracket_price_for's overlap fallback the way a two-bracket finding can - Sedan
            # (1-3) and Hiace (1-8) both start at 1 pax, so they always overlap EACH OTHER too,
            # and with only one bracket in the finding there is no second, more-specific bracket
            # to win the exact-match check first. Left as overlap, a Sedan-only round would also
            # match (and reprice) the live Hiace option, and vice versa - silently pricing a
            # vehicle this round said nothing about. So a one-vehicle round only trusts an EXACT
            # boundary match against this route's own live option (never a guess at which one is
            # "close enough") - if this route's live brackets don't exactly line up with FTS's
            # own 1-3/1-8 convention (e.g. a human edited them since), it is left out of this
            # round rather than risked.
            #
            # This also covers a pair that is priced in only ONE of two uploaded files (the
            # "one_sided" case in combine_fts_transfer_matrix's own terms) - previously such a
            # pair was dropped from a both-files round entirely; it now prices the vehicle that
            # genuinely has a price, under this same single-bracket safety rule.
            vehicle_label, price, target_bracket = priced[0]
            live_match = next((o for o in route["options"]
                               if (o.get("min_pax"), o.get("max_pax")) == target_bracket), None)
            if live_match is not None:
                brackets.append({"min_pax": live_match["min_pax"], "max_pax": live_match["max_pax"],
                                 "price": round(price, 2), "child_price": None, "infant_price": None})
                # See build_proposals' matching "only_option_code" comment - restricts this
                # finding to the one option it actually has a price for, so a lone bracket can
                # never overlap-match the OTHER live option this round says nothing about.
                only_option_code = live_match.get("code")
            else:
                # CONFIRMED REAL BUG (product owner, 2026-09-11): "the App... misses out on the
                # price errors. Example Transport from Marsa Matruh to Siwa and the price was not
                # detected by the App" - the document WAS matched to this route (best/score below)
                # and DID have a price, but this route's live option brackets don't exactly equal
                # FTS's 1-3/1-8 convention (see the comment above for why this path refuses to
                # guess via overlap), so the route used to just fall through to `continue` and
                # land in the generic "not found in the document" bucket with zero trace of why -
                # indistinguishable from a route the sheet genuinely never priced at all. Record a
                # diagnostic non-finding instead so app.py's "not found" list can show exactly what
                # happened and the live brackets that didn't line up, rather than the operator
                # having to guess whether it's a real supplier gap or an app bug.
                live_brackets = ", ".join(f"{o.get('min_pax')}-{o.get('max_pax')}"
                                          for o in route["options"] if not o.get("fetch_failed")) \
                    or "no brackets recorded"
                findings[i] = {
                    "found": False, "brackets": [], "confidence": "low", "minimum_pax": 1,
                    "currency": "",
                    "matched_row": f"{m['departure_name']} -> {m['arrival_name']} (FTS matrix, "
                                   f"match score {best.get('score')})",
                    "note": (f"Found ${round(price, 2)} for {vehicle_label} in the FTS matrix, "
                             f"but this transport's live option brackets ({live_brackets}) don't "
                             f"exactly match FTS's {target_bracket[0]}-{target_bracket[1]} pax "
                             f"convention, so it was left unpriced rather than guessed at - fix "
                             f"the option's passenger range in Travel Compositor, or upload both "
                             f"the Sedan and Hiace files together this round."),
                }
                continue
        if not brackets:
            continue
        finding = {
            "found": True,
            "matched_row": f"{m['departure_name']} -> {m['arrival_name']} (FTS matrix, "
                           f"match score {best.get('score')})",
            "currency": "USD",
            "minimum_pax": 1,
            "brackets": brackets,
            "confidence": "high",
            "note": "Matched deterministically from the FTS rate matrix (no AI) - see matched_row.",
        }
        if only_option_code is not None:
            finding["only_option_code"] = only_option_code
        findings[i] = finding
    return findings, None


# ----------------------------------------------------------------------
# Ticket (Phase 1): asking the document for each ticket's price, by CODE
# ----------------------------------------------------------------------
# Unlike Transport/Transfer, a Ticket route does not need place-name/airport-code matching at
# all - the rate sheet states the same code the live product already has (e.g. "ALX-01"), so
# matching is exact-string, not fuzzy. The AI's job shrinks further than it already was: find
# the row for a KNOWN code, report its price per bracket.
TICKET_PRICE_LOOKUP_SYSTEM_PROMPT = """You are reading a supplier's rate sheet to find the NEW PRICE for tickets/
excursions that already exist in a booking system. You are NOT deciding which tickets exist - that list is given
to you, by its own CODE, and it is correct. Your only job is to find each code's price in the document.

You will be given a numbered list of TICKETS, each with its own CODE, name, and the passenger-count brackets it
is sold in, then the document. For each ticket, find the row whose code MATCHES (the document usually states the
code directly, e.g. "ALX-01", "SHM-04" - match it exactly, ignoring case/whitespace differences only) and report
the price for each bracket.

You may also be given an OPERATOR INSTRUCTION - a human typed this in their own words to point you at part of
the document. Match it by meaning; it only narrows which tickets you report as found, never a reason to report
one "found": false that the document genuinely prices.

PRICES:
- Report the number exactly as the document states it for each pax-count column/bracket (e.g. "1 pax", "2-3
  pax", "4-6 pax"). Never convert a currency, never apply a discount, never interpolate a bracket the document
  does not price.
- "child_price": ONLY set this when the document gives a genuinely SEPARATE child/infant price for that
  bracket (a distinct column or a stated child rate). If the document prices only one figure per bracket with
  no visible child/infant split, leave "child_price" null - never guess or assume a child discount.
- If the document does not price a code at all, say so with "found": false. A ticket the supplier dropped this
  season should not be guessed at.
- If you are unsure which row a code matches, set "confidence": "low" and say why in "note".

Output ONLY valid JSON, no markdown fences:
{
  "routes": [
    {"index": 0,
     "found": true,
     "matched_row": "the document's own wording for the row you used, quoted",
     "currency": "the currency code if the document states one, else empty",
     "minimum_pax": 1,
     "brackets": [{"min_pax": 1, "max_pax": 1, "price": 45.0, "child_price": null, "infant_price": null}],
     "confidence": "high",
     "note": ""}
  ]
}
Report every ticket you were given, in the same order, including the ones you did not find."""


def lookup_ticket_prices(routes: List[Dict[str, Any]], raw_text: str,
                         model: str = "claude-sonnet-5", human_hint: str = "") -> Dict[int, Dict[str, Any]]:
    """Find each known Ticket's price-per-bracket in the document. Returns {route index: finding}.

    DEDUPES by ticket_code before calling the AI: several live Modalities can share one Ticket
    code (adult/child variants, different languages, different group-size structures), and the
    document prices that code ONCE - asking once per unique code and fanning the same finding
    back out to every route sharing it avoids N identical (and potentially disagreeing) AI
    calls for what is really one lookup."""
    if not routes or not (raw_text or "").strip():
        return {}
    code_to_indices: Dict[str, List[int]] = {}
    codes_seen: List[str] = []
    for i, route in enumerate(routes):
        code = route.get("ticket_code") or ""
        if not code:
            continue
        code_to_indices.setdefault(code, []).append(i)
        if code not in codes_seen:
            codes_seen.append(code)
    if not codes_seen:
        return {}

    described = []
    for ci, code in enumerate(codes_seen):
        first_route = routes[code_to_indices[code][0]]
        all_brackets = sorted({(o["min_pax"], o["max_pax"])
                               for idx in code_to_indices[code] for o in routes[idx]["options"]})
        brackets_desc = ", ".join(f"{mn}-{mx} pax" for mn, mx in all_brackets) or "no brackets recorded"
        described.append(
            f"{ci}. code: {code} | name: {first_route.get('name') or '(unnamed)'} | "
            f"brackets: {brackets_desc} | currency now: {first_route.get('currency') or '?'}")
    instruction = f"OPERATOR INSTRUCTION: {human_hint.strip()}\n\n" if (human_hint or "").strip() else ""
    user_content = (f"{instruction}TICKETS TO PRICE:\n" + "\n".join(described) +
                    f"\n\n--- DOCUMENT ---\n{raw_text}")
    data = ai_extractor._call_claude(TICKET_PRICE_LOOKUP_SYSTEM_PROMPT, user_content, model,
                                     max_tokens=8192, input_schema=PRICE_LOOKUP_TOOL_SCHEMA) or {}
    by_code_index = {}
    for item in (data.get("routes") or []):
        if not isinstance(item, dict):
            continue
        try:
            ci = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if not (0 <= ci < len(codes_seen)):
            continue
        brackets = []
        for b in (item.get("brackets") or []):
            if not isinstance(b, dict):
                continue
            price = _num(b.get("price"), fallback=-1.0)
            if price < 0:
                continue
            brackets.append({
                "min_pax": int(_num(b.get("min_pax"), 1)),
                "max_pax": int(_num(b.get("max_pax"), 1)),
                "price": round(price, 2),
                "child_price": None if b.get("child_price") is None else round(_num(b.get("child_price")), 2),
                "infant_price": None if b.get("infant_price") is None else round(_num(b.get("infant_price")), 2),
            })
        by_code_index[ci] = {
            "found": bool(item.get("found")) and bool(brackets),
            "matched_row": str(item.get("matched_row") or "").strip(),
            "currency": str(item.get("currency") or "").strip().upper(),
            "minimum_pax": int(_num(item.get("minimum_pax"), 1)) or 1,
            "brackets": sorted(brackets, key=lambda b: b["min_pax"]),
            "confidence": str(item.get("confidence") or "low").lower(),
            "note": str(item.get("note") or "").strip(),
        }
    findings = {}
    for ci, code in enumerate(codes_seen):
        finding = by_code_index.get(ci) or {"found": False, "brackets": [], "confidence": "low",
                                            "note": "", "matched_row": "", "minimum_pax": 1,
                                            "currency": ""}
        for idx in code_to_indices[code]:
            findings[idx] = finding
    return findings


# ----------------------------------------------------------------------
# Turning a finding into a proposal
# ----------------------------------------------------------------------
def options_are_alternatives(options: List[Dict[str, Any]]) -> bool:
    """True when this route's live modalities OVERLAP each other in passenger range.

    CONFIRMED REAL BUG (product owner, 2026-09-11, reviewing bulk Transport update): live Sedan
    (1-3 pax) and Hiace (1-8 pax) both start at 1 pax, so they overlap. That overlap means they
    are ALTERNATIVE products - two vehicle classes a customer chooses between - not sequential
    party-size tiers of one product. bracket_price_for's overlap fallback ("a document that
    prices 2-9 still tells you what a live 2-6 bracket costs") is right for tiers and badly wrong
    for alternatives: against the real TRANSPORT-418748 (Sedan 175, Hiace 200), a rate sheet
    pricing only the 1-3 Sedan line proposed Sedan 175 -> 95 AND Hiace 200 -> 95, wiping the whole
    Hiace supplement off a modality the document never mentioned.

    Disjoint brackets (a live 2-6 alongside a live 7-9) are tiers of one product and keep the
    overlap fallback - that is what lets a rate sheet whose tier boundaries differ from the live
    ones still price correctly, which every other supplier's document already relies on."""
    usable = [o for o in (options or []) if not o.get("fetch_failed")]
    for i, a in enumerate(usable):
        for b in usable[i + 1:]:
            if a.get("min_pax", 0) <= b.get("max_pax", 0) and b.get("min_pax", 0) <= a.get("max_pax", 0):
                return True
    return False


def bracket_price_for(finding: Dict[str, Any], min_pax: int, max_pax: int,
                      minimum_pax: int, price_per_pax: bool = True,
                      allow_overlap: bool = True) -> Optional[float]:
    """The new unit price for one EXISTING bracket, from what the document said.

    The solo bracket is checked FIRST, ahead of any exact match. Failing that: exact bracket
    match, then an overlapping one - a document that prices "2-9" still tells you what a live
    "2-6" bracket costs. `allow_overlap=False` turns that last step off, for a route whose
    modalities are alternatives rather than tiers (see options_are_alternatives).

    CONFIRMED REAL BUG (product owner, 2026-09-11: "the App must understand the difference
    between BasePrice (per vehicle or per Pax)"): the minimum-party multiplication below is a
    PER-PERSON rule - "$32 p.p., minimum 2 pax" means a lone traveller pays 2 x 32. A PER-VEHICLE
    price means nothing of the sort: the vehicle costs what it costs no matter how many people
    ride in it, and a minimum party size is a capacity/booking rule, not a multiplier. Running
    the multiplication on a per-vehicle transport turned a real $45 vehicle rate into $90.
    price_per_pax defaults True (the confirmed common case, and what every existing caller of
    this function means) so only a genuinely per-vehicle Transport takes the new branch.

    CONFIRMED REAL BUG (product owner, real document: "HRG Airport to El Quseir", "Private
    Transfer p.p. valid for (Min.2 pax)" priced at 32 - the live 1-pax bracket should become 64
    (32*2), but was proposed at 32 unchanged). Root cause: the solo-bracket multiplication used
    to run only when NO exact match existed for the requested 1-pax bracket. The prompt asks
    the AI not to compute the 1-pax price itself, but doesn't stop it from still emitting a
    min_pax=1 entry carrying the raw (un-multiplied) per-person number "as stated" - and that
    entry then satisfied the exact-match check below, short-circuiting the multiplication
    entirely before it ever ran. Checking the minimum-party rule FIRST makes this correct
    regardless of whether the AI included a spurious 1-pax entry or, per the prompt, correctly
    omitted one - an AI-reported min_pax=1 price is never trusted directly once a real minimum
    party size is known, since a genuine minimum-party rate means the document never actually
    prices 1 pax on its own."""
    if not finding.get("found"):
        return None
    brackets = finding.get("brackets") or []
    if max_pax == 1 and minimum_pax > 1 and price_per_pax:
        base = next((b["price"] for b in brackets if b["min_pax"] == minimum_pax), None)
        if base is None:
            base = next((b["price"] for b in brackets if b["min_pax"] > 1), None)
        if base is None and brackets:
            base = brackets[0]["price"]
        return round(base * minimum_pax, 2) if base is not None else None
    for b in brackets:
        if b["min_pax"] == min_pax and b["max_pax"] == max_pax:
            return b["price"]
    if not allow_overlap:
        return None
    for b in brackets:
        if b["min_pax"] <= max_pax and b["max_pax"] >= min_pax:
            return b["price"]
    return None


def _matched_bracket_for(finding: Dict[str, Any], min_pax: int, max_pax: int,
                         minimum_pax: int, price_per_pax: bool = True,
                         allow_overlap: bool = True) -> Tuple[Optional[Dict[str, Any]], float]:
    """The SAME bracket dict bracket_price_for would use to answer this route's price, plus the
    multiplier bracket_price_for would apply to it (the solo-bracket minimum-party rule). Shared
    by bracket_price_for and bracket_periods_for so the two can never disagree about which row
    of the document answers a given (min_pax, max_pax) - one finds a single number in it, the
    other every season it carries."""
    if not finding.get("found"):
        return None, 1.0
    brackets = finding.get("brackets") or []
    if max_pax == 1 and minimum_pax > 1 and price_per_pax:
        matched = next((b for b in brackets if b["min_pax"] == minimum_pax), None)
        if matched is None:
            matched = next((b for b in brackets if b["min_pax"] > 1), None)
        if matched is None and brackets:
            matched = brackets[0]
        return matched, float(minimum_pax)
    for b in brackets:
        if b["min_pax"] == min_pax and b["max_pax"] == max_pax:
            return b, 1.0
    if not allow_overlap:
        return None, 1.0
    for b in brackets:
        if b["min_pax"] <= max_pax and b["max_pax"] >= min_pax:
            return b, 1.0
    return None, 1.0


def bracket_periods_for(finding: Dict[str, Any], min_pax: int, max_pax: int,
                        minimum_pax: int, price_per_pax: bool = True,
                        allow_overlap: bool = True) -> List[Dict[str, Any]]:
    """Like bracket_price_for, but returns EVERY period the document states for the matched
    bracket rather than one flat number - built for the confirmed real gap (product owner,
    2026-09-11): "Hiace had three supplements because of a high season and peak season time, but
    the app never filled out the actual prices." A bracket with no "seasons" (the common, non-
    seasonal case) still returns exactly one period, with start_date/end_date both None - so a
    caller can treat every bracket uniformly as "a list of periods to write" without a separate
    flat-price code path.

    Uses the exact same matching (solo-bracket multiplication, exact bracket, then overlap) as
    bracket_price_for via _matched_bracket_for, so this can never pick a different row of the
    document than the single-price lookup would.

    Each period: {"price", "child_price", "infant_price", "start_date", "end_date"} - price
    (and child/infant, when present) already has the minimum-party multiplier applied, exactly
    like bracket_price_for's own return value."""
    matched, multiplier = _matched_bracket_for(finding, min_pax, max_pax, minimum_pax,
                                                price_per_pax=price_per_pax,
                                                allow_overlap=allow_overlap)
    if matched is None:
        return []
    seasons = matched.get("seasons") or []
    if not seasons:
        price = matched.get("price")
        if price is None:
            return []
        return [{"price": round(price * multiplier, 2),
                 "child_price": matched.get("child_price"),
                 "infant_price": matched.get("infant_price"),
                 "start_date": None, "end_date": None}]
    out = []
    for s in seasons:
        price = s.get("price")
        if price is None:
            continue
        out.append({"price": round(price * multiplier, 2),
                    "child_price": s.get("child_price"), "infant_price": s.get("infant_price"),
                    "start_date": s.get("start_date") or None, "end_date": s.get("end_date") or None})
    return out


def modality_groups(routes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The distinct modalities across a supplier's whole product list, for the review screen's
    "which modality does this rate sheet price?" chooser.

    CONFIRMED REAL REQUEST (product owner, 2026-09-11): "should we build a separate human
    confirmation for the bulk transport price update. So the app detects all modalities first,
    and before the AI reads the document, the app asks the human which modality it is and which
    price it shall touch."

    Grouped by PASSENGER RANGE rather than by option code, because real option codes are not
    consistent across a supplier - confirmed real examples include "ASWHRG", "PraslinLaDigue12",
    and codes literally equal to the transport's own name (see transport_matcher's docstring).
    The bracket is the one thing that means the same on every transport, and it is what an
    operator recognises ("the 1 to 3 pax Sedan line"). A representative modality name is carried
    along purely for display.

    Each group: {"min_pax", "max_pax", "label", "codes" (every live option code in it),
    "route_count"}. Sorted widest-first so the common bracket leads."""
    groups: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for route in routes:
        for option in route.get("options") or []:
            if option.get("fetch_failed"):
                continue
            key = (option.get("min_pax"), option.get("max_pax"))
            group = groups.setdefault(key, {"min_pax": key[0], "max_pax": key[1],
                                            "names": [], "codes": set(), "route_count": 0})
            group["route_count"] += 1
            group["codes"].add(option.get("code"))
            name = (option.get("name") or "").strip()
            if name and name not in group["names"]:
                group["names"].append(name)
    out = []
    for key in sorted(groups, key=lambda k: (-(k[1] - k[0]), k[0])):
        group = groups[key]
        sample = group["names"][0] if group["names"] else ", ".join(sorted(c for c in group["codes"] if c))
        out.append({"min_pax": group["min_pax"], "max_pax": group["max_pax"],
                    "codes": sorted(c for c in group["codes"] if c),
                    "route_count": group["route_count"],
                    "label": f"{group['min_pax']}-{group['max_pax']} pax — {sample}"
                             if sample else f"{group['min_pax']}-{group['max_pax']} pax"})
    return out


def flat_price_modalities(route: Dict[str, Any]) -> List[str]:
    """Option codes among this route's ALTERNATIVE modalities (see options_are_alternatives)
    that our own read currently shows at the SAME live price as another one of them.

    CONFIRMED REAL RULE (product owner, 2026-09-11): "if a transport has 2 or more modalities,
    there will be always base price and multiple price supplements. It does not make sense to
    have two modalities with the same price." A Sedan and a Hiace are different vehicles a
    customer chooses between - if this app's read says they cost the same, that is not a
    legitimate rate, it is a signal the read itself is wrong.

    CONFIRMED REAL MECHANISM this exists to catch (same day, TRANSPORT-423015 - see
    apply_proposals' own comment and claude/transport-supplement-admin-ui-vs-api-mismatch-2026-
    09-10.md): Travel Compositor's public API can return adultPriceSupplement: 0.0 for a
    supplement its own admin Prices tab shows as nonzero. When that happens, EVERY alternative
    modality on the transport reads back as exactly the shared base price - which is exactly
    this symptom - and the route silently files as "unchanged" even though a real, human-typed
    supplement exists and this app simply cannot see it. This does not fix the underlying
    platform issue (there is no other field to read instead - see that doc), but it stops the
    route from disappearing into "unchanged" unexplained, which is what let TRANSPORT-423015's
    lost supplement go unnoticed until the product owner spotted it by hand.

    Returns the codes involved, or [] when there is nothing to flag (fewer than two alternative,
    readable options, or every one of them already has a distinct price)."""
    usable = [o for o in (route.get("options") or []) if not o.get("fetch_failed")]
    if len(usable) < 2 or not options_are_alternatives(usable):
        return []
    by_price: Dict[float, List[str]] = {}
    for option in usable:
        key = round(_num(option.get("unit_price")), 2)
        by_price.setdefault(key, []).append(option.get("code"))
    flat = []
    for codes in by_price.values():
        if len(codes) > 1:
            flat.extend(codes)
    return flat


def build_proposals(routes: List[Dict[str, Any]],
                    findings: Dict[int, Dict[str, Any]],
                    scoped_brackets: Optional[List[Tuple[int, int]]] = None
                    ) -> List[Dict[str, Any]]:
    """One proposal per live route: what each modality costs now, and what it would become.

    scoped_brackets, when given, is the human's explicit answer to "which modality does this rate
    sheet price?" (see modality_groups) as a list of (min_pax, max_pax) pairs. Every option
    outside it is left entirely alone - not repriced, not counted as missing. None means every
    modality is in scope, which is the default and what every caller before 2026-09-11 meant."""
    proposals = []
    scope = set(scoped_brackets) if scoped_brackets else None
    for i, route in enumerate(routes):
        finding = findings.get(i) or {"found": False, "brackets": [], "confidence": "low",
                                      "note": "", "matched_row": "", "minimum_pax": 1,
                                      "currency": ""}
        # CONFIRMED HAZARD (2026-09-11, building the FTS one-vehicle-round price refresh - see
        # lookup_prices_from_fts_matrix): a finding naming only ONE bracket cannot safely rely on
        # bracket_price_for's overlap fallback across every option on the route the way a finding
        # naming a bracket per option can - two live brackets that both start at 1 pax (Sedan
        # 1-3, Hiace 1-8) always overlap EACH OTHER too, so a single Sedan-only bracket would
        # also overlap-match (and reprice) the live Hiace option it says nothing about. A finding
        # may set "only_option_code" to restrict itself to exactly one option by its own live
        # code - every other option on the route is then left alone entirely (not counted as
        # missing or unchanged, simply not part of this round), rather than risk bracket_price_for
        # guessing which live option a lone bracket was meant for. No caller besides the FTS
        # single-vehicle path sets this key, so every other document's behavior is unchanged.
        only_option_code = finding.get("only_option_code")
        # CONFIRMED REAL BUG, same day, one layer wider than only_option_code: the guard above
        # only ever protected the FTS path, because nothing else sets that key. Every OTHER
        # supplier's document - the AI path, which is what the remaining 200+ suppliers use - was
        # still exposed. Reproduced against the real TRANSPORT-418748 shape: a rate sheet pricing
        # only the 1-3 Sedan line proposed "Sedan 175 -> 95" AND "Hiace 200 -> 95". The fix is
        # not another special case but the general rule: when a route's modalities are
        # ALTERNATIVES (they overlap each other - see options_are_alternatives), a document
        # bracket may only claim the modality it matches EXACTLY. Tiered brackets are untouched
        # and keep the overlap fallback they have always relied on.
        per_pax = bool(route.get("price_per_pax", True))
        allow_overlap = not options_are_alternatives(route.get("options") or [])
        currency_changed = bool(finding.get("currency")) and \
            finding["currency"] != str(route.get("currency") or "").upper()

        # CONFIRMED REAL BUG (audit, 2026-08-24): an option whose live price could not be READ
        # must block this whole route, not be quietly skipped. See rebuild_prices' identical
        # refusal for why: a partial read can never safely be turned into a base/supplement
        # split, so nothing about this route is safe to propose.
        unreadable = [o.get("code") for o in route["options"] if o.get("fetch_failed")]
        usable_options = [o for o in route["options"] if not o.get("fetch_failed")]

        zero_supplement_errors: List[Dict[str, Any]] = []

        if unreadable:
            status = "blocked_unreadable"
            changes, unchanged, missing = [], 0, 0
        elif route.get("kind") == KIND_TRANSFER:
            # Transfer has no vehicle/supplement modality concept at all (see
            # _rebuild_transfer_prices) - unchanged from before this overhaul.
            changes, unchanged, missing = [], 0, 0
            for option in usable_options:
                if only_option_code is not None and option.get("code") != only_option_code:
                    continue
                if scope is not None and (option.get("min_pax"), option.get("max_pax")) not in scope:
                    continue
                new_price = bracket_price_for(finding, option["min_pax"], option["max_pax"],
                                              finding.get("minimum_pax", 1),
                                              price_per_pax=per_pax,
                                              allow_overlap=allow_overlap or scope is not None)
                if new_price is None:
                    missing += 1
                    continue
                if abs(new_price - option["unit_price"]) < 0.005:
                    unchanged += 1
                    continue
                changes.append({"code": option["code"], "min_pax": option["min_pax"],
                                "max_pax": option["max_pax"], "old": option["unit_price"],
                                "new": new_price, "name": option.get("name", "")})
            if changes:
                status = "changed"
            elif not finding.get("found"):
                status = "not_in_document"
            elif missing and not unchanged:
                status = "not_in_document"
            else:
                status = "unchanged"
        else:
            # CONFIRMED FINAL TRANSPORT PRICE-STRUCTURE MODEL (product owner, 2026-09-11, full
            # 8-question Q&A - see claude/ project docs for the transcript). A transport's real
            # sell price for any modality is ONE shared Vehicle/baseAdultPrice number at the
            # PARENT level plus that modality's OWN price supplement - and which modality is
            # "the vehicle-price one" is a HUMAN decision made per round, never auto-detected
            # (Travel Compositor's API is confirmed to under-report a real supplement as 0.0, and
            # this app's own prior update run is confirmed to have zeroed out real supplements by
            # trusting that read - see _current_base_option's docstring). So: when a route has
            # two or more live brackets, a designation is REQUIRED (route["base_bracket_override"]
            # - the human's answer to app.py's "Which modality is the BASE/vehicle price?"
            # question) before anything is proposed at all. A single-bracket route has nothing to
            # designate - that one bracket is trivially the vehicle price.
            live_brackets = sorted({(o["min_pax"], o["max_pax"]) for o in usable_options})
            base_bracket = route.get("base_bracket_override")
            needs_base_designation = len(live_brackets) >= 2 and base_bracket is None
            if needs_base_designation:
                status = "blocked_needs_base_designation"
                changes, unchanged, missing = [], 0, 0
            else:
                if base_bracket is None and live_brackets:
                    base_bracket = live_brackets[0]
                vehicle_option = next((o for o in usable_options
                                       if (o["min_pax"], o["max_pax"]) == base_bracket), None)
                base_reference = vehicle_option["unit_price"] if vehicle_option is not None else 0.0
                changes, unchanged, missing = [], 0, 0
                for option in usable_options:
                    if only_option_code is not None and option.get("code") != only_option_code:
                        continue
                    if scope is not None and (option.get("min_pax"), option.get("max_pax")) not in scope:
                        continue
                    is_vehicle = (option["min_pax"], option["max_pax"]) == base_bracket
                    if is_vehicle:
                        new_price = bracket_price_for(finding, option["min_pax"], option["max_pax"],
                                                      finding.get("minimum_pax", 1),
                                                      price_per_pax=per_pax,
                                                      allow_overlap=allow_overlap or scope is not None)
                        if new_price is None:
                            missing += 1
                            continue
                        if abs(new_price - option["unit_price"]) < 0.005:
                            unchanged += 1
                            continue
                        changes.append({"code": option["code"], "min_pax": option["min_pax"],
                                        "max_pax": option["max_pax"], "old": option["unit_price"],
                                        "new": new_price, "name": option.get("name", ""),
                                        "write_kind": "vehicle",
                                        "start_date": None, "end_date": None})
                        continue
                    # A non-vehicle (supplement) bracket - every SEASON the document states for
                    # it, each written to its own period (product owner, 2026-09-11: "Hiace had
                    # three supplements... but the app never filled out the actual prices").
                    periods = bracket_periods_for(finding, option["min_pax"], option["max_pax"],
                                                  finding.get("minimum_pax", 1),
                                                  price_per_pax=per_pax,
                                                  allow_overlap=allow_overlap or scope is not None)
                    if not periods:
                        missing += 1
                        continue
                    any_reported = False
                    for period in periods:
                        supplement = round(period["price"] - base_reference, 2)
                        if abs(supplement) < 0.005:
                            # CONFIRMED ABSOLUTE RULE (product owner, 2026-09-11): "Supplement
                            # cannot be 0, if it is 0 there is an error." A hard block, not a
                            # warning - two different vehicle classes can never legitimately cost
                            # the same, so a computed 0 means the read (or the base designation)
                            # is wrong, not that nothing needs to change.
                            zero_supplement_errors.append({
                                "code": option["code"], "name": option.get("name", ""),
                                "start_date": period["start_date"], "end_date": period["end_date"],
                                "would_be_price": period["price"],
                            })
                            any_reported = True
                            continue
                        existing_entry = _find_entry_for_period(option, period["start_date"],
                                                                period["end_date"])
                        if existing_entry is not None:
                            old_total = round(base_reference + _num(existing_entry.get("adultPriceSupplement")), 2)
                        else:
                            old_total = None
                        if old_total is not None and abs(period["price"] - old_total) < 0.005:
                            unchanged += 1
                            any_reported = True
                            continue
                        changes.append({"code": option["code"], "min_pax": option["min_pax"],
                                        "max_pax": option["max_pax"],
                                        "old": old_total if old_total is not None else option["unit_price"],
                                        "new": period["price"], "name": option.get("name", ""),
                                        "write_kind": "supplement",
                                        "start_date": period["start_date"], "end_date": period["end_date"],
                                        "is_new_period": old_total is None})
                        any_reported = True
                    if not any_reported:
                        missing += 1
            if needs_base_designation:
                pass  # status already set above - never overwritten by the read below
            elif zero_supplement_errors:
                status = "blocked_zero_supplement"
            elif changes:
                status = "changed"
            elif not finding.get("found"):
                status = "not_in_document"
            elif missing and not unchanged:
                status = "not_in_document"
            else:
                status = "unchanged"

        proposals.append({
            "index": i, "route": route, "finding": finding, "changes": changes,
            "unchanged": unchanged, "missing": missing, "status": status,
            "currency_changed": currency_changed,
            "unreadable_options": unreadable,
            # CONFIRMED REAL RULE (product owner, 2026-09-11) - see flat_price_modalities' own
            # docstring. Computed regardless of status: a route can be flagged whether this round
            # is proposing changes to it or not, since the concern is about the CURRENT read, not
            # this document.
            "flat_price_codes": flat_price_modalities(route),
            # CONFIRMED REAL REQUEST (product owner, 2026-09-11) - see
            # supplement_calculation_examples' own docstring. Computed from the SAME changes list
            # shown on screen, so the example always matches what's actually about to be applied,
            # even after a hand-edit to a "new price" field re-runs this.
            "supplement_examples": supplement_calculation_examples(route, changes),
            # CONFIRMED ABSOLUTE RULE (product owner, 2026-09-11) - see the zero-supplement block
            # above. Non-empty means this route CANNOT be published until resolved (a wrong read,
            # a wrong base designation, or a clarification typed into the AI text field), however
            # many valid changes it also contains.
            "zero_supplement_errors": zero_supplement_errors,
            # Only genuine changes are pre-ticked, and never a route that's blocked for any
            # reason - an accept-all button must not sweep up a route the document never
            # mentioned, one we couldn't fully read, one with no base designation, or one with a
            # zero-supplement error.
            "accepted": status == "changed",
        })
    return proposals


def build_ticket_proposals(routes: List[Dict[str, Any]],
                           findings: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One proposal per live (Ticket, Modality): what it costs now, and what it would become.

    Modelled on build_proposals() above (same statuses, same accept-by-default-only-when-
    changed rule) with two Ticket-specific additions: a route whose Modality could not be read
    is reported as blocked_unreadable (like build_proposals' own unreadable-option rule, just
    for the whole route since there is only one "option" here - the Modality itself), and a
    route whose priceType this phase doesn't understand (DISTRIBUTION/SERVICE) is reported as
    unsupported_price_type rather than silently skipped or guessed at.

    Each change also carries "child_old"/"child_new": a child/infant price at the SAME bracket
    moves alongside the adult price (see rebuild_ticket_prices) exactly like Transport already
    scales baseChildrenPrice/baseInfantPrice with the adult base - it is reported here so the
    review screen can show it, but is never a separate accept/reject choice of its own."""
    proposals = []
    for i, route in enumerate(routes):
        finding = findings.get(i) or {"found": False, "brackets": [], "confidence": "low",
                                      "note": "", "matched_row": "", "minimum_pax": 1,
                                      "currency": ""}
        if route.get("fetch_failed"):
            proposals.append({
                "index": i, "route": route, "finding": finding, "changes": [],
                "unchanged": 0, "missing": 0, "status": "blocked_unreadable",
                "currency_changed": False,
                "unreadable_options": [route.get("modality_code")], "accepted": False,
            })
            continue
        if not _ticket_price_type_supported(route.get("price_type")):
            proposals.append({
                "index": i, "route": route, "finding": finding, "changes": [],
                "unchanged": 0, "missing": 0, "status": "unsupported_price_type",
                "currency_changed": False, "unreadable_options": [], "accepted": False,
            })
            continue

        child_by_code = {c["code"]: c for c in (route.get("child_options") or [])}
        changes, unchanged, missing = [], 0, 0
        for option in route["options"]:
            new_price = bracket_price_for(finding, option["min_pax"], option["max_pax"],
                                          finding.get("minimum_pax", 1))
            child_price_from_doc = None
            for b in (finding.get("brackets") or []):
                if b["min_pax"] <= option["max_pax"] and b["max_pax"] >= option["min_pax"] \
                        and b.get("child_price") is not None:
                    child_price_from_doc = b["child_price"]
                    break
            if new_price is None:
                if child_price_from_doc is None:
                    missing += 1
                    continue
                new_price = option["unit_price"]  # only the child moves; adult stays as-is
            adult_changed = abs(new_price - option["unit_price"]) >= 0.005
            if not adult_changed and child_price_from_doc is None:
                unchanged += 1
                continue
            child_row = child_by_code.get(option["code"])
            changes.append({
                "code": option["code"], "min_pax": option["min_pax"], "max_pax": option["max_pax"],
                "old": option["unit_price"], "new": new_price, "name": option.get("name", ""),
                "child_old": child_row["unit_price"] if child_row else None,
                "child_new": child_price_from_doc,
            })
        currency_changed = bool(finding.get("currency")) and \
            finding["currency"] != str(route.get("currency") or "").upper()
        if changes:
            status = "changed"
        elif not finding.get("found"):
            status = "not_in_document"
        elif missing and not unchanged:
            status = "not_in_document"
        else:
            status = "unchanged"
        proposals.append({
            "index": i, "route": route, "finding": finding, "changes": changes,
            "unchanged": unchanged, "missing": missing, "status": status,
            "currency_changed": currency_changed, "unreadable_options": [],
            "accepted": status == "changed",
        })
    return proposals


def rebuild_ticket_prices(route: Dict[str, Any], changes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """New prices onto a Ticket Modality, keeping everything else - structure, dates, languages,
    supplements - exactly as it is. Same rule as Transfer/Transport (CONFIRMED PRODUCT-OWNER
    RULE): only the numbers move; startDate/endDate are never touched here.

    baseAdultPrice is deliberately left UNTOUCHED for an OCCUPANCY Modality: builder.py already
    treats it as a required-but-inert placeholder outside DISTRIBUTION mode (a harmless 1.0 when
    creating one) - the real per-headcount prices live entirely in occupancyPrices, which is
    what this function actually rewrites."""
    payload = json.loads(json.dumps(route["raw"]))
    resolved_adult = {c["code"]: round(float(c["new"]), 2) for c in changes}
    resolved_child = {c["code"]: round(float(c["child_new"]), 2)
                      for c in changes if c.get("child_new") is not None}
    adult_by_code = {o["code"]: o for o in route["options"]}

    new_rows = []
    for row in (payload.get("occupancyPrices") or []):
        if not isinstance(row, dict):
            new_rows.append(row)
            continue
        code = f"occ{int(_num(row.get('occupancy'), 0))}"
        row = dict(row)
        if isinstance(row.get("ageRange"), dict):
            if code in resolved_child:
                row["amount"] = resolved_child[code]
            elif code in resolved_adult and adult_by_code.get(code, {}).get("unit_price", 0) > 0:
                # No explicit child price in the document for this bracket - move the child
                # price with the adult price, same ratio-scaling rule Transport already uses
                # for baseChildrenPrice/baseInfantPrice, rather than leaving it stale.
                ratio = resolved_adult[code] / adult_by_code[code]["unit_price"]
                row["amount"] = round(_num(row.get("amount")) * ratio, 2)
        elif code in resolved_adult:
            row["amount"] = resolved_adult[code]
        new_rows.append(row)
    payload["occupancyPrices"] = new_rows
    return payload


def apply_ticket_proposals(client, supplier_id: str, proposals: List[Dict[str, Any]],
                           progress: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]:
    """Push the accepted Ticket price proposals. Each Modality is PUT back whole, with only its
    occupancyPrices changed - same belt-and-braces shape as apply_proposals() above."""
    accepted = [p for p in proposals if p.get("accepted") and p.get("changes")]
    out = {"updated": [], "failed": [], "skipped": len(proposals) - len(accepted)}
    for n, proposal in enumerate(accepted):
        route = proposal["route"]
        if progress:
            progress(n + 1, len(accepted), route.get("name", ""))
        payload = rebuild_ticket_prices(route, proposal["changes"])
        payload["code"] = route.get("modality_code")
        try:
            res = client.update_ticket_option(supplier_id, route.get("ticket_code"), payload)
            if isinstance(res, dict) and "error" in res:
                out["failed"].append({"name": route.get("name"),
                                      "detail": str(res.get("message") or res.get("error"))})
                continue
            out["updated"].append({"name": route.get("name"), "changes": proposal["changes"]})
        except Exception as e:
            out["failed"].append({"name": route.get("name"),
                                  "detail": ai_extractor.friendly_error_message(e)})
    return out


def _rebuild_transfer_prices(route: Dict[str, Any],
                             new_unit_prices: Dict[str, float]) -> Dict[str, Any]:
    """New prices onto a transfer record, keeping its structure and its DATES.

    CONFIRMED REAL RULE (product owner): "if the price will be refreshed, the date of Transfer
    and the date of Transports will most likely be until 2049 or even 2099, but the price must
    still be updated." So startDate and endDate are never touched here - the document's own
    season is irrelevant to a product deliberately left open-ended, and overwriting a 2099 end
    date with a rate sheet's July 2027 would silently retire the product next summer."""
    payload = json.loads(json.dumps(route["raw"]))
    brackets = route["options"]
    resolved = {b["code"]: round(float(new_unit_prices.get(b["code"], b["unit_price"])), 2)
                for b in brackets}
    default = next((b for b in brackets if b["code"] == DEFAULT_BRACKET_CODE), None)
    base = resolved.get(DEFAULT_BRACKET_CODE, _num(payload.get("basePrice")))
    payload["basePrice"] = base

    currency = payload.get("currency") or "EUR"
    entries = []
    for bracket in brackets:
        if bracket["code"] == DEFAULT_BRACKET_CODE:
            continue
        price = resolved[bracket["code"]]
        if abs(price - base) < 0.005:
            # An entry equal to the default is redundant - schemas.TransferOccupancyPriceVO
            # exists only for occupancies that genuinely differ.
            continue
        entry = dict(bracket.get("raw") or {"occupancy": bracket["min_pax"]})
        existing = entry.get("basePrice")
        entry["basePrice"] = {"amount": price,
                              "currency": (existing or {}).get("currency", currency)
                              if isinstance(existing, dict) else currency}
        entries.append(entry)
    payload["pricesByOccupancy"] = entries
    if default is None:
        payload["basePrice"] = base
    return {"transport": payload, "options": []}


def _option_supplement(option: Dict[str, Any]) -> float:
    """This option's CURRENT live adultPriceSupplement (0.0 if it has no price entry at all -
    the confirmed real shape for a bracket that costs exactly the base rate, see
    schemas.ContractTransportOptionPriceVO's own docstring)."""
    for price in ((option.get("raw") or {}).get("prices") or []):
        if isinstance(price, dict):
            return _num(price.get("adultPriceSupplement"))
    return 0.0


def _current_base_option(options: List[Dict[str, Any]],
                         forced_bracket: Optional[Tuple[int, int]] = None) -> Dict[str, Any]:
    """Which option IS the transport's base modality right now - not a guess from bracket
    width, but whichever option already carries no supplement live.

    forced_bracket, when given, is the human's own explicit answer to "which modality is the
    base price this round?" (product owner, 2026-09-11, reviewing the confirmed real
    TRANSPORT-423134/423015 read: "if the selected modality we want to update in this exact
    update process, if this will be the base price or if it has to be calculated to the base
    price on top? Sedan Modality would be base price and if I would update the Hiace the price
    difference must be calculated"). The "zero supplement live" heuristic below is exactly the
    thing that can be WRONG when Travel Compositor's API under-reports a real supplement as 0.0
    (see apply_proposals' own comment and claude/transport-supplement-admin-ui-vs-api-mismatch-
    2026-09-10.md) - in that failure mode, the corrupted-looking option and the genuine base both
    read as "no supplement", so the heuristic can pick either one. A human who knows the
    supplier's real structure (Sedan is always base) is a better answer than that read, so it
    always wins when it names exactly one live option on this route; if it names zero or more
    than one (the bracket isn't actually present on this route, or the data is otherwise
    unexpected), this falls through to the live-read heuristic exactly as if nothing were
    forced - a route that doesn't match the human's assumption is never left without a base.

    CONFIRMED REAL PRODUCTION BUG (product owner, 2026-09-11): this used to always pick the
    WIDEST bracket as base and express every OTHER option as a supplement against it - correct
    for a transport that was built that way, but WRONG for FTS's Sedan/Hiace structure, where
    Sedan (the NARROWER 1-3 pax bracket) was deliberately made the base at creation time (see
    fts_transfer_matrix.py's own module docstring: "Sedan is base price as one modality...
    Hiace is second modality and is used as price supplement with Sedan" -
    builder.build_transport_payloads is even called with
    force_base_occupancy=FTS_SEDAN_BRACKET specifically to override the default widest-wins
    heuristic at creation time). Recomputing "widest wins" from scratch on every price refresh
    silently FLIPPED which option carries the supplement on every run - Sedan (previously
    supplement-free) suddenly needed a brand-new price entry it had never had before, and
    Hiace's real existing supplement was thrown away - even on an Apply that only meant to
    touch Hiace's own price (a one-vehicle round, see only_option_code above). Real symptom: a
    real bulk Apply against 13 FTS-matched Transports failed all 13 with "TransportContractPrice.
    startDate/endDate: must not be null" - Sedan's option had never carried a price entry
    before, so building one from scratch (see rebuild_prices below) had no dates to carry
    forward, the SAME class of bug already found and fixed once for the parent record's own
    airlineCode (see load_supplier_transports' docstring above), just one level down on the
    option sub-resource instead.

    Fix: keep whichever option is ALREADY the live base (a "no supplement" option) rather than
    re-deriving it from bracket width - this reflects how the transport is ACTUALLY structured
    today, so a refresh can never restructure a transport that was deliberately built with a
    non-widest base. Only when the live data is ambiguous (no option is currently
    zero-supplement, or more than one is) does this fall back to the original widest-bracket
    heuristic, which is exactly correct for a transport that has never had this choice made
    explicitly - and is also why every pre-existing single/two-option test fixture in this
    codebase (none of which set up a genuinely ambiguous case) still passes unchanged."""
    if forced_bracket is not None:
        forced = [o for o in options if (o["min_pax"], o["max_pax"]) == forced_bracket]
        if len(forced) == 1:
            return forced[0]
    zero_supplement = [o for o in options if abs(_option_supplement(o)) < 0.005]
    if len(zero_supplement) == 1:
        return zero_supplement[0]
    return max(options, key=lambda o: (o["max_pax"] - o["min_pax"], -o["min_pax"]))


def rebuild_prices(route: Dict[str, Any], changes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The payloads that put these prices live, keeping base and supplements consistent.

    CONFIRMED FINAL TRANSPORT PRICE-STRUCTURE MODEL (product owner, 2026-09-11 - see
    build_proposals' own comment for the full transcript reference): a round touches EXACTLY ONE
    thing - either the shared Vehicle/baseAdultPrice field (a "vehicle" round) or the touched
    modality's own price-supplement entries (a "supplement" round) - never both, and never any
    OTHER modality's data, not even for display. `changes` is a proposal's own "changes" list
    (as build_proposals produces it), each entry already carrying which kind it is
    ("write_kind": "vehicle" or "supplement") and, for a supplement entry, which season/period
    it belongs to ("start_date"/"end_date", both None for a flat, non-seasonal price).

    Returns {"transport": the parent payload to PUT (None if refused), "options": a list of
    {"code", "payload"} for ONLY the option(s) this round actually touches (empty for a vehicle
    round - nothing else is written), "write_kind": "vehicle" or "supplement" (absent for
    Transfer, which has no such concept and always PUTs the whole record as before)}."""
    if route.get("kind") == KIND_TRANSFER:
        new_unit_prices = {c["code"]: c["new"] for c in changes}
        return _rebuild_transfer_prices(route, new_unit_prices)
    # CONFIRMED REAL BUG (audit, 2026-08-24): see build_proposals' "blocked_unreadable" comment.
    # Refusing here as well as at the proposal stage is deliberate belt-and-braces: this function
    # is what actually computes the shared base price, so it is the place where repricing every
    # other modality around an unread option would happen. A partial read can never produce a safe
    # rebuild, so it produces nothing at all.
    if any(o.get("fetch_failed") for o in route["options"]):
        return {"transport": None, "options": [],
                "blocked": "Some of this route's live option prices could not be read."}
    options = [o for o in route["options"] if not o.get("fetch_failed")]
    if not options:
        return {"transport": None, "options": []}
    if not changes:
        return {"transport": None, "options": [], "blocked": "Nothing to write."}

    # CONFIRMED REAL REQUEST (product owner, 2026-09-11): the human's own answer to "which
    # modality is the vehicle/base price this round?" - stashed on the route dict itself rather
    # than threaded through every caller's signature, since every caller of this function already
    # receives the SAME route object build_proposals was given. A route with two or more live
    # brackets and no designation is refused here too (belt-and-braces with build_proposals'
    # identical refusal) - there is no "auto-detect" fallback left in this write path at all, per
    # the product owner's explicit instruction: no more silently guessing which modality is base.
    live_brackets = sorted({(o["min_pax"], o["max_pax"]) for o in options})
    base_bracket = route.get("base_bracket_override")
    if len(live_brackets) >= 2 and base_bracket is None:
        return {"transport": None, "options": [],
                "blocked": "Which modality is the vehicle/base price was not designated for this route."}
    if base_bracket is None and live_brackets:
        base_bracket = live_brackets[0]
    base_option = next((o for o in options if (o["min_pax"], o["max_pax"]) == base_bracket), None)
    if base_option is None:
        # Belt-and-braces only - build_proposals should never hand back a change for a bracket
        # that no longer exists live, but a stale/hand-edited proposal is not a reason to guess.
        return {"transport": None, "options": [],
                "blocked": "The designated base bracket is no longer among this route's live modalities."}

    vehicle_changes = [c for c in changes if c["code"] == base_option["code"]]
    supplement_changes = [c for c in changes if c["code"] != base_option["code"]]

    per_pax = bool(route.get("price_per_pax", True))
    base_field = "baseAdultPrice" if per_pax else "vehiclePrice"
    old_base = _num((route.get("raw") or {}).get(base_field))
    base_for_supplement_calc = round(_num(vehicle_changes[0]["new"]), 2) if vehicle_changes else old_base

    # CONFIRMED ABSOLUTE RULE (product owner, 2026-09-11): "Supplement cannot be 0, if it is 0
    # there is an error." build_proposals already screens these out of `changes` before a human
    # ever sees them as an acceptable change, but this is the actual write path, so it refuses
    # the WHOLE round rather than trust a stale or hand-edited proposal to have done that.
    zero = [c for c in supplement_changes
            if abs(round(_num(c["new"]) - base_for_supplement_calc, 2)) < 0.005]
    if zero:
        codes = ", ".join(sorted({c["code"] for c in zero}))
        return {"transport": None, "options": [],
                "blocked": f"Computed supplement is 0 for {codes} - a modality can never "
                           "legitimately cost exactly the vehicle/base price."}

    parent = json.loads(json.dumps(route["raw"]))
    # BELT AND SUSPENDERS (2026-09-11, "airlineCode: must not be null" incident - see
    # load_supplier_transports' own docstring above for the real fix, which is fetching each
    # transport's full individual record so this field is populated in the first place). This
    # call is only the last-resort fallback for the rare case where even the individual record
    # is missing the field (same defensive pattern cancellation_bulk_transport.apply_proposals
    # already uses) - it never overwrites a real value that's actually present, only fills a
    # field that is genuinely still None.
    normalize_for_put(parent, "Transport")

    write_kind = "vehicle" if vehicle_changes else "supplement"

    if vehicle_changes:
        # CONFIRMED REAL PRODUCT DECISION (product owner, 2026-09-11): "but if we only update the
        # Sedan price whey should the app touches even the Hiace price supplement?" - a vehicle
        # round writes ONLY the parent's shared base field. No option is read, computed, shown, or
        # written - not even the one this round happens to also be scoped to structurally, since
        # the base bracket IS the vehicle bracket and carries no supplement of its own to touch.
        new_base = round(_num(vehicle_changes[0]["new"]), 2)
        parent[base_field] = new_base
        # Child and infant prices move with the adult price rather than being left at last
        # season's number, which would silently change the child discount. Per-vehicle transports
        # have no separate child/infant base at all - there is nothing here to scale.
        if per_pax and old_base > 0:
            ratio = new_base / old_base
            for key in ("baseChildrenPrice", "baseInfantPrice"):
                if _num(parent.get(key)) > 0:
                    parent[key] = round(_num(parent.get(key)) * ratio, 2)
        return {"transport": parent, "options": [], "write_kind": "vehicle"}

    # A supplement round: write ONLY the touched option(s)' own price entries - the parent record
    # is returned unchanged (still needed by apply_proposals' caller shape) but is never PUT for
    # this write kind (see apply_proposals). Every period this round did NOT touch (including
    # every season on every OTHER, untouched option) survives byte for byte.
    option_payloads = []
    touched_codes = sorted({c["code"] for c in supplement_changes})
    for option in options:
        if option["code"] not in touched_codes:
            continue
        this_option_changes = [c for c in supplement_changes if c["code"] == option["code"]]
        raw_entries = [p for p in ((option.get("raw") or {}).get("prices") or []) if isinstance(p, dict)]
        touched_ids = set()
        for c in this_option_changes:
            entry = _find_entry_for_period(option, c.get("start_date"), c.get("end_date"))
            if entry is not None:
                touched_ids.add(id(entry))
        # CONFIRMED REAL GAP (product owner, 2026-09-11): "it can have a price supplement for
        # different modalities and it can have different period of times but the time can not
        # overlap within the same modality" - a modality's `prices` list can hold more than one
        # entry, each its own non-overlapping date range. Every period this round is NOT writing
        # (a season the document didn't mention, or a season belonging to a different round)
        # survives here byte for byte.
        new_entries = [json.loads(json.dumps(e)) for e in raw_entries if id(e) not in touched_ids]
        for c in this_option_changes:
            existing = _find_entry_for_period(option, c.get("start_date"), c.get("end_date"))
            updated = dict(existing) if existing else {}
            updated["adultPriceSupplement"] = round(_num(c["new"]) - base_for_supplement_calc, 2)
            # CONFIRMED REAL PRODUCTION BUG (product owner, 2026-09-11, real bulk Apply failure -
            # see _current_base_option's docstring above): a NEW price entry (no existing entry
            # matched this period) had no startDate/endDate at all, and Travel Compositor's
            # TransportContractPrice requires both non-null on write. A period the DOCUMENT itself
            # stated a date for uses that date; otherwise the parent's own startDate/endDate (never
            # touched by this flow) are the correct values to carry forward, exactly matching how
            # builder.build_transport_payloads seeds a brand-new price entry at create time.
            if c.get("start_date"):
                updated["startDate"] = c["start_date"]
            elif not updated.get("startDate"):
                updated["startDate"] = parent.get("startDate") or ""
            if c.get("end_date"):
                updated["endDate"] = c["end_date"]
            elif not updated.get("endDate"):
                updated["endDate"] = parent.get("endDate") or "2049-12-31"
            new_entries.append(updated)
        payload = json.loads(json.dumps(option["raw"]))
        # DEFENSIVE FIX (2026-09-11, investigating a real report of one option's price update
        # apparently landing on a DIFFERENT modality): api_client.update_transport_option's own
        # docstring confirms there is NO option code in the PUT url - Travel Compositor decides
        # which option gets overwritten purely from the 'code' field INSIDE the payload body.
        # Pinning it explicitly to the SAME code this function already trusts removes any
        # dependency on the GET response's own body being correct.
        payload["code"] = option["code"]
        payload["prices"] = new_entries
        option_payloads.append({"code": option["code"], "payload": payload})
    return {"transport": parent, "options": option_payloads, "write_kind": "supplement"}


def supplement_calculation_examples(route: Dict[str, Any],
                                    changes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One worked example per changed modality that will be written as a price SUPPLEMENT rather
    than becoming this transport's own base price - the exact arithmetic the Apply step is about
    to do, shown before Publish rather than only discoverable afterwards in Travel Compositor.

    CONFIRMED REAL REQUEST (product owner, 2026-09-11, right after the base-modality
    confirmation question above shipped): "if a price is matching to a modality which will be
    added to the price supplement, the app shall give one example and show the human what the
    app would calculate and add to the product." Their own worked example from earlier the same
    day is exactly what this reproduces: "Sedan Modality would be base price and if I would
    update the Hiace the price difference must be calculated and the price must then be added to
    the price supplement" - i.e. new Hiace price 90 = base (Sedan) 40 + supplement 50.

    Iterates every CHANGE entry (not deduped by code), so a modality with multiple seasonal
    periods in one round gets its own worked example per period (product owner, 2026-09-11:
    "Hiace had three supplements because of a high season and peak season time"). Uses
    _current_base_option (the SAME function rebuild_prices calls, honouring
    route["base_bracket_override"] exactly the way it does) to find which changed option is the
    base and which are supplements, so this can never disagree with what Apply actually writes.
    Any change entry that IS the base option's own vehicle change is skipped - it's written
    directly, no supplement arithmetic involved. Empty for Transfers (which have no
    modality/supplement concept at all) and when nothing is changing."""
    if route.get("kind") == KIND_TRANSFER or not changes:
        return []
    options = [o for o in (route.get("options") or []) if not o.get("fetch_failed")]
    if len(options) < 2:
        return []  # a single modality is never expressed as a supplement against itself
    base_option = _current_base_option(options, forced_bracket=route.get("base_bracket_override"))
    new_by_code = {c["code"]: c["new"] for c in changes if c["code"] == base_option["code"]}
    base_new_price = round(_num(new_by_code.get(base_option["code"], base_option["unit_price"])), 2)
    out = []
    for c in changes:
        if c["code"] == base_option["code"]:
            continue
        supplement = round(_num(c["new"]) - base_new_price, 2)
        out.append({
            "code": c["code"], "name": c.get("name") or c["code"],
            "base_code": base_option["code"],
            "base_name": base_option.get("name") or base_option["code"],
            "base_price": base_new_price, "new_price": round(_num(c["new"]), 2),
            "supplement": supplement,
            "start_date": c.get("start_date"), "end_date": c.get("end_date"),
        })
    return out


def apply_proposals(client, supplier_id: str, proposals: List[Dict[str, Any]],
                    progress: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]:
    """Push the accepted proposals. Each record is PUT back whole, with only prices changed."""
    accepted = [p for p in proposals if p.get("accepted") and p.get("changes")]
    out = {"updated": [], "failed": [], "skipped": len(proposals) - len(accepted)}
    for n, proposal in enumerate(accepted):
        route = proposal["route"]
        if progress:
            progress(n + 1, len(accepted), route.get("name", ""))
        payloads = rebuild_prices(route, proposal["changes"])
        if not payloads["transport"]:
            out["failed"].append({
                "name": route.get("name"),
                # rebuild_prices names WHY when it refused on purpose (some option prices were
                # unreadable, no base designation, or a zero-supplement round), rather than
                # reporting the same "no readable modalities" for every refusal reason.
                "detail": payloads.get("blocked") or "no readable modalities",
            })
            continue
        # CONFIRMED FINAL TRANSPORT PRICE-STRUCTURE MODEL (product owner, 2026-09-11): "but if we
        # only update the Sedan price whey should the app touches even the Hiace price
        # supplement?" - a round writes EXACTLY ONE thing. write_kind "vehicle" means ONLY the
        # parent record's shared base field moves - no option is read, recomputed, or PUT at all,
        # not even the base bracket's own (supplement-free) entry. write_kind "supplement" means
        # ONLY the touched option(s)' own price entries move - the parent record is never PUT, so
        # its base field (and every OTHER modality's untouched supplement) is not even sent back
        # to Travel Compositor, let alone rewritten. This is also what a real prior data-loss
        # incident this design exists to prevent needed: "Error of the App as last update fucked
        # up and deleted the correct supplements" (product owner, 2026-09-11) - the old
        # unconditional "PUT parent, then PUT every listed option" behaviour is kept ONLY for
        # Transfer (write_kind absent - Transfer has no vehicle/supplement concept at all, see
        # _rebuild_transfer_prices), exactly as before this overhaul.
        write_kind = payloads.get("write_kind")
        updater = (client.update_transfer if route.get("kind") == KIND_TRANSFER
                   else client.update_transport)
        # CONFIRMED REAL DIAGNOSTIC NEED (product owner, 2026-09-11): a real bulk update reported
        # "3 transport(s) repriced" (every PUT returned 200, no "error" key) but Travel
        # Compositor's own admin Prices tab still showed the touched modality's supplement as
        # 0,00 afterwards - i.e. this app cannot currently tell "TC accepted and applied our
        # write" apart from "TC accepted the HTTP call but silently didn't persist it" or "our
        # write landed on the wrong option". Both possibilities were traced as far as static code
        # reading can go without live credentials in this sandbox - the only thing left that can
        # settle it is the EXACT request body we sent and the EXACT body TC handed back, captured
        # at the moment of a real failure. Every accepted/updated route now carries that under
        # "debug" so the review screen can show it without needing Postman.
        debug = {"write_kind": write_kind, "transport_request": None, "transport_response": None,
                 "option_requests": [], "option_responses": []}
        try:
            if write_kind != "supplement":
                debug["transport_request"] = payloads["transport"]
                res = updater(supplier_id, payloads["transport"])
                debug["transport_response"] = res
                if isinstance(res, dict) and "error" in res:
                    out["failed"].append({"name": route.get("name"),
                                          "detail": str(res.get("message") or res.get("error")),
                                          "debug": debug})
                    continue
            option_errors = []
            if write_kind != "vehicle":
                for opt in payloads["options"]:
                    debug["option_requests"].append({"code": opt["code"], "payload": opt["payload"]})
                    res = client.update_transport_option(supplier_id, route.get("id"), opt["payload"])
                    debug["option_responses"].append({"code": opt["code"], "response": res})
                    if isinstance(res, dict) and "error" in res:
                        option_errors.append(f"{opt['code']}: {res.get('message') or res.get('error')}")
            if option_errors:
                # The parent went through, so the base price has already moved. Saying so
                # matters: leaving it at "failed" would suggest nothing had changed.
                out["failed"].append({
                    "name": route.get("name"),
                    "detail": "the transport updated but " + "; ".join(option_errors)
                              + " — re-run to finish it",
                    "debug": debug})
            else:
                out["updated"].append({"name": route.get("name"),
                                       "changes": proposal["changes"], "debug": debug})
        except Exception as e:
            out["failed"].append({"name": route.get("name"),
                                  "detail": ai_extractor.friendly_error_message(e)})
    return out


def build_manual_adjustment_proposals(routes: List[Dict[str, Any]], mode: str, value: float
                                      ) -> List[Dict[str, Any]]:
    """CONFIRMED REAL PRODUCT DECISION (product owner, 2026-09-11, right after confirming the
    Sept 11 bulk write into TRANSPORT-423137/423138/423142/423134 had actually persisted
    correctly - see claude/transport-supplement-write-not-persisting-debug-capture-2026-09-11.md):
    "I will add the second modality by hand... we must then update in the future in bulk only the
    pricevehicle or the priceperpax. The modalities are not needed then... ignore the modalities."
    This is a deliberate SIMPLIFICATION, not a variant of the document-reading flow above: no
    rate sheet is read, no modality/option is fetched or written, no supplement is ever touched -
    only the parent Transport record's own shared vehiclePrice/baseAdultPrice field moves, exactly
    like a "vehicle" round in build_proposals/rebuild_prices, but the new number comes from doing
    arithmetic on the CURRENT live price instead of reading a document for it.

    CONFIRMED REAL FORMULA (product owner, 2026-09-11, verbatim worked examples): "human adds
    manually 10% the app must calculate the new price by currentprice * percentage, in this
    example 100*1,1=110" (mode="percentage", value=10 -> 100 * 1.10 = 110) "Or if number adds in
    absolute price and it says 12... 100+12=112" (mode="absolute", value=12 -> 100 + 12 = 112).
    A negative value works the same way in reverse (a price cut), never rejected here - only a
    resulting price at or below zero is refused, as an obvious data-entry mistake rather than a
    real price.

    CONFIRMED REAL SCOPE (product owner, follow-up answer, 2026-09-11): "One number for the whole
    batch" - the SAME mode/value is applied to every route passed in, each against ITS OWN
    current live price, not a single shared price. Which routes that batch actually is (all of a
    supplier's transports, or a hand-picked subset) is the caller's decision, made by which
    `routes` are passed in here - this function has no opinion on that.

    Returns one proposal dict per route: {"route", "name", "old", "new", "mode", "value",
    "blocked" (a reason string, or None), "accepted" (True only when unblocked and the price
    actually moves)} - the same "old -> new, human can accept or reject" shape every other
    proposal list in this module already uses, so the review screen can reuse the same pattern."""
    out = []
    for route in routes:
        per_pax = bool(route.get("price_per_pax", True))
        base_field = "baseAdultPrice" if per_pax else "vehiclePrice"
        old_base = _num((route.get("raw") or {}).get(base_field))
        if mode == "percentage":
            new_base = round(old_base * (1.0 + _num(value) / 100.0), 2)
        else:
            new_base = round(old_base + _num(value), 2)
        blocked = None
        if route.get("kind") == KIND_TRANSFER:
            # Belt-and-braces only - the UI never offers this mode for Transfer, which has no
            # single shared vehicle/base price field the way Transport does.
            blocked = "Manual adjustment is only for Transport, not Transfer."
        elif new_base <= 0:
            blocked = f"Computed price ({new_base}) is zero or negative - check the value."
        out.append({
            "route": route, "name": route.get("name"), "old": old_base, "new": new_base,
            "mode": mode, "value": _num(value), "blocked": blocked,
            "accepted": blocked is None and abs(new_base - old_base) >= 0.005,
        })
    return out


def rebuild_manual_adjustment(route: Dict[str, Any], new_base: float) -> Dict[str, Any]:
    """The parent payload for one manual vehicle/base-price adjustment (see
    build_manual_adjustment_proposals). Touches ONLY the shared base field (vehiclePrice or
    baseAdultPrice) - no option/modality is read or written at all, unlike rebuild_prices' own
    "vehicle" round which this mirrors. Same child/infant proportional scaling as that branch
    (product owner, 2026-09-11, confirmed keep it: a child discount must not silently change)."""
    per_pax = bool(route.get("price_per_pax", True))
    base_field = "baseAdultPrice" if per_pax else "vehiclePrice"
    old_base = _num((route.get("raw") or {}).get(base_field))
    parent = json.loads(json.dumps(route["raw"]))
    normalize_for_put(parent, "Transport")
    parent[base_field] = round(_num(new_base), 2)
    if per_pax and old_base > 0:
        ratio = _num(new_base) / old_base
        for key in ("baseChildrenPrice", "baseInfantPrice"):
            if _num(parent.get(key)) > 0:
                parent[key] = round(_num(parent.get(key)) * ratio, 2)
    return parent


def apply_manual_adjustments(client, supplier_id: str, proposals: List[Dict[str, Any]],
                             progress: Optional[Callable[[int, int, str], None]] = None
                             ) -> Dict[str, Any]:
    """Push the accepted manual %/absolute adjustments. Each round PUTs only the parent Transport
    record, with only its own base price (and, for a per-pax transport, child/infant scaled the
    same ratio) changed - see rebuild_manual_adjustment. Carries the same raw request/response
    "debug" capture apply_proposals grew after the 2026-09-11 write-not-persisting investigation,
    for the same reason: an HTTP 200 alone doesn't prove Travel Compositor actually applied it."""
    accepted = [p for p in proposals if p.get("accepted")]
    out = {"updated": [], "failed": [], "skipped": len(proposals) - len(accepted)}
    for n, proposal in enumerate(accepted):
        route = proposal["route"]
        if progress:
            progress(n + 1, len(accepted), route.get("name", ""))
        payload = rebuild_manual_adjustment(route, proposal["new"])
        debug = {"transport_request": payload, "transport_response": None}
        try:
            res = client.update_transport(supplier_id, payload)
            debug["transport_response"] = res
            if isinstance(res, dict) and "error" in res:
                out["failed"].append({"name": route.get("name"),
                                      "detail": str(res.get("message") or res.get("error")),
                                      "debug": debug})
                continue
            out["updated"].append({"name": route.get("name"), "old": proposal["old"],
                                   "new": proposal["new"], "debug": debug})
        except Exception as e:
            out["failed"].append({"name": route.get("name"),
                                  "detail": ai_extractor.friendly_error_message(e), "debug": debug})
    return out


def transport_price_consistency_report(routes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """CONFIRMED REAL REQUEST (product owner, 2026-09-11, right after the manual %/absolute
    adjustment shipped): "can the app help then, to identify price errors at least for contract
    transport... once a year we must review the new prices and we must identify price differences
    for the transport modalities... No upload from the app, but the app must be able to help and
    to calculate what the modalities usually must be." This is deliberately READ-ONLY - there is
    no proposal/accept/apply path here at all, unlike every other function in this module. It
    flags nothing as definitively wrong; it ranks routes by how far their own numbers sit from
    what this SAME supplier's other routes suggest is normal, and leaves the judgement call to
    the human, same as they already make by hand for a modality's own price.

    CONFIRMED MODEL (AskUserQuestion, same day):
      - The "usual" relationship between a route's vehicle/base bracket and each other bracket is
        treated as a roughly FIXED ABSOLUTE markup (not a ratio) - "A fixed absolute amount."
      - Compared ONLY within the SAME supplier's own routes, never pooled across suppliers -
        different suppliers can have completely different fleets/pricing without tripping each
        other's flags. Pass this function one supplier's routes at a time.
      - No hard pass/fail threshold - returns every comparable (route, bracket) row ranked by
        |deviation| descending, so the human eyeballs and decides what looks wrong.

    For each route with 2+ live brackets, the SMALLEST-pax bracket is treated as the vehicle/base
    (the same fallback convention build_proposals/rebuild_prices already use when no human
    designation exists), and every OTHER bracket's supplement is its own current live total price
    minus the vehicle bracket's current live total price - reading `unit_price` as already fetched
    (base + whichever price entry is active today), never re-deriving it, so this reports exactly
    what a human looking at Travel Compositor's own Prices tab would see right now. Every
    supplement is grouped by (min_pax, max_pax) SIGNATURE across the supplier's own routes (the
    one thing that means the same on every transport - modality codes don't), and the supplier's
    own MEDIAN supplement for that bracket is "typical" (median, not mean, so one wildly wrong
    entry can't drag its own baseline toward itself). A bracket signature only ONE route in the
    supplier has at all is skipped entirely - there is nothing to compare it against.

    Returns a list of {"route_id", "route_name", "option_code", "option_name", "bracket"
    (min_pax, max_pax), "vehicle_price", "modality_price", "supplement", "typical_supplement",
    "deviation" (this route's supplement minus the supplier's typical for that bracket),
    "deviation_pct" (None if typical is 0, to avoid a division by zero), "sample_size"} rows,
    sorted by |deviation| descending."""
    by_bracket: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
    for route in routes:
        if route.get("kind", KIND_TRANSPORT) == KIND_TRANSFER:
            continue  # this report is Transport-only - Transfer has no vehicle/modality concept
        options = [o for o in (route.get("options") or []) if not o.get("fetch_failed")]
        if len(options) < 2:
            continue  # nothing to compare a single-bracket transport's price against
        live_brackets = sorted({(o["min_pax"], o["max_pax"]) for o in options})
        vehicle_bracket = live_brackets[0]
        vehicle_option = next((o for o in options
                               if (o["min_pax"], o["max_pax"]) == vehicle_bracket), None)
        if vehicle_option is None:
            continue
        vehicle_price = _num(vehicle_option.get("unit_price"))
        for option in options:
            bracket = (option["min_pax"], option["max_pax"])
            if bracket == vehicle_bracket:
                continue
            modality_price = _num(option.get("unit_price"))
            supplement = round(modality_price - vehicle_price, 2)
            by_bracket.setdefault(bracket, []).append({
                "route_id": route.get("id"), "route_name": route.get("name"),
                "option_code": option.get("code"), "option_name": option.get("name", ""),
                "bracket": bracket, "vehicle_price": vehicle_price,
                "modality_price": modality_price, "supplement": supplement,
            })

    rows: List[Dict[str, Any]] = []
    for bracket, entries in by_bracket.items():
        if len(entries) < 2:
            continue  # only one route in this supplier has this bracket - no baseline to compare
        supplements = sorted(e["supplement"] for e in entries)
        n = len(supplements)
        mid = n // 2
        typical = supplements[mid] if n % 2 else round((supplements[mid - 1] + supplements[mid]) / 2, 2)
        for e in entries:
            deviation = round(e["supplement"] - typical, 2)
            rows.append({
                **e, "typical_supplement": typical, "deviation": deviation,
                "deviation_pct": (round(deviation / abs(typical) * 100.0, 1) if abs(typical) >= 0.005 else None),
                "sample_size": n,
            })
    rows.sort(key=lambda r: abs(r["deviation"]), reverse=True)
    return rows


def suggest_route_for_row(row_text: str, routes: List[Dict[str, Any]], limit: int = 5
                          ) -> List[Dict[str, Any]]:
    """Best guesses for which live transport a document row belongs to, for manual matching.

    CONFIRMED REAL RULE (product owner): a row that matched nothing must be something the
    "human shall manually be able to match... and add the price to it". Reuses the same
    similarity scoring the upload flow already uses to recognise an existing transport."""
    parts = [p.strip() for p in str(row_text or "").replace("→", "|").replace("->", "|").split("|")
             if p.strip()]
    dep = parts[0] if parts else str(row_text or "")
    arr = parts[1] if len(parts) > 1 else ""
    if routes and routes[0].get("kind") == KIND_TRANSFER:
        scored = transfer_matcher.suggest_existing_transfer_matches(
            dep, arr, [r["raw"] for r in routes], top_n=limit)
    else:
        scored = transport_matcher.suggest_existing_transport_matches(
            dep, arr, [r["raw"] for r in routes], top_n=limit)
    # The matcher reports the id under "transport_id", not "id".
    by_id = {r["id"]: r for r in routes}
    out = []
    for candidate in scored:
        route = by_id.get(candidate.get("transport_id") or candidate.get("transfer_id")
                          or candidate.get("id"))
        if route:
            out.append({"route": route, "score": candidate.get("score", 0)})
    return out
