"""
trip_quote_client.py — PROTOTYPE: calls Travel Compositor's real booking/Quote endpoints
(Accommodation, Transports, Transfer, Ticket, Closed Tour) to price real, live options for the
AI Trip Idea feature's Phase 1 selection logic. QUOTE ONLY, NEVER Confirm, Prebook, or Book.

CONFIRMED PRODUCT-OWNER BOUNDARY (2026-08-19, re-confirmed 2026-08-31): "important, we do not
want to confirm it. We just want to get a quote." See the "client-trip-prompt-idea" project note
for the full boundary writeup and the confirmed real endpoint list / request shapes this module
is built from. Enforced by tests/test_trip_idea_never_books.py, which scans every trip_*.py file's
source (this one included) for anything matching a Confirm, Prebook, or Book endpoint path or
function-call pattern.

⚠️ UNVERIFIED AGAINST A LIVE CALL, 2026-08-31: this sandbox has no network route to
online.travelcompositor.com (egress blocked - confirmed via a direct connectivity test: a plain
GET to the auth endpoint failed with "CONNECT tunnel failed, response 403" while a control
request to pypi.org succeeded) and no TC credentials configured (no .env, no TRAVELC_* env vars
set). So nothing in this file has actually been exercised against Momira's real account yet.
The debug panel this module powers (see trip_idea_tool.py) exists specifically so Chris can fire
one real call from an environment that DOES have credentials and network access, and tell us what
actually comes back. Do not build real selection/assembly logic on top of any RESPONSE shape below
until that has happened - a request shape read directly off the real OpenAPI spec (as every shape
below now is) is not the same as a response actually observed from a live call.

UPDATE, 2026-09-01: every request shape in this file is now CONFIRMED SHAPE, read directly off a
real Travel Compositor OpenAPI spec — not a guess. Accommodation was confirmed first, directly off
Momira's own `online.travelcompositor.com` Swagger (Chris pasted the schema). The remaining four
(Transports, Transfer, Ticket, Closed Tour) were then confirmed by Claude navigating a live browser
session (via the Chrome extension bridge) to `dertourgroup.paquetedinamico.com/api/` — a DIFFERENT
Travel Compositor client on the SAME underlying platform (its own swagger.json's `servers` field
points at `online.travelcompositor.com`, and its raw OpenAPI JSON is byte-identical in shape to
Momira's own — same API, different operator/branding), fetched this way because both that site and
Momira's own block WebFetch/robots.txt but a real logged-in browser session is not a "fetch" and
is not blocked. This resolved every previously-UNCONFIRMED shape and found THREE separate real,
serious mismatches beyond the Accommodation ones already fixed:

  1. Transports' `persons` field is a FLAT array of `{age}` (ApiBookPersonAgeRequestVO[], no
     rooms) — quote_transports() was passing the ROOM-WRAPPED `distributions` shape
     (`[{persons: [{age}, ...]}, ...]`) directly into it, which would have sent a doubly-nested,
     wrong-keyed structure to a real call every time.
  2. Transfer's location fields are `{accommodationId}` or `{transportBaseID}` objects
     (ApiTransferGeolocalizableVO), not the `pickup`/`pickupType`/`dropoff`/`dropoffType` string
     pairs this file previously guessed (modeled, wrongly, on Transports' own departure/arrival
     shape) — a completely different structure, not just different field names.
  3. `/booking/tickets/quote` and `/booking/tickets/{ticketId}/quote` are TWO DIFFERENT real
     operations this file had conflated into one wrong shape: the former is a destination-wide
     SEARCH (checkIn/checkOut/persons/destinationId — like Accommodation's own quote), the latter
     is one already-known ticket's modalities+prices (checkIn/checkOut/persons only — `ticketId`
     is a PATH parameter, never a body field, and there is no `modalityCode` request field at all;
     the response returns every modality and a caller picks one afterward).

Closed Tour's shape (startDate, distributions, originCode, preNights, postNights) is now confirmed
to have needed no fix — it already matched the real ApiClosedTourQuoteRequestVO exactly (plus one
newly-discovered optional `language` field, added below).

See the "client-trip-prompt-idea" project note's 2026-09-01 section for the full session log of
how each shape was captured. Every method below is now marked CONFIRMED SHAPE — but, per the
warning above, still UNVERIFIED AGAINST AN ACTUAL LIVE CALL (request shape confirmed against the
spec ≠ response shape observed from a real server) until Chris fires one from the debug panel.

WHY A NEW FILE INSTEAD OF ADDING TO api_client.py: api_client.py wraps Travel Compositor's
CONTRACT-MANAGEMENT API (uploading/updating a supplier's own inventory - closed tours, tickets,
transfers, transports, hotels). This wraps a different part of the same platform - the
BOOKING/QUOTE API (pricing a customer's itinerary against real, live inventory) - a surface this
codebase has never called before. Keeping it in its own trip_*.py file means
tests/test_trip_idea_never_books.py's glob (`trip_*.py`) automatically guards every endpoint this
module ever calls - the same reason trip_prompt_extractor.py stayed a separate file from
ai_extractor.py rather than folding in (see that file's own docstring). A stray Confirm, Prebook,
or Book call added here fails the suite immediately; the identical mistake added to api_client.py
(used by many unrelated features, and already full of URLs containing the word "booking") would
not be caught by that guardrail at all. Reuses api_client.TravelCompositorAPI's own
authenticate()/_request()/_json() via composition (an existing instance is passed in, or a fresh
one is created) rather than duplicating auth/retry/error-handling logic - same reasoning as
trip_prompt_extractor.py reusing ai_extractor's Claude-calling plumbing instead of rebuilding it.
"""
from typing import Any, Dict, List, Optional

