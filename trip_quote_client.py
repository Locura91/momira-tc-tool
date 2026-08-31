"""
trip_quote_client.py — PROTOTYPE: calls Travel Compositor's real booking/Quote endpoints
(Accommodation, Transports, Transfer, Ticket, Closed Tour) to price real, live options for the
AI Trip Idea feature's Phase 1 selection logic. QUOTE ONLY, NEVER Confirm, Prebook, or Book.

CONFIRMED PRODUCT-OWNER BOUNDARY (2026-08-19, re-confirmed 2026-08-31): "important, we do not
want to confirm it. We just want to get a quote." See the "client-trip-prompt-idea" project note
for the full boundary writeup and the confirmed real endpoint list / request shapes this module
is built from (read from a live Swagger of a same-platform operator,
dertourgroup.paquetedinamico.com, since momira.travel's own API docs require login and both
block automated fetching via robots.txt). Enforced by tests/test_trip_idea_never_books.py, which
scans every trip_*.py file's source (this one included) for anything matching a
Confirm, Prebook, or Book endpoint path or function-call pattern.

⚠️ UNVERIFIED AGAINST A LIVE CALL, 2026-08-31: this sandbox has no network route to
online.travelcompositor.com (egress blocked - confirmed via a direct connectivity test: a plain
GET to the auth endpoint failed with "CONNECT tunnel failed, response 403" while a control
request to pypi.org succeeded) and no TC credentials configured (no .env, no TRAVELC_* env vars
set). So nothing in this file has actually been exercised against Momira's real account yet. The
URL paths and request-body field names below are transcribed as exactly as the project doc
captured them - themselves read from a real OpenAPI spec on the same TC platform, but for a
DIFFERENT operator - so they're the best-available guess, not a confirmed-working integration.
Methods below are marked CONFIRMED SHAPE (the project doc captured the exact field names) or
⚠️ UNCONFIRMED SHAPE (the endpoint's existence is confirmed, its body is a reasonable guess) -
see each method's own docstring. The debug panel this module powers (see trip_idea_tool.py)
exists specifically so Chris can fire one real call from an environment that DOES have
credentials and network access, and tell us what actually comes back. Do not build real
selection/assembly logic on top of these RESPONSE shapes until that has happened - the response
shape (as opposed to the request shape) has never been observed at all, from the UI or otherwise.

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

# CONFIRMED, 2026-08-31 (project doc, the "four booking-shape fields" the conversational flow
# collects once the customer approves the itinerary): "number of rooms (max 4), number of
# travellers (max 9 pax)". Enforced here as hard caps so a caller mistake can't build a request
# that's already known to violate the confirmed limits.
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
    """Builds the `distributions` array that Accommodation's and Closed Tour's Quote requests
    both use (CONFIRMED SHAPE, project doc): ApiBookDistributionRequestVO[] - one entry per ROOM,
    each holding one ApiBookPersonAgeRequestVO (`{"age": int}`) per PERSON in that room, adult or
    child alike (no separate "type" flag in the real schema - age is the only field). Every
    product's Quote request that needs party info references this identical shape (Accommodation,
    Transports, Transfer, Ticket all use ApiBookPersonAgeRequestVO/ApiBookDistributionRequestVO
    per the project doc), so this one function is reused everywhere a party needs shaping -
    see quote_transports()'s docstring for how it maps onto that endpoint's own "persons" field
    name.

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


