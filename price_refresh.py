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
MODULE_BUILD = "2026-08-14-price-refresh-skip-duplicate-product-type-question"

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

import ai_extractor
import transfer_matcher
import transport_matcher

# The two product types this flow can refresh. Both are priced per occupancy and both arrive
# on the same kind of rate sheet, but they store the numbers very differently - see
# load_supplier_products() and rebuild_prices().
KIND_TRANSPORT = "Transport"
KIND_TRANSFER = "Transfer"

PRICE_LOOKUP_SYSTEM_PROMPT = """You are reading a supplier's rate sheet to find the NEW PRICE for routes that
already exist in a booking system. You are NOT deciding which products exist - that list is given to you and
it is correct. Your only job is to find each one's price in the document.

You will be given a numbered list of ROUTES, each with the passenger brackets it is sold in, and then the
document. For each route, report the price the document states for each bracket.

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

PRICES:
- Report the number exactly as the document states it. Never convert a currency, never apply a discount,
  never interpolate a bracket the document does not price.
- "per person, minimum 2 pax" means the 2+ bracket takes the stated number. A 1-pax bracket, where one
  exists, is that number times the minimum - but do NOT calculate it yourself, just report the stated
  per-person price and set minimum_pax to what the document says.
- If the document does not price a route at all, say so with "found": false. That is a useful, correct
  answer - a route the supplier dropped this season should not be guessed at.
- If you are unsure which row a route matches, set "confidence": "low" and say why in "note". A human
  confirms every price before anything is written, so an honest doubt is far more useful than a guess
  presented as fact.

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
                                     max_tokens=8192) or {}
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
# Turning a finding into a proposal
# ----------------------------------------------------------------------
def bracket_price_for(finding: Dict[str, Any], min_pax: int, max_pax: int,
                      minimum_pax: int) -> Optional[float]:
    """The new unit price for one EXISTING bracket, from what the document said.

    Exact bracket match first. Failing that, an overlapping one - a document that prices
    "2-9" still tells you what a live "2-6" bracket costs. The solo bracket is the special
    case: on a per-person rate with a minimum party size, one passenger pays the per-person
    rate times that minimum, which is the rule the upload flow already applies."""
    if not finding.get("found"):
        return None
    brackets = finding.get("brackets") or []
    for b in brackets:
        if b["min_pax"] == min_pax and b["max_pax"] == max_pax:
            return b["price"]
    if max_pax == 1 and minimum_pax > 1:
        base = next((b["price"] for b in brackets if b["min_pax"] == minimum_pax), None)
        if base is None and brackets:
            base = brackets[0]["price"]
        return round(base * minimum_pax, 2) if base is not None else None
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
        proposals.append({
            "index": i, "route": route, "finding": finding, "changes": changes,
            "unchanged": unchanged, "missing": missing, "status": status,
            "currency_changed": currency_changed,
            # Only genuine changes are pre-ticked. An accept-all button must not sweep up a
            # route the document never mentioned.
            "accepted": status == "changed",
        })
    return proposals


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
            out["failed"].append({"name": route.get("name"), "detail": "no readable modalities"})
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