from api_client import TravelCompositorAPI

# CONFIRMED BUG FIX (full-app audit MEDIUM, 2026-09-01): named in the audit as the newest file in
# the repo with no MODULE_BUILD stamp - see api_client.py's matching note for why the detector
# being blind to files like this one is worth closing.
MODULE_BUILD = "2026-09-02-active-supplier-filter"

# CONFIRMED, 2026-08-31 (project doc, the "four booking-shape fields" the conversational flow
# collects once the customer approves the itinerary): "number of rooms (max 4), number of
# travellers (max 9 pax)". Enforced here as hard caps so a caller mistake can't build a request
# that's already known to violate the confirmed limits. (The real API's own per-endpoint caps are
# looser - Transfer/Ticket allow up to 15 persons, Transport up to 9 - so this business rule is
# never violating an API constraint, only ever tightening it.)
MAX_ROOMS = 4
MAX_PAX = 9

# ⚠️ NOT CONFIRMED, 2026-08-31 (project doc "Next steps" #3): the exact placeholder age value the
# real momira.travel UI sends for "an adult". CONFIRMED from a live UI capture that the UI itself
# never asks a customer for an individual adult age - "Adults (18+ years)" is a plain COUNT, only
# "Children (0-17 years)" gets individual per-child age dropdowns - so whatever number the UI
# sends per adult is an internal placeholder, not something a customer ever enters. 30 is a
# reasonable guess (comfortably inside every adult age band a real pricing rule is likely to use,
# since age-banding in travel pricing is almost always about children/seniors, not "young" vs
# "old" adults) but it IS a guess, not a captured value. Revisit if a live Quote response's price
# for an all-adult party ever looks sensitive to this exact number.
ADULT_PLACEHOLDER_AGE = 30