def build_transport_journey(departure_code: str, departure_type: str, arrival_code: str,
                             arrival_type: str, departure_date: str) -> Dict[str, Any]:
    """One entry of ApiTransportJourneyRequestVO (CONFIRMED SHAPE, project doc):
    {departure, departureType, arrival, arrivalType, departureDate}. `departure_type`/
    `arrival_type` must each be "DESTINATION" or "TRANSPORT_BASE" (ApiTransportQuoteRequestLocationType).
    TRANSPORT_BASE is exactly the concept api_client.py's resolve_transport_base() already
    resolves for Transfer/Transport contracts (airports, ports, stations) - reuse that resolver
    to turn a departure airport name into the code this expects, rather than building a new
    lookup. A round-trip itinerary is two journeys (outbound + return), each its own entry in
    quote_transports()'s `journeys` list."""
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
    # ACCOMMODATION — CONFIRMED endpoint + filter shape, UNCONFIRMED full body
    # ------------------------------------------------------------------
    def quote_accommodations(self, distributions: List[Dict[str, Any]], date_from: str, date_to: str,
                              destination_code: Optional[str] = None,
                              accommodation_ids: Optional[List[str]] = None,
                              best_combinations: bool = True,
                              include_on_request_options: bool = False,
                              max_combinations: Optional[int] = None) -> Dict[str, Any]:
        """POST /booking/accommodations/quote - prices real, live hotel combinations for a
        destination + date range + party. CONFIRMED SHAPE (project doc): the filter sub-object is
        exactly ApiAccommodationQuoteFilterRequestVO = {bestCombinations, includeOnRequestOptions,
        maxCombinations} - notably NO star-rating/board-type/review-score field. This means
        trip_search_rules.py's confirmed budget-tier rules (3/4/5-star, breakfast, review 8+)
        CANNOT be sent as a request filter here; they still need to be applied either by
        pre-selecting candidate `accommodations` ids via a separate search endpoint, or by
        filtering this response afterward - see the project doc's "Next steps" #2, still open.

        ⚠️ destination_code/accommodation_ids: the project doc only captured the filter
        sub-object in detail, not the exact top-level field names for "which destination" or
        "which specific hotels". `destination`/`accommodations` are reasonable, TC-naming-
        convention guesses - adjust once a live response (or a 400 validation error naming a
        missing/wrong field) tells us the real full shape.

        Use the sibling endpoint (project doc: "/booking/accommodations/{id}/quote" - not yet
        wrapped here, add quote_accommodation_combinations() alongside this once needed) to get
        one specific accommodation's full combination set (room types, board types, prices).
        """
        payload: Dict[str, Any] = {
            "distributions": distributions,
            "dateFrom": date_from,
            "dateTo": date_to,
            "filter": {
                "bestCombinations": best_combinations,
                "includeOnRequestOptions": include_on_request_options,
                **({"maxCombinations": max_combinations} if max_combinations is not None else {}),
            },
        }
        if destination_code:
            payload["destination"] = destination_code
        if accommodation_ids:
            payload["accommodations"] = accommodation_ids
        return self._post("/booking/accommodations/quote", payload)

    # ------------------------------------------------------------------
    # TRANSPORTS (flights etc.) — CONFIRMED shape
    # ------------------------------------------------------------------
    def quote_transports(self, journeys: List[Dict[str, Any]], distributions: List[Dict[str, Any]],
                          trip_type: str = "ROUND_TRIP",
                          filter_opts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """POST /booking/transports/quote. CONFIRMED SHAPE (project doc): ApiTransportQuoteRequestVO
        = {journeys, persons, tripType, filter}. Build `journeys` with build_transport_journey()
        (one per leg - an outbound+return round trip is two entries) and `distributions` with
        build_distributions() - the project doc confirms every product's Quote request shares the
        identical ApiBookPersonAgeRequestVO/ApiBookDistributionRequestVO party shape, even though
        THIS endpoint's own field for it is called "persons" rather than "distributions" (kept as
        a `distributions` parameter name here for consistency with every other quote_* method -
        it's mapped onto the request body's real "persons" key inside this function).
        `trip_type` is a guess at the real ApiTransportQuoteRequestVO enum value for a round trip
        (the project doc didn't capture the exact enum members) - "ROUND_TRIP" vs "ONE_WAY" is
        the most likely pairing given the field name, but unconfirmed. There is also a sibling
        `quoteFareFamily` endpoint mentioned in the project doc (not yet wrapped here) for a
        specific fare family's own full price breakdown once a flight has been picked.
        """
        payload: Dict[str, Any] = {
            "journeys": journeys,
            "persons": distributions,
            "tripType": trip_type,
        }
        if filter_opts:
            payload["filter"] = filter_opts
        return self._post("/booking/transports/quote", payload)

    # ------------------------------------------------------------------
    # TRANSFER — ⚠️ endpoint CONFIRMED, full body UNCONFIRMED
    # ------------------------------------------------------------------
    def quote_transfers(self, distributions: List[Dict[str, Any]], pickup_code: str, pickup_type: str,
                         dropoff_code: str, dropoff_type: str, pickup_date: str,
                         filter_opts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """POST /booking/transfer/quote. ⚠️ UNCONFIRMED FULL SHAPE - the project doc confirms
        this endpoint exists and that it shares the same party/distributions structure as
        Accommodation/Closed Tour, but never captured its full request body (unlike Transports'
        `journeys` or Closed Tour's own fields, both fully captured). Modeled here on the same
        departure/arrival-location-type pattern Transports uses (TRANSPORT_BASE/DESTINATION,
        via build_transport_journey()'s same two location-type values), since a transfer is
        conceptually a single pickup->dropoff leg. This is the best-available guess, not a
        confirmed shape - expect to revise field names once a real response (or a 400 naming a
        missing/wrong field) comes back from the live debug panel.
        """
        payload: Dict[str, Any] = {
            "distributions": distributions,
            "pickup": pickup_code,
            "pickupType": pickup_type,
            "dropoff": dropoff_code,
            "dropoffType": dropoff_type,
            "pickupDate": pickup_date,
        }
        if filter_opts:
            payload["filter"] = filter_opts
        return self._post("/booking/transfer/quote", payload)

    # ------------------------------------------------------------------
    # TICKET (Activities/Excursions) — ⚠️ endpoint CONFIRMED, full body UNCONFIRMED
    # ------------------------------------------------------------------
    def quote_tickets(self, distributions: List[Dict[str, Any]], ticket_id: str, date: str,
                       modality_code: Optional[str] = None,
                       filter_opts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """POST /booking/tickets/quote (project doc also lists a sibling
        "/booking/tickets/{id}/quote" for one specific ticket's full modality/combination set -
        not yet wrapped here, add quote_ticket_options() alongside this once needed).
        ⚠️ UNCONFIRMED FULL SHAPE - same caveat as quote_transfers(): the project doc confirms
        the endpoint and the shared party shape, not the full body. Modeled on the real captured
        Ticket UI structure (project doc's "real Ticket (Activities) product structure" section:
        a ticket has a date, a **modality** - a language/service-level variant, e.g. "Standard
        Private" vs. "Standard Private (Italian, French, Spanish or Arabic)" - and per-head or
        per-group pax pricing) - best-available guess, not confirmed.
        """
        payload: Dict[str, Any] = {
            "distributions": distributions,
            "ticketId": ticket_id,
            "date": date,
        }
        if modality_code:
            payload["modalityCode"] = modality_code
        if filter_opts:
            payload["filter"] = filter_opts
        return self._post("/booking/tickets/quote", payload)

    # ------------------------------------------------------------------
    # CLOSED TOUR — CONFIRMED shape
    # ------------------------------------------------------------------
    def quote_closed_tour(self, closed_tour_id: str, start_date: str, distributions: List[Dict[str, Any]],
                           origin_code: str = "", pre_nights: int = 0, post_nights: int = 0) -> Dict[str, Any]:
        """POST /booking/closedtour/{id}/quote. CONFIRMED SHAPE (project doc):
        ApiClosedTourQuoteRequestVO = {startDate, distributions, originCode, preNights, postNights}.
        `origin_code` is Closed Tour's own, simpler flight-origin field - separate from the
        general Transports `journeys` structure quote_transports() uses, only relevant for a
        flight-inclusive Closed Tour package.
        """
        payload = {
            "startDate": start_date,
            "distributions": distributions,
            "originCode": origin_code,
            "preNights": pre_nights,
            "postNights": post_nights,
        }
        return self._post(f"/booking/closedtour/{closed_tour_id}/quote", payload)
