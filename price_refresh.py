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
  * the parent record's baseAdultPrice / baseChildrenPrice / baseInfantPrice;
  * each option's prices[].adultPriceSupplement, which is added to the base.
So a modality's real price is base + its own supplement. Changing prices means recomputing
both together, which is why this module owns that arithmetic rather than leaving it to a
caller - see rebuild_prices().
"""

# Stamped on every delivery. app.py compares this against its own build string and says
# so on screen when they differ - a partial push (one file committed, another not) used to
# surface only as a traceback whose line numbers pointed at unrelated code.
MODULE_BUILD = "2026-08-30-hotel-matching-fixes"

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

import ai_extractor
import transfer_matcher
import transport_matcher

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
def option_unit_price(option: Dict[str, Any], base_adult: float) -> float:
    """What one passenger in this bracket actually costs: base plus this option's supplement."""
    supplement = 0.0
    for price in (option.get("prices") or []):
        if isinstance(price, dict):
            supplement = _num(price.get("adultPriceSupplement"))
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
    for i, record in enumerate(records):
        name = record.get("name") or ""
        if progress:
            progress(i + 1, len(records), name)
        base_adult = _num(record.get("baseAdultPrice"))
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
                "unit_price": option_unit_price(opt, base_adult),
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
            "price_per_pax": bool(record.get("pricePerPax")),
            "base_adult": base_adult,
            "base_child": _num(record.get("baseChildrenPrice")),
            "base_infant": _num(record.get("baseInfantPrice")),
            "options": sorted(options, key=lambda o: o.get("min_pax", 0)),
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
            brackets.append({
                "min_pax": int(_num(b.get("min_pax"), 1)),
                "max_pax": int(_num(b.get("max_pax"), 1)),
                "price": round(price, 2),
                "child_price": None if b.get("child_price") is None else round(_num(b.get("child_price")), 2),
                "infant_price": None if b.get("infant_price") is None else round(_num(b.get("infant_price")), 2),
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
def bracket_price_for(finding: Dict[str, Any], min_pax: int, max_pax: int,
                      minimum_pax: int) -> Optional[float]:
    """The new unit price for one EXISTING bracket, from what the document said.

    The solo bracket is checked FIRST, ahead of any exact match. Failing that: exact bracket
    match, then an overlapping one - a document that prices "2-9" still tells you what a live
    "2-6" bracket costs.

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
    if max_pax == 1 and minimum_pax > 1:
        base = next((b["price"] for b in brackets if b["min_pax"] == minimum_pax), None)
        if base is None:
            base = next((b["price"] for b in brackets if b["min_pax"] > 1), None)
        if base is None and brackets:
            base = brackets[0]["price"]
        return round(base * minimum_pax, 2) if base is not None else None
    for b in brackets:
        if b["min_pax"] == min_pax and b["max_pax"] == max_pax:
            return b["price"]
    for b in brackets:
        if b["min_pax"] <= max_pax and b["max_pax"] >= min_pax:
            return b["price"]
    return None


def build_proposals(routes: List[Dict[str, Any]],
                    findings: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One proposal per live route: what each modality costs now, and what it would become."""
    proposals = []
    for i, route in enumerate(routes):
        finding = findings.get(i) or {"found": False, "brackets": [], "confidence": "low",
                                      "note": "", "matched_row": "", "minimum_pax": 1,
                                      "currency": ""}
        changes, unchanged, missing = [], 0, 0
        for option in route["options"]:
            if option.get("fetch_failed"):
                continue
            new_price = bracket_price_for(finding, option["min_pax"], option["max_pax"],
                                          finding.get("minimum_pax", 1))
            if new_price is None:
                missing += 1
                continue
            if abs(new_price - option["unit_price"]) < 0.005:
                unchanged += 1
                continue
            changes.append({"code": option["code"], "min_pax": option["min_pax"],
                            "max_pax": option["max_pax"], "old": option["unit_price"],
                            "new": new_price, "name": option.get("name", "")})
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
        # CONFIRMED REAL BUG (audit, 2026-08-24): an option whose live price could not be READ
        # must block this whole route, not be quietly skipped.
        #
        # WHY THE WHOLE ROUTE: rebuild_prices derives ONE base price for the transport from the
        # WIDEST bracket and then expresses every other option as a supplement relative to it. It
        # used to compute that base from the SURVIVING options only, so a single transient GET
        # failure (and GETs are deliberately never retried - see api_client._request) had two
        # silent effects: the unread option kept its old supplement against a NEW base, becoming a
        # price nobody chose, and if the failed option happened to BE the widest bracket, the base
        # was taken from a narrower one - often the 1-pax solo rate - repricing every modality on
        # the transport. baseChildrenPrice/baseInfantPrice are then scaled by base/old_base, so the
        # error compounds into the child prices too.
        #
        # Nothing surfaced any of this: every UI site filters fetch_failed out without a word, and
        # apply_proposals reported success. stop_sales_tool.py (~500) already handles the identical
        # "couldn't read it" case correctly - naming it and excluding it from Apply - and this is
        # that same treatment.
        unreadable = [o.get("code") for o in route["options"] if o.get("fetch_failed")]
        if unreadable:
            status = "blocked_unreadable"
        proposals.append({
            "index": i, "route": route, "finding": finding, "changes": changes,
            "unchanged": unchanged, "missing": missing, "status": status,
            "currency_changed": currency_changed,
            "unreadable_options": unreadable,
            # Only genuine changes are pre-ticked. An accept-all button must not sweep up a
            # route the document never mentioned - nor one we couldn't fully read.
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


def rebuild_prices(route: Dict[str, Any], new_unit_prices: Dict[str, float]) -> Dict[str, Any]:
    """The payloads that put these prices live, keeping base and supplements consistent.

    A modality's price is base + its own supplement, so a new set of prices has to be split
    across the parent record and every option. The base is taken from the WIDEST bracket -
    the same rule the upload flow uses - so the common bracket carries no supplement and only
    genuine outliers (the solo surcharge) do. Sending an option's supplement without updating
    the base, or the reverse, would silently reprice every OTHER modality on the transport."""
    if route.get("kind") == KIND_TRANSFER:
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
    resolved = {o["code"]: round(float(new_unit_prices.get(o["code"], o["unit_price"])), 2)
                for o in options}
    widest = max(options, key=lambda o: (o["max_pax"] - o["min_pax"], -o["min_pax"]))
    base = resolved[widest["code"]]

    parent = json.loads(json.dumps(route["raw"]))
    old_base = _num(parent.get("baseAdultPrice"))
    parent["baseAdultPrice"] = base
    # Child and infant prices move with the adult price rather than being left at last
    # season's number, which would silently change the child discount.
    if old_base > 0:
        ratio = base / old_base
        for key in ("baseChildrenPrice", "baseInfantPrice"):
            if _num(parent.get(key)) > 0:
                parent[key] = round(_num(parent.get(key)) * ratio, 2)

    option_payloads = []
    for option in options:
        payload = json.loads(json.dumps(option["raw"]))
        supplement = round(resolved[option["code"]] - base, 2)
        if abs(supplement) < 0.005:
            payload["prices"] = []
        else:
            existing = (payload.get("prices") or [{}])[0]
            if not isinstance(existing, dict):
                existing = {}
            existing = dict(existing)
            existing["adultPriceSupplement"] = supplement
            payload["prices"] = [existing]
        option_payloads.append({"code": option["code"], "payload": payload,
                                "unit_price": resolved[option["code"]]})
    return {"transport": parent, "options": option_payloads}


def apply_proposals(client, supplier_id: str, proposals: List[Dict[str, Any]],
                    progress: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]:
    """Push the accepted proposals. Each record is PUT back whole, with only prices changed."""
    accepted = [p for p in proposals if p.get("accepted") and p.get("changes")]
    out = {"updated": [], "failed": [], "skipped": len(proposals) - len(accepted)}
    for n, proposal in enumerate(accepted):
        route = proposal["route"]
        if progress:
            progress(n + 1, len(accepted), route.get("name", ""))
        new_prices = {c["code"]: c["new"] for c in proposal["changes"]}
        payloads = rebuild_prices(route, new_prices)
        if not payloads["transport"]:
            out["failed"].append({
                "name": route.get("name"),
                # rebuild_prices names WHY when it refused on purpose (some option prices were
                # unreadable), rather than reporting the same "no readable modalities" for both
                # "this route has nothing" and "this route could not be safely repriced".
                "detail": payloads.get("blocked") or "no readable modalities",
            })
            continue
        updater = (client.update_transfer if route.get("kind") == KIND_TRANSFER
                   else client.update_transport)
        try:
            res = updater(supplier_id, payloads["transport"])
            if isinstance(res, dict) and "error" in res:
                out["failed"].append({"name": route.get("name"),
                                      "detail": str(res.get("message") or res.get("error"))})
                continue
            option_errors = []
            for opt in payloads["options"]:
                res = client.update_transport_option(supplier_id, route.get("id"), opt["payload"])
                if isinstance(res, dict) and "error" in res:
                    option_errors.append(f"{opt['code']}: {res.get('message') or res.get('error')}")
            if option_errors:
                # The parent went through, so the base price has already moved. Saying so
                # matters: leaving it at "failed" would suggest nothing had changed.
                out["failed"].append({
                    "name": route.get("name"),
                    "detail": "the transport updated but " + "; ".join(option_errors)
                              + " — re-run to finish it"})
            else:
                out["updated"].append({"name": route.get("name"),
                                       "changes": proposal["changes"]})
        except Exception as e:
            out["failed"].append({"name": route.get("name"),
                                  "detail": ai_extractor.friendly_error_message(e)})
    return out


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