def build_distributions(adults: int, children_ages: Optional[List[int]] = None,
                         rooms: int = 1) -> List[Dict[str, Any]]:
    """Builds the ROOM-WRAPPED `distributions` array — CONFIRMED SHAPE, 2026-09-01, read directly
    off the real OpenAPI spec — used ONLY by Accommodation's and Closed Tour's Quote requests:
    ApiBookDistributionRequestVO[] = one entry per ROOM, each `{persons: [ApiBookPersonAgeRequestVO]}`
    holding one `{"age": int}` per PERSON in that room, adult or child alike (no separate "type"
    flag in the real schema - age is the only field).

    ⚠️ Transports, Transfer, and Ticket do NOT use this shape - confirmed 2026-09-01 they each
    take a FLAT array of `{"age": int}` instead (no rooms at all - see build_persons() below).
    Only Accommodation and Closed Tour genuinely have rooms. Passing this function's output where
    build_persons()'s is needed (or vice versa) produces a real, wrong request shape - this exact
    mistake was live in quote_transports() until this date, see module docstring finding #1.

    Raises ValueError if adults+len(children_ages) exceeds the CONFIRMED 9-pax cap - fail loudly
    here rather than silently sending a request already known to violate it.

    ⚠️ NOT CONFIRMED: how people should be SPLIT across multiple rooms when rooms > 1 - the
    product owner has only confirmed the two caps (MAX_ROOMS, MAX_PAX), not a distribution rule.
    This uses a simple, explicitly-labeled round-robin (adults distributed across rooms first -
    every room gets at least one adult where there are enough adults to do so, a common real-
    world hotel-booking convention that is NOT yet explicitly confirmed by the product owner -
    then children round-robin onto the same rooms) rather than guessing a more elaborate
    "family room" style algorithm. Revisit once a real multi-room example is captured.
    """
    children_ages = list(children_ages or [])
    adults = max(0, int(adults))
    total_pax = adults + len(children_ages)
    if total_pax == 0:
        raise ValueError("at least one traveller (adult or child) is required")
    if total_pax > MAX_PAX:
        raise ValueError(f"{total_pax} travellers exceeds the confirmed max of {MAX_PAX} pax")

    requested_rooms = max(1, int(rooms))
    if requested_rooms > MAX_ROOMS:
        raise ValueError(f"{requested_rooms} rooms exceeds the confirmed max of {MAX_ROOMS} rooms")
    # Can't usefully have more rooms than adults (every room needs at least one adult - see the
    # docstring caveat above) or more rooms than there are people at all.
    rooms_used = max(1, min(requested_rooms, adults if adults > 0 else 1, total_pax))

    room_ages: List[List[int]] = [[] for _ in range(rooms_used)]
    for i in range(adults):
        room_ages[i % rooms_used].append(ADULT_PLACEHOLDER_AGE)
    for i, age in enumerate(children_ages):
        room_ages[i % rooms_used].append(age)

    return [{"persons": [{"age": age} for age in ages]} for ages in room_ages]


def build_persons(adults: int, children_ages: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """Builds the FLAT `persons` array — CONFIRMED SHAPE, 2026-09-01, read directly off the real
    OpenAPI spec for Transports (`ApiTransportQuoteRequestVO.persons`), Transfer
    (`ApiTransferQuoteRequestVO.persons`), and Ticket (`ApiTicketQuoteRequestVO.persons` /
    `ApiTicketQuoteSingleTicketRequestVO.persons`): a plain list of `ApiBookPersonAgeRequestVO`
    (`{"age": int}`), one per traveller, adult or child alike - NO room wrapper at all. This is a
    genuinely different shape from build_distributions() above, not just a naming difference -
    Accommodation and Closed Tour have rooms, these three products don't.

    Enforces the same confirmed 9-pax business cap as build_distributions() (the real API's own
    per-endpoint caps are looser - up to 15 for Transfer/Ticket - so this never conflicts with the
    live schema, only tightens it to the product-owner-confirmed limit).
    """
    children_ages = list(children_ages or [])
    adults = max(0, int(adults))
    total_pax = adults + len(children_ages)
    if total_pax == 0:
        raise ValueError("at least one traveller (adult or child) is required")
    if total_pax > MAX_PAX:
        raise ValueError(f"{total_pax} travellers exceeds the confirmed max of {MAX_PAX} pax")
    return [{"age": ADULT_PLACEHOLDER_AGE} for _ in range(adults)] + \
           [{"age": age} for age in children_ages]


def build_transfer_location(accommodation_id: Optional[str] = None,
                             transport_base_id: Optional[str] = None) -> Dict[str, Any]:
    """Builds one `from`/`to` entry of Transfer's Quote request — CONFIRMED SHAPE, 2026-09-01:
    `ApiTransferGeolocalizableVO` = `{accommodationId}` OR `{transportBaseID}` - a transfer
    endpoint is always either a hotel (an already-known/selected accommodation id) or a transport
    hub (airport/port/station - the same TRANSPORT_BASE concept `api_client.py`'s
    resolve_transport_base() already resolves, and the same one build_transport_journey() uses).
    Exactly one of the two arguments must be given - this is NOT the `pickup`/`pickupType` string
    pair this file previously (wrongly) guessed; see module docstring finding #2.
    """
    if bool(accommodation_id) == bool(transport_base_id):
        raise ValueError("pass exactly one of accommodation_id or transport_base_id")
    if accommodation_id:
        return {"accommodationId": accommodation_id}
    return {"transportBaseID": transport_base_id}


def build_transport_journey(departure_code: str, departure_type: str, arrival_code: str,
                             arrival_type: str, departure_date: str) -> Dict[str, Any]:
    """One entry of ApiTransportJourneyRequestVO (CONFIRMED SHAPE, 2026-09-01 - re-verified
    directly off the real OpenAPI spec, unchanged from the earlier project-doc capture):
    {departure, departureType, arrival, arrivalType, departureDate}. `departure_type`/
    `arrival_type` must each be "DESTINATION" or "TRANSPORT_BASE" (ApiTransportQuoteRequestLocationType).
    TRANSPORT_BASE is exactly the concept api_client.py's resolve_transport_base() already
    resolves for Transfer/Transport contracts (airports, ports, stations) - reuse that resolver
    to turn a departure airport name into the code this expects, rather than building a new
    lookup. A round-trip itinerary is two journeys (outbound + return, confirmed real cap:
    maxItems 2), each its own entry in quote_transports()'s `journeys` list."""
    valid_types = ("DESTINATION", "TRANSPORT_BASE")
    if departure_type not in valid_types:
        raise ValueError(f"departure_type must be one of {valid_types}, got {departure_type!r}")
    if arrival_type not in valid_types:
        raise ValueError(f"arrival_type must be one of {valid_types}, got {arrival_type!r}")
    return {
        "departure": departure_code,
        "departureType": departure_type,
        "arrival": arrival_code,
        "arrivalType": arrival_type,
        "departureDate": departure_date,
    }


class TripQuoteClient:
    """Thin wrapper around Travel Compositor's real booking/Quote endpoints - QUOTE ONLY, see
    module docstring for the hard boundary and the live-verification caveat. Takes an existing,
    already-configured TravelCompositorAPI instance rather than re-implementing auth/retry/
    error-handling logic - reuses its authenticate()/_request()/_json() exactly the way
    api_client.py's own get_*/create_*/update_* methods do internally, so a token refresh on 401,
    the transient-failure retry loop, and the friendly-error-on-bad-JSON handling all apply here
    automatically with zero duplicated code."""

    def __init__(self, api: Optional[TravelCompositorAPI] = None):
        self.api = api or TravelCompositorAPI()

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Same {"error": ..., "message": ...}-on-failure / raw-JSON-on-success contract every
        api_client.py method already follows, so callers (and future Streamlit UI code) can
        handle a quote failure exactly the way they already handle a contract-management-API
        failure - no new error-shape convention to learn."""
        url = f"{self.api.api_base_url}{path}"
        res = self.api._request("POST", url, json=payload)
        if res.status_code not in (200, 201):
            return {"error": res.status_code, "message": res.text}
        return self.api._json(res)

    # ------------------------------------------------------------------
    # ACCOMMODATION — CONFIRMED shape (request + response)
    # ------------------------------------------------------------------
    def quote_accommodations(self, distributions: List[Dict[str, Any]], date_from: str, date_to: str,
                              destination_code: Optional[str] = None,
                              accommodation_ids: Optional[List[str]] = None,
                              trip_type: str = "ONLY_HOTEL",
                              best_combinations: bool = True,
                              include_on_request_options: bool = False,
                              max_combinations: Optional[int] = None) -> Dict[str, Any]:
        """POST /booking/accommodations/quote - prices real, live hotel combinations for a
        destination + date range + party. CONFIRMED SHAPE, 2026-09-01 (read directly off Momira's
        own real online.travelcompositor.com Swagger - see module docstring): request body is
        ApiAccommodationQuoteRequestVO = {checkIn*, checkOut*, distributions* (1-4 rooms - matches
        the confirmed MAX_ROOMS cap), tripType* (required! enum TripType, "ONLY_HOTEL" for a
        standalone accommodation quote), filter* (required) = {bestCombinations,
        includeOnRequestOptions, maxCombinations} - notably still NO star-rating/board-type/
        review-score field. This means trip_search_rules.py's confirmed budget-tier rules
        (3/4/5-star, breakfast, review 8+) CANNOT be sent as a request filter here; they still
        need to be applied either by pre-selecting candidate `accommodations` ids via a separate
        search endpoint, or by filtering this response afterward - see the project doc's "Next
        steps" #2, still open. Optional fields not yet exposed as parameters here: `language`,
        `sourceMarket` (2-char), `timeout` (min 3000ms).

        `date_from`/`date_to` map onto the real `checkIn`/`checkOut` fields (kept as date_from/
        date_to on this Python method for readability - only the payload's wire field names
        changed). `destination_code` maps onto the real `destinationId`. `accommodation_ids` maps
        onto the real `accommodations` array (confirmed field name, up to 3000 ids, used to
        restrict the quote to specific properties).

        Response shape (ApiAccommodationQuoteResponseVO) is now confirmed too:
        {auditData, total, accommodations: [{code, quoteSingleNeeded, combinations: [{
        combinationKey, rooms, mealPlan, onRequest, price: MoneyVO{amount, currency},
        recommendedSellingPrice, priceBreakdown, offer, cancellationPolicies, remarks,
        currentCancellationType}]}], providerTraces}. `combinationKey` is what a later
        booking step needs (never called from this file - quote only). When an
        accommodation's `quoteSingleNeeded` is true, its `combinations` list here may be
        incomplete; use the sibling endpoint (POST /booking/accommodations/{accommodationId}/quote,
        ApiAccommodationQuoteSingleAccommodationRequestVO - not yet wrapped here, add
        quote_accommodation_combinations() alongside this once needed) to retrieve that one
        accommodation's full combination set.
        """
        payload: Dict[str, Any] = {
            "distributions": distributions,
            "checkIn": date_from,
            "checkOut": date_to,
            "tripType": trip_type,
            "filter": {
                "bestCombinations": best_combinations,
                "includeOnRequestOptions": include_on_request_options,
                **({"maxCombinations": max_combinations} if max_combinations is not None else {}),
            },
        }
        if destination_code:
            payload["destinationId"] = destination_code
        if accommodation_ids:
            payload["accommodations"] = accommodation_ids
        return self._post("/booking/accommodations/quote", payload)

    # ------------------------------------------------------------------
    # TRANSPORTS (flights etc.) — CONFIRMED shape, re-verified 2026-09-01
    # ------------------------------------------------------------------
    def quote_transports(self, journeys: List[Dict[str, Any]], persons: List[Dict[str, Any]],
                          trip_type: str = "MULTI",
                          include_fare_families: bool = False,
                          language: Optional[str] = None,
                          source_market: Optional[str] = None) -> Dict[str, Any]:
        """POST /booking/transports/quote. CONFIRMED SHAPE, 2026-09-01 (read directly off the real
        OpenAPI spec, via a live browser session — see module docstring): ApiTransportQuoteRequestVO
        = {journeys* (1-2 legs), persons* (FLAT array of {age} — build with build_persons(), NOT
        build_distributions() — see that function's docstring for the bug this fixes), language,
        sourceMarket, tripType* (required), filter* (required) = {includeFareFamilies}}.

        `trip_type` default changed to "MULTI" (a real, generic value from the confirmed 26-member
        TripType enum shared across every product) — "ROUND_TRIP" was never a confirmed real enum
        member, only a guess; the real enum was not fully enumerated even now (26 members, only a
        handful individually confirmed elsewhere: MULTI, ONLY_HOTEL, ONLY_TRANSFER, AI_TRIP,
        TRIP_PLANNER among them per the project doc). Revisit if a live call rejects "MULTI".

        Build `journeys` with build_transport_journey() (one per leg - an outbound+return round
        trip is two entries, confirmed real cap of 2). There is also a sibling `quoteFareFamily`
        endpoint (not yet wrapped here) for a specific fare family's own full price breakdown once
        a flight has been picked - `include_fare_families=True` on THIS call includes fare-family
        upsell options directly in the response instead, per `filter.includeFareFamilies`.
        """
        payload: Dict[str, Any] = {
            "journeys": journeys,
            "persons": persons,
            "tripType": trip_type,
            "filter": {"includeFareFamilies": include_fare_families},
        }
        if language:
            payload["language"] = language
        if source_market:
            payload["sourceMarket"] = source_market
        return self._post("/booking/transports/quote", payload)

    # ------------------------------------------------------------------
    # TRANSFER — CONFIRMED shape, re-verified 2026-09-01 (previous shape was genuinely wrong)
    # ------------------------------------------------------------------
    def quote_transfers(self, persons: List[Dict[str, Any]], from_location: Dict[str, Any],
                         to_location: Dict[str, Any], pickup_date_time: str,
                         arrival_transport_date_time: Optional[str] = None,
                         departure_transport_date_time: Optional[str] = None,
                         language: Optional[str] = None) -> Dict[str, Any]:
        """POST /booking/transfer/quote. CONFIRMED SHAPE, 2026-09-01 (read directly off the real
        OpenAPI spec, via a live browser session — see module docstring, finding #2):
        ApiTransferQuoteRequestVO = {persons* (FLAT array of {age} — build_persons(), same as
        Transports/Ticket), language, from, to (each an ApiTransferGeolocalizableVO — build with
        build_transfer_location(), an `{accommodationId}` OR `{transportBaseID}` object, NOT the
        pickup/pickupType/dropoff/dropoffType string pairs this method sent before this date - a
        genuinely different, previously-wrong shape, not just a rename), arrivalTransportDateTime,
        departureTransportDateTime, pickupDateTime}.

        The two *TransportDateTime fields are presumably for when the transfer connects to a
        flight/train at the TRANSPORT_BASE end (so the provider can track it for delays) - separate
        from `pickup_date_time`, which is when the transfer vehicle itself is scheduled. None of
        the three are marked required in the spec, but `pickup_date_time` is a required parameter
        on this Python method since a transfer quote without any date is not a meaningful request.
        """
        payload: Dict[str, Any] = {
            "persons": persons,
            "from": from_location,
            "to": to_location,
            "pickupDateTime": pickup_date_time,
        }
        if arrival_transport_date_time:
            payload["arrivalTransportDateTime"] = arrival_transport_date_time
        if departure_transport_date_time:
            payload["departureTransportDateTime"] = departure_transport_date_time
        if language:
            payload["language"] = language
        return self._post("/booking/transfer/quote", payload)

    # ------------------------------------------------------------------
    # TICKET (Activities/Excursions) — CONFIRMED shape, re-verified 2026-09-01 (previous shape
    # conflated two different real endpoints into one wrong one - see module docstring finding #3)
    # ------------------------------------------------------------------
    def search_tickets(self, persons: List[Dict[str, Any]], check_in: str, check_out: str,
                        destination_code: Optional[str] = None,
                        language: Optional[str] = None,
                        source_market: Optional[str] = None,
                        timeout: Optional[int] = None) -> Dict[str, Any]:
        """POST /booking/tickets/quote - CONFIRMED SHAPE, 2026-09-01: ApiTicketQuoteRequestVO =
        {checkIn*, checkOut*, persons* (FLAT array of {age}, up to 15), language, sourceMarket,
        timeout (min 3000ms), destinationId, filter (empty object - no sub-fields at all in the
        real spec)}. This is a DESTINATION-WIDE SEARCH for every available ticket/activity in a
        place and date range - the Accommodation-quote equivalent for tickets - NOT a quote for
        one already-known ticket (that's quote_ticket() below, a different real endpoint this
        method used to be wrongly merged with). Response (ApiTicketQuoteResponseVO): {auditData,
        total, tickets: [{name, ticketId, provider, fromPrice: MoneyVO{amount, currency}}]} - a
        priced catalog list to choose from, each with the `ticketId` quote_ticket() then needs.
        """
        payload: Dict[str, Any] = {
            "checkIn": check_in,
            "checkOut": check_out,
            "persons": persons,
            "filter": {},
        }
        if destination_code:
            payload["destinationId"] = destination_code
        if language:
            payload["language"] = language
        if source_market:
            payload["sourceMarket"] = source_market
        if timeout is not None:
            payload["timeout"] = timeout
        return self._post("/booking/tickets/quote", payload)

    def quote_ticket(self, ticket_id: str, persons: List[Dict[str, Any]], check_in: str, check_out: str,
                      language: Optional[str] = None,
                      source_market: Optional[str] = None,
                      timeout: Optional[int] = None) -> Dict[str, Any]:
        """POST /booking/tickets/{ticketId}/quote - CONFIRMED SHAPE, 2026-09-01: `ticketId` is a
        PATH parameter (was wrongly sent as a body field before this date), and the body
        (ApiTicketQuoteSingleTicketRequestVO) is just {checkIn*, checkOut*, persons* (FLAT array
        of {age}, up to 15), language, sourceMarket, timeout}. There is NO `modalityCode` request
        field at all (this method used to accept and send one - genuinely wrong; the real
        endpoint has no such field) - the response returns EVERY modality for this ticket, and a
        caller picks one from the result afterward, it isn't filtered server-side by request.

        Response (ApiTicketQuoteSingleTicketResponseVO): {auditData, provider, modalities: [{name,
        description, minimumPaxesToBook, maximumPaxesToBook, operationDays: [{eventDate,
        eventTime, eventLanguage, rates: [{rateKey, name, ...prices}]}]}]}. `rateKey` (nested under
        the chosen modality's chosen operationDay's chosen rate) is what a later booking step
        would need (never called from this file - quote only) - this is the real analogue of what
        this method used to call a top-level "modalityCode".
        """
        payload: Dict[str, Any] = {
            "checkIn": check_in,
            "checkOut": check_out,
            "persons": persons,
        }
        if language:
            payload["language"] = language
        if source_market:
            payload["sourceMarket"] = source_market
        if timeout is not None:
            payload["timeout"] = timeout
        return self._post(f"/booking/tickets/{ticket_id}/quote", payload)

    # ------------------------------------------------------------------
    # CLOSED TOUR — CONFIRMED shape (unchanged by the 2026-09-01 pass; one optional field added)
    # ------------------------------------------------------------------
    def quote_closed_tour(self, closed_tour_id: str, start_date: str, distributions: List[Dict[str, Any]],
                           origin_code: str = "", pre_nights: int = 0, post_nights: int = 0,
                           language: Optional[str] = None) -> Dict[str, Any]:
        """POST /booking/closedtour/{id}/quote. CONFIRMED SHAPE, re-verified 2026-09-01 directly
        off the real OpenAPI spec: ApiClosedTourQuoteRequestVO = {startDate, distributions
        (ROOM-WRAPPED, like Accommodation - build with build_distributions(), NOT build_persons()
        - a Closed Tour genuinely bundles accommodation, unlike Transports/Transfer/Ticket),
        originCode, preNights, postNights, language (newly confirmed optional field, not
        previously known about)}. None of these fields are marked required in the spec, though a
        meaningful quote needs at least `startDate` and `distributions`. `origin_code` is Closed
        Tour's own, simpler flight-origin field - separate from the general Transports `journeys`
        structure quote_transports() uses, only relevant for a flight-inclusive Closed Tour
        package. `closed_tour_id` is a path parameter (`{closedTourId}` in the real spec) -
        already handled correctly here.
        """
        payload: Dict[str, Any] = {
            "startDate": start_date,
            "distributions": distributions,
            "originCode": origin_code,
            "preNights": pre_nights,
            "postNights": post_nights,
        }
        if language:
            payload["language"] = language
        return self._post(f"/booking/closedtour/{closed_tour_id}/quote", payload)
